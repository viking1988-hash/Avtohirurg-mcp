from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import os
import uuid

import pytest

from jarvis_return_worker.ai_cost import (
    calculate_cost_micro_usd, cost_summary, normalize_usage, usd_string,
)
from jarvis_return_worker.queue import QueueRepository
from jarvis_return_worker.benchmark import benchmark, corpus


def usage(request_id=None, correlation_id=None, *, booking_success=False):
    return {
        "request_id": request_id or str(uuid.uuid4()),
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "provider": "test-provider",
        "model": "synthetic-model-v1",
        "input_tokens": 1000,
        "output_tokens": 500,
        "input_rate_micro_usd_per_million": 2_000_000,
        "output_rate_micro_usd_per_million": 8_000_000,
        "latency_ms": 125,
        "outcome": "SUCCESS",
        "booking_success": booking_success,
    }


def test_cost_uses_exact_integer_micro_usd_and_half_up_rounding():
    assert calculate_cost_micro_usd(1000, 500, 2_000_000, 8_000_000) == 6_000
    assert calculate_cost_micro_usd(1, 0, 500_000, 0) == 1
    assert usd_string(6_000) == "0.006000"


@pytest.mark.parametrize("field,value", [
    ("input_tokens", -1), ("output_tokens", True), ("latency_ms", 1.5),
    ("provider", "unsafe token value"), ("outcome", "unknown-ish"),
])
def test_rejects_invalid_metering_values(field, value):
    record = usage()
    record[field] = value
    with pytest.raises(ValueError):
        normalize_usage(record)


def test_rejects_prompt_or_secret_fields():
    record = usage()
    record["prompt"] = "synthetic text"
    record["api_key"] = "not-a-real-secret"
    with pytest.raises(ValueError, match="invalid_usage_fields"):
        normalize_usage(record)


def test_cost_per_request_and_successful_booking_is_counted_once():
    corr = str(uuid.uuid4())
    rows = [usage(correlation_id=corr, booking_success=True),
            usage(correlation_id=corr, booking_success=False)]
    summary = cost_summary(rows)
    assert summary == {
        "request_count": 2,
        "successful_model_calls": 2,
        "successful_bookings": 1,
        "total_cost_micro_usd": 12_000,
        "cost_per_request_micro_usd": 6_000,
        "cost_per_successful_booking_micro_usd": 12_000,
        "currency": "USD",
    }


def test_offline_benchmark_uses_100_synthetic_rows_and_never_claims_model_test():
    report = benchmark()
    assert len(corpus()) == 100
    assert report["count"] == 100 and report["unique_texts"] == 100
    assert report["model_tested"] is False
    assert report["accuracy"]["mode"] == 1
    assert report["unsafe_routes"] == 0
    assert report["api_cost"] == 0
    assert "No LLM accuracy" in report["limitations"][1]


def test_postgres_ledger_is_idempotent_and_summary_counts_unique_booking():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("requires isolated CI PostgreSQL")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    correlation = "ai-meter-" + str(uuid.uuid4())
    request_id = "ai-request-" + str(uuid.uuid4())
    first = usage(request_id, correlation, booking_success=True)
    assert repo.record_ai_usage(first) is True
    assert repo.record_ai_usage(first) is False
    # Unique partial index prevents double-counting a booking even under retry.
    second = usage("ai-request-" + str(uuid.uuid4()), correlation, booking_success=True)
    assert repo.record_ai_usage(second) is False
    summary = repo.ai_cost_summary()
    assert summary["request_count"] >= 1
    assert summary["successful_bookings"] >= 1


def test_concurrent_duplicate_usage_is_saved_once():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("requires isolated CI PostgreSQL")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    row = usage("ai-request-race-" + str(uuid.uuid4()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: repo.record_ai_usage(row), range(2)))
    assert sorted(outcomes) == [False, True]


def test_schema_is_sandbox_guarded_and_does_not_store_content():
    schema = (Path(__file__).parents[1] / "schema.sql").read_text()
    ledger = schema[schema.index("CREATE TABLE IF NOT EXISTS jarvis_ai_usage_events"):]
    assert "jarvis_return_queue_test" in schema
    assert "current_user <> 'jarvis_sandbox'" in schema
    assert "prompt" not in ledger.lower()
    assert "response" not in ledger.lower()

def test_cost_overflow_is_rejected():
    with pytest.raises(ValueError, match="invalid_cost_micro_usd"):
        calculate_cost_micro_usd(
            2_147_483_647, 0, 9_223_372_036_854_775_807, 0,
        )
