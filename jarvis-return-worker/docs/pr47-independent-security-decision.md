# PR #47 — Independent security decision

**Status: BLOCKED / HOLD.** This document requests a qualified independent decision; it is not approval.

## Current evidence (2026-10-30)

- Inspect the branch's current HEAD on GitHub before review. Product code/test implementation: `996650561682306fc0e53ed66fe6a9ea8b570557`; later changes documented in this checkpoint are CI/docs only.
- PR is OPEN, Draft, unmerged. Ten review threads: nine resolved, one stale-worker external-effect P1 open. Existing review submissions are COMMENTED; no independent APPROVED review.
- Worker CI [41087125537](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41087125537): SUCCESS, 302 passed, 0 skipped, 1 warning, 9 subtests; PostgreSQL restart/receipt recovery PASS.
- MCP CI [41087125445](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41087125445): SUCCESS, 65 passed, 0 skipped.
- Railway staging was checked read-only by variable names: original Sandbox worker and webhook-worker showed no Telegram/Webhook opt-in flags or corresponding provider credentials/endpoints. Values were redacted; no endpoint was called or setting changed. This does not cover Production.

## P1: stale worker and remote effect

A worker can pass PostgreSQL lease verification, pause, lose its lease, and later invoke a provider. A later database fence can reject its receipt/state update, but it cannot revoke the request. Adversarial synthetic tests document the residual. Exactly-once remote delivery is not established.

### Required reviewer decision

Record one explicit outcome with evidence and scope:

- **NO-GO for external delivery:** keep Telegram and Webhook disabled; fake-only tests may continue under separate owner authorization.
- **Conditional test-only scope:** only if the independent reviewer defines and accepts the residual risk and the owner separately authorizes the exact isolated test. This document grants no permission for real messages or webhook effects.
- **Request redesign:** require a receiver/provider contract or other verifiable control before any external delivery.

Telegram Bot API idempotency for this workflow has not been verified. The candidate Railway HTTP sandbox sink is documented in `WEBHOOK_IDEMPOTENCY_ACCEPTANCE.md` as failing durable receiver acceptance. The actual authoritative webhook receiver has not been identified and verified.

## Required receiver evidence for any real webhook

Receiver owner must prove key-scoped atomic deduplication across concurrency, changed-payload rejection, restart, acceptance followed by lost response, retry/reconciliation, and the full retention horizon. The receiver must expose an authenticated read-only receipt/status lookup by stable operation identity. Identify the implementation/version and endpoint without exposing secrets. Sender-side synthetic CI alone is insufficient.

## Operational state

Provider registration in `app.py` requires exact outbound opt-in flags plus credentials/endpoint. Current inspected Sandbox staging variable names lacked those flags and settings. Production configuration was not inspected. No live sends, endpoint probes, deployments, migrations, or Production changes were made.

## Independent review record

- Reviewer identity and GitHub login:
- Exact PR HEAD reviewed:
- Decision: GO / CONDITIONAL GO / NO-GO:
- Scope and threat model:
- Evidence links and tests inspected:
- Residual risks and required mitigations:
- Configuration / rollback scope:
- Date and independent review reference:

Keep PR #47 Draft and unmerged until a qualified independent reviewer records the decision and the owner separately authorizes any scoped release.