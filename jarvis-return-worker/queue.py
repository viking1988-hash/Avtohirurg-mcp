from time import monotonic
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .approval import action_fingerprint, valid_approval
from .audit import record

class Conflict(Exception):
    pass

class QueueRepository:
    def __init__(self, config):
        self.config = config

    @contextmanager
    def connection(self):
        with psycopg.connect(self.config.database_url, connect_timeout=5, row_factory=dict_row,
                            options='-c statement_timeout=5000 -c lock_timeout=3000') as conn:
            identity = conn.execute('SELECT current_database() AS db,current_user AS role').fetchone()
            if identity != {'db': 'jarvis_return_queue_test', 'role': 'jarvis_sandbox'}:
                raise RuntimeError('server sandbox identity mismatch')
            yield conn

    def migrate(self):
        with self.connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(20261010, 1)')
            c.execute(Path(__file__).with_name('schema.sql').read_text())

    def enqueue(self, task_id, destination, payload, correlation_id, actor='sandbox-preparer'):
        if not task_id or not correlation_id or not destination.startswith('sandbox:'):
            raise ValueError('sandbox task and correlation required')
        fp = action_fingerprint(task_id, destination, payload)
        with self.connection() as c:
            r = c.execute('''INSERT INTO jarvis_return_actions
                (task_id,destination_key,payload,correlation_id,action_fingerprint,state,chat_id)
                VALUES (%s,%s,%s,%s,%s,'READY',%s) ON CONFLICT DO NOTHING RETURNING *''',
                (task_id,destination,Jsonb(payload),correlation_id,fp,
                 destination.removeprefix('sandbox:telegram:') if destination.startswith('sandbox:telegram:') else None)).fetchone()
            if r:
                # OPEN and READY are recorded in the same transaction: validation prepares the task.
                record(c,None,{**r,'state':'OPEN'},'enqueue',actor)
                record(c,{**r,'state':'OPEN'},r,'prepare',actor)
                return r
            r = c.execute('SELECT * FROM jarvis_return_actions WHERE task_id=%s',(task_id,)).fetchone()
            if r['action_fingerprint'] != fp or r['correlation_id'] != correlation_id:
                raise Conflict('task id already bound to another action')
            if destination.startswith('sandbox:telegram:'):
                from .metrics import prevent_telegram_duplicate
                prevent_telegram_duplicate()
            record(c,r,r,'enqueue_duplicate',actor)
            return r

    def get(self, task_id):
        with self.connection() as c:
            return c.execute('SELECT * FROM jarvis_return_actions WHERE task_id=%s',(task_id,)).fetchone()

    def list(self, limit=100):
        with self.connection() as c:
            return c.execute('SELECT * FROM jarvis_return_actions ORDER BY created_at,task_id LIMIT %s',(limit,)).fetchall()

    def approve(self, task_id, fingerprint, expires_at, human):
        if not human or expires_at.tzinfo is None:
            raise Conflict('authenticated human and timezone required')
        with self.connection() as c:
            b = c.execute('SELECT * FROM jarvis_return_actions WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
            now = c.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            if (not b or b['state'] != 'READY' or b['payload'] is None
                or not now < expires_at <= now + __import__('datetime').timedelta(hours=24)
                or fingerprint != b['action_fingerprint']
                or fingerprint != action_fingerprint(task_id,b['destination_key'],b['payload'])):
                raise Conflict('approval must bind an eligible unchanged task for <=24 hours')
            r = c.execute('''UPDATE jarvis_return_actions SET state='APPROVED',approved_by=%s,
                approved_at=clock_timestamp(),approval_expires_at=%s,approved_fingerprint=%s,
                version=version+1,updated_at=now() WHERE task_id=%s RETURNING *''',
                (human,expires_at,fingerprint,task_id)).fetchone()
            record(c,b,r,'approve',human)
            return r

    def claim(self, owner):
        if not owner:
            raise ValueError('lease owner required')
        with self.connection() as c:
            b = c.execute('''SELECT * FROM jarvis_return_actions WHERE state='APPROVED'
                AND payload IS NOT NULL AND correlation_id IS NOT NULL
                AND destination_key LIKE 'sandbox:%%' AND approval_expires_at>clock_timestamp()
                AND approved_fingerprint=action_fingerprint AND next_attempt_at<=clock_timestamp()
                AND attempt_count<%s ORDER BY created_at,task_id FOR UPDATE SKIP LOCKED LIMIT 1''',
                (self.config.max_attempts,)).fetchone()
            if not b:
                return None
            if not valid_approval(b):
                r = c.execute("UPDATE jarvis_return_actions SET state='FAILED',last_error='approval_integrity',version=version+1 WHERE task_id=%s RETURNING *",(b['task_id'],)).fetchone()
                record(c,b,r,'claim_rejected',owner)
                return None
            r = c.execute('''UPDATE jarvis_return_actions SET state='PROCESSING',lease_owner=%s,
                lease_until=clock_timestamp()+(%s*interval '1 second'),heartbeat_at=clock_timestamp(),
                processing_started_at=clock_timestamp(),attempt_count=attempt_count+1,
                version=version+1,updated_at=now() WHERE task_id=%s RETURNING *''',
                (owner,self.config.lease_seconds,b['task_id'])).fetchone()
            record(c,b,r,'claim',owner)
            return r

    def _owned(self,c,task_id,owner,attempt):
        b = c.execute('SELECT * FROM jarvis_return_actions WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        now = c.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        if not b or b['state']!='PROCESSING' or b['lease_owner']!=owner or b['attempt_count']!=attempt or b['lease_until']<=now:
            raise Conflict('stale lease or fencing token')
        return b

    def heartbeat(self, task_id, owner, attempt):
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            if not valid_approval(b):
                raise Conflict('approval no longer valid')
            r=c.execute('''UPDATE jarvis_return_actions SET heartbeat_at=clock_timestamp(),
                lease_until=clock_timestamp()+(%s*interval '1 second'),updated_at=now()
                WHERE task_id=%s RETURNING *''',(self.config.lease_seconds,task_id)).fetchone()
            record(c,b,r,'heartbeat',owner)
            return r

    def mark_delivery_started(self, task_id, owner, attempt):
        """Persist the external-effect boundary before invoking any transport."""
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            if not valid_approval(b):
                raise Conflict('approval no longer valid before delivery')
            if b['delivery_started_at']:
                if b['destination_key'].startswith('sandbox:telegram:'):
                    from .metrics import prevent_telegram_duplicate
                    prevent_telegram_duplicate()
                raise Conflict('delivery was already started for this lease')
            r=c.execute('''UPDATE jarvis_return_actions SET delivery_started_at=clock_timestamp(),
                updated_at=now() WHERE task_id=%s RETURNING *''',(task_id,)).fetchone()
            record(c,b,r,'delivery_started',owner)
            return r

    def save_delivery_receipt(self, task_id, owner, attempt, receipt_id, message_id=None, chat_id=None, response=None):
        """Durably bind provider receipt/message identifiers before DONE."""
        if not receipt_id:
            raise ValueError('receipt id required')
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            if b['delivery_receipt'] and (b['delivery_receipt']!=str(receipt_id)
                or b['external_message_id']!=(str(message_id) if message_id is not None else None)):
                raise Conflict('delivery receipt identifiers cannot be replaced')
            if b['delivery_receipt'] and (chat_id is not None and b.get('chat_id')!=str(chat_id)):
                raise Conflict('delivery chat id cannot be replaced')
            r=c.execute('''UPDATE jarvis_return_actions SET delivery_receipt=%s,
                external_message_id=%s,chat_id=coalesce(%s,chat_id),delivery_response=coalesce(%s,delivery_response),
                updated_at=now() WHERE task_id=%s RETURNING *''',
                (str(receipt_id),str(message_id) if message_id is not None else None,
                 str(chat_id) if chat_id is not None else None,Jsonb(response) if response is not None else None,task_id)).fetchone()
            record(c,b,r,'delivery_receipt_saved',owner,metadata={'has_message_id':message_id is not None,'has_chat_id':chat_id is not None})
            return r

    def fake_delivery(self, task_id, owner, attempt):
        # Lock + verify immediately before Fake effect; durable deduplication and audit are atomic.
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            if not valid_approval(b):
                raise Conflict('approval invalid before transport')
            key=task_id+':'+b['action_fingerprint']
            receipt='sandbox:receipt:'+key
            c.execute('''INSERT INTO jarvis_fake_receipts(idempotency_key,task_id,fingerprint,receipt)
                VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(key,task_id,b['action_fingerprint'],receipt))
            r=c.execute('SELECT receipt FROM jarvis_fake_receipts WHERE idempotency_key=%s',(key,)).fetchone()
            if r['receipt']!=receipt:
                raise Conflict('receipt integrity failure')
            record(c,b,b,'fake_receipt',owner)
            return receipt

    def finish(self, task_id, owner, attempt, receipt):
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            key=task_id+':'+b['action_fingerprint']
            actual=c.execute('SELECT receipt FROM jarvis_fake_receipts WHERE idempotency_key=%s AND task_id=%s AND fingerprint=%s',
                (key,task_id,b['action_fingerprint'])).fetchone()
            fake_receipt = actual and receipt==actual['receipt'] and receipt=='sandbox:receipt:'+key
            if not fake_receipt and (not b['delivery_receipt'] or receipt!=b['delivery_receipt']):
                raise Conflict('durable delivery receipt required')
            r=c.execute('''UPDATE jarvis_return_actions SET state='DONE',delivery_receipt=%s,
                lease_owner=NULL,lease_until=NULL,version=version+1,updated_at=now()
                WHERE task_id=%s RETURNING *''',(receipt,task_id)).fetchone()
            record(c,b,r,'finish',owner, metadata={'duration': max(0,(datetime.now(timezone.utc)-b['processing_started_at']).total_seconds())})
            return r

    def cancel(self,task_id,actor):
        with self.connection() as c:
            b=c.execute('SELECT * FROM jarvis_return_actions WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
            if not b or b['state'] not in ('OPEN','READY','APPROVED','RETRY','FAILED'):
                raise Conflict('cannot cancel terminal or in-flight task; reconcile first')
            r=c.execute("UPDATE jarvis_return_actions SET state='CANCELLED',lease_owner=NULL,lease_until=NULL,version=version+1,updated_at=now() WHERE task_id=%s RETURNING *",(task_id,)).fetchone()
            record(c,b,r,'cancel',actor)
            return r

    def requeue(self,task_id,actor):
        with self.connection() as c:
            b=c.execute('SELECT * FROM jarvis_return_actions WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
            if not b or b['state'] not in ('FAILED','RETRY') or b['payload'] is None or b['attempt_count']>=self.config.max_attempts:
                raise Conflict('task not eligible for requeue')
            if c.execute('SELECT 1 FROM jarvis_fake_receipts WHERE task_id=%s',(task_id,)).fetchone():
                raise Conflict('delivery already recorded; reconcile instead')
            r=c.execute('''UPDATE jarvis_return_actions SET state='READY',approved_by=NULL,approved_at=NULL,
                approval_expires_at=NULL,approved_fingerprint=NULL,lease_owner=NULL,lease_until=NULL,
                heartbeat_at=NULL,delivery_started_at=NULL,delivery_receipt=NULL,external_message_id=NULL,
                next_attempt_at=now(),version=version+1,updated_at=now()
                WHERE task_id=%s RETURNING *''',(task_id,)).fetchone()
            record(c,b,r,'requeue_requires_fresh_approval',actor)
            return r

    def fail(self, task_id,owner,attempt):
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            uncertain=bool(b['delivery_started_at'] and not b['delivery_receipt'])
            completed=bool(b['delivery_receipt'])
            state='DONE' if completed else 'RECONCILIATION_REQUIRED' if uncertain else 'FAILED'
            error=None if completed else 'delivery_outcome_unknown' if uncertain else 'worker_failure'
            r=c.execute("UPDATE jarvis_return_actions SET state=%s,last_error=%s,lease_owner=NULL,lease_until=NULL,version=version+1,updated_at=now() WHERE task_id=%s RETURNING *",(state,error,task_id)).fetchone()
            record(c,b,r,'receipt_recovered' if completed else 'delivery_uncertain' if uncertain else 'fail',owner)
            return r

    def retry_confirmed_rejection(self, task_id, owner, attempt, retry_after=1):
        """Retry only after Telegram explicitly rejects the request (e.g. HTTP 429)."""
        delay=max(1,min(int(retry_after or 1),3600))
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            if b['delivery_receipt']:
                raise Conflict('receipt exists; refusing retry')
            r=c.execute('''UPDATE jarvis_return_actions SET state='APPROVED',delivery_started_at=NULL,
                lease_owner=NULL,lease_until=NULL,heartbeat_at=NULL,last_error='telegram_rate_limited',
                next_attempt_at=clock_timestamp()+(%s*interval '1 second'),version=version+1,updated_at=now()
                WHERE task_id=%s RETURNING *''',(delay,task_id)).fetchone()
            record(c,b,r,'telegram_retry_after_rejection',owner,metadata={'retry_after_seconds':delay})
            return r

    def fail_permanent_rejection(self, task_id, owner, attempt, code):
        """A definitive provider rejection proves that no delivery occurred."""
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            if b['delivery_receipt']:
                raise Conflict('receipt exists; refusing failure transition')
            r=c.execute('''UPDATE jarvis_return_actions SET state='FAILED',delivery_started_at=NULL,
                lease_owner=NULL,lease_until=NULL,heartbeat_at=NULL,last_error=%s,
                version=version+1,updated_at=now() WHERE task_id=%s RETURNING *''',
                (str(code)[:100],task_id)).fetchone()
            record(c,b,r,'telegram_permanent_rejection',owner)
            return r

    def recover(self,owner):
        recovered=0
        with self.connection() as c:
            rows=c.execute('''SELECT * FROM jarvis_return_actions WHERE state='PROCESSING'
                AND payload IS NOT NULL AND correlation_id IS NOT NULL
                AND lease_until<clock_timestamp()
                AND (heartbeat_at IS NULL OR heartbeat_at<clock_timestamp()-(%s*interval '1 second'))
                ORDER BY lease_until FOR UPDATE SKIP LOCKED LIMIT 100''',(self.config.heartbeat_seconds*2,)).fetchall()
            for b in rows:
                if b['delivery_receipt']:
                    state='DONE'
                    error=None
                elif b['delivery_started_at']:
                    state='RECONCILIATION_REQUIRED'
                    error='delivery_outcome_unknown'
                else:
                    valid=valid_approval(b)
                    eligible=valid and b['attempt_count']<self.config.max_attempts
                    state='APPROVED' if eligible else 'FAILED'
                    error='lease_expired'
                r=c.execute('''UPDATE jarvis_return_actions SET state=%s,lease_owner=NULL,lease_until=NULL,
                    heartbeat_at=NULL,version=version+1,updated_at=now(),last_error=%s,
                    next_attempt_at=clock_timestamp()+(%s*interval '1 second') WHERE task_id=%s RETURNING *''',
                    (state,error,min(300,2**min(b['attempt_count'],8)),b['task_id'])).fetchone()
                record(c,b,r,'recovery',owner)
                recovered+=1
        return recovered

    def reconcile(self,task_id,actor,outcome,receipt_id=None,message_id=None):
        """Human-only resolution; uncertain delivery is never retried automatically."""
        if not actor or outcome not in ('delivered','not_delivered'):
            raise Conflict('human actor and explicit delivery outcome required')
        if outcome=='delivered' and not receipt_id:
            raise Conflict('receipt id required to confirm delivery')
        with self.connection() as c:
            b=c.execute('SELECT * FROM jarvis_return_actions WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
            if not b or b['state']!='RECONCILIATION_REQUIRED':
                raise Conflict('task is not awaiting reconciliation')
            if outcome=='delivered':
                r=c.execute("""UPDATE jarvis_return_actions SET state='DONE',delivery_receipt=%s,
                    external_message_id=%s,lease_owner=NULL,lease_until=NULL,last_error=NULL,
                    version=version+1,updated_at=now() WHERE task_id=%s RETURNING *""",
                    (str(receipt_id),str(message_id) if message_id is not None else None,task_id)).fetchone()
            else:
                r=c.execute("""UPDATE jarvis_return_actions SET state='READY',approved_by=NULL,
                    approved_at=NULL,approval_expires_at=NULL,approved_fingerprint=NULL,
                    delivery_started_at=NULL,delivery_receipt=NULL,external_message_id=NULL,
                    lease_owner=NULL,lease_until=NULL,heartbeat_at=NULL,next_attempt_at=now(),
                    last_error='operator_confirmed_not_delivered',version=version+1,updated_at=now()
                    WHERE task_id=%s RETURNING *""",(task_id,)).fetchone()
            record(c,b,r,'reconcile_'+outcome,actor,metadata={'has_message_id':message_id is not None})
            return r

    def stats(self):
        with self.connection() as c:
            rows=c.execute('SELECT state,count(*) AS count FROM jarvis_return_actions GROUP BY state').fetchall()
            oldest=c.execute("SELECT coalesce(extract(epoch FROM clock_timestamp()-min(created_at)),0)::float AS age FROM jarvis_return_actions WHERE state IN ('READY','APPROVED','RETRY')").fetchone()['age']
            counts=c.execute("SELECT coalesce(sum(attempt_count-1) FILTER(WHERE attempt_count>1),0) AS retry_count FROM jarvis_return_actions").fetchone()
            events=c.execute("SELECT metadata->>'action' AS action,count(*) AS count FROM jarvis_return_action_events WHERE metadata->>'action' IN ('approve','recovery') GROUP BY 1").fetchall()
            return {**{r['state']:r['count'] for r in rows},'oldest_task':max(0,oldest),**counts,**{r['action']:r['count'] for r in events}}
