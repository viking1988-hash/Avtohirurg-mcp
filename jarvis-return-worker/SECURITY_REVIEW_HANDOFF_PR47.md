# PR #47 — Independent Security Review Handoff

Repository: `viking1988-hash/Avtohirurg-mcp`  
PR: https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47  
Security issue: https://github.com/viking1988-hash/Avtohirurg-mcp/issues/69

## Scope and safety

Review the Jarvis Return Worker in the PR branch, focusing on Telegram/webhook external effects, approval binding, PostgreSQL fencing and reconciliation. Use synthetic fixtures, disposable PostgreSQL and fake receivers only. Do not use real customer data, Telegram bots/chats, or Production. Do not merge, deploy, enable outbound transports or resolve review threads without evidence.

## Historical implementation baseline (2026-10-30; code commit `d77c635`)

Implementation commit tested: `d77c635e2b181867b2e7f497bccc97112c16d92c`. The PR head was subsequently advanced by a handoff-documentation-only commit; no implementation files changed in that documentation update.
- Worker CI: https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41066733687 — SUCCESS; 299 passed, 2 skipped, 1 warning, 9 subtests passed; PostgreSQL restart recovery PASS.
- MCP check: https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41066733700 — SUCCESS.
- The two skipped tests are disposable-receiver contract tests skipped because no receiver endpoint is configured; they are not evidence of receiver-side idempotency. The warning is the Starlette TestClient deprecated AnyIO BlockingPortal alias.
- At this earlier code checkpoint, PR was open, Draft and unmerged with 10 inline threads (8 unresolved, 2 resolved). This is historical; see the current disposition below.
- Telegram registration requires credentials and exact `JARVIS_TELEGRAM_OUTBOUND_APPROVED=1`; webhook registration requires exact `JARVIS_WEBHOOK_OUTBOUND_APPROVED=1`. Both flags are unset by default. Do **not** set either flag outside synthetic isolated review.
- Current-head adversarial PostgreSQL regression `test_separate_process_stale_sender_after_recovery_is_fenced` confirms a worker paused after DB verification can still resume and cause a synthetic remote effect after recovery. Fencing rejects stale DB writes and prevents a new claim while reconciliation is required, but cannot revoke an already-authorized/in-flight external request. This is evidence of the residual TOCTOU, not exactly-once delivery.

## Current full-pass disposition (2026-10-30)

Current tested implementation/test commit: `996650561682306fc0e53ed66fe6a9ea8b570557`. It adds one synthetic unit regression, `test_http_approval_audit_uses_http_provider`, for the approval audit provider field.

- [Jarvis Return Worker run 41078590801](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41078590801): SUCCESS; 300 passed, 2 skipped, 1 warning, 9 subtests passed; PostgreSQL restart/receipt recovery PASS. JUnit artifact confirms 302 cases, 0 failures, and the new regression ran.
- [Avtohirurg MCP check run 41078591059](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41078591059): SUCCESS.
- Two disposable receiver tests remain skipped because no receiver endpoint is configured; receiver-side idempotency is NOT VERIFIED.
- Nine of ten review threads are now resolved with evidence replies. The only remaining open thread is the stale-worker external-effect TOCTOU P1. The webhook lease-budget finding is resolved for the bounded request budget; arbitrary process suspension after the pre-send fence remains part of the P1 risk. No review is APPROVED; no independent security signoff exists.
- PR remains open, Draft and unmerged. Current security decision is **HOLD / NO-GO** for real sends, deployment and merge. Outbound opt-in flags stay unset.

The complete finding-by-finding table and the safe-but-not-authorized fake-only Sandbox plan are maintained in [Issue #69](https://github.com/viking1988-hash/Avtohirurg-mcp/issues/69).


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
