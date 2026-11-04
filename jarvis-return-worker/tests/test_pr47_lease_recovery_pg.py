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


def test_pg_unknown_after_real_http_acceptance_reconciles_without_resend(repo, tmp_path):
    """PG lease expiry + actual loopback HTTP acceptance + lost response.

    The HTTP receiver is synthetic and durable SQLite; no Telegram or CRM calls.
    """
    import http.client
    import importlib.util
    import json
    import sqlite3
    import threading
    from pathlib import Path

    path = tmp_path / "receiver.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
        db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
    source = Path(__file__).resolve().parents[1] / "isolated_receiver_http.py"
    spec = importlib.util.spec_from_file_location("pr47_pg_http_receiver", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = module.serve_local(str(path), "synthetic-private-test-token-only")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    task_id, event_id = prepare(repo, uuid.uuid4().hex)
    _, count = replay_approval(repo, event_id)
    assert count == 1
    task = repo.claim("ci-http-original-worker")
    assert task["task_id"] == task_id
    repo.mark_delivery_started(task_id, "ci-http-original-worker", task["attempt_count"], "fake")
    key = "pr47-durable-pg-http-" + uuid.uuid4().hex

    def http_request(method, route, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        headers = {"Authorization": "Bearer synthetic-private-test-token-only",
                   "Idempotency-Key": key}
        try:
            connection.request(method, route, body=json.dumps(body) if body is not None else None,
                               headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        # Remote side effect commits; simulated network loss means the PG sender
        # does not persist the response/receipt.
        status, receipt = http_request("POST", "/messages", {"task": str(task_id)})
        assert status == 201
        with repo.connection() as conn:
            conn.execute(
                """UPDATE jarvis_return_actions
                   SET lease_until = clock_timestamp() - interval '60 seconds',
                       heartbeat_at = clock_timestamp() - interval '60 seconds'
                   WHERE task_id = %s""", (task_id,))
        assert repo.recover("ci-http-recovery-worker") == 1
        row = repo.get(task_id)
        assert row["state"] == "RECONCILIATION_REQUIRED"
        assert row["delivery_status"] == "UNKNOWN"
        assert repo.claim("ci-http-other-worker") is None
        assert http_request("GET", "/receipts/" + key) == (200, receipt)
        assert http_request("GET", "/audit/effects/" + key) == (200, {"effect_count": 1})
        # Receiver restart must preserve the effect and receipt.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    server = module.serve_local(str(path), "synthetic-private-test-token-only")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert http_request("GET", "/receipts/" + key) == (200, receipt)
        assert http_request("GET", "/audit/effects/" + key) == (200, {"effect_count": 1})
        assert repo.claim("ci-http-postrestart-worker") is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_orchestrator_http_effect_after_pg_recovery_stays_reconciliation_required(repo, tmp_path):
    """Actual Orchestrator dispatch crosses a PG fence before a synthetic HTTP effect.

    Worker A pauses after the real lease check. Worker B recovers the expired
    delivery marker. A then reaches the durable receiver, loses its acknowledgement,
    and must not overwrite B's reconciliation decision. Both workers and the
    receiver are synthetic; this is a CI loopback test, not a Railway process test.
    """
    import asyncio
    import hashlib
    import http.client
    import importlib.util
    import json
    import sqlite3
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from jarvis_return_worker.dispatcher import TransportDispatcher
    from jarvis_return_worker.orchestrator import Orchestrator
    from jarvis_return_worker.transport import Classification, DeliveryError

    dbpath = tmp_path / "orchestrator-receiver.sqlite3"
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE inbox (principal TEXT NOT NULL, idem_key TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (principal, idem_key))")
        db.execute("CREATE TABLE effects (id INTEGER PRIMARY KEY, principal TEXT NOT NULL, idem_key TEXT NOT NULL)")
    receiver_path = Path(__file__).resolve().parents[1] / "isolated_receiver_http.py"
    spec = importlib.util.spec_from_file_location("pr47_orchestrator_http_receiver", receiver_path)
    receiver_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(receiver_module)
    token = "synthetic-orchestrator-test-token-only"
    server = receiver_module.serve_local(str(dbpath), token)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    task_id, event_id = prepare(repo, uuid.uuid4().hex)
    _, intent_count = replay_approval(repo, event_id)
    assert intent_count == 1
    task = repo.claim("ci-orchestrator-worker-a")
    assert task["task_id"] == task_id
    assert task["provider"] == "fake"

    fence_passed = threading.Event()
    resume_a = threading.Event()

    class PausingRepository:
        config = repo.config

        def __getattr__(self, name):
            return getattr(repo, name)

        def verify_dispatch_lease(self, *args):
            result = repo.verify_dispatch_lease(*args)
            fence_passed.set()
            if not resume_a.wait(timeout=15):
                raise TimeoutError("worker A release barrier timed out")
            return result

    class HttpProvider:
        provider = "fake"

        def send(self, action, idempotency_key):
            receiver_key = "pr47-orchestrator-" + hashlib.sha256(
                idempotency_key.encode("utf-8")
            ).hexdigest()
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=10
            )
            try:
                connection.request(
                    "POST", "/messages",
                    body=json.dumps({"task_id": action["task_id"],
                                     "payload": action["payload"]}),
                    headers={"Authorization": "Bearer " + token,
                             "Idempotency-Key": receiver_key,
                             "Content-Type": "application/json"},
                )
                response = connection.getresponse()
                body = json.loads(response.read())
                assert response.status == 201
                # The receiver committed the effect, but the worker loses its
                # acknowledgement before saving a PostgreSQL receipt.
                raise DeliveryError(
                    Classification.UNKNOWN,
                    "synthetic_ack_lost_after_receiver_acceptance",
                )
            finally:
                connection.close()

    provider = HttpProvider()
    dispatcher = TransportDispatcher()
    dispatcher.register("fake", provider, enabled=True)
    worker_a = Orchestrator(PausingRepository(), dispatcher)
    worker_b = Orchestrator(repo, TransportDispatcher())

    def run_a():
        asyncio.run(worker_a.process(task, "ci-orchestrator-worker-a"))

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_a)
            assert fence_passed.wait(timeout=15), "worker A did not pass the DB fence"
            with repo.connection() as conn:
                conn.execute(
                    """UPDATE jarvis_return_actions
                       SET lease_until = clock_timestamp() - interval '60 seconds',
                           heartbeat_at = clock_timestamp() - interval '60 seconds'
                       WHERE task_id = %s""",
                    (task_id,),
                )
            assert worker_b.recover("ci-orchestrator-worker-b") == 1
            assert repo.get(task_id)["state"] == "RECONCILIATION_REQUIRED"
            resume_a.set()
            future.result(timeout=20)

        row = repo.get(task_id)
        assert row["state"] == "RECONCILIATION_REQUIRED"
        assert row["delivery_status"] == "UNKNOWN"
        assert row["reconciliation_status"] == "REQUIRED"
        assert repo.claim("ci-orchestrator-worker-c") is None
        audit = "pr47-orchestrator-" + hashlib.sha256(
            (task_id + ":" + task["action_fingerprint"]).encode("utf-8")
        ).hexdigest()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            connection.request(
                "GET", "/audit/effects/" + audit,
                headers={"Authorization": "Bearer " + token},
            )
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"effect_count": 1}
        finally:
            connection.close()
    finally:
        resume_a.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
