# AI usage ledger v2 — implemented sandbox contract (Draft)

## Scope and compatibility

The sandbox-scoped v2 ledger is implemented in `queue.py`, `ai_components.py`,
`orchestrator.py` and `schema.sql`. It has **not** been deployed to
Production and PR #42 remains Draft. The repository's migration guard limits
the ledger to disposable `jarvis_return_queue_test` with role `jarvis_sandbox`.

Legacy `record_ai_usage()` rows use `pricing_version=1`, including after
repeated additive migrations. The opt-in `record_ai_usage_v2()` path writes
`pricing_version=2`. Old rows are not recalculated or backfilled.

## Implemented cost breakdown

`jarvis_ai_usage_cost_components` has:
- `request_id`: FK to parent event; deletion restricted;
- `component`: `standard_input`, `cached_input`,
  `cache_write_input`, or `output`;
- `token_count`: nonnegative integer;
- `rate_micro_usd_per_million`: nonnegative BIGINT rate snapshot;
- primary key `(request_id, component)`.

The parent row stores the authoritative `cost_micro_usd`, calculated by
rounding the sum of all component token-rate numerators half-up **once**.
There is **no** `provider_surcharge` component, individual component-cost
column, or database trigger that makes component rows universally immutable.
The repository enforces snapshot consistency on retries; other database
writers must not mutate these rows. Do not claim DB-enforced immutability.

Input components must be disjoint after provider-specific normalization;
their token counts sum to `input_tokens`. Output components sum to
`output_tokens`. Never assume provider cache-write tokens are disjoint
without verifying that provider's semantics. The v1 two-rate path remains
unchanged and is not cache-aware.

## Idempotency and safety

Parent and component inserts occur in one transaction. An exact replay of
the same v2 request returns `False`; conflicting metadata, pricing version,
legacy rate snapshots, or component snapshots raise `Conflict`. A distinct
request ID attempting to mark the same correlation as another successful
booking also raises `Conflict`. The unique partial index guarantees at
most one successful booking per correlation.

The v2 API records only validated metadata, never prompt/response text,
customer messages, provider credentials or raw provider errors. It does
not call a model or authorize a booking.

## Evidence and limitations

At commit `251f4b1c54c1be8eebfe6ad80cfac75ff55640ab`, GitHub Actions
reported 145 Worker tests PASS and 54 MCP tests PASS (11 skipped).
The Worker workflow confirmed v1 and v2 persistence through a real restart
of its disposable PostgreSQL container. These are **CI** checks, not a
Railway Sandbox deployment or an external model-cost reconciliation.

Still required before production enablement:
1. Isolated Railway staging E2E using the exact PR HEAD and fake provider.
2. Provider-specific token/category normalization and independently verified
   tariff snapshots for each candidate.
3. Approved spending cap, account-specific model availability, and
   real-model accuracy/latency/cost comparison on synthetic inputs.
4. Mixed v1/v2 reporting separation and reconciliation of unknown usage.
5. Review whether database-level snapshot immutability is required.

Do not merge or deploy PR #42 to Production based solely on CI evidence.
