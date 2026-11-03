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
4. Telegram has no established receiver-side idempotency contract in this project. Fail closed by default and require explicit independent risk acceptance for any real send; do not claim exactly-once.
5. Run isolated PostgreSQL tests and full CI on the exact resulting commit. Record skipped tests and warnings. Obtain independent reviewer decision before any release.

## Release constraints
No real Telegram/Webhook traffic, credentials, merge, production change, or deployment from this evidence branch.
Keep PR #47 Draft and HOLD/NO-GO until external-effect safety is independently demonstrated.


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

### Outstanding release blockers (NO-GO)
1. Main Railway Bun HTTP sink uses volatile in-process Maps; it has no
   demonstrated persistent inbox/receipt transaction across process restart.
2. No demonstrated two-worker PostgreSQL lease-expiry race through the actual
   external receiver and durable receipt recovery after a lost response.
3. Real Telegram delivery lacks a receiver-side idempotency contract.
4. No independent submitted security approval for PR #78.
5. PR #47 is not cleared for merge or Production traffic.

**Release decision: HOLD.** Green synthetic tests are necessary but not
sufficient. No Production mutation or real sends authorized.
