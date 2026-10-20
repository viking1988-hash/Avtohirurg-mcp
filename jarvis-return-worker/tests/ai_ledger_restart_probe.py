"""Verify AI usage ledger durability across a real PostgreSQL restart in CI.

Invoked before and after the existing CI PostgreSQL container restart.
No provider API or production connection is used.
"""
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_return_worker.queue import QueueRepository

dsn = os.environ["JARVIS_WORKER_CI_DATABASE_URL"]
repo = QueueRepository(SimpleNamespace(database_url=dsn))
repo.migrate()
marker = os.environ["JARVIS_AI_RESTART_MARKER"]
request_id = "ai-restart-" + marker
correlation_id = "ai-restart-correlation-" + marker
row = {
    "request_id": request_id,
    "correlation_id": correlation_id,
    "provider": "synthetic-provider",
    "model": "synthetic-model",
    "input_tokens": 1000,
    "output_tokens": 500,
    "input_rate_micro_usd_per_million": 2_000_000,
    "output_rate_micro_usd_per_million": 8_000_000,
    "latency_ms": 100,
    "outcome": "SUCCESS",
    "booking_success": True,
}
phase = sys.argv[1]
if phase == "before":
    assert repo.record_ai_usage(row) is True
elif phase == "after":
    with repo.connection() as conn:
        found = conn.execute(
            """SELECT request_id, correlation_id, provider, model, input_tokens,
                      output_tokens, cost_micro_usd, booking_success
                 FROM jarvis_ai_usage_events WHERE request_id=%s""",
            (request_id,),
        ).fetchone()
    assert found is not None, "AI ledger entry lost after PostgreSQL restart"
    assert found["correlation_id"] == correlation_id
    assert found["provider"] == "synthetic-provider"
    assert found["model"] == "synthetic-model"
    assert found["input_tokens"] == 1000
    assert found["output_tokens"] == 500
    assert found["cost_micro_usd"] == 6000
    assert found["booking_success"] is True
    assert repo.record_ai_usage(row) is False, "idempotency lost after restart"
    print("AI usage ledger PostgreSQL restart recovery: PASS")
else:
    raise ValueError("expected before or after")
