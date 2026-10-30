# PR #47 final acceptance checklist — BLOCKED

## Current checkpoint — 2026-10-30

- PR HEAD before this documentation refresh: `79828206eb5697c9904f6953b3ce494672ddc893`; OPEN, Draft, unmerged.
- 10 inline review threads: 9 resolved with evidence; one stale-worker external-effect P1 remains open. Reviews are COMMENTED; no independent APPROVED review.
- Product code/test implementation remains at `996650561682306fc0e53ed66fe6a9ea8b570557`; the later `927c1c6` and `7982820` commits changed CI harnesses only.

## Automated verification

- [Jarvis Return Worker CI](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41087125537): SUCCESS — 302 passed, 0 skipped, 1 warning, 9 subtests; PostgreSQL restart/receipt recovery PASS.
- [Avtohirurg MCP check](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41087125445): SUCCESS — 65 passed, 0 skipped, including the PostgreSQL tests against ephemeral PostgreSQL 18.
- Worker receiver-contract tests use only synthetic loopback HTTP/SQLite. These do not prove Telegram or authoritative webhook receiver idempotency.
- Warnings: deprecated AnyIO BlockingPortal alias in Starlette TestClient; GitHub Actions Node.js 20-to-24 transition; ephemeral PostgreSQL trust-auth initialization. No workflow failed.

## Finding disposition

- **FIXED (9 threads):** legacy TaskApproved binding; case-insensitive response headers; audit provider labeling; reserved headers; HTTP-date Retry-After in client and queue; duplicate response-ID completion; durable receipt evidence; bounded webhook timeout/retry lease budget. Regression evidence and test identifiers are in Issue #69 and the handoff.
- **PARTIAL / BLOCKING (1 thread):** stale worker can still invoke an external provider after the PostgreSQL lease check if suspended and resumed. Fencing protects DB writes, not the remote effect. No exactly-once claim.

## Read-only operational checks

- Source code requires exact `JARVIS_TELEGRAM_OUTBOUND_APPROVED=1` plus Telegram credentials/test-chat allowlist; Webhook requires exact `JARVIS_WEBHOOK_OUTBOUND_APPROVED=1` plus endpoint configuration.
- Railway staging variable-name inspection on 2026-10-30 found no Telegram/Webhook opt-in flags or matching provider credentials/endpoints on `Avtohirurg-Jarvis-Sandbox/staging` worker services or the PR47 disposable worker. Values were redacted; no endpoint requests or configuration changes were made. Production was not inspected.
- The documented candidate Railway HTTP sink fails the durable receiver acceptance contract. The actual authoritative receiver is not established.

## Remaining gates — do not waive by CI

- [ ] Qualified independent reviewer inspects the current branch HEAD and records a written decision on stale-worker/remote-effect risk.
- [ ] Actual webhook receiver is identified and demonstrates durable idempotency across concurrency, changed-payload conflicts, restart, lost response/retry, retention, and authenticated status lookup—or real webhook dispatch is explicitly excluded.
- [ ] Reviewer states whether Telegram external delivery is NO-GO or accepts a strictly scoped residual-risk model; no exactly-once assertion.
- [ ] Before any rollout, separately authorized owner rechecks exact environment, flags, credentials, rollback and current CI.
- [ ] Keep PR Draft, unmerged, and external sending disabled until those gates are recorded.

## Safety record

No Production changes, migrations, real Telegram messages, webhook requests, deployment, merge, or Ready transition were performed. Green CI is not release approval.