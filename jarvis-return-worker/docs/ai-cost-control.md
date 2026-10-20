# Jarvis AI usage and cost control

## Data recorded

`Orchestrator.record_ai_usage()` accepts provider-reported request metadata and passes it to the sandbox-scoped repository. The ledger records request/correlation IDs, provider, model, input/output token counts, the USD rate snapshot, integer micro-USD cost, latency, outcome, and whether that request completed a booking. It does not accept or store prompt text, model output, customer data, API tokens, or connection strings.

Rates are integer micro-USD per million tokens. Cost uses integer arithmetic and half-up rounding; the input and output rates are stored with every event so a later price change does not rewrite historical costs. A unique request ID prevents duplicate accounting. A partial unique index allows at most one successful booking marker for a correlation ID. Queries return total cost, average cost per request, and average total cost per successful booking.

Usage must be recorded at the Orchestrator boundary after the selected Provider returns its usage metadata. This telemetry path does not select a model, call an AI API, send messages, or bypass the Policy Engine, Dispatcher, Outbox, or Worker.

## 100-case synthetic baseline

Run `python -m jarvis_return_worker.benchmark --output benchmark-report.json --corpus benchmark-corpus.json` from the worker package directory. The fixture contains 100 unique synthetic Russian intake examples and separately curated labels for make, intent, urgency, symptom group, and AUTO/APPROVAL/HUMAN routing. It reports accuracy, p50/p95 latency, and unsafe critical-case routing. It runs an offline keyword baseline only and performs no network calls.

The existing Railway benchmark deployment confirms **no candidate AI model was connected**: its recorded report has `model_tested=false`, `NOT_VERIFIED: no candidate API connected`, 100/100 synthetic rows, and explicitly limits the result to an offline baseline. Thus the result is not evidence of AI-model accuracy or price.

## Provider comparison gate

Before benchmarking a real candidate, verify account-specific access with that provider's official model-list or equivalent endpoint in the isolated Sandbox. Verify model ID and input/output rates against the provider's official model and pricing documentation on the same date. Record those rates as an immutable per-request snapshot. Do not infer model availability from an announcement, and do not substitute the current production model. Use synthetic inputs only, redact provider errors, and report any unavailable model as blocked rather than assigning it a score.

At this branch's current Sandbox state, the Worker environment contains only queue, transport, and sandbox settings; the benchmark service has no configured variables or model API credentials. No real-model availability check or 100-request model comparison was run. The offline baseline and accounting code are testable; external model results remain blocked until a test provider is explicitly configured in Sandbox.

## Safety and recovery

The schema refuses to create the ledger unless the connected database is `jarvis_return_queue_test` and the role is `jarvis_sandbox`; the repository independently verifies the same identity. The ledger uses an idempotent insert and never overwrites an existing request. On retry, submit the same request ID; `False` means the usage event already exists. Never replay an AI request just to reconstruct missing accounting data. If provider usage is unknown, record an `UNKNOWN` result and reconcile from provider-side usage data before estimating or backfilling costs.

Application rollback can remove the reporting integration while keeping the additive ledger for audit. Do not delete usage history as part of rollback. The existing delivery state machine and AUTO → APPROVAL → HUMAN rules remain authoritative for all customer-facing actions.


## Cached-input calculation — opt-in only

`ai_tiered_cost.calculate_tiered_cost_micro_usd()` is an isolated, pure
calculator with explicit standard input, cached input and output rates. Cached
tokens are a subset of total input tokens, not an additional quantity. It
rejects missing cached-input pricing, impossible token counts and BIGINT
overflow. The calculator has CI unit tests.

**Important:** this helper is not connected to `normalize_usage()`,
`Orchestrator.record_ai_usage()` or the PostgreSQL ledger. The existing
ledger still records two rate snapshots and calculates its cost from ordinary
input/output totals. Do not represent its historical or current rows as
cache-aware, and do not send cached provider usage into that two-rate path
unless a separate, reviewed accounting contract is implemented. This is a
known functional limitation, not a verified model-cost comparison.

Before enabling cache-aware production reporting, design an additive schema
that preserves the provider-reported token categories and their immutable
rates, validate the provider's category semantics, and test migration,
idempotency, PostgreSQL restart and mixed legacy/new reporting in an isolated
Sandbox. Do not rewrite existing historical cost rows.

## Verification evidence and remaining blockers

GitHub Actions at commit `7edbb8cc46266ed841547912d105159e896fd7e3`
reported 136 Worker tests passed and 54 MCP tests passed (11 MCP skips).
The Worker workflow also printed
`AI usage ledger PostgreSQL restart recovery: PASS` after restarting the
ephemeral PostgreSQL container. This verifies ledger persistence and
idempotency in CI, not a Railway deployment or real model inference.

Real candidate API access, official account-specific model availability,
actual latency/accuracy on 100 synthetic examples per candidate, token
category metadata, and provider-billed cost reconciliation remain NOT RUN.
The baseline's `model_tested=false` must remain unchanged until an
actual, budget-authorized model benchmark has run.
