"""New service integration scenarios; ephemeral CI DB, not the old acceptance suite."""
import os
import uuid
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
    receipt=FakeTransport(repo,'one').deliver(t)
    assert FakeTransport(repo,'one').deliver(t)==receipt
    assert repo.finish(id,'one',t['attempt_count'],receipt)['state']=='DONE'

def test_recovery_fences_old_attempt_and_deduplicates_receipt(repo):
    id=make(repo); t=repo.claim('one'); receipt=FakeTransport(repo,'one').deliver(t)
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET lease_until=now()-interval '1 minute',heartbeat_at=now()-interval '1 minute' WHERE task_id=%s",(id,))
    assert repo.recover('recovery')>=1
    with pytest.raises(Conflict): repo.finish(id,'one',t['attempt_count'],receipt)
    with repo.connection() as c:
        c.execute('UPDATE jarvis_return_actions SET next_attempt_at=now() WHERE task_id=%s',(id,))
    new=repo.claim('two')
    assert new['attempt_count']==2
    assert FakeTransport(repo,'two').deliver(new)==receipt
    assert repo.finish(id,'two',2,receipt)['state']=='DONE'
    with repo.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM jarvis_fake_receipts WHERE task_id=%s',(id,)).fetchone()['n']==1

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
