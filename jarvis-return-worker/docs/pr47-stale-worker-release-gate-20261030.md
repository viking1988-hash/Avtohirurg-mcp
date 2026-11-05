# PR47 stale-worker release gate — evidence requirements

Baseline: PR #47 HEAD d77c635e2b181867b2e7f497bccc97112c16d92c (2026-10-30).
This is a test/evidence branch, **not a fix or authorization to send**.

## Observed code
- `orchestrator.py:fenced_provider_send` checks the dispatch lease then calls the provider; the remote side effect is not part of the database transaction.
- `orchestrator.py:fenced_send` similarly checks the webhook lease before calling `provider.send`.
- `tests/test_orchestrator.py:test_lease_expires_after_fence_before_provider_call_is_not_atomic` documents the gap.

## Blocking acceptance tests
1. PostgreSQL with two independent connections/workers, barrier after successful lease check, expiry/reclaim by worker B, release worker A. Capture all outbound calls through a deterministic fake receiver; assert **no silent duplicate and no automatic replay of ambiguous outcomes**.
2. Inject timeout after remote acceptance, process crash before receipt commit, restart, and repeated claim. Require UNKNOWN/reconciliation; never automatically resend.
3. Verify stable persisted idempotency key for each logical webhook operation across retries and restarts. Receiver must atomically deduplicate same-key concurrent requests, reject conflicting payloads, survive restart, and publish retention guarantees.
4. Telegram has no established receiver-side idempotency contract in this project. Fail closed by default; any future real-send risk decision belongs to the owner under the project's approval process. Do not claim exactly-once.
5. Run isolated PostgreSQL tests and full CI on the exact resulting commit. Record skipped tests and warnings. Complete an explicit AI security audit and owner decision; a second human approval is required only if a mandatory repository rule requires it.

## Release constraints
No real Telegram/Webhook traffic, credentials, merge, production change, or deployment from this evidence branch.
Keep PR #47 Draft and HOLD/NO-GO until external-effect safety is demonstrated and the owner accepts the documented delivery semantics.


## Verified incremental evidence — 2026-11-03

- PR #78 test branch HEAD `9802a1ebfe88f7c83772dc445cdc8c493c47bce8`.
- GitHub Actions `worker-tests` SUCCESS, run 41623120222: 311 passed,
  1 non-blocking Starlette/AnyIO deprecation warning, 9 subtests passed.
- `syntax` SUCCESS, run 41623120425.
- The synthetic isolated SQLite-backed HTTP receiver now has tests for:
  concurrent same-key same-payload replay (eight senders, one effect),
  conflicting payload rejection (409), receipt recovery after receiver restart,
  malformed idempotency keys, and authenticated receipt lookup.
- Ephemeral PostgreSQL recovery tests cover two competing recoverers and
  prevent claiming an UNKNOWN delivery for automatic resend.
- These CI tests are NOT an end-to-end proof of a real external side effect.
  The previous Railway synthetic E2E deployment predates this commit.

### Updated verification — 2026-11-04

- PR #78 code HEAD `0bf693666fb114511bc281d48affcf74769da22c`: `Jarvis Return Worker` run 41884745803 and `Avtohirurg MCP check` run 41884745576 both completed SUCCESS.
- Worker CI: 315 passed, 1 Starlette/AnyIO deprecation warning, 9 subtests; PostgreSQL restart-recovery step PASS.
- `test_orchestrator_http_effect_after_pg_recovery_stays_reconciliation_required` now runs worker A and recovery worker B as separate Python OS processes with independent PostgreSQL repository instances. A pauses after the successful database lease fence; B recovers the expired delivery marker; A resumes and commits one effect to the synthetic durable loopback HTTP receiver while simulating a lost acknowledgement. The row remains `RECONCILIATION_REQUIRED`, another dispatcher cannot claim it, and receiver audit reports `effect_count=1`.
- The separate-process test is GitHub CI evidence against disposable PostgreSQL and the loopback receiver. Railway staging separately confirms durable synthetic receiver persistence and same-key duplicate/conflict handling; the integrated worker race itself was not run on Railway.
- Release blockers: no receiver-side idempotency guarantee for real Telegram and no demonstrated production receiver parity/authorization. Keep real sends fail-closed and the release HOLD; no real sends or Production changes. A second human review is not a project gate unless required by mandatory repository rules.
