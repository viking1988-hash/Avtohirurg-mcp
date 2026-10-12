"""New service integration scenarios; ephemeral CI DB, not the old acceptance suite."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from jarvis_return_worker.queue import QueueRepository,Conflict
from jarvis_return_worker.transport import FakeTransport

@pytest.fixture
def repo():
    url=os.environ.get('JARVIS_WORKER_CI_DATABASE_URL')
    if not url: pytest.skip('ephemeral worker CI PostgreSQL not configured')
    r=QueueRepository(SimpleNamespace(database_url=url,worker_id='ci',lease_seconds=30,
        heartbeat_seconds=5,max_attempts=3))
    r.migrate()
    # Each integration scenario gets an isolated fixture inside the disposable CI database.
    with r.connection() as c:
        c.execute('DELETE FROM jarvis_return_action_events')
        c.execute('DELETE FROM jarvis_fake_receipts')
        c.execute('DELETE FROM jarvis_return_actions')
    return r

def make(repo):
    id='worker-ci-'+uuid.uuid4().hex
    t=repo.enqueue(id,'sandbox:never-send','new-worker-test',id)
    repo.approve(id,t['action_fingerprint'],datetime.now(timezone.utc)+timedelta(minutes=10),'ci-human')
    return id

def test_heartbeat_receipt_and_done(repo):
    id=make(repo); t=repo.claim('one')
    assert t['task_id']==id
    assert repo.heartbeat(id,'one',t['attempt_count'])['heartbeat_at']
    with pytest.raises(Conflict): repo.finish(id,'one',t['attempt_count'],'invented')
    repo.mark_delivery_started(id,'one',t['attempt_count'])
    receipt=FakeTransport(repo,'one').deliver(t)
    assert FakeTransport(repo,'one').deliver(t)==receipt
    repo.save_delivery_receipt(id,'one',t['attempt_count'],receipt,'fake-message-1')
    assert repo.finish(id,'one',t['attempt_count'],receipt)['state']=='DONE'
    assert repo.get(id)['external_message_id']=='fake-message-1'

def test_failure_before_send_recovers_and_fences_old_attempt(repo):
    id=make(repo); t=repo.claim('one')
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET lease_until=now()-interval '1 minute',heartbeat_at=now()-interval '1 minute' WHERE task_id=%s",(id,))
    assert repo.recover('recovery')>=1
    with pytest.raises(Conflict): repo.finish(id,'one',t['attempt_count'],'sandbox:receipt:none')
    with repo.connection() as c:
        c.execute('UPDATE jarvis_return_actions SET next_attempt_at=now() WHERE task_id=%s',(id,))
    new=repo.claim('two')
    assert new['attempt_count']==2
    assert new['state']=='PROCESSING'

def test_send_started_without_receipt_requires_reconciliation(repo):
    id=make(repo); t=repo.claim('one')
    sends=[]
    repo.mark_delivery_started(id,'one',t['attempt_count'])
    sends.append('external-fake-effect')
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET lease_until=now()-interval '1 minute',heartbeat_at=now()-interval '1 minute' WHERE task_id=%s",(id,))
    repo.recover('recovery')
    assert repo.get(id)['state']=='RECONCILIATION_REQUIRED'
    assert repo.claim('two') is None
    assert sends==['external-fake-effect']
    assert repo.reconcile(id,'ci-human','delivered','provider-receipt-1','provider-message-1')['state']=='DONE'
    assert repo.get(id)['external_message_id']=='provider-message-1'

def test_receipt_saved_before_done_recovers_to_done_without_resend(repo):
    id=make(repo); t=repo.claim('one')
    repo.mark_delivery_started(id,'one',t['attempt_count'])
    receipt=FakeTransport(repo,'one').deliver(t)
    repo.save_delivery_receipt(id,'one',t['attempt_count'],receipt,'fake-message-2')
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET lease_until=now()-interval '1 minute',heartbeat_at=now()-interval '1 minute' WHERE task_id=%s",(id,))
    repo.recover('recovery')
    assert repo.get(id)['state']=='DONE'
    assert repo.get(id)['external_message_id']=='fake-message-2'
    assert repo.claim('two') is None
    with repo.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM jarvis_return_actions WHERE task_id=%s AND provider_receipt IS NOT NULL',(id,)).fetchone()['n']==1

def test_requeue_requires_new_approval_and_cancel(repo):
    id=make(repo); t=repo.claim('one'); repo.fail(id,'one',t['attempt_count'])
    assert repo.requeue(id,'ci-human')['state']=='READY'
    assert repo.claim('two') is None
    assert repo.cancel(id,'ci-human')['state']=='CANCELLED'

def test_recovery_expired_approval_fails_closed(repo):
    id=make(repo); repo.claim('one')
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET approval_expires_at=now()-interval '1 minute',lease_until=now()-interval '1 minute',heartbeat_at=NULL WHERE task_id=%s",(id,))
    repo.recover('recovery')
    assert repo.get(id)['state']=='FAILED'
    assert repo.cancel(id,'ci-human')['state']=='CANCELLED'

def test_live_lease_not_recovered_and_audit_fields(repo):
    id=make(repo); t=repo.claim('one'); repo.recover('recovery')
    assert repo.get(id)['state']=='PROCESSING'
    repo.fail(id,'one',t['attempt_count']); repo.cancel(id,'ci-human')
    with repo.connection() as c:
        rows=c.execute('SELECT * FROM jarvis_return_action_events WHERE task_id=%s',(id,)).fetchall()
        assert all(r['correlation_id']==id and {'worker_id','lease_owner','duration'}<=r['metadata'].keys() for r in rows)

def test_duplicate_request_and_duplicate_finish_are_idempotent(repo):
    id=make(repo)
    first=repo.get(id)
    duplicate=repo.enqueue(id,first['destination_key'],'new-worker-test',id)
    assert duplicate['action_fingerprint']==first['action_fingerprint']
    t=repo.claim('one');repo.mark_delivery_started(id,'one',t['attempt_count'])
    receipt=FakeTransport(repo,'one').deliver(t)
    repo.save_delivery_receipt(id,'one',t['attempt_count'],receipt,'dedup-message')
    assert repo.finish(id,'one',t['attempt_count'],receipt)['state']=='DONE'
    with pytest.raises(Conflict): repo.finish(id,'one',t['attempt_count'],receipt)
    assert repo.claim('two') is None
    with repo.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM jarvis_return_actions WHERE task_id=%s AND provider_receipt IS NOT NULL',(id,)).fetchone()['n']==1

def test_two_workers_cannot_claim_same_task(repo):
    id=make(repo)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(repo.claim,['worker-a','worker-b']))
    matched=[c for c in claims if c and c['task_id']==id]
    assert len(matched)==1
    assert repo.get(id)['state']=='PROCESSING'

def test_crash_after_provider_message_id_before_receipt_requires_reconciliation(repo):
    id=make(repo);task=repo.claim('worker-a')
    repo.mark_delivery_started(id,'worker-a',task['attempt_count'])
    # Provider answered with a message id but the process died before durable receipt save.
    external_sends=['message-71']
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET lease_until=now()-interval '1 minute',heartbeat_at=now()-interval '1 minute' WHERE task_id=%s",(id,))
    assert repo.recover('recovery-worker')==1
    assert repo.get(id)['state']=='RECONCILIATION_REQUIRED'
    assert repo.claim('restart-worker') is None
    assert external_sends==['message-71']

def test_restart_repository_never_resends_completed_action(repo):
    id=make(repo);t=repo.claim('worker-a');repo.mark_delivery_started(id,'worker-a',t['attempt_count'])
    receipt=FakeTransport(repo,'worker-a').deliver(t)
    repo.save_delivery_receipt(id,'worker-a',t['attempt_count'],receipt,'restart-safe')
    repo.finish(id,'worker-a',t['attempt_count'],receipt)
    restarted=QueueRepository(repo.config)
    assert restarted.get(id)['state']=='DONE'
    assert restarted.claim('worker-after-restart') is None
