# PR47 Telegram P1 security gate (Draft / not approved for Production)

## Verified evidence (2026-10-29)

- GitHub commit `a36f83f3de23e06382eee1249aa854f5feed5e77`: Jarvis Return Worker 265 passed, 2 skipped; PostgreSQL restart recovery PASS; MCP workflow SUCCESS.
- Isolated Railway Staging private HTTP sink at `ef2d2c2c788daf555910b531817bde7dc2cb3f97`: eight concurrent synthetic HTTP requests produced one persisted SQLite effect. This does **not** validate Telegram exactly-once.
- Synthetic unit tests cover expiry at the pre-send fence, expiry immediately after that fence, shared send marker across two orchestrators, and stale receipt/failure rejection.
- **2026-10-29 updated CI evidence:** commit `f07da078acf53f0c5c039a919f3a2a14a718017f`; [Jarvis Return Worker workflow 40936173377](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/40936173377) SUCCESS (**267 passed, 2 skipped, 1 warning, 9 subtests passed**); [MCP check 40936173431](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/40936173431) SUCCESS. Disposable PostgreSQL restart test confirms receipt, CRM intent, and outbox marker persistence and one synthetic replay with no external CRM request.
- The new `test_separate_process_stale_sender_after_recovery_is_fenced` exercises two actual OS processes and independent PostgreSQL connections: process A passes the pre-send fence, process B recovers an expired lease, A models a synthetic remote effect and its late receipt/failure are rejected. **This validates the local database fence, not remote Telegram exactly-once.**

## Remaining P1 failure window

`verify_dispatch_lease()` commits and releases its PostgreSQL transaction **before** the network request. The lease can expire between the successful check and the provider send. A recovery worker can transition the task to `RECONCILIATION_REQUIRED`, while the stale worker can still invoke Telegram. A late receipt is fenced and cannot overwrite the reconciliation state; the remote side effect may nonetheless have occurred.

Telegram Bot API provides no reliable receiver-side idempotency key for this flow. **Neither database locks nor a second lease check can prove exactly-once remote delivery.** Never infer Telegram delivery from a successful local test or from a missing receipt.

## Required independent acceptance checks

1. **CI SATISFIED for synthetic local fencing; independent review still required.** Run two **separate OS processes** against a disposable PostgreSQL instance. Pause process A immediately after a successful lease check and before the transport call; expire its lease; let process B execute recovery; resume A. Assert the task is `RECONCILIATION_REQUIRED`, B never sends, and A cannot persist a late receipt or change reconciliation state. Record that A's remote call may occur.
2. Inject provider acceptance followed by response loss, timeout, worker crash, and PostgreSQL restart. Verify no automatic retry and that the operator has enough evidence to reconcile safely.
3. Verify retry is allowed **only** for a documented, definitive provider rejection with no accepted external effect. Ambiguous network errors and HTTP 5xx must not be classified as definitive rejections.
4. Independently inspect Telegram adapter error mapping, worker ownership fencing, cancellation, logging/redaction, and manual reconciliation authorization/audit.
5. Confirm no real chat IDs, bot tokens, customer data, or outbound Telegram requests appear in synthetic CI/Staging tests.

## Release decision

**BLOCKED / P1 OPEN.** Keep PR Draft; do not enable real Telegram sends or merge into Production until independent review documents the acceptable delivery semantics and signs off the evidence. The safe default for an ambiguous outcome is manual reconciliation, not automatic resend.
