"""Provider-neutral token and cost metering. Never accepts prompt or response text."""
from decimal import Decimal
import re

MAX_BIGINT = (1 << 63) - 1
MAX_PG_INT = (1 << 31) - 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,120}$")
_OUTCOMES = {"SUCCESS", "RETRYABLE", "PERMANENT", "UNKNOWN"}
_FIELDS = {
    "request_id", "correlation_id", "provider", "model", "input_tokens",
    "output_tokens", "input_rate_micro_usd_per_million",
    "output_rate_micro_usd_per_million", "latency_ms", "outcome",
    "booking_success",
}


def _integer(value, name, *, maximum=MAX_BIGINT):
    if type(value) is not int or value < 0 or value > maximum:
        raise ValueError(f"invalid_{name}")
    return value


def calculate_cost_micro_usd(input_tokens, output_tokens,
                             input_rate_micro_usd_per_million,
                             output_rate_micro_usd_per_million):
    """Round USD to micro-USD using integer arithmetic, half up."""
    values = (
        _integer(input_tokens, "input_tokens", maximum=MAX_PG_INT),
        _integer(output_tokens, "output_tokens", maximum=MAX_PG_INT),
        _integer(input_rate_micro_usd_per_million, "input_rate"),
        _integer(output_rate_micro_usd_per_million, "output_rate"),
    )
    numerator = values[0] * values[2] + values[1] * values[3]
    cost = (numerator + 500_000) // 1_000_000
    return _integer(cost, "cost_micro_usd")


def normalize_usage(usage):
    """Validate a usage-only object; reject accidental prompt/token fields."""
    if not isinstance(usage, dict) or set(usage) - _FIELDS:
        raise ValueError("invalid_usage_fields")
    for name in ("request_id", "correlation_id", "provider", "model"):
        value = usage.get(name)
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid_{name}")
    input_tokens = _integer(usage.get("input_tokens"), "input_tokens", maximum=MAX_PG_INT)
    output_tokens = _integer(usage.get("output_tokens"), "output_tokens", maximum=MAX_PG_INT)
    input_rate = _integer(usage.get("input_rate_micro_usd_per_million"), "input_rate")
    output_rate = _integer(usage.get("output_rate_micro_usd_per_million"), "output_rate")
    latency_ms = _integer(usage.get("latency_ms"), "latency_ms", maximum=MAX_PG_INT)
    outcome = usage.get("outcome")
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        raise ValueError("invalid_outcome")
    booking_success = usage.get("booking_success", False)
    if type(booking_success) is not bool or (booking_success and outcome != "SUCCESS"):
        raise ValueError("invalid_booking_success")
    return {
        "request_id": usage["request_id"],
        "correlation_id": usage["correlation_id"],
        "provider": usage["provider"],
        "model": usage["model"],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_rate_micro_usd_per_million": input_rate,
        "output_rate_micro_usd_per_million": output_rate,
        "cost_micro_usd": calculate_cost_micro_usd(input_tokens, output_tokens, input_rate, output_rate),
        "latency_ms": latency_ms,
        "outcome": outcome,
        "booking_success": booking_success,
    }


def cost_summary(rows):
    """Compute aggregate request and successful-booking unit costs."""
    rows = [normalize_usage(row) for row in rows]
    request_count = len(rows)
    successful_calls = sum(row["outcome"] == "SUCCESS" for row in rows)
    # The database's partial unique index guarantees one booking-success row per correlation.
    booking_count = len({row["correlation_id"] for row in rows if row["booking_success"]})
    total = sum(row["cost_micro_usd"] for row in rows)
    return summarize_totals(request_count, successful_calls, booking_count, total)


def summarize_totals(request_count, successful_calls, booking_count, total):
    request_count = _integer(request_count, "request_count", maximum=MAX_BIGINT)
    successful_calls = _integer(successful_calls, "successful_calls", maximum=MAX_BIGINT)
    booking_count = _integer(booking_count, "booking_count", maximum=MAX_BIGINT)
    total = _integer(total, "cost_micro_usd")
    if successful_calls > request_count or booking_count > request_count:
        raise ValueError("invalid_cost_totals")
    def average(denominator):
        return None if denominator == 0 else (total + denominator // 2) // denominator
    return {
        "request_count": request_count,
        "successful_model_calls": successful_calls,
        "successful_bookings": booking_count,
        "total_cost_micro_usd": total,
        "cost_per_request_micro_usd": average(request_count),
        "cost_per_successful_booking_micro_usd": average(booking_count),
        "currency": "USD",
    }


def usd_string(micro_usd):
    """Format integer micro-USD without binary floating-point arithmetic."""
    return f"{Decimal(_integer(micro_usd, 'cost_micro_usd')) / Decimal(1_000_000):.6f}"
