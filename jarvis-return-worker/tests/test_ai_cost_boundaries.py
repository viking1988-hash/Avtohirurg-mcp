"""Additional accounting boundary regressions; no provider calls or secrets."""
import pytest
from jarvis_return_worker.ai_cost import calculate_cost_micro_usd, summarize_totals, normalize_usage


def test_no_bookings_means_undefined_cost_per_booking():
    result = summarize_totals(2, 1, 0, 900)
    assert result["cost_per_successful_booking_micro_usd"] is None
    assert result["cost_per_request_micro_usd"] == 450


def test_empty_summary_has_no_fabricated_unit_cost():
    result = summarize_totals(0, 0, 0, 0)
    assert result["cost_per_request_micro_usd"] is None
    assert result["cost_per_successful_booking_micro_usd"] is None


def test_reject_inconsistent_aggregate_counts():
    with pytest.raises(ValueError, match="invalid_cost_totals"):
        summarize_totals(1, 2, 0, 0)
    with pytest.raises(ValueError, match="invalid_cost_totals"):
        summarize_totals(1, 1, 2, 0)


def test_cost_half_up_boundary_and_zero():
    assert calculate_cost_micro_usd(0, 0, 0, 0) == 0
    assert calculate_cost_micro_usd(1, 0, 499_999, 0) == 0
    assert calculate_cost_micro_usd(1, 0, 500_000, 0) == 1


def test_reject_boolean_as_integer_token_count():
    with pytest.raises(ValueError, match="invalid_input_tokens"):
        calculate_cost_micro_usd(True, 0, 0, 0)
