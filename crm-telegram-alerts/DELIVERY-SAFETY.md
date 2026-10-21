# CRM Telegram delivery safety

Delivery v2 implementation is present on Draft PR #63. It is not deployed. Tests use a disposable PostgreSQL database and a local loopback mock Telegram server; no real bot token or customer notification is used.

- The logical notification key is unique `(lead_id, alert_type)`; workers use an atomic `FOR UPDATE SKIP LOCKED` claim.
- Confirmed Telegram `ok:true` is persisted as `sent`, including `telegram_message_id`; the legacy claim table is written in the same PostgreSQL transaction.
- Explicit HTTP 429 with a valid bounded `retry_after` is stored as `rejected` with a delayed retry. Permanent 4xx, including 403, remain rejected without automatic retry.
- Timeout, connection failure, malformed response, HTTP 5xx, DB confirmation failure after a successful Telegram response, and expired `sending` lease become or are reconciled to `uncertain`; they are never automatically resent.
- Operator CLI exposes uncertain rows and requires operator identity plus reason for decisions. Resend requires literal `--accept-duplicate-risk YES` and writes an audit row.
- This is **not exactly-once delivery**. Telegram `sendMessage` has no general idempotency key; an operator-authorized resend can duplicate a notification.
- Structured logs exclude token, chat ID, request URL, message text, and raw response/error data.
- Historical `crm_telegram_alert_log` rows remain unchanged. The worker skips them to avoid replay; no historical row is silently labeled confirmed in v2.

## Not yet cleared for Production

- Latest CI must pass on the latest PR HEAD; see PR #63 checks.
- Production schema backup, migration/cutover and rollback rehearsal are not performed.
- Railway Sandbox E2E and operational alerting/cron verification remain pending.
- Do not deploy or run against CRM Production until those gates are reviewed and separately authorized.
