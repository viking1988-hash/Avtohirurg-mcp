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
