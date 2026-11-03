"""PostgreSQL regression for lease expiry after durable delivery marker.

Uses only the ephemeral CI database and synthetic task. No external transport.
"""
import uuid
import pytest
from test_outbox_dispatch_chain import prepare, replay_approval, repo


def test_expired_delivery_marker_requires_manual_reconciliation(repo):
    task_id, event_id = prepare(repo, uuid.uuid4().hex)
    _, count = replay_approval(repo, event_id)
    assert count == 1
    task = repo.claim("outbox-chain-ci")
    assert task["task_id"] == task_id
    repo.mark_delivery_started(task_id, "outbox-chain-ci", task["attempt_count"], "fake")
    with repo.connection() as conn:
        conn.execute(
            """UPDATE jarvis_return_actions
               SET lease_until = clock_timestamp() - interval '60 seconds',
                   heartbeat_at = clock_timestamp() - interval '60 seconds'
               WHERE task_id = %s""",
            (task_id,),
        )
    assert repo.recover("ci-recovery-worker") == 1
    row = repo.get(task_id)
    assert row["state"] == "RECONCILIATION_REQUIRED"
    assert row["delivery_status"] == "UNKNOWN"
    assert row["reconciliation_status"] == "REQUIRED"
    assert repo.claim("ci-second-worker") is None

    from jarvis_return_worker.queue import Conflict
    with pytest.raises(Conflict, match="stale lease or fencing token"):
        repo.verify_dispatch_lease(
            task_id, "outbox-chain-ci", task["attempt_count"]
        )
    assert repo.get(task_id)["state"] == "RECONCILIATION_REQUIRED"


def test_two_recovery_workers_cannot_reclaim_unknown_delivery(repo):
    """Competing PostgreSQL recoverers must fence an ambiguous external effect.

    This checks database state and claims, not real network delivery.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    task_id, event_id = prepare(repo, uuid.uuid4().hex)
    _, count = replay_approval(repo, event_id)
    assert count == 1
    claimed = repo.claim("ci-original-worker")
    assert claimed["task_id"] == task_id
    repo.mark_delivery_started(
        task_id, "ci-original-worker", claimed["attempt_count"], "fake"
    )
    with repo.connection() as conn:
        conn.execute(
            """UPDATE jarvis_return_actions
               SET lease_until = clock_timestamp() - interval '60 seconds',
                   heartbeat_at = clock_timestamp() - interval '60 seconds'
               WHERE task_id = %s""",
            (task_id,),
        )

    gate = Barrier(2, timeout=10)

    def recover(worker):
        gate.wait()
        return repo.recover(worker)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(recover, ("ci-recover-a", "ci-recover-b")))
    assert sorted(outcomes) == [0, 1]
    row = repo.get(task_id)
    assert row["state"] == "RECONCILIATION_REQUIRED"
    assert row["delivery_status"] == "UNKNOWN"
    assert row["reconciliation_status"] == "REQUIRED"
    assert repo.claim("ci-new-dispatcher") is None

    from jarvis_return_worker.queue import Conflict
    with pytest.raises(Conflict, match="stale lease or fencing token"):
        repo.verify_dispatch_lease(
            task_id, "ci-original-worker", claimed["attempt_count"]
        )
