# Telegram Delivery v2 — draft implementation and release gates

**Status:** implemented in Draft PR #63; schema and worker are exercised only in GitHub Actions disposable PostgreSQL with a local mock Telegram server. Not deployed to Railway or Production. No historical rows are reclassified.

## Safety invariants

- Logical notification key is unique `(lead_id, alert_type)`.
- Only Telegram `ok:true` is treated as a confirmed send. `message_id` is stored when confirmed.
- Network exception, malformed response, HTTP 5xx, or failure to persist a confirmed response never triggers an automatic retry. A stale `sending` lease is moved to `uncertain`.
- Explicit HTTP/API 4xx rejections are `rejected`; HTTP 403 is permanent. HTTP 429 is retried only with a valid bounded `retry_after` and while under the attempt limit.
- This is **not exactly-once** delivery. Telegram `sendMessage` provides no general caller-supplied idempotency key. Operator-authorized replay carries explicit duplicate risk.
- Logs contain lead ID, alert type, attempt and a safe error class. Bot token, chat ID, request URL, message text and response body are not logged.

## State transitions

| From | Event | To | Action |
|---|---|---|---|
| pending | worker claims atomically | sending | increment attempt, record timestamps |
| sending | Telegram `ok:true` and DB transaction commits | sent | persist message ID and legacy success log |
| sending | 429 with valid `retry_after` | rejected | schedule bounded retry |
| sending | permanent 4xx, including 403 | rejected | no automatic retry |
| sending | timeout, malformed result, 5xx, or uncommitted success | uncertain | hold from automatic processing |
| sending | expired lease after worker/process failure | uncertain | hold from automatic processing |
| uncertain | operator confirms delivered | sent | audit operator, reason and decision |
| uncertain | operator authorizes resend with explicit acknowledgement | pending | audit duplicate-risk acceptance; next claim is allowed |

## Schema and historical compatibility

Migration `migrations/001_delivery_v2.sql` is additive and creates v2 delivery and operator-audit tables. The existing `crm_telegram_alert_log` remains intact. The worker avoids enqueueing notifications already present in that legacy table, so historical claims block duplicate sends without being silently marked as confirmed in v2. No bulk backfill, deletion, or reinterpretation is performed.

Rollback is to stop/disable the v2 worker and preserve both tables and audit history. Do not drop or truncate either table. Production migration, dual-worker cutover, backup, and rollback rehearsal are separate release gates and have not been performed.

## Operator CLI

`operator.mjs list-uncertain` lists unresolved records. `confirm-delivered` requires lead ID, alert type, operator identity and reason. `authorize-resend` additionally requires `--accept-duplicate-risk YES`. This is a direct internal CLI using the database connection; no public management API is exposed.

## Verification

The CI workflow applies the migration to an ephemeral PostgreSQL service and runs the worker against synthetic leads plus a mock HTTP server on loopback. No Telegram credentials or real sends are used. Coverage includes successful delivery/message ID, simultaneous workers, retry_after scheduling, ambiguous timeout, operator resend audit, stale lease recovery, and database confirmation failure. The latest CI result must be read from PR #63 for the tested commit SHA.

Railway Sandbox execution and Production operational monitoring remain separate release gates. Do not deploy this draft until the E2E is green, migration compatibility and rollback are reviewed, and production cutover is separately authorized.
