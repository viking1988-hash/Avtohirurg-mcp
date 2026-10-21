import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from jarvis_return_worker.ai_components import normalize_components
from jarvis_return_worker.queue import QueueRepository


def usage():
    rid = "ai-v2-" + str(uuid.uuid4())
    return dict(
        request_id=rid, correlation_id=rid,
        provider="synthetic-provider", model="synthetic-model",
        input_tokens=1000, output_tokens=500,
        input_rate_micro_usd_per_million=2_000_000,
        output_rate_micro_usd_per_million=8_000_000,
        expected_cost_micro_usd=5400,
        latency_ms=10, outcome="SUCCESS", booking_success=False,
    )


def components():
    return [
        dict(component="standard_input", token_count=600, rate_micro_usd_per_million=2_000_000),
        dict(component="cached_input", token_count=400, rate_micro_usd_per_million=500_000),
        dict(component="output", token_count=500, rate_micro_usd_per_million=8_000_000),
    ]


def test_v2_rejects_double_count_and_unexpected_fields():
    u = usage()
    with pytest.raises(ValueError, match="input_components_mismatch"):
        normalize_components(components() + [
            dict(component="cache_write_input", token_count=1, rate_micro_usd_per_million=0)
        ], input_tokens=u["input_tokens"], output_tokens=u["output_tokens"],
            expected_cost_micro_usd=u["expected_cost_micro_usd"])
    with pytest.raises(ValueError, match="invalid_component_fields"):
        normalize_components([dict(components()[0], prompt="secret")],
            input_tokens=600, output_tokens=0, expected_cost_micro_usd=1200)


def test_v2_atomic_insert_and_concurrent_idempotency():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("disposable PostgreSQL required")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    u, parts = usage(), components()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: repo.record_ai_usage_v2(u, parts), range(2)))
    assert sorted(outcomes) == [False, True]
    with repo.connection() as conn:
        row = conn.execute("SELECT cost_micro_usd, pricing_version FROM jarvis_ai_usage_events WHERE request_id=%s",
                           (u["request_id"],)).fetchone()
        count = conn.execute("SELECT count(*) AS n FROM jarvis_ai_usage_cost_components WHERE request_id=%s",
                             (u["request_id"],)).fetchone()["n"]
    assert row["cost_micro_usd"] == 5400 and row["pricing_version"] == 2 and count == 3
    assert repo.record_ai_usage_v2(u, parts) is False


def test_v2_invalid_total_does_not_create_parent():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("disposable PostgreSQL required")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    u = usage()
    u["expected_cost_micro_usd"] = 1
    with pytest.raises(ValueError, match="component_cost_mismatch"):
        repo.record_ai_usage_v2(u, components())
    with repo.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM jarvis_ai_usage_events WHERE request_id=%s",
                            (u["request_id"],)).fetchone()["n"] == 0


def test_v2_conflicting_retry_is_rejected():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("disposable PostgreSQL required")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    u, parts = usage(), components()
    assert repo.record_ai_usage_v2(u, parts)
    altered = dict(u, model="different-model")
    with pytest.raises(Exception, match="different metadata"):
        repo.record_ai_usage_v2(altered, parts)
    altered_rate = dict(u, input_rate_micro_usd_per_million=u["input_rate_micro_usd_per_million"] + 1)
    with pytest.raises(Exception, match="different metadata"):
        repo.record_ai_usage_v2(altered_rate, parts)
    changed_parts = [dict(x) for x in parts]
    changed_parts[0]["rate_micro_usd_per_million"] += 1
    # Rounding is unchanged, but immutable rate snapshots must still conflict.
    with pytest.raises(Exception, match="different cost components"):
        repo.record_ai_usage_v2(u, changed_parts)


def test_v2_rejects_reuse_of_legacy_request_id():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("disposable PostgreSQL required")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    u, parts = usage(), components()
    legacy = {k: v for k, v in u.items() if k != "expected_cost_micro_usd"}
    assert repo.record_ai_usage(legacy) is True
    with pytest.raises(Exception, match="different metadata"):
        repo.record_ai_usage_v2(u, parts)
    with repo.connection() as conn:
        existing = conn.execute(
            "SELECT pricing_version FROM jarvis_ai_usage_events WHERE request_id=%s",
            (u["request_id"],),
        ).fetchone()
        parts_count = conn.execute(
            "SELECT count(*) AS n FROM jarvis_ai_usage_cost_components WHERE request_id=%s",
            (u["request_id"],),
        ).fetchone()["n"]
    assert existing["pricing_version"] == 1
    assert parts_count == 0


def test_v2_rejects_distinct_request_id_for_same_successful_booking():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("disposable PostgreSQL required")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    first, parts = usage(), components()
    first["booking_success"] = True
    assert repo.record_ai_usage_v2(first, parts) is True
    second = dict(first, request_id="ai-v2-" + str(uuid.uuid4()))
    with pytest.raises(Exception, match="correlation_id already has a successful booking"):
        repo.record_ai_usage_v2(second, parts)
    with repo.connection() as conn:
        count = conn.execute(
            "SELECT count(*) AS n FROM jarvis_ai_usage_events WHERE correlation_id=%s",
            (first["correlation_id"],),
        ).fetchone()["n"]
    assert count == 1


def test_pricing_version_summary_keeps_legacy_and_v2_separate():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("disposable PostgreSQL required")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    before = repo.ai_cost_summary_by_pricing_version()
    v1, v2 = usage(), usage()
    legacy = {k: v for k, v in v1.items() if k != "expected_cost_micro_usd"}
    assert repo.record_ai_usage(legacy) is True
    assert repo.record_ai_usage_v2(v2, components()) is True
    after = repo.ai_cost_summary_by_pricing_version()
    for version, delta in ((1, 6000), (2, 5400)):
        prior = before.get(version, {})
        assert after[version]["total_cost_micro_usd"] - prior.get("total_cost_micro_usd", 0) == delta
        assert after[version]["request_count"] - prior.get("request_count", 0) == 1


def test_version_summary_flags_unknown_usage_as_incomplete():
    dsn = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not dsn:
        pytest.skip("disposable PostgreSQL required")
    repo = QueueRepository(SimpleNamespace(database_url=dsn))
    repo.migrate()
    prior = repo.ai_cost_summary_by_pricing_version().get(2, {})
    u = usage()
    u["outcome"] = "UNKNOWN"
    assert repo.record_ai_usage_v2(u, components()) is True
    current = repo.ai_cost_summary_by_pricing_version()[2]
    assert current["unknown_usage_requests"] == prior.get("unknown_usage_requests", 0) + 1
    assert current["cost_completeness"] == "INCOMPLETE"


def test_v2_cache_write_component_is_accounted_once():
    u = usage()
    parts = [
        dict(component="standard_input", token_count=500, rate_micro_usd_per_million=2_000_000),
        dict(component="cached_input", token_count=300, rate_micro_usd_per_million=500_000),
        dict(component="cache_write_input", token_count=200, rate_micro_usd_per_million=3_000_000),
        dict(component="output", token_count=500, rate_micro_usd_per_million=8_000_000),
    ]
    assert normalize_components(parts, input_tokens=1000, output_tokens=500,
                                expected_cost_micro_usd=5750) == parts
    with pytest.raises(ValueError, match="input_components_mismatch"):
        normalize_components(parts, input_tokens=800, output_tokens=500,
                             expected_cost_micro_usd=5750)


def test_v2_rejects_cost_overflow_without_database_write():
    u = usage()
    parts = [
        dict(component="standard_input", token_count=1000,
             rate_micro_usd_per_million=(1 << 63) - 1),
        dict(component="output", token_count=500,
             rate_micro_usd_per_million=(1 << 63) - 1),
    ]
    with pytest.raises(ValueError, match="invalid_cost_micro_usd"):
        normalize_components(parts, input_tokens=u["input_tokens"],
                             output_tokens=u["output_tokens"],
                             expected_cost_micro_usd=u["expected_cost_micro_usd"])
