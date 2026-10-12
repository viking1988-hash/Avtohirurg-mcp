# Railway Sandbox integration gate — Telegram Outbox

Status: **isolated Railway mock PASS** on commit `bcb330ff` (deployment `435e0da9-e693-4492-a8bb-0b344d150360`, 2026-10-12). Latest GitHub CI safety tests: **3/3 PASS** at `d14cd89c`. This is not a real Telegram transport test.

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
- An isolated rollback-only mock worker ran successfully in Railway staging, but **no real outbox delivery worker** has been deployed or exercised.
- No proof of real network timeout, 429 handling or mock-transport crash-after-acknowledgement.
- Manual review and reconciliation workflow is not implemented.
- No rollout/rollback evidence for an existing production database.

## Verified evidence (2026-10-12)
- Railway project `Avtohirurg-Alerts-Sandbox`, environment `staging`: disposable PostgreSQL, isolated test service and `telegram-outbox-mock-runner` each reported `SUCCESS`.
- Pinned mock runner `bcb330ff`: deployment log recorded `PASS: isolated staging schema applied` and `PASS: staging mock claim, receipt, fencing and rollback`.
- Mock runner rejects Telegram credentials, requires `OUTBOX_STAGING_MOCK=true`, checks database name against `outbox_ci` and `outbox_sandbox`, and intentionally rolls back the fake job transaction.
- GitHub Actions `Avtohirurg MCP check`, `CRM Telegram alerts isolated tests`, `Telegram Outbox PostgreSQL integration` passed on `d14cd89c`; integration CI also tests missing safety flag and injected Telegram token.
- The schema persists in the **disposable** Railway database. The mock transaction rolls back its test row; an independent read-only Railway row-count verification has **not** yet been performed.
- No live Telegram delivery, production deployment, production database migration, or PR merge was performed. PR #32 remains draft.

## Simulated transport failure policy (CI PASS at `5ce028c`, 2026-10-12)
- `outbox_transport_policy.mjs` is a **pure classification module**; it does not send Telegram requests and is not wired into the deployed mock worker or production.
- CI `node --test` covers HTTP 429 (bounded retry-after), HTTP 500/503, timeout, connection reset, crash after acknowledgement, malformed accepted receipt and HTTP 400.
- Confirmed successful receipt maps to `sent`; explicit HTTP 429 maps to bounded `retryable_error`; uncertain 5xx/network/crash outcomes map to manual `review` to avoid duplicate delivery.
- All three PR workflows passed on `5ce028cca33d1190b0319fdc5540a89b6d801e7b`.
- **Not yet verified:** a real staging delivery worker applying this policy to PostgreSQL transitions, process-kill fault injection, read-only Railway row-count query, and manual review/reconciliation interface. No real Telegram sends.

## Database-backed simulated transport integration (CI PASS at `413cc32`, 2026-10-12)
- `outbox_transport_db_test.mjs` executes six mocked delivery outcomes against disposable PostgreSQL 16, validating actual `sent`, `retryable_error` and `review` transitions, receipt fields, bounded retry delay, lease cleanup and non-claimability of uncertain outcomes.
- Every case deliberately throws an expected rollback and asserts no fake lead rows persist.
- All three workflows passed on commit `413cc32095d8a8cb421466c699ad2aeadfb302a2`.
- This is CI database-backed simulation, **not** a deployed delivery worker, real Telegram HTTP call, or proof of Railway row counts.
