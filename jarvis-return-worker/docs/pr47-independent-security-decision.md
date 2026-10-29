# PR47 — independent security decision request

**Status: BLOCKED / Draft.** This document requests an independent decision; it is **not** approval.

## Verified baseline

Head before this document: `5d757f51cbb0ae05d119538b30a708793e9ad58b`. Worker CI: 292 passed, 2 skipped, 1 warning, 9 subtests passed; PostgreSQL restart recovery PASS; MCP check SUCCESS. See PR #47 and `pr47-final-release-checklist.md`. Eight review threads remain open; seven findings have fixes/regressions, Telegram stale-worker P1 only partially mitigated.

## P1 decision: Telegram stale sender

A sender can pass a PostgreSQL ownership/lease check, pause, lose its lease to recovery, then invoke Telegram. Database fencing prevents late state/receipt writes but **cannot roll back a remote send**. Telegram Bot API offers no receiver-side idempotency token for this path. Exactly-once must **not** be claimed.

Reviewer must explicitly choose one:

- **NO-GO for Telegram:** keep transport disabled; permit separately reviewed fake-only/DB-only features.
- **Conditional GO for test-only Telegram:** accept possible duplicate external effects under documented manual reconciliation, strict test-chat allowlist, outbound kill switch, bounded attempts, operator approval, logging redaction, and explicit test scope. No customer sends.
- **Request redesign:** define a verifiable provider contract or operational compensating controls before any outbound use.

For every decision record reviewer identity, date, threat model, evidence, residual risks, affected configuration and rollback plan. A PR author or automated test cannot substitute for an independent reviewer.

## Webhook decision

Before real webhook sends, the **actual receiver owner** must prove durable deduplication keyed by the outbound idempotency key across concurrent requests, restarts, timeout-after-acceptance and retries; document retention window and response semantics. Verify actual deployed timeout/retry/lease settings and uncertain-outcome manual reconciliation. Until verified, outbound webhook stays disabled.

## Proposed safe separation

1. Keep PR Draft and Production unchanged until review and authorized rollout approval.
2. Evaluate the queue, durable outbox, PostgreSQL recovery, manual approval and fake transport **separately** from outbound Telegram/webhook. Any partial deployment requires a fresh scoped PR and explicit authorization; no silent activation.
3. Do not resolve open review threads without reviewer evidence. Review CI skips, warnings, runtime warnings and test database trust authentication before acceptance.

## Reviewer response template

- Decision: GO / CONDITIONAL GO / NO-GO
- Scope: fake-only / test Telegram / real webhook / customer notifications
- Evidence examined: links to tests, logs and code
- Residual risks and required mitigations:
- Configuration and rollback/kill switch:
- Reviewer name, date and independent approval reference:

**No Production changes, no real Telegram or webhook sends, no merge or Draft removal are authorized by this document.**
