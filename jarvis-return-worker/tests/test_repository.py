"""New service integration scenarios; ephemeral CI DB, not the old acceptance suite."""
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import psycopg
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
    receipt=FakeTransport(repo,'one').deliver(t)  # stand-in external effect with durable Fake receipt
    sends.append(receipt)
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET lease_until=now()-interval '1 minute',heartbeat_at=now()-interval '1 minute' WHERE task_id=%s",(id,))
    repo.recover('recovery')
    assert repo.get(id)['state']=='RECONCILIATION_REQUIRED'
    assert repo.claim('two') is None
    assert sends==[receipt]
    assert repo.reconcile(id,'ci-human','delivered',receipt,'fake-message-after-crash')['state']=='DONE'
    saved=repo.get(id)
    assert saved['delivery_receipt']==receipt
    assert saved['external_message_id']=='fake-message-after-crash'
    assert repo.claim('two') is None
    with repo.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM jarvis_fake_receipts WHERE task_id=%s',(id,)).fetchone()['n']==1

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


def test_two_workers_cannot_claim_the_same_task(repo):
    id=make(repo)
    barrier=Barrier(3)
    def claim(owner):
        barrier.wait()
        return repo.claim(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one=pool.submit(claim,'worker-one')
        two=pool.submit(claim,'worker-two')
        barrier.wait()
        claims=[one.result(),two.result()]
    active=[row for row in claims if row is not None]
    assert len(active)==1
    assert active[0]['task_id']==id
    assert repo.get(id)['attempt_count']==1


def test_delivery_identifiers_are_idempotent_and_unique(repo):
    first=make(repo); a=repo.claim('one')
    repo.mark_delivery_started(first,'one',a['attempt_count'])
    saved=repo.save_delivery_receipt(first,'one',a['attempt_count'],'provider-receipt-unique','provider-message-unique')
    version=saved['version']
    same=repo.save_delivery_receipt(first,'one',a['attempt_count'],'provider-receipt-unique','provider-message-unique')
    assert same['version']==version
    second=make(repo); b=repo.claim('two')
    repo.mark_delivery_started(second,'two',b['attempt_count'])
    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.save_delivery_receipt(second,'two',b['attempt_count'],'provider-receipt-other','provider-message-unique')
    third=make(repo); d=repo.claim('three')
    repo.mark_delivery_started(third,'three',d['attempt_count'])
    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.save_delivery_receipt(third,'three',d['attempt_count'],'provider-receipt-unique',None)
    with repo.connection() as c:
        assert c.execute("SELECT count(*) AS n FROM jarvis_return_actions WHERE delivery_receipt='provider-receipt-unique'").fetchone()['n']==1
        assert c.execute("SELECT count(*) AS n FROM jarvis_return_actions WHERE external_message_id='provider-message-unique'").fetchone()['n']==1
        assert c.execute("SELECT count(*) AS n FROM jarvis_return_action_events WHERE task_id=%s AND metadata->>'action'='delivery_receipt_saved'",(first,)).fetchone()['n']==1


def test_database_constraints_audit_chain_and_version_progress(repo):
    import json
    id=make(repo); task=repo.claim('one')
    repo.heartbeat(id,'one',task['attempt_count'])
    repo.mark_delivery_started(id,'one',task['attempt_count'])
    receipt=FakeTransport(repo,'one').deliver(task)
    repo.save_delivery_receipt(id,'one',task['attempt_count'],receipt,'audit-message-'+id)
    repo.finish(id,'one',task['attempt_count'],receipt)
    with pytest.raises(psycopg.errors.CheckViolation):
        with repo.connection() as c:
            c.execute("UPDATE jarvis_return_actions SET state='NOT_A_STATE' WHERE task_id=%s",(id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        with repo.connection() as c:
            c.execute("UPDATE jarvis_return_actions SET attempt_count=-1 WHERE task_id=%s",(id,))
    with repo.connection() as c:
        events=c.execute("SELECT event_id,from_state,to_state,metadata FROM jarvis_return_action_events WHERE task_id=%s ORDER BY event_id",(id,)).fetchall()
        current=c.execute("SELECT state,version FROM jarvis_return_actions WHERE task_id=%s",(id,)).fetchone()
    assert events and [e['event_id'] for e in events]==sorted(e['event_id'] for e in events)
    transitions=[(e['from_state'],e['to_state']) for e in events if e['from_state']!=e['to_state']]
    assert all(prev[1]==nxt[0] for prev,nxt in zip(transitions,transitions[1:]))
    versions=[e['metadata'].get('version_after') for e in events]
    versions=[v for v in versions if v is not None]
    assert versions==sorted(versions)
    assert current['version']==versions[-1]
    assert current['state']==events[-1]['to_state']
    for e in events:
        before=e['metadata'].get('version_before'); after=e['metadata'].get('version_after')
        if before is not None and after is not None:
            assert 0<=after-before<=1


def test_persisted_prometheus_counters_cover_safety_events(repo):
    from jarvis_return_worker.metrics import render_metrics
    delivered=make(repo); a=repo.claim('one')
    repo.mark_delivery_started(delivered,'one',a['attempt_count'])
    receipt=FakeTransport(repo,'one').deliver(a)
    assert FakeTransport(repo,'one').deliver(a)==receipt
    repo.save_delivery_receipt(delivered,'one',a['attempt_count'],receipt,'metric-message-'+delivered)
    repo.finish(delivered,'one',a['attempt_count'],receipt)

    uncertain=make(repo); b=repo.claim('two')
    repo.mark_delivery_started(uncertain,'two',b['attempt_count'])
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET lease_until=now()-interval '1 minute',heartbeat_at=now()-interval '1 minute' WHERE task_id=%s",(uncertain,))
    repo.recover('recovery')

    expired=make(repo); d=repo.claim('three')
    with repo.connection() as c:
        c.execute("UPDATE jarvis_return_actions SET approval_expires_at=now()-interval '1 minute',lease_until=now()-interval '1 minute',heartbeat_at=NULL WHERE task_id=%s",(expired,))
    repo.recover('recovery')

    exposition=render_metrics(repo).decode()
    expected=('reconciliation_required_total','delivery_receipt_saved_total',
        'duplicate_delivery_prevented_total','approval_expired_total','lease_recovery_total')
    for metric in expected:
        match=re.search(r'^'+re.escape(metric)+r' (\\d+(?:\\.\\d+)?)$',exposition,re.M)
        assert match and float(match.group(1))>=1, (metric,exposition)
