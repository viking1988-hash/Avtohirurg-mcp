# PR #47 — Independent Security Review Handoff

Repository: `viking1988-hash/Avtohirurg-mcp`  
PR: https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47  
Security issue: https://github.com/viking1988-hash/Avtohirurg-mcp/issues/69

## Scope and safety

Review the Jarvis Return Worker in the PR branch, focusing on Telegram/webhook external effects, approval binding, PostgreSQL fencing and reconciliation. Use synthetic fixtures, disposable PostgreSQL and fake receivers only. Do not use real customer data, Telegram bots/chats, or Production. Do not merge, deploy, enable outbound transports or resolve review threads without evidence.

## Verified baseline (2026-10-30)

Commit `d9413ea3dec8831e853f48ca4c81d5fc66d6bfa2`.
- Worker CI: https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41057065848 — SUCCESS; 297 passed, 2 skipped, 1 warning, 9 subtests passed; PostgreSQL restart recovery PASS.
- MCP check: https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41057065842 — SUCCESS.
- PR Draft, not merged. At last review, 10 inline threads: 8 unresolved, 2 resolved; no independent APPROVED.
- Telegram registration requires credentials and exact `JARVIS_TELEGRAM_OUTBOUND_APPROVED=1`. Flag unset by default. Do **not** set this flag outside synthetic isolated review.

## Required independent verification

1. **P1 stale-worker external send TOCTOU.** Inspect `orchestrator.py` `fenced_provider_send` and `queue.py` `verify_dispatch_lease`. The DB ownership transaction ends before the external send. Prove behavior when worker A pauses after the check, its lease expires and worker B recovers the operation, then A resumes. Distinguish prevention of stale database writes from prevention of remote duplicate effects. Do not claim Telegram exactly-once based on a pre-send fence.
2. **Ambiguous acceptance.** Test accepted-then-timeout, lost response, process crash, concurrent workers, restart and manual reconciliation. Confirm unknown outcomes never trigger unsafe automatic resend.
3. **Webhook receiver-side idempotency.** In a disposable receiver backed by durable storage, demonstrate atomic deduplication by stable operation key across concurrency, restart and timeout-after-acceptance. If no such receiver is available, record this as NOT VERIFIED and block real delivery.
4. **Approval binding and legacy events.** Verify old `TaskApproved` v1 events are handled without infinite retries and that fresh human approval is required where bindings are missing.
5. **HTTP protections.** Check reserved custom headers case-insensitively, HTTP-date `Retry-After` parsing in client and queue, duplicate response ID concurrency, durable response-ID-only reconciliation.
6. **Webhook lease budget.** Verify configured timeout/retry worst case against lease expiration and check that an expired worker cannot persist a false success.
7. **Operational gate.** Verify Telegram is disabled when approval flag is absent, malformed or credentials missing; confirm deployment configuration does not set the opt-in unintentionally.

## Required review output

For each of the eight unresolved PR threads: cite exact commit/file/line, a reproducible test or reason why verification is unavailable, and mark FIXED / PARTIAL / OPEN. Give explicit decisions on P1 remote duplicate risk and receiver-side idempotency. Attach CI links and independent reviewer identity. Only a qualified independent reviewer may submit an independent approval.

## Release gates

- No real Telegram or webhook sends until independent review and explicit owner authorization.
- No Production deployment, PR merge, or Draft-to-Ready transition from this handoff alone.
- Synthetic green CI is necessary but not sufficient for release.
