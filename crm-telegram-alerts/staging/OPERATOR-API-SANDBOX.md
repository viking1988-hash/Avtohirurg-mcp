# Telegram Outbox operator API — isolated Sandbox runbook

This is **not** a production deployment procedure. Never merge PR #32 or deploy
the operator API to a public service without an independent security review.

## Preconditions
- Dedicated Railway Sandbox service, not `telegram-outbox-mock-runner`.
- Private disposable PostgreSQL named `outbox_sandbox`, never CRM or production.
- Apply `001_telegram_outbox.sql` then `006_outbox_manual_review.sql`
  **only** after verifying `SELECT current_database()` and taking a snapshot.
- Build with `Dockerfile.outbox-operator`; run `node staging/outbox_operator_api.mjs`.
- Set `OUTBOX_STAGING_MOCK=true`, `OUTBOX_OPERATOR_API_ENABLED=true`,
  `OUTBOX_OPERATOR_TOKEN` (random >=32 bytes) and disposable `DATABASE_URL`.
- No `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, public domain or TCP proxy.
- API binds **127.0.0.1 only**, so it is not accessible from outside its container.
  Use container-local tests only; do not expose it by changing the bind address.

## Verification
1. Validate the image builds and starts only with explicit opt-in.
2. Validate missing/wrong bearer token -> HTTP 401; valid token -> GET /review.
3. Validate invalid receipt, invalid action and duplicate decision -> rejected.
4. Confirm every accepted decision creates an audit row and updates exactly one
   `review` job; confirm rejected decisions change nothing.
5. Confirm no real Telegram transport, no real customer data and no production access.
6. Stop and remove the disposable service after testing; retain CI logs and results.

## Rollback
Stop the dedicated service. Do not roll back shared schemas or delete audit rows.
Do not use this image to replace the existing mock runner.

## Verified Sandbox evidence — 2026-10-13

Scope: `Avtohirurg-Alerts-Sandbox` / `staging` /
`telegram-outbox-operator-api-isolated` only.

- Railway deployment: `1d48ed7b-a73a-4b97-9571-681e97c4fe70`,
  commit `402ed0ffa380581ee53373da9c7c7336747b456d`, status SUCCESS.
- Container log: `Operator API listening on loopback only`.
  No application errors appeared in the available deployment logs.
- Railway SSH into the isolated container; HTTP requests sent to
  `http://127.0.0.1:8787` on loopback, with no public endpoint.
- Actual HTTP results reported from that running container:

  | Scenario | Expected | Observed |
  | --- | --- | --- |
  | GET /review without token | 401 | 401 PASS |
  | GET /review with bearer token | 200 | 200 PASS |
  | Authenticated unknown route | 404 | 404 PASS |
  | Authenticated invalid JSON | 400 | 400 PASS |
  | Authenticated invalid action | 400 | 400 PASS |

- CI workflows for that commit: `38371705318` (PostgreSQL integration),
  `38371705315` (isolated Telegram alerts), `38371705374` (MCP check):
  all completed successfully. CI additionally checks rejected decisions and
  database non-mutation against its disposable `outbox_ci` database.
- The five live HTTP probes did not submit a valid operator decision, create a
  job, or send Telegram messages. Response bodies were not printed.
- SSH session closure after the tests is not by itself evidence of an API crash;
  deployment remained SUCCESS when rechecked.

**Limits:** These observations do not prove load tolerance, full recovery,
complete audit behavior in Railway, or that the database remained unchanged
under every possible request. No production readiness or merge approval is
implied. Keep PR #32 unmerged and the service private until a separate review.

**Next safe gate:** verify runtime availability and database/audit state with
read-only checks; then test any accepted manual decision only against
purpose-built disposable fixtures, with explicit rollback and no real transport.
