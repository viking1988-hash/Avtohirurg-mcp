# Telegram delivery state machine — implementation design (Issue #62)

**Status:** design only. Not deployed. No schema changes have been executed.

## Invariants

- A unique logical notification is `(lead_id, alert_type)`.
- A Telegram HTTP success response with `ok:true` is the only confirmed sent outcome.
- A timeout, connection reset, malformed response or HTTP 5xx is **uncertain**, never automatically retried without an explicit duplicate-risk decision.
- An explicit API rejection is **rejected**, eligible for bounded retry only after classification (e.g. 429 with `retry_after`, 403 permanent).
- A process crash during `sending` is uncertain; automatic reclaim must not assume non-delivery.
- Neither a claim row nor a successful DB commit proves Telegram delivery. Telegram Bot API offers no general caller-supplied idempotency key for `sendMessage`.

## Proposed schema (migration plan, NOT executed)

Add fields to a new or carefully migrated delivery table:

```sql
CREATE TABLE crm_telegram_delivery_v2 (
  lead_id bigint NOT NULL,
  alert_type text NOT NULL,
  state text NOT NULL CHECK (state IN ('pending','sending','sent','rejected','uncertain')),
  attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  claimed_at timestamptz,
  last_attempt_at timestamptz,
  next_attempt_at timestamptz,
  sent_at timestamptz,
  telegram_message_id bigint,
  last_error_class text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (lead_id, alert_type)
);
```

## Transitions

| From | Event | To | Action |
|---|---|---|---|
| pending | atomic worker claim | sending | Increment attempt count and timestamp |
| sending | confirmed Telegram success | sent | Persist message ID |
| sending | definite transient rejection | rejected | Schedule bounded retry after backoff |
| rejected | retry due, attempts below limit | sending | Atomically reclaim |
| sending | ambiguous network/API outcome | uncertain | Alert operator; no auto-retry |
| sending | worker crash / expired lease | uncertain | Alert operator; no auto-retry |
| uncertain | human verifies receipt | sent | Record evidence and operator |
| uncertain | human explicitly authorizes resend | pending | Record duplicate risk acceptance |
| rejected | permanent rejection / exhausted retries | rejected | Alert operator |

## Crash consistency

Claim the DB row atomically before calling Telegram, but **never** assume that a stale `sending` claim is safe to resend. A sweeper can mark it `uncertain` after a conservative timeout; it must not send messages. An operator queue must show `uncertain` and exhausted `rejected` rows.

## Migration and rollback

1. Back up schema and data; test on disposable PostgreSQL with synthetic leads.
2. Backfill historical `crm_telegram_alert_log` claims as `sent_unverified` in a separate reconciliation report (not `sent` unless verified); do not silently reinterpret old claims.
3. Deploy a dual-read/explicit feature-flag approach only after tests and operator workflow are ready. Avoid two concurrent sending workers.
4. Rollback must disable the new sender first, preserve delivery history, and **never** bulk-delete claims.

## Required isolated tests

- Fresh/overdue and deduplication; simultaneous workers; explicit 429/403; 5xx; timeout after acceptance; process crash before and after send; failed DB confirmation after Telegram success; lease expiry; operator reconciliation; restart and rollback.
- Assert **zero external Telegram calls** using a strict mock and disposable PostgreSQL.
- Require evidence of Railway cron exit codes and alerts before any Production release.
