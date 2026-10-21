# AI usage ledger v2 — additive migration contract (Draft)

## Goal and compatibility

The current `jarvis_ai_usage_events` table stores one input and one output rate.
It is valid for those two categories only. The isolated
`ai_tiered_cost.calculate_tiered_cost_micro_usd` helper is not yet wired into
the ledger. **Never treat existing rows as cache-aware**.

A future ledger v2 must be additive, never rewrite old rows, and must remain
limited to the ephemeral `jarvis_return_queue_test` database and
`jarvis_sandbox` role. Production migration is out of scope.

## Proposed immutable detail relation

`jarvis_ai_usage_cost_components`:
- `request_id` — FK to `jarvis_ai_usage_events.request_id`;
- `component` — one of `standard_input`, `cached_input`,
  `cache_write_input`, `output`, `provider_surcharge`;
- `token_count` — nonnegative PostgreSQL integer for token-based components;
- `rate_micro_usd_per_million` — nonnegative BIGINT rate snapshot;
- `cost_micro_usd` — nonnegative BIGINT component cost;
- primary key `(request_id, component)`.

This is a design contract, **not a deployed schema**. A provider may report
cache-write tokens as a subset of input or as a separate billable category;
normalize each provider explicitly before creating components. Never
automatically assume that cached tokens and cache writes are disjoint.
For provider surcharges that are not token based, use a separate explicit
amount field in a reviewed schema rather than inventing synthetic tokens.

## Atomicity and reporting

1. Validate provider-reported usage and exact official tariff snapshot.
2. Calculate total cost from the sum of unrounded component numerators and
   round half-up **once**, so the result matches the invoice's rounding
   convention when applicable. Also preserve per-component breakdown.
3. Insert parent and all components in a **single transaction** with one
   request idempotency key; reject conflicting retries rather than overwrite.
4. Never mark `booking_success` without a confirmed booking receipt.
5. Legacy rows retain `pricing_version=1` semantics; new rows require
   explicit `pricing_version=2` metadata and verified components. No silent
   reinterpretation or retroactive backfill.
6. Queries must report mixed legacy/v2 totals separately and label any
   unknown provider usage as `UNKNOWN`, not zero cost.

## Required tests before implementation is enabled

- Schema guard: reject non-sandbox database and role.
- Legacy rows and summary unchanged after additive migration.
- Provider token category mapping, cached subset and cache-write semantics.
- Single rounding at total, BIGINT overflow and negative values.
- Concurrent duplicate requests, conflicting retries, partial transaction
  rollback, and one booking marker per correlation.
- PostgreSQL restart retains parent and every component.
- No prompt/response, PII, API credentials, or raw provider errors stored.
- Railway isolated staging E2E with fake provider; no real customer messages.

## Gate

No schema or runtime integration is enabled by this document. Real provider
comparison remains blocked pending Sandbox API access, confirmed model IDs,
pricing and an approved spending cap. Do not merge PR #42 or deploy Production
based on the offline keyword baseline.
