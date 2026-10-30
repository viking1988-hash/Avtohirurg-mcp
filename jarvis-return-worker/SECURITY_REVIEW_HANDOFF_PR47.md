# PR #47 — Independent Security Review Handoff

**Review target:** inspect the branch's current HEAD before review. Product implementation and tests were last changed at `996650561682306fc0e53ed66fe6a9ea8b570557`; subsequent commits in this checkpoint are CI harness/documentation only. Confirm the exact review SHA on GitHub.
**PR:** https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47  
**Security gates:** [Issue #68](https://github.com/viking1988-hash/Avtohirurg-mcp/issues/68), [Issue #69](https://github.com/viking1988-hash/Avtohirurg-mcp/issues/69)

## Decision requested

**Current disposition: BLOCKED / HOLD.** PR #47 is OPEN, Draft and unmerged. Nine of ten inline threads are resolved with evidence; the stale-worker external-effect P1 remains open. No independent APPROVED review exists. This handoff is not an approval.

Review the remaining P1 and decide whether any scoped release is acceptable. Do not claim exactly-once Telegram or webhook delivery. Do not enable external sends, deploy, merge, or mark the PR Ready under this handoff.

## Verified CI checkpoint — 2026-10-30

- PR HEAD before documentation refresh: `79828206eb5697c9904f6953b3ce494672ddc893`; product implementation remains at `996650561682306fc0e53ed66fe6a9ea8b570557`.
- [Jarvis Return Worker run 41087125537](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41087125537): SUCCESS — 302 passed, 0 skipped, 1 pytest warning, 9 subtests passed. PostgreSQL restart/receipt recovery passed.
- [Avtohirurg MCP check 41087125445](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41087125445): SUCCESS — 65 tests passed, 0 skipped; PostgreSQL integration tests used an ephemeral CI database.
- Worker receiver-contract tests use a loopback-only synthetic HTTP/SQLite receiver. They prove that test receiver's behavior, not Telegram or an authoritative real webhook receiver.
- Warnings: deprecated AnyIO BlockingPortal alias in Starlette TestClient; GitHub Actions Node.js 20-to-24 transition; ephemeral PostgreSQL trust-auth initialization. These did not fail CI.
- Restart recovery retained receipt/outbox state. No real CRM request, Telegram message, or external webhook was sent.

## Finding disposition

| Thread | Finding | Status | Evidence |
|---|---|---|---|
| [#4387799591](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387799591) | Legacy `TaskApproved` without approval binding | FIXED | `test_legacy_event_requires_fresh_human_approval`; `test_legacy_event_does_not_change_nonapproved_task`. |
| [#4387799676](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387799676) | Case-insensitive HTTP response headers | FIXED | Lowercase response ID and Retry-After regression tests; thread resolved. |
| [#4387799743](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387799743) | Stale worker can produce external effect after lease fence | PARTIAL — BLOCKING P1 | `test_separate_process_stale_sender_after_recovery_is_fenced`; `test_two_stale_workers_can_reach_provider_without_receiver_dedup`; `test_lease_lost_after_pre_send_check_never_claims_exactly_once`. DB state is fenced; external request is not revocable or atomic with PostgreSQL. |
| [#4387799805](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387799805) | HTTP provider mislabeled as fake in audit | FIXED | `test_http_approval_audit_uses_http_provider`. |
| [#4387799865](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387799865) | Reserved request-header override | FIXED | `test_reserved_headers_rejected_case_insensitively`. |
| [#4387799959](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387799959) | HTTP-date Retry-After in client retries | FIXED | `test_http_date_retry_after_is_bounded`; `test_lowercase_retry_after_http_date_on_429`. |
| [#4387800033](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387800033) | Concurrent duplicate provider response IDs | FIXED | PostgreSQL tests serialize completion and preserve duplicate receipt/reconciliation evidence. |
| [#4387800109](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387800109) | HTTP-date Retry-After persistence in queue | FIXED | PostgreSQL `test_retry_after_http_date_is_persisted_as_bounded_retry`. |
| [#4387800161](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387800161) | Response ID without durable receipt | FIXED | PostgreSQL `test_webhook_reconciliation_with_response_id_persists_durable_receipt`. |
| [#4387800251](https://github.com/viking1988-hash/Avtohirurg-mcp/pull/47#discussion_r4387800251) | Webhook lease shorter than bounded request budget | FIXED for bounded budget | Startup and remaining-lease guards; tests `test_short_lease_rejected_for_webhook_retry_budget`, `test_rejects_claim_with_insufficient_remaining_budget`, plus PostgreSQL budget/no-send tests. Arbitrary process suspension remains covered by the separate open P1. |

## External-effect and receiver findings

- `verify_webhook_dispatch_lease()` and the subsequent provider call are separate from the external system's transaction. If a worker pauses after the lease check and resumes later, PostgreSQL fencing cannot prevent the request from reaching the receiver.
- Ambiguous outcomes remain reconciliation-only; do not claim exactly-once. Telegram's API path has no receiver-side idempotency contract verified for this flow.
- `WEBHOOK_IDEMPOTENCY_ACCEPTANCE.md` records that the candidate Railway HTTP sandbox sink fails acceptance: in-memory/process-local state in the inspected candidate, no same-key/different-body rejection, incomplete deduplication across mutations, and no authenticated lookup by stable operation key. This is a recorded source-level finding, not a fresh inspection of the actual configured authoritative receiver.
- Read-only Railway staging variable-name check on 2026-10-30: `Avtohirurg-Jarvis-Sandbox/staging` `jarvis-return-worker` and `jarvis-webhook-sandbox-worker` lacked the Telegram/Webhook outbound approval flags and associated Telegram credentials/test-chat or webhook URL/token names. The PR47 disposable worker also lacked real outbound settings. Values were redacted; no endpoint was called and no config or deployment changed.
- Source gates in `app.py` register Telegram only with exact `JARVIS_TELEGRAM_OUTBOUND_APPROVED=1` plus bot/test-chat credentials; Webhook only with exact `JARVIS_WEBHOOK_OUTBOUND_APPROVED=1` plus endpoint configuration. This is code-level fail-closed behavior, not proof of uninspected environments.

## Independent acceptance still required

1. Independent reviewer inspects current PR HEAD and records a written P1 decision; author and automation cannot self-approve.
2. Reviewer adjudicates Telegram stale-send semantics and residual duplicate risk. Exactly-once is not supported by current evidence.
3. Identify the actual configured webhook receiver without exposing credentials, or explicitly exclude real webhook delivery. Receiver owner must provide durable idempotency evidence across concurrent duplicates, changed-payload conflicts, restart, response-loss and retry, plus retention and authenticated status lookup.
4. Reconfirm exact environment identity and outbound flags immediately before any separately authorized rollout. No such rollout is authorized here.
5. Keep PR Draft and unmerged until the independent decision and owner authorization are separately recorded.

## Safe test scope and limits

Use fake transport, synthetic data, disposable PostgreSQL, and loopback/private disposable receiver only. The staging configuration check above was read-only. No Production changes, migrations, real Telegram/webhook requests, deployment, merge, or Ready transition occurred.