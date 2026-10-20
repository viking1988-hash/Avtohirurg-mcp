"""Pure, opt-in AI provider pricing calculations.

This module does not change the existing usage ledger or call external APIs.
Rates are integer micro-USD per million tokens; all rounding is half-up.
"""
from jarvis_return_worker.ai_cost import MAX_BIGINT, MAX_PG_INT, _integer


def calculate_tiered_cost_micro_usd(
    *, input_tokens, output_tokens, input_rate_micro_usd_per_million,
    output_rate_micro_usd_per_million, cached_input_tokens=0,
    cached_input_rate_micro_usd_per_million=None,
):
    """Return exact total including an explicitly priced cached-input subset.

    input_tokens is the total input including cached_input_tokens. Unpriced
    cached input is rejected rather than silently charged at standard rates.
    """
    total_in = _integer(input_tokens, "input_tokens", maximum=MAX_PG_INT)
    total_out = _integer(output_tokens, "output_tokens", maximum=MAX_PG_INT)
    cached = _integer(cached_input_tokens, "cached_input_tokens", maximum=MAX_PG_INT)
    if cached > total_in:
        raise ValueError("invalid_cached_input_tokens")
    input_rate = _integer(input_rate_micro_usd_per_million, "input_rate")
    output_rate = _integer(output_rate_micro_usd_per_million, "output_rate")
    if cached_input_rate_micro_usd_per_million is None:
        if cached:
            raise ValueError("missing_cached_input_rate")
        cached_rate = 0
    else:
        cached_rate = _integer(cached_input_rate_micro_usd_per_million, "cached_input_rate")
    numerator = (
        (total_in - cached) * input_rate
        + cached * cached_rate
        + total_out * output_rate
    )
    return _integer((numerator + 500_000) // 1_000_000, "cost_micro_usd")
