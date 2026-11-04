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
    """Independent OS workers exercise the real Orchestrator fence and HTTP effect.

    Worker A pauses just after PostgreSQL lease verification. Worker B, started as
    another Python process with a separate repository connection, recovers the
    expired delivery marker. A then reaches a durable synthetic HTTP receiver and
    loses its acknowledgement. The task must stay in manual reconciliation and
    the receiver must record exactly one effect.
    """
    import hashlib
    import http.client
    import importlib.util
    import json
    import os
    import sqlite3
    import subprocess
    import sys
    import threading
    import time
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

    worker_root = str(Path(__file__).resolve().parents[1])
    tests_root = str(Path(__file__).resolve().parent)
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = os.pathsep.join(
        part for part in (worker_root, tests_root, child_env.get("PYTHONPATH")) if part
    )
    gate_dir = tmp_path / "process-gates"
    gate_dir.mkdir()
    fence_path = gate_dir / "fence-passed"
    resume_path = gate_dir / "resume-worker-a"

    worker_a_code = r"""
import asyncio
import hashlib
import http.client
import json
import os
import pathlib
import sys
import time
from types import SimpleNamespace

worker_root, database_url, task_id, owner, port, token, gate_dir = sys.argv[1:]
sys.path.insert(0, worker_root)
from jarvis_return_worker.dispatcher import TransportDispatcher
from jarvis_return_worker.orchestrator import Orchestrator
from jarvis_return_worker.transport import Classification, DeliveryError
from jarvis_return_worker.queue import QueueRepository

config = SimpleNamespace(database_url=database_url, worker_id=owner,
                         lease_seconds=30, heartbeat_seconds=5, max_attempts=3,
                         require_outbox_dispatch=True)
repository = QueueRepository(config)

class PausingRepository:
    def __getattr__(self, name):
        return getattr(repository, name)

    def verify_dispatch_lease(self, *args):
        result = repository.verify_dispatch_lease(*args)
        pathlib.Path(gate_dir, "fence-passed").write_text("verified", encoding="utf-8")
        deadline = time.monotonic() + 30
        while not pathlib.Path(gate_dir, "resume-worker-a").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("worker A release barrier timed out")
            time.sleep(0.02)
        return result

class HttpProvider:
    provider = "fake"

    def send(self, action, idempotency_key):
        receiver_key = "pr47-durable-orchestrator-" + hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()
        connection = http.client.HTTPConnection("127.0.0.1", int(port), timeout=10)
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
            if response.status != 201:
                raise AssertionError("synthetic receiver rejected request: " + repr(body))
            raise DeliveryError(
                Classification.UNKNOWN,
                "synthetic_ack_lost_after_receiver_acceptance",
            )
        finally:
            connection.close()

dispatcher = TransportDispatcher()
dispatcher.register("fake", HttpProvider(), enabled=True)
worker = Orchestrator(PausingRepository(), dispatcher)
asyncio.run(worker.process(repository.get(task_id), owner))
print("worker-a-complete")
"""

    worker_b_code = r"""
import sys
from types import SimpleNamespace

worker_root, database_url, owner = sys.argv[1:]
sys.path.insert(0, worker_root)
from jarvis_return_worker.dispatcher import TransportDispatcher
from jarvis_return_worker.orchestrator import Orchestrator
from jarvis_return_worker.queue import QueueRepository

config = SimpleNamespace(database_url=database_url, worker_id=owner,
                         lease_seconds=30, heartbeat_seconds=5, max_attempts=3,
                         require_outbox_dispatch=True)
repository = QueueRepository(config)
worker = Orchestrator(repository, TransportDispatcher())
print(worker.recover(owner))
"""

    worker_a = subprocess.Popen(
        [sys.executable, "-c", worker_a_code, worker_root,
         str(repo.config.database_url), str(task_id), "ci-orchestrator-worker-a",
         str(server.server_port), token, str(gate_dir)],
        cwd=worker_root, env=child_env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not fence_path.exists():
            if worker_a.poll() is not None:
                stdout, stderr = worker_a.communicate()
                pytest.fail(
                    "worker A exited before passing the DB fence "
                    f"(exit={worker_a.returncode}) stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail("worker A did not pass the DB fence within 20 seconds")
            time.sleep(0.05)

        with repo.connection() as conn:
            conn.execute(
                """UPDATE jarvis_return_actions
                   SET lease_until = clock_timestamp() - interval '60 seconds',
                       heartbeat_at = clock_timestamp() - interval '60 seconds'
                   WHERE task_id = %s""",
                (task_id,),
            )

        worker_b = subprocess.run(
            [sys.executable, "-c", worker_b_code, worker_root,
             str(repo.config.database_url), "ci-process-b"],
            cwd=worker_root, env=child_env, capture_output=True,
            text=True, timeout=25, check=False,
        )
        assert worker_b.returncode == 0, (
            f"worker B exited {worker_b.returncode}; "
            f"stdout={worker_b.stdout!r} stderr={worker_b.stderr!r}"
        )
        assert worker_b.stdout.strip().splitlines()[-1] == "1"
        assert repo.get(task_id)["state"] == "RECONCILIATION_REQUIRED"

        resume_path.write_text("resume", encoding="utf-8")
        try:
            stdout, stderr = worker_a.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            worker_a.terminate()
            stdout, stderr = worker_a.communicate(timeout=5)
            pytest.fail(
                f"worker A timed out (exit={worker_a.returncode}) "
                f"stdout={stdout!r} stderr={stderr!r}"
            )
        assert worker_a.returncode == 0, (
            f"worker A exited {worker_a.returncode}; "
            f"stdout={stdout!r} stderr={stderr!r}"
        )
        assert "worker-a-complete" in stdout

        row = repo.get(task_id)
        assert row["state"] == "RECONCILIATION_REQUIRED"
        assert row["delivery_status"] == "UNKNOWN"
        assert row["reconciliation_status"] == "REQUIRED"
        assert repo.claim("ci-orchestrator-worker-c") is None
        audit = "pr47-durable-orchestrator-" + hashlib.sha256(
            (str(task_id) + ":" + task["action_fingerprint"]).encode("utf-8")
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
        resume_path.write_text("resume", encoding="utf-8")
        if worker_a.poll() is None:
            worker_a.terminate()
            try:
                worker_a.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                worker_a.kill()
                worker_a.communicate(timeout=5)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
