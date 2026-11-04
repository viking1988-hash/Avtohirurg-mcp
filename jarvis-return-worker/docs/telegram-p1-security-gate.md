# PR47 Telegram P1 security gate (Draft / not approved for Production)

## Current checkpoint (2026-10-30)

PR head at checkpoint: `3a9bd0ea2a2b0f10d2d80559464ecefc39c9fc53`; tested implementation/test commit: `996650561682306fc0e53ed66fe6a9ea8b570557`.

- [Jarvis Return Worker run 41080346045](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41080346045) SUCCESS: 300 passed, 2 skipped, 1 warning, 9 subtests passed; PostgreSQL restart/receipt recovery PASS.
- [Avtohirurg MCP check 41080346005](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/41080346005) SUCCESS.
- Adversarial tests `test_separate_process_stale_sender_after_recovery_is_fenced`, `test_two_stale_workers_can_reach_provider_without_receiver_dedup`, and `test_lease_lost_after_pre_send_check_never_claims_exactly_once` show the residual: stale DB writes are fenced, but a synthetic provider side effect may still occur after the check. No exactly-once claim is justified.
- 9/10 review threads are resolved with evidence; only stale-worker external-effect P1 remains open. No independent APPROVED review exists.
- Receiver-side durable idempotency is now verified for synthetic requests against the disposable Railway staging receiver. This does not prove the integrated two-worker PostgreSQL lease-expiry path or real Telegram delivery. Real Telegram/webhook sends, deployment, and merge remain blocked. Outbound flags remain fail-closed.


## Verified evidence (2026-10-29)

- GitHub commit `a36f83f3de23e06382eee1249aa854f5feed5e77`: Jarvis Return Worker 265 passed, 2 skipped; PostgreSQL restart recovery PASS; MCP workflow SUCCESS.
- Isolated Railway Staging private HTTP sink at `ef2d2c2c788daf555910b531817bde7dc2cb3f97`: eight concurrent synthetic HTTP requests produced one persisted SQLite effect. This does **not** validate Telegram exactly-once.
- Synthetic unit tests cover expiry at the pre-send fence, expiry immediately after that fence, shared send marker across two orchestrators, and stale receipt/failure rejection.
- **2026-10-29 updated CI evidence:** commit `f07da078acf53f0c5c039a919f3a2a14a718017f`; [Jarvis Return Worker workflow 40936173377](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/40936173377) SUCCESS (**267 passed, 2 skipped, 1 warning, 9 subtests passed**); [MCP check 40936173431](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/40936173431) SUCCESS. Disposable PostgreSQL restart test confirms receipt, CRM intent, and outbox marker persistence and one synthetic replay with no external CRM request.
- The new `test_separate_process_stale_sender_after_recovery_is_fenced` exercises two actual OS processes and independent PostgreSQL connections: process A passes the pre-send fence, process B recovers an expired lease, A models a synthetic remote effect and its late receipt/failure are rejected. **This validates the local database fence, not remote Telegram exactly-once.**


- **Latest CI (2026-10-29):** commit `77546c34085f53a640bac8685fd812f20c8e669a`; [Return Worker run 40942113711](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/40942113711) SUCCESS (**284 passed, 2 skipped, 1 warning, 9 subtests passed**); [MCP check 40942113960](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/40942113960) SUCCESS. Manual delivered confirmation requires nonblank provider and receipt/message ID; API tests enforce operator token and audit rejected requests. A disposable PostgreSQL integration test verifies uncertain deliveries cannot be requeued or claimed without manual resolution and fresh approval. This is synthetic evidence only, not independent signoff.

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


## Railway staging HTTP verification — 2026-11-04

- Verified against PR #78 HEAD `2d5afbeb4313482910e6e37ecd4d8da27a332339`; GitHub `worker-tests` and `syntax` checks completed SUCCESS (2/2).
- Railway staging deployment `1fd6f455-3510-4ab6-ba97-bf3423d92f89` is SUCCESS. Service `jarvis-http-sandbox-sink` runs the synthetic SQLite sink from `Dockerfile.sink`; persistent volume `jarvis-http-sandbox-sink-sqlite` is mounted at `/data`.
- On 2026-11-04, two concurrent authenticated synthetic POSTs to `/messages` returned HTTP 201 and 200. A same-key conflicting payload returned 409. Authenticated `/audit/effects/{key}` returned 200 with `effect_count=1`. Railway HTTP logs at 2026-11-04T11:10:49Z–11:10:50Z confirm all four requests.
- This proves network-level atomic deduplication and conflict rejection by the staging receiver. It is not the integrated two-worker PostgreSQL lease-expiry/lost-ack/recovery test. Railway cloud-agent tasks are disabled for this account, so that remote worker scenario was not run.
- No Telegram/Webhook provider request, production data, or Production change was made. Real Telegram delivery remains fail-closed for ambiguous outcomes; exactly-once delivery is not established.

### Post-redeploy persistence confirmation — 2026-11-03

- Railway deployment `1fd6f455-3510-4ab6-ba97-bf3423d92f89` completed successfully at 2026-11-03T15:23:49Z with the persistent SQLite volume mounted at `/data`.
- After deployment, authenticated `GET /messages/synthetic-1` returned HTTP 200 at 2026-11-03T15:26:50Z. The operator-run PowerShell check confirmed the response body retained status `CANCELLED`; bearer token was entered locally and not printed.
- On 2026-11-04, Railway HTTP logs independently recorded the concurrent receiver check: same-key POSTs 201 and 200, conflicting payload 409, and audit GET 200. The authenticated audit response reported `effect_count=1`.
- These checks establish persistence and receiver-side deduplication for synthetic HTTP requests. They do not exercise two worker processes against PostgreSQL lease expiry or establish exactly-once Telegram delivery.


### CI integration test scope clarification — 2026-11-04

- On PR #78 HEAD `f96261393e9bd1750ee8b86f6add89e0a493f3eb`, GitHub Actions run `41844506736` passed: 314 tests, 9 subtests, 1 warning; the isolated PostgreSQL restart-recovery step also passed.
- `test_pg_unknown_after_real_http_acceptance_reconciles_without_resend` uses the real PostgreSQL test repository and a loopback HTTP SQLite receiver in one test. The test itself performs the HTTP POST directly, then manually expires the PostgreSQL lease and invokes recovery; it does not call the production `Orchestrator` transport path or run two independent dispatchers competing on the same task.
- Therefore this is integrated state/receiver evidence for lost-ack reconciliation, but it is **not** proof of the two-worker stale-lease race through the actual dispatch path, and it is not a Railway deployment test. Do not describe it as either.
- Remaining verification: run two independent worker processes against one disposable PostgreSQL task, synchronize them around the actual pre-send fence and HTTP transport, induce lease expiry/reclaim and response loss, then verify the durable receiver has exactly one effect, the task is not automatically replayed, and reconciliation survives restart.
- Release status remains **HOLD**. No production mutation or real provider send.


### Orchestrator-to-HTTP stale-fence regression — updated 2026-11-04

- Verified code commit `0bf693666fb114511bc281d48affcf74769da22c`: `Jarvis Return Worker` run 41884745803 and `Avtohirurg MCP check` run 41884745576 both completed SUCCESS. Worker suite: **315 passed, 1 warning, 9 subtests passed**; PostgreSQL restart recovery PASS.
- `test_orchestrator_http_effect_after_pg_recovery_stays_reconciliation_required` exercises the real `Orchestrator.process` path with independent OS processes. Worker A pauses after PostgreSQL lease verification. Worker B, using a separate repository instance, recovers the expired delivery marker. A then calls the durable synthetic loopback HTTP receiver and loses its acknowledgement. The task remains `RECONCILIATION_REQUIRED`, another claim is denied, and receiver audit reports `effect_count=1`.
- Scope: Worker B performs recovery, not a competing provider send. This test ran in GitHub Actions against disposable PostgreSQL plus a loopback synthetic receiver; it is not a Railway worker deployment test. Railway HTTP logs independently confirm the staging receiver’s restart persistence, duplicate response (201/200), conflict response (409), and audit GET (200, operator reported `effect_count=1`).
- Real Telegram remains fail-closed for ambiguous outcomes. There is no receiver-side Telegram idempotency contract or independent submitted security approval. **Release status: HOLD / NO-GO.** No real send, merge, or Production mutation.

## Owner review policy clarification — 2026-11-04

- Project acceptance uses one owner plus an explicit AI security audit and recorded test/Staging evidence. A second human approval is required only if repository branch protection or another mandatory repository rule requires it.
- The repository rulesets API returned an empty list. The GitHub integration cannot read branch protection for the target branch (HTTP 403), so required approvals remain **unverified**, not assumed absent.
- The current PR #78 CI and synthetic evidence are recorded in the PR description. This policy clarification does not remove the external-effect risk: Telegram receiver-side idempotency is not established, so ambiguous real sends must remain fail-closed/manual reconciliation. No real sends or Production changes are authorized by this document.
- Keep the real Telegram/Webhook release decision **HOLD / NO-GO** until delivery semantics are made safe or the owner explicitly accepts the documented risk under the project's approval process.
