# Email Recovery Guide

1. Read the operation with `GET /email/operations/{event_id}` and note its
   `state`, `version`, `attempt_count`, SMTP Message-ID, receipt, and reason.
2. `PENDING` operations are replayed from PostgreSQL after an application or
   Worker restart. Replay uses the durable processed-event marker. A lease that
   expires after a send claim becomes `RECONCILIATION_REQUIRED`; it is not
   returned to `PENDING`.
3. `QUEUED` means the SMTP relay accepted the message. It does not mean delivered.
   Standard SMTP cannot provide a delivery lookup or recall.
4. For `RECONCILIATION_REQUIRED`, inspect the isolated sink's own logs. Then an
   operator may post a versioned decision to
   `POST /email/operations/{event_id}/reconcile`, with `expected_version`, an
   evidence-backed `outcome` (`delivered` or `not_delivered`), and a reason.
   Stale versions are rejected. The `not_delivered` decision marks the operation
   failed; it does not trigger a resend. A new request needs a new event and
   fresh authorization under application policy.
5. `CANCELLED` is available only before the first SMTP attempt. SMTP cannot
   recall a message once accepted.

Do not manually reset an operation row, clear its receipt, or replay a send
attempt after an unknown result. Preserve the database evidence for audit.
