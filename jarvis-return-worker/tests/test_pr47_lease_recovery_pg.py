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


def _run_stale_http_worker_a(database_url, task, port, token, fence_passed, resume):
    import asyncio
    import hashlib
    import http.client
    import json
    from types import SimpleNamespace
    from jarvis_return_worker.dispatcher import TransportDispatcher
    from jarvis_return_worker.orchestrator import Orchestrator
    from jarvis_return_worker.transport import Classification, DeliveryError
    from jarvis_return_worker.queue import QueueRepository

    config = SimpleNamespace(database_url=database_url, worker_id="ci-process-a",
                             lease_seconds=30, heartbeat_seconds=5, max_attempts=3,
                             require_outbox_dispatch=True)
    repository = QueueRepository(config)

    class PausingRepository:
        def __getattr__(self, name):
            return getattr(repository, name)

        def verify_dispatch_lease(self, *args):
            result = repository.verify_dispatch_lease(*args)
            fence_passed.set()
            if not resume.wait(timeout=20):
                raise TimeoutError("worker A release barrier timed out")
            return result

    class HttpProvider:
        provider = "fake"

        def send(self, action, idempotency_key):
            receiver_key = "pr47-durable-orchestrator-" + hashlib.sha256(
                idempotency_key.encode("utf-8")
            ).hexdigest()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
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
                assert response.status == 201, body
                raise DeliveryError(
                    Classification.UNKNOWN,
                    "synthetic_ack_lost_after_receiver_acceptance",
                )
            finally:
                connection.close()

    dispatcher = TransportDispatcher()
    dispatcher.register("fake", HttpProvider(), enabled=True)
    worker = Orchestrator(PausingRepository(), dispatcher)
    asyncio.run(worker.process(task, "ci-process-a"))


def _run_stale_http_worker_b(database_url, owner, result_queue):
    from types import SimpleNamespace
    from jarvis_return_worker.dispatcher import TransportDispatcher
    from jarvis_return_worker.orchestrator import Orchestrator
    from jarvis_return_worker.queue import QueueRepository

    config = SimpleNamespace(database_url=database_url, worker_id=owner,
                             lease_seconds=30, heartbeat_seconds=5, max_attempts=3,
                             require_outbox_dispatch=True)
    repository = QueueRepository(config)
    worker = Orchestrator(repository, TransportDispatcher())
    result_queue.put(worker.recover(owner))


def test_two_os_workers_http_effect_after_pg_recovery_stays_reconciliation_required(repo, tmp_path):
    """Separate worker processes exercise the real Orchestrator fence and HTTP effect.

    A pauses after PostgreSQL lease verification. B, in a separate process and
    connection, recovers the expired delivery marker. A then reaches the durable
    synthetic HTTP receiver and loses its acknowledgement. The task must remain
    in manual reconciliation and the receiver must record only one effect.
    """
    import hashlib
    import http.client
    import importlib.util
    import json
    import multiprocessing
    import sqlite3
    import threading
    from pathlib import Path

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

    context = multiprocessing.get_context("spawn")
    fence_passed = context.Event()
    resume_a = context.Event()
    worker_a = context.Process(
        target=_run_stale_http_worker_a,
        args=(repo.config.database_url, task, server.server_port, token,
              fence_passed, resume_a),
    )
    worker_b = None
    result_queue = context.Queue()
    try:
        worker_a.start()
        assert fence_passed.wait(timeout=20), "worker A did not pass the DB fence"
        with repo.connection() as conn:
            conn.execute(
                """UPDATE jarvis_return_actions
                   SET lease_until = clock_timestamp() - interval '60 seconds',
                       heartbeat_at = clock_timestamp() - interval '60 seconds'
                   WHERE task_id = %s""",
                (task_id,),
            )

        worker_b = context.Process(
            target=_run_stale_http_worker_b,
            args=(repo.config.database_url, "ci-process-b", result_queue),
        )
        worker_b.start()
        worker_b.join(timeout=20)
        assert not worker_b.is_alive(), "worker B recovery process timed out"
        assert worker_b.exitcode == 0
        assert result_queue.get(timeout=5) == 1
        assert repo.get(task_id)["state"] == "RECONCILIATION_REQUIRED"

        resume_a.set()
        worker_a.join(timeout=20)
        assert not worker_a.is_alive(), "worker A dispatch process timed out"
        assert worker_a.exitcode == 0

        row = repo.get(task_id)
        assert row["state"] == "RECONCILIATION_REQUIRED"
        assert row["delivery_status"] == "UNKNOWN"
        assert row["reconciliation_status"] == "REQUIRED"
        assert repo.claim("ci-orchestrator-worker-c") is None
        audit = "pr47-durable-orchestrator-" + hashlib.sha256(
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
        if worker_a.is_alive():
            worker_a.terminate()
            worker_a.join(timeout=5)
        if worker_b is not None and worker_b.is_alive():
            worker_b.terminate()
            worker_b.join(timeout=5)
        result_queue.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
