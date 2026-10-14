# Webhook Recovery Guide

Operation state and receipts are durable PostgreSQL records. Pending operations remain claimable after worker restart. Expired leases transition to `RECONCILIATION_REQUIRED`; recovery never assumes a timed-out request had no external effect.

Outbox replay is idempotent through event/subscriber uniqueness. Replaying a materialized event does not create another operation. Completed operations remain completed across PostgreSQL restart.

For UNKNOWN, inspect request ID, provider status, response ID, HTTP status, and receipt. Resolve with evidence and the current optimistic-lock version; stale versions are rejected. Do not resolve as undelivered without provider evidence. A new operation requires an explicit audited action.
