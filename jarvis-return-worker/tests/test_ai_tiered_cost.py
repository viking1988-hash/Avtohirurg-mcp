import pytest
from jarvis_return_worker.ai_tiered_cost import calculate_tiered_cost_micro_usd as cost
from jarvis_return_worker.ai_cost import calculate_cost_micro_usd


def test_without_cache_matches_existing_ledger_calculation():
    assert cost(input_tokens=1000, output_tokens=500,
                input_rate_micro_usd_per_million=2_000_000,
                output_rate_micro_usd_per_million=8_000_000) == calculate_cost_micro_usd(
                    1000, 500, 2_000_000, 8_000_000)


def test_cached_tokens_are_subset_not_double_counted():
    assert cost(input_tokens=1000, output_tokens=500, cached_input_tokens=400,
                input_rate_micro_usd_per_million=2_000_000,
                cached_input_rate_micro_usd_per_million=500_000,
                output_rate_micro_usd_per_million=8_000_000) == 5400


def test_cached_input_requires_explicit_rate():
    with pytest.raises(ValueError, match="missing_cached_input_rate"):
        cost(input_tokens=100, output_tokens=0, cached_input_tokens=20,
             input_rate_micro_usd_per_million=1_000_000,
             output_rate_micro_usd_per_million=1_000_000)


def test_cached_input_cannot_exceed_total_input():
    with pytest.raises(ValueError, match="invalid_cached_input_tokens"):
        cost(input_tokens=10, output_tokens=0, cached_input_tokens=11,
             input_rate_micro_usd_per_million=1_000_000,
             cached_input_rate_micro_usd_per_million=1_000_000,
             output_rate_micro_usd_per_million=1_000_000)


def test_half_up_and_bigint_overflow():
    assert cost(input_tokens=1, output_tokens=0,
                input_rate_micro_usd_per_million=500_000,
                output_rate_micro_usd_per_million=0) == 1
    with pytest.raises(ValueError, match="invalid_cost_micro_usd"):
        cost(input_tokens=2_147_483_647, output_tokens=0,
             input_rate_micro_usd_per_million=9_223_372_036_854_775_807,
             output_rate_micro_usd_per_million=0)
