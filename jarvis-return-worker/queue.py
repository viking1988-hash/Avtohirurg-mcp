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

    def mark_delivery_started(self, task_id, owner, attempt, provider=None):
        """Persist the external-effect boundary before invoking any transport."""
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            if not valid_approval(b):
                raise Conflict('approval no longer valid before delivery')
            if provider and b.get('provider') not in (None, provider):
                raise Conflict('delivery provider cannot change during an attempt')
            if b['delivery_started_at']:
                if b['destination_key'].startswith('sandbox:telegram:'):
                    from .metrics import prevent_telegram_duplicate
                    prevent_telegram_duplicate()
                raise Conflict('delivery was already started for this lease')
            r=c.execute('''UPDATE jarvis_return_actions SET delivery_started_at=clock_timestamp(),
                provider=coalesce(%s,provider),delivery_status='STARTED',updated_at=now()
                WHERE task_id=%s RETURNING *''',(provider,task_id)).fetchone()
            record(c,b,r,'delivery_started',owner)
            return r

    def save_delivery_receipt(self, task_id, owner, attempt, receipt_id, message_id=None, chat_id=None, response=None, provider=None):
        """Durably bind provider receipt/message identifiers before DONE."""
        if not receipt_id:
            raise ValueError('receipt id required')
        with self.connection() as c:
            b=self._owned(c,task_id,owner,attempt)
            if provider and b.get('provider') not in (None, provider):
                raise Conflict('delivery provider cannot change within one task attempt')
            if b['delivery_receipt'] and (b['delivery_receipt']!=str(receipt_id)
                or b['external_message_id']!=(str(message_id) if message_id is not None else None)):
                raise Conflict('delivery receipt identifiers cannot be replaced')
            if b['delivery_receipt'] and (chat_id is not None and b.get('chat_id')!=str(chat_id)):
                raise Conflict('delivery chat id cannot be replaced')
            r=c.execute('''UPDATE jarvis_return_actions SET delivery_receipt=%s,
                provider_receipt=%s,external_message_id=%s,provider_message_id=%s,
                provider=coalesce(%s,provider),delivery_status='DELIVERED',
                chat_id=coalesce(%s,chat_id),delivery_response=coalesce(%s,delivery_response),
                updated_at=now() WHERE task_id=%s RETURNING *''',
                (str(receipt_id),str(receipt_id),str(message_id) if message_id is not None else None,
                 str(message_id) if message_id is not None else None,provider,
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
            state='APPROVED' if b['attempt_count']<self.config.max_attempts else 'FAILED'
            r=c.execute('''UPDATE jarvis_return_actions SET state=%s,delivery_started_at=NULL,
                lease_owner=NULL,lease_until=NULL,heartbeat_at=NULL,last_error='telegram_rate_limited',
                next_attempt_at=clock_timestamp()+(%s*interval '1 second'),version=version+1,updated_at=now()
                WHERE task_id=%s RETURNING *''',(state,delay,task_id)).fetchone()
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
                    heartbeat_at=NULL,delivery_status=CASE WHEN %s='RECONCILIATION_REQUIRED' THEN 'UNKNOWN' WHEN %s='DONE' THEN 'DELIVERED' ELSE delivery_status END,
                    reconciliation_status=CASE WHEN %s='RECONCILIATION_REQUIRED' THEN 'REQUIRED' WHEN %s='DONE' THEN 'DELIVERED_CONFIRMED' ELSE reconciliation_status END,
                    reconciliation_reason=CASE WHEN %s='RECONCILIATION_REQUIRED' THEN 'delivery_outcome_unknown' ELSE reconciliation_reason END,
                    reconciliation_at=CASE WHEN %s='RECONCILIATION_REQUIRED' THEN clock_timestamp() ELSE reconciliation_at END,
                    version=version+1,updated_at=now(),last_error=%s,
                    next_attempt_at=clock_timestamp()+(%s*interval '1 second') WHERE task_id=%s RETURNING *''',
                    (state,state,state,state,state,state,state,error,min(300,2**min(b['attempt_count'],8)),b['task_id'])).fetchone()
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

    def claim_crm_operation(self,owner):
        """Fence one CRM intent; an expired send is uncertain, never re-queued."""
        if not owner:
            raise ValueError('CRM lease owner required')
        with self.connection() as c:
            expired=c.execute("""UPDATE jarvis_crm_operations SET state='RECONCILIATION_REQUIRED',
                reconciliation_reason='lease_expired_after_claim',lease_owner=NULL,lease_until=NULL,
                updated_at=clock_timestamp() WHERE state='PROCESSING' AND lease_until<clock_timestamp()
                RETURNING event_id""").fetchall()
            for item in expired:
                c.execute("""INSERT INTO jarvis_crm_operation_events(event_id,action,actor,reason)
                    VALUES (%s,'reconciliation_required',%s,'lease_expired_after_claim')""",
                    (item['event_id'],owner))
            if expired:
                from .metrics import crm_reconciliation_required
                for _ in expired: crm_reconciliation_required()
            row=c.execute("""SELECT * FROM jarvis_crm_operations WHERE state='PENDING'
                AND next_attempt_at<=clock_timestamp() ORDER BY created_at,event_id
                FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if not row:
                return None
            claimed=c.execute("""UPDATE jarvis_crm_operations SET state='PROCESSING',lease_owner=%s,
                lease_until=clock_timestamp()+(%s*interval '1 second'),attempt_count=attempt_count+1,
                updated_at=clock_timestamp() WHERE event_id=%s RETURNING *""",
                (owner,self.config.lease_seconds,row['event_id'])).fetchone()
            c.execute("""INSERT INTO jarvis_crm_operation_events(event_id,action,actor)
                VALUES (%s,'claimed',%s)""",(row['event_id'],owner))
            return claimed

    def _crm_owned(self,c,event_id,owner,attempt):
        row=c.execute("SELECT * FROM jarvis_crm_operations WHERE event_id=%s FOR UPDATE",(event_id,)).fetchone()
        now=c.execute("SELECT clock_timestamp() AS now").fetchone()['now']
        if (not row or row['state']!='PROCESSING' or row['lease_owner']!=owner
                or row['attempt_count']!=attempt or row['lease_until']<=now):
            raise Conflict('stale CRM lease or fencing token')
        return row

    def finish_crm_operation(self,event_id,owner,attempt,result):
        receipt=result.get('receipt_id') or result.get('provider_receipt')
        external=result.get('external_booking_id')
        status=result.get('status')
        if not receipt or not external or not status:
            raise Conflict('CRM receipt, external booking ID and status required')
        with self.connection() as c:
            row=self._crm_owned(c,event_id,owner,attempt)
            saved=c.execute("""UPDATE jarvis_crm_operations SET state='DONE',crm_status=%s,
                external_booking_id=%s,provider_receipt=%s,reconciliation_reason=NULL,
                lease_owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                WHERE event_id=%s RETURNING *""",
                (str(status)[:80],str(external)[:256],str(receipt)[:512],event_id)).fetchone()
            c.execute("""INSERT INTO jarvis_crm_operation_events(event_id,action,actor)
                VALUES (%s,'completed',%s)""",(event_id,owner))
            return saved

    def fail_crm_operation(self,event_id,owner,attempt,classification,reason,retry_after=None):
        """Only definitive provider rejection can return an operation to PENDING."""
        from .transport import Classification
        kind=Classification(classification)
        delay=max(1,min(int(retry_after or 1),3600))
        if kind==Classification.RETRYABLE:
            state='PENDING'; recon_reason=None; last_error=str(reason)[:100]
        elif kind==Classification.PERMANENT:
            state='FAILED'; recon_reason=None; last_error=str(reason)[:100]
        else:
            state='RECONCILIATION_REQUIRED'; recon_reason=str(reason)[:200] or 'provider_outcome_unknown'; last_error=recon_reason
        with self.connection() as c:
            row=self._crm_owned(c,event_id,owner,attempt)
            if kind==Classification.RETRYABLE and row['attempt_count']>=self.config.max_attempts:
                state='FAILED'
            saved=c.execute("""UPDATE jarvis_crm_operations SET state=%s,
                reconciliation_reason=%s,lease_owner=NULL,lease_until=NULL,
                next_attempt_at=clock_timestamp()+(%s*interval '1 second'),updated_at=clock_timestamp()
                WHERE event_id=%s RETURNING *""",
                (state,recon_reason,delay,event_id)).fetchone()
            c.execute("""INSERT INTO jarvis_crm_operation_events(event_id,action,actor,reason)
                VALUES (%s,%s,%s,%s)""",
                (event_id,'reconciliation_required' if state=='RECONCILIATION_REQUIRED' else state.lower(),owner,last_error))
            if state=='RECONCILIATION_REQUIRED':
                from .metrics import crm_reconciliation_required
                crm_reconciliation_required()
            return saved

    def get_crm_operation(self,event_id):
        with self.connection() as c:
            return c.execute("SELECT * FROM jarvis_crm_operations WHERE event_id=%s",(event_id,)).fetchone()

    def resolve_crm_reconciliation(self,event_id,actor,result,reason,expected_version):
        if not actor:
            raise Conflict('authenticated operator required')
        external=result.get('external_booking_id')
        receipt=result.get('receipt_id') or result.get('provider_receipt')
        status=result.get('status')
        proven=bool(external and receipt and status and str(status).upper() not in ('UNKNOWN','NOT_FOUND'))
        with self.connection() as c:
            row=c.execute("SELECT * FROM jarvis_crm_operations WHERE event_id=%s FOR UPDATE",(event_id,)).fetchone()
            if not row or row['state']!='RECONCILIATION_REQUIRED':
                raise Conflict('CRM operation is not awaiting reconciliation')
            if row['version'] != expected_version:
                raise Conflict('stale CRM operation version')
            state='DONE' if proven else 'RECONCILIATION_REQUIRED'
            updated=c.execute("""UPDATE jarvis_crm_operations SET state=%s,
                crm_status=coalesce(%s,crm_status),external_booking_id=coalesce(%s,external_booking_id),
                provider_receipt=coalesce(%s,provider_receipt),
                reconciliation_reason=%s,version=version+1,updated_at=clock_timestamp()
                WHERE event_id=%s RETURNING *""",
                (state,str(status)[:80] if status else None,
                 str(external)[:256] if external else None,str(receipt)[:512] if receipt else None,
                 None if proven else str(reason or 'provider_did_not_prove_outcome')[:200],event_id)).fetchone()
            c.execute("""INSERT INTO jarvis_crm_operation_events(event_id,action,actor,reason)
                VALUES (%s,%s,%s,%s)""",
                (event_id,'reconciled_delivered' if proven else 'reconciliation_pending',actor,
                 None if proven else str(reason or 'provider_did_not_prove_outcome')[:200]))
            return updated

    @staticmethod
    def _append_email_lifecycle(conn, operation, event_type, action):
        from .outbox import append_event
        append_event(conn, aggregate_id=operation['event_id'], aggregate_type='email',
            event_type=event_type, event_version=1,
            payload={'request_event_id':operation['event_id'],'state':operation['state'],
                     'action':action,'provider_message_id':operation.get('provider_message_id'),
                     'smtp_message_id':operation.get('smtp_message_id')},
            correlation_id=operation.get('correlation_id'),
            causation_id=f"email:{operation['event_id']}:{event_type}:{operation['version']}")

    def claim_email_operation(self, owner):
        if not owner:
            raise ValueError('Email lease owner required')
        with self.connection() as c:
            expired=c.execute("""UPDATE jarvis_email_operations SET state='RECONCILIATION_REQUIRED',
                reconciliation_reason='lease_expired_after_smtp_attempt',lease_owner=NULL,lease_until=NULL,
                version=version+1,updated_at=clock_timestamp() WHERE state='PROCESSING'
                AND lease_until<clock_timestamp() RETURNING *""").fetchall()
            for operation in expired:
                c.execute("""INSERT INTO jarvis_email_operation_events(event_id,action,actor,reason)
                    VALUES (%s,'reconciliation_required',%s,'lease_expired_after_smtp_attempt')""",
                    (operation['event_id'],owner))
                self._append_email_lifecycle(c,operation,'EmailUnknown','lease_timeout')
                from .metrics import email_reconciliation_required
                email_reconciliation_required()
            row=c.execute("""SELECT * FROM jarvis_email_operations WHERE state='PENDING'
                AND next_attempt_at<=clock_timestamp() ORDER BY created_at,event_id
                FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if not row:
                return None
            claimed=c.execute("""UPDATE jarvis_email_operations SET state='PROCESSING',lease_owner=%s,
                lease_until=clock_timestamp()+(%s*interval '1 second'),attempt_count=attempt_count+1,
                updated_at=clock_timestamp() WHERE event_id=%s RETURNING *""",
                (owner,self.config.lease_seconds,row['event_id'])).fetchone()
            c.execute("""INSERT INTO jarvis_email_operation_events(event_id,action,actor)
                VALUES (%s,'claimed',%s)""",(row['event_id'],owner))
            return claimed

    def _email_owned(self,c,event_id,owner,attempt):
        row=c.execute("SELECT * FROM jarvis_email_operations WHERE event_id=%s FOR UPDATE",(event_id,)).fetchone()
        now=c.execute("SELECT clock_timestamp() AS now").fetchone()['now']
        if (not row or row['state']!='PROCESSING' or row['lease_owner']!=owner
                or row['attempt_count']!=attempt or row['lease_until']<=now):
            raise Conflict('stale Email lease or fencing token')
        return row

    def finish_email_operation(self,event_id,owner,attempt,result):
        receipt=result.get('receipt'); smtp_message_id=result.get('smtp_message_id')
        if not receipt or not smtp_message_id or result.get('provider_status')!='accepted':
            raise Conflict('SMTP acceptance receipt required')
        with self.connection() as c:
            row=self._email_owned(c,event_id,owner,attempt)
            saved=c.execute("""UPDATE jarvis_email_operations SET state='QUEUED',
                provider_message_id=%s,smtp_message_id=%s,receipt=%s,provider_status=%s,
                delivery_time=%s,reconciliation_reason=NULL,lease_owner=NULL,lease_until=NULL,
                version=version+1,updated_at=clock_timestamp() WHERE event_id=%s RETURNING *""",
                (result.get('provider_message_id'),str(smtp_message_id)[:256],str(receipt)[:512],
                 'accepted',result.get('delivery_time'),event_id)).fetchone()
            c.execute("""INSERT INTO jarvis_email_operation_events(event_id,action,actor)
                VALUES (%s,'smtp_accepted',%s)""",(event_id,owner))
            self._append_email_lifecycle(c,saved,'EmailQueued','smtp_accepted')
            return saved

    def fail_email_operation(self,event_id,owner,attempt,classification,reason,retry_after=None):
        from .transport import Classification
        kind=Classification(classification)
        delay=max(1,min(int(retry_after or 1),3600))
        if kind==Classification.RETRYABLE:
            state='PENDING'; recon_reason=None; action='retry_scheduled'
        elif kind==Classification.PERMANENT:
            state='FAILED'; recon_reason=None; action='failed'
        else:
            state='RECONCILIATION_REQUIRED'; recon_reason=str(reason or 'smtp_outcome_unknown')[:200]; action='reconciliation_required'
        with self.connection() as c:
            row=self._email_owned(c,event_id,owner,attempt)
            if kind==Classification.RETRYABLE and row['attempt_count']>=self.config.max_attempts:
                state='FAILED'; action='failed'
            saved=c.execute("""UPDATE jarvis_email_operations SET state=%s,reconciliation_reason=%s,
                lease_owner=NULL,lease_until=NULL,next_attempt_at=clock_timestamp()+(%s*interval '1 second'),
                version=version+1,updated_at=clock_timestamp() WHERE event_id=%s RETURNING *""",
                (state,recon_reason,delay,event_id)).fetchone()
            c.execute("""INSERT INTO jarvis_email_operation_events(event_id,action,actor,reason)
                VALUES (%s,%s,%s,%s)""",(event_id,action,owner,str(reason)[:200]))
            if state=='RECONCILIATION_REQUIRED':
                self._append_email_lifecycle(c,saved,'EmailUnknown',str(reason)[:200])
                from .metrics import email_reconciliation_required
                email_reconciliation_required()
            elif state=='FAILED':
                self._append_email_lifecycle(c,saved,'EmailFailed',str(reason)[:200])
            return saved

    def cancel_email_operation(self,event_id,actor,expected_version):
        if not actor:
            raise Conflict('authenticated operator required')
        with self.connection() as c:
            row=c.execute("SELECT * FROM jarvis_email_operations WHERE event_id=%s FOR UPDATE",(event_id,)).fetchone()
            if not row or row['state']!='PENDING':
                raise Conflict('only unsent pending Email operations can be cancelled')
            if row['version']!=expected_version:
                raise Conflict('stale Email operation version')
            saved=c.execute("""UPDATE jarvis_email_operations SET state='CANCELLED',version=version+1,
                updated_at=clock_timestamp() WHERE event_id=%s RETURNING *""",(event_id,)).fetchone()
            c.execute("""INSERT INTO jarvis_email_operation_events(event_id,action,actor)
                VALUES (%s,'cancelled',%s)""",(event_id,actor))
            self._append_email_lifecycle(c,saved,'EmailCancelled','operator_cancelled_before_send')
            return saved

    def resolve_email_reconciliation(self,event_id,actor,expected_version,outcome,evidence):
        if not actor or outcome not in ('delivered','not_delivered'):
            raise Conflict('authenticated operator and valid reconciliation outcome required')
        evidence=evidence or {}
        if outcome=='delivered' and not (evidence.get('receipt') or evidence.get('provider_message_id')):
            raise Conflict('delivery evidence required')
        with self.connection() as c:
            row=c.execute("SELECT * FROM jarvis_email_operations WHERE event_id=%s FOR UPDATE",(event_id,)).fetchone()
            if not row:
                raise Conflict('Email operation is not awaiting reconciliation')
            if row['version']!=expected_version:
                raise Conflict('stale Email operation version')
            if row['state']!='RECONCILIATION_REQUIRED':
                raise Conflict('Email operation is not awaiting reconciliation')
            new_state='SENT' if outcome=='delivered' else 'FAILED'
            status='delivered_confirmed' if outcome=='delivered' else 'undelivered_confirmed'
            saved=c.execute("""UPDATE jarvis_email_operations SET state=%s,provider_status=%s,
                provider_message_id=coalesce(%s,provider_message_id),receipt=coalesce(%s,receipt),
                delivery_time=CASE WHEN %s='SENT' THEN coalesce(delivery_time,clock_timestamp()) ELSE delivery_time END,
                reconciliation_reason=NULL,version=version+1,updated_at=clock_timestamp()
                WHERE event_id=%s RETURNING *""",
                (new_state,status,evidence.get('provider_message_id'),evidence.get('receipt'),new_state,event_id)).fetchone()
            c.execute("""INSERT INTO jarvis_email_operation_events(event_id,action,actor,reason)
                VALUES (%s,%s,%s,%s)""",(event_id,'reconciled_'+outcome,actor,str(evidence.get('reason',''))[:200]))
            self._append_email_lifecycle(c,saved,'EmailSent' if outcome=='delivered' else 'EmailFailed',status)
            return saved

    def get_email_operation(self,event_id):
        with self.connection() as c:
            return c.execute("SELECT * FROM jarvis_email_operations WHERE event_id=%s",(event_id,)).fetchone()

    def record_ai_usage(self, usage):
        """Persist usage metadata only; request and response content are never accepted."""
        from .ai_cost import normalize_usage
        row=normalize_usage(usage)
        with self.connection() as c:
            inserted=c.execute("""INSERT INTO jarvis_ai_usage_events
                (request_id,correlation_id,provider,model,input_tokens,output_tokens,
                 input_rate_micro_usd_per_million,output_rate_micro_usd_per_million,
                 cost_micro_usd,latency_ms,outcome,booking_success)
                VALUES (%(request_id)s,%(correlation_id)s,%(provider)s,%(model)s,
                 %(input_tokens)s,%(output_tokens)s,%(input_rate_micro_usd_per_million)s,
                 %(output_rate_micro_usd_per_million)s,%(cost_micro_usd)s,%(latency_ms)s,
                 %(outcome)s,%(booking_success)s)
                ON CONFLICT DO NOTHING RETURNING request_id""",row).fetchone()
            return inserted is not None

    def record_ai_usage_v2(self, usage, components):
        """Atomically persist a costed parent and immutable category snapshots.

        The legacy two-rate calculator remains authoritative for legacy calls.
        V2 callers must supply an explicit expected cost; this path never
        silently infers cached-input pricing from the legacy rates.
        """
        from .ai_cost import normalize_usage, _integer
        from .ai_components import normalize_components
        if not isinstance(usage, dict) or "expected_cost_micro_usd" not in usage:
            raise ValueError("missing_expected_cost_micro_usd")
        expected = _integer(usage["expected_cost_micro_usd"], "expected_cost_micro_usd")
        row = normalize_usage({k: v for k, v in usage.items() if k != "expected_cost_micro_usd"})
        normalized = normalize_components(
            components, input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"], expected_cost_micro_usd=expected,
        )
        row["cost_micro_usd"] = expected
        with self.connection() as c:
            inserted = c.execute("""INSERT INTO jarvis_ai_usage_events
                (request_id,correlation_id,provider,model,input_tokens,output_tokens,
                 input_rate_micro_usd_per_million,output_rate_micro_usd_per_million,
                 cost_micro_usd,latency_ms,outcome,booking_success,pricing_version)
                VALUES (%(request_id)s,%(correlation_id)s,%(provider)s,%(model)s,
                 %(input_tokens)s,%(output_tokens)s,%(input_rate_micro_usd_per_million)s,
                 %(output_rate_micro_usd_per_million)s,%(cost_micro_usd)s,%(latency_ms)s,
                 %(outcome)s,%(booking_success)s,2)
                ON CONFLICT DO NOTHING RETURNING request_id""", row).fetchone()
            if inserted is None:
                existing = c.execute("""SELECT pricing_version,correlation_id,provider,model,input_tokens,
                    output_tokens,input_rate_micro_usd_per_million,
                    output_rate_micro_usd_per_million,cost_micro_usd,latency_ms,outcome,booking_success
                    FROM jarvis_ai_usage_events WHERE request_id=%s FOR UPDATE""",
                    (row["request_id"],)).fetchone()
                if existing is None:
                    raise Conflict("AI usage correlation_id already has a successful booking")
                expected_fields = ("correlation_id","provider","model","input_tokens",
                    "output_tokens","input_rate_micro_usd_per_million",
                    "output_rate_micro_usd_per_million","cost_micro_usd","latency_ms","outcome","booking_success")
                if existing["pricing_version"] != 2 or any(existing[k] != row[k] for k in expected_fields):
                    raise Conflict("AI usage request_id bound to different metadata")
                old_parts = c.execute("""SELECT component,token_count,rate_micro_usd_per_million
                    FROM jarvis_ai_usage_cost_components WHERE request_id=%s""",
                    (row["request_id"],)).fetchall()
                expected_parts = {tuple(p[k] for k in ("component","token_count","rate_micro_usd_per_million")) for p in normalized}
                found_parts = {tuple(p[k] for k in ("component","token_count","rate_micro_usd_per_million")) for p in old_parts}
                if found_parts != expected_parts:
                    raise Conflict("AI usage request_id bound to different cost components")
                return False
            for part in normalized:
                c.execute("""INSERT INTO jarvis_ai_usage_cost_components
                    (request_id,component,token_count,rate_micro_usd_per_million)
                    VALUES (%s,%s,%s,%s)""",
                    (row["request_id"],part["component"],part["token_count"],
                     part["rate_micro_usd_per_million"]))
            return True

    def ai_cost_summary_by_pricing_version(self):
        """Return separately labeled v1/v2 totals; never conflate tariff semantics."""
        from .ai_cost import summarize_totals
        with self.connection() as c:
            rows = c.execute("""SELECT pricing_version,
                count(*) AS request_count,
                count(*) FILTER (WHERE outcome='SUCCESS') AS successful_calls,
                count(*) FILTER (WHERE booking_success) AS successful_bookings,
                coalesce(sum(cost_micro_usd),0) AS total_cost_micro_usd
                FROM jarvis_ai_usage_events
                GROUP BY pricing_version ORDER BY pricing_version""").fetchall()
        return {
            int(row["pricing_version"]): summarize_totals(
                int(row["request_count"]), int(row["successful_calls"]),
                int(row["successful_bookings"]), int(row["total_cost_micro_usd"])
            ) for row in rows
        }

    def ai_cost_summary(self):
        from .ai_cost import summarize_totals
        with self.connection() as c:
            row=c.execute("""SELECT count(*) AS request_count,
                count(*) FILTER (WHERE outcome='SUCCESS') AS successful_calls,
                count(*) FILTER (WHERE booking_success) AS successful_bookings,
                coalesce(sum(cost_micro_usd),0) AS total_cost_micro_usd
                FROM jarvis_ai_usage_events""").fetchone()
        return summarize_totals(int(row['request_count']),int(row['successful_calls']),
            int(row['successful_bookings']),int(row['total_cost_micro_usd']))


