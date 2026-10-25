"""Offline comparison for immutable, source-backed model price snapshots.

Rates are USD per million tokens. This module makes no provider calls and does
not infer that a model is enabled for a particular account.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def estimate_usd(input_tokens, output_tokens, input_usd_per_million, output_usd_per_million):
    values = (input_tokens, output_tokens)
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in values):
        raise ValueError("invalid_token_count")
    rates = (input_usd_per_million, output_usd_per_million)
    if any(not isinstance(v, (int, float, Decimal)) or isinstance(v, bool) or Decimal(str(v)) < 0 for v in rates):
        raise ValueError("invalid_rate")
    total = (Decimal(input_tokens) * Decimal(str(input_usd_per_million)) +
             Decimal(output_tokens) * Decimal(str(output_usd_per_million))) / Decimal(1_000_000)
    return total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def compare(candidates, input_tokens, output_tokens, as_of=None):
    """Return comparable costs only for dated snapshots with official sources.

    Each candidate must have id, input/output rates, source_url, checked_on,
    and availability (VERIFIED, UNVERIFIED, or UNAVAILABLE). Unverified
    availability is preserved as metadata, not promoted to a verified claim.
    """
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("empty_candidates")
    rows = []
    for item in candidates:
        required = {"id", "input_usd_per_million", "output_usd_per_million",
                    "source_url", "checked_on", "availability"}
        if not isinstance(item, dict) or not required <= item.keys():
            raise ValueError("incomplete_price_snapshot")
        if not str(item["source_url"]).startswith("https://"):
            raise ValueError("non_https_source")
        try:
            checked = date.fromisoformat(item["checked_on"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_checked_on") from exc
        if as_of is not None and checked > date.fromisoformat(as_of):
            raise ValueError("snapshot_from_future")
        if item["availability"] not in {"VERIFIED", "UNVERIFIED", "UNAVAILABLE"}:
            raise ValueError("invalid_availability")
        rows.append({
            "id": item["id"],
            "availability": item["availability"],
            "checked_on": checked.isoformat(),
            "source_url": item["source_url"],
            "estimated_cost_usd": str(estimate_usd(
                input_tokens, output_tokens,
                item["input_usd_per_million"], item["output_usd_per_million"])),
        })
    return sorted(rows, key=lambda row: (Decimal(row["estimated_cost_usd"]), row["id"]))
