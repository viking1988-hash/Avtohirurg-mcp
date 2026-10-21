"""Validated v2 component snapshots, without prompt/response data."""
from .ai_cost import MAX_PG_INT, _integer

COMPONENTS = frozenset(("standard_input", "cached_input", "cache_write_input", "output"))


def normalize_components(components, *, input_tokens, output_tokens, expected_cost_micro_usd):
    if not isinstance(components, list) or not components or len(components) > len(COMPONENTS):
        raise ValueError("invalid_components")
    seen = set()
    result = []
    numerator = 0
    for item in components:
        if not isinstance(item, dict) or set(item) != {"component", "token_count", "rate_micro_usd_per_million"}:
            raise ValueError("invalid_component_fields")
        kind = item["component"]
        if type(kind) is not str or kind not in COMPONENTS or kind in seen:
            raise ValueError("invalid_component")
        seen.add(kind)
        count = _integer(item["token_count"], "token_count", maximum=MAX_PG_INT)
        rate = _integer(item["rate_micro_usd_per_million"], "rate")
        numerator += count * rate
        result.append({"component": kind, "token_count": count, "rate_micro_usd_per_million": rate})
    # v2 contract requires disjoint input categories; provider-specific cache
    # semantics must be normalized before calling this function.
    actual_input = sum(x["token_count"] for x in result if x["component"] != "output")
    actual_output = sum(x["token_count"] for x in result if x["component"] == "output")
    if actual_input != _integer(input_tokens, "input_tokens", maximum=MAX_PG_INT):
        raise ValueError("input_components_mismatch")
    if actual_output != _integer(output_tokens, "output_tokens", maximum=MAX_PG_INT):
        raise ValueError("output_components_mismatch")
    total = _integer((numerator + 500_000) // 1_000_000, "cost_micro_usd")
    if total != _integer(expected_cost_micro_usd, "expected_cost_micro_usd"):
        raise ValueError("component_cost_mismatch")
    return result
