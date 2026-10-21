"""Verify AI usage ledger durability across a real PostgreSQL restart in CI.

Invoked before and after the existing CI PostgreSQL container restart.
No provider API or production connection is used.
"""
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

package = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "jarvis_return_worker", package / "__init__.py",
    submodule_search_locations=[str(package)],
)
module = importlib.util.module_from_spec(spec)
sys.modules["jarvis_return_worker"] = module
spec.loader.exec_module(module)
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
v2_request_id = "ai-v2-restart-" + marker
v2_row = dict(row, request_id=v2_request_id, correlation_id="ai-v2-correlation-" + marker,
              booking_success=False, expected_cost_micro_usd=5400)
v2_parts = [
    {"component":"standard_input","token_count":600,"rate_micro_usd_per_million":2_000_000},
    {"component":"cached_input","token_count":400,"rate_micro_usd_per_million":500_000},
    {"component":"output","token_count":500,"rate_micro_usd_per_million":8_000_000},
]
phase = sys.argv[1]
if phase == "before":
    assert repo.record_ai_usage(row) is True
    assert repo.record_ai_usage_v2(v2_row, v2_parts) is True
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
    with repo.connection() as conn:
        v2_parent = conn.execute(
            "SELECT cost_micro_usd FROM jarvis_ai_usage_events WHERE request_id=%s",
            (v2_request_id,),
        ).fetchone()
        v2_components = conn.execute(
            """SELECT component, token_count, rate_micro_usd_per_million
               FROM jarvis_ai_usage_cost_components WHERE request_id=%s
               ORDER BY component""",
            (v2_request_id,),
        ).fetchall()
    assert v2_parent is not None and v2_parent["cost_micro_usd"] == 5400
    assert len(v2_components) == 3
    assert {p["component"]: (p["token_count"], p["rate_micro_usd_per_million"])
            for p in v2_components} == {
                "standard_input": (600, 2_000_000),
                "cached_input": (400, 500_000),
                "output": (500, 8_000_000),
            }
    assert repo.record_ai_usage_v2(v2_row, v2_parts) is False
    print("AI usage ledger v1 and v2 components PostgreSQL restart recovery: PASS")
else:
    raise ValueError("expected before or after")
