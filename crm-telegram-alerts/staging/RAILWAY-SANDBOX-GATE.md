# Railway Sandbox integration gate — Telegram Outbox

Status: **not executed in Railway**. PostgreSQL 16 disposable CI integration: PASS at c12ae3e.

## Preconditions
1. Use **Avtohirurg-Alerts-Sandbox**, staging only, with a dedicated disposable PostgreSQL instance. Do not connect the worker to CRM production or CRM Staging data.
2. Confirm the database is empty/disposable, has no real clients, and is backed up or reproducible.
3. Ensure the test service cannot send real Telegram messages: no TELEGRAM_BOT_TOKEN and no TELEGRAM_CHAT_ID; use mocked transport.
4. Verify that Railway has no pending configuration changes before beginning. Never create a public TCP proxy for PostgreSQL.

## Safe execution order
1. Deploy the isolated migration runner from a pinned reviewed commit, with private PostgreSQL networking.
2. Run `staging/001_telegram_outbox.sql` on that disposable database.
3. Run `staging/003_outbox_readonly_verification.sql` and save actual output.
4. Run `staging/004_outbox_postgres_smoke.sql` with ON_ERROR_STOP=1. It uses BEGIN/ROLLBACK; verify row count remains zero.
5. Test two concurrent workers with separate DB connections and verify each outbox row has at most one active lease.
6. Inject process termination immediately after lease claim and after mocked Telegram acknowledgement. Verify expired leases move to `review` without blind retries.
7. Test HTTP 429 and transient 5xx with bounded backoff; distinguish confirmed rejection from uncertain delivery.
8. Capture deploy logs and PostgreSQL evidence. Roll back or delete disposable runner only after preserving results.

## Exit criteria
- PASS: schema, constraints, unique key, retry ceiling, stale lease token, acknowledgement and rollback.
- PASS: concurrent claim, crash recovery, uncertain-delivery review, no real Telegram sends.
- FAIL/STOP: production DB reference, customer data, missing backup, public DB access, real Telegram token, or unverified runner privileges.

## Known limitations
The SQL scripts are a prototype and do not yet implement an end-to-end outbox worker. Telegram Bot API cannot guarantee exactly-once delivery under ambiguous network failures. Do not merge into a production release or switch the active alerts worker until the full staging gate is verified.

## Additional CI evidence required before Railway testing
- Validate one-job/two-worker and two-job/two-worker claims using independent connections.
- Verify PostgreSQL terminates an uncommitted transaction without leaving a lease.
- Verify a rival worker skips an uncommitted locked row and rollback restores it.
- Verify a stale lease token cannot acknowledge a newer lease or a quarantined review row.
- Verify negative attempts, missing receipt timestamp, missing lease token and duplicate keys are rejected.
- Verify next_attempt_at ordering, future retry exclusion and exhausted-retry ceiling.
- Reapply migration with a preexisting sent receipt and verify it survives unchanged.
- Require zero leftover test rows after all CI scenarios.

## Explicit blockers to production readiness
- No actual outbox worker has been deployed or exercised against Railway staging.
- No proof of real network timeout, 429 handling or mock-transport crash-after-acknowledgement.
- Manual review and reconciliation workflow is not implemented.
- No rollout/rollback evidence for an existing production database.
