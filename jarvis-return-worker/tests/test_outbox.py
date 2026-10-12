import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from jarvis_return_worker.outbox import (
    OutboxReplay,
    PermanentSubscriberError,
    SubscriberRegistry,
    append_event,
)
from jarvis_return_worker.queue import QueueRepository


@pytest.fixture(scope="module")
def repo():
    url=os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration URL is not configured")
    value=QueueRepository(SimpleNamespace(database_url=url,worker_id="outbox-tests",
        lease_seconds=30,heartbeat_seconds=5,max_attempts=3))
    value.migrate()
    with value.connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS jarvis_outbox_test_effects(
            event_id text PRIMARY KEY, calls integer NOT NULL DEFAULT 0)""")
    return value


def new_event(repo, event_type="TestEvent"):
    suffix=str(uuid.uuid4())
    with repo.connection() as conn:
        event_id=append_event(conn,aggregate_id="aggregate-"+suffix,
            aggregate_type="test",event_type=event_type,event_version=1,
            payload={"test":True},correlation_id="corr-"+suffix,
            causation_id="cause-"+suffix)
    return event_id

def registry(name, event_type, handler):
    result=SubscriberRegistry()
    result.register(name,event_type,handler)
    return result


def effect_handler(conn,event):
    conn.execute("""INSERT INTO jarvis_outbox_test_effects(event_id,calls)
        VALUES (%s,1) ON CONFLICT(event_id) DO UPDATE SET calls=jarvis_outbox_test_effects.calls+1""",
        (event["event_id"],))


def delivery(repo,event_id,name="test-subscriber"):
    with repo.connection() as conn:
        return conn.execute("""SELECT * FROM jarvis_outbox_deliveries
            WHERE event_id=%s AND subscriber_name=%s""",(event_id,name)).fetchone()


def test_event_committed_before_worker_restart_replays_once(repo):
    event_id=new_event(repo,"RestartTestEvent")
    # Simulate a process restart after business commit but before replay.
    replay=OutboxReplay(repo,registry("test-subscriber","RestartTestEvent",effect_handler),owner="restarted")
    replay.register_subscribers()
    assert replay.replay_one() is True
    assert replay.replay_one() is False
    with repo.connection() as conn:
        assert conn.execute("SELECT calls FROM jarvis_outbox_test_effects WHERE event_id=%s",
            (event_id,)).fetchone()["calls"]==1
        assert conn.execute("SELECT count(*) AS n FROM jarvis_processed_events WHERE event_id=%s",
            (event_id,)).fetchone()["n"]==1
        assert conn.execute("SELECT status FROM jarvis_outbox_events WHERE event_id=%s",
            (event_id,)).fetchone()["status"]=="PUBLISHED"


def test_two_replay_workers_do_not_process_same_event_twice(repo):
    event_id=new_event(repo,"ConcurrentTestEvent")
    calls=[]
    lock=threading.Lock()
    def handler(conn,event):
        time.sleep(0.15)
        with lock:
            calls.append(event["event_id"])
        effect_handler(conn,event)
    reg=registry("concurrent-subscriber","ConcurrentTestEvent",handler)
    first=OutboxReplay(repo,reg,owner="worker-a")
    second=OutboxReplay(repo,reg,owner="worker-b")
    first.register_subscribers()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda replay: replay.replay_one(),(first,second)))
    assert sorted(results)==[False,True]
    assert calls==[event_id]
    assert delivery(repo,event_id,"concurrent-subscriber")["status"]=="PUBLISHED"



def test_committed_marker_repairs_delivery_without_repeating_handler(repo):
    event_id=new_event(repo,"AckRecoveryEvent")
    calls=[]
    def handler(conn,event):
        calls.append(event["event_id"])
        effect_handler(conn,event)
    replay=OutboxReplay(repo,registry("ack-subscriber","AckRecoveryEvent",handler),
                        owner="ack-recovery")
    replay.register_subscribers()
    # Model a commit whose acknowledgement was lost: business effect and processed
    # marker committed, while the delivery row still appears pending.
    with repo.connection() as conn:
        conn.execute("""INSERT INTO jarvis_outbox_test_effects(event_id,calls)
            VALUES (%s,1) ON CONFLICT(event_id) DO NOTHING""",(event_id,))
        conn.execute("""INSERT INTO jarvis_outbox_deliveries
            (event_id,subscriber_name,event_version,status,attempt_count)
            VALUES (%s,'ack-subscriber',1,'PENDING',1)""",(event_id,))
        conn.execute("""INSERT INTO jarvis_processed_events
            (event_id,subscriber_name,event_version) VALUES (%s,'ack-subscriber',1)""",
            (event_id,))
    assert replay.replay_one() is False
    assert calls==[]
    assert delivery(repo,event_id,"ack-subscriber")["status"]=="PUBLISHED"
    assert replay.pending_count()==0

def test_transient_subscriber_error_retries_without_losing_event(repo):
    event_id=new_event(repo,"FlakyTestEvent")
    attempts={"count":0}
    def flaky(conn,event):
        attempts["count"]+=1
        if attempts["count"]==1:
            raise RuntimeError("temporary database outage")
        effect_handler(conn,event)
    replay=OutboxReplay(repo,registry("flaky-subscriber","FlakyTestEvent",flaky),
                        owner="retry-worker",retry_base_seconds=1)
    replay.register_subscribers()
    assert replay.replay_one() is True
    failed=delivery(repo,event_id,"flaky-subscriber")
    assert failed["status"]=="PENDING"
    assert failed["attempt_count"]==1
    with repo.connection() as conn:
        conn.execute("""UPDATE jarvis_outbox_deliveries SET next_attempt_at=clock_timestamp()-interval '1 second'
            WHERE event_id=%s AND subscriber_name='flaky-subscriber'""",(event_id,))
    assert replay.replay_one() is True
    assert delivery(repo,event_id,"flaky-subscriber")["status"]=="PUBLISHED"
    assert attempts["count"]==2


def test_permanent_subscriber_error_is_audited_and_not_retried(repo):
    event_id=new_event(repo,"PermanentTestEvent")
    def reject(conn,event):
        raise PermanentSubscriberError("unsupported event version")
    replay=OutboxReplay(repo,registry("rejecting-subscriber","PermanentTestEvent",reject),owner="reject-worker")
    replay.register_subscribers()
    assert replay.replay_one() is True
    assert replay.replay_one() is False
    assert delivery(repo,event_id,"rejecting-subscriber")["status"]=="FAILED"
    with repo.connection() as conn:
        audit=conn.execute("""SELECT action FROM jarvis_outbox_audit
            WHERE event_id=%s AND subscriber_name='rejecting-subscriber'""",(event_id,)).fetchall()
        assert [row["action"] for row in audit]==["permanent_failure"]


def test_outbox_insert_rolls_back_with_business_transaction(repo):
    event_id=str(uuid.uuid4())
    with pytest.raises(RuntimeError,match="simulate business rollback"):
        with repo.connection() as conn:
            append_event(conn,aggregate_id="rollback-test",aggregate_type="test",
                event_type="RollbackEvent",event_version=1,payload={},
                causation_id="rollback-"+event_id)
            raise RuntimeError("simulate business rollback")
    with repo.connection() as conn:
        assert conn.execute("SELECT 1 FROM jarvis_outbox_events WHERE causation_id=%s",
            ("rollback-"+event_id,)).fetchone() is None


def test_outbox_metrics_are_exported_from_durable_rows(repo):
    from jarvis_return_worker.metrics import render_metrics
    output=render_metrics(repo).decode()
    for name in ("outbox_events_total","outbox_pending_total","outbox_replayed_total",
                 "outbox_failed_total","subscriber_errors_total","replay_latency_seconds",
                 "replay_queue_size"):
        assert name in output
