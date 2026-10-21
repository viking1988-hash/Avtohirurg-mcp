"""Sandbox-only additive ledger v2 schema compatibility tests."""
import os
import uuid
from types import SimpleNamespace

import pytest

from jarvis_return_worker.queue import QueueRepository


def test_cost_components_migration_preserves_legacy_rows():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("requires disposable PostgreSQL")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    rid = "ai-legacy-migration-" + str(uuid.uuid4())
    row = {
        "request_id": rid, "correlation_id": rid,
        "provider": "synthetic-provider", "model": "synthetic-model",
        "input_tokens": 1000, "output_tokens": 500,
        "input_rate_micro_usd_per_million": 2_000_000,
        "output_rate_micro_usd_per_million": 8_000_000,
        "latency_ms": 12, "outcome": "SUCCESS", "booking_success": False,
    }
    assert repo.record_ai_usage(row)
    repo.migrate()
    with repo.connection() as conn:
        parent = conn.execute(
            "SELECT cost_micro_usd FROM jarvis_ai_usage_events WHERE request_id=%s",
            (rid,),
        ).fetchone()
        components = conn.execute(
            "SELECT count(*) AS n FROM jarvis_ai_usage_cost_components WHERE request_id=%s",
            (rid,),
        ).fetchone()
    assert parent["cost_micro_usd"] == 6000
    assert components["n"] == 0


def test_components_are_fk_guarded_and_reject_invalid_counts():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("requires disposable PostgreSQL")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    with pytest.raises(Exception):
        with repo.connection() as conn:
            conn.execute(
                """INSERT INTO jarvis_ai_usage_cost_components
                   (request_id,component,token_count,rate_micro_usd_per_million)
                   VALUES (%s,'cached_input',10,100)""",
                ("nonexistent-" + str(uuid.uuid4()),),
            )
    rid = "ai-invalid-count-" + str(uuid.uuid4())
    assert repo.record_ai_usage({
        "request_id": rid, "correlation_id": rid,
        "provider": "synthetic-provider", "model": "synthetic-model",
        "input_tokens": 10, "output_tokens": 0,
        "input_rate_micro_usd_per_million": 1_000_000,
        "output_rate_micro_usd_per_million": 0,
        "latency_ms": 0, "outcome": "SUCCESS", "booking_success": False,
    })
    with pytest.raises(Exception):
        with repo.connection() as conn:
            conn.execute(
                """INSERT INTO jarvis_ai_usage_cost_components
                   (request_id,component,token_count,rate_micro_usd_per_million)
                   VALUES (%s,'cached_input',-1,100)""",
                (rid,),
            )
