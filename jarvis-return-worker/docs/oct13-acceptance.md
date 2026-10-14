# October 13 stability acceptance

PR #40 is a draft stacked on MCP PR #39. At this verification point, PR #40's starting
head was `8a527296e87260138e9fd299364b38b2f38eb680`; base PR #39 was
`19637a37f038419faefff16b815aa5001ba8d9ce`. The final implementation SHA and CI run
are recorded in the PR description after verification. `main`, production, customer data,
and existing bookings are outside this change.

## CRM approval gate

The outbox adapter stores a canonical SHA-256 fingerprint over event ID, booking ID,
operation, and payload. The authenticated human approval endpoint binds the same fingerprint
to the row's expected version and an expiry no more than 24 hours away. Stale versions,
changed payloads, wrong fingerprints, and expired approvals are rejected. The claim query
only leases pending CRM operations with an active matching approval; dispatch policy
recomputes the fingerprint immediately before provider use. Legacy rows without a stored
fingerprint remain held until explicitly approved. Approval actions are recorded in the CRM
operation event log.

CRM network behavior remains Sandbox-only and unknown provider outcomes are quarantined
for reconciliation; they are never blindly resent. Approval does not claim external
exactly-once delivery.

## Channel tests

Email, Telegram, SMS, and CRM run through synthetic AUTO → APPROVAL → HUMAN contracts
against disposable localhost PostgreSQL and in-memory sinks. The SMS sink only accepts
`sandbox:contract:sms` and `sms-synthetic:fixture`; it has no phone number or network
transport. Telegram HTTP uses a mocked opener. The matrix covers duplicate enqueue,
payload mutation conflict, dual-worker claim, confirmed-rejection retry, lost response,
crash recovery, durable receipt recovery, expired approval, and stale-worker fencing.

These tests verify the internal contracts and provider-specific validation. They do not
authorize or exercise customer messaging, live Telegram, live SMS, or production CRM writes.
Run destructive PostgreSQL acceptance tests only against the disposable CI database, never
against the shared Railway Sandbox database.

## Benchmark and CRM staging

The 100-request benchmark uses synthetic requests and the keyword baseline only. It is not
an LLM comparison or production-readiness result; no unverified model is connected to
production. CRM diagnostic-card and make/fault analytics remain in the separately tracked
CRM staging PR. This MCP change does not modify CRM staging or existing CRM records.

## Rollback

Keep the PR draft and unmerged to roll back the code path. The CRM schema changes are
additive `ADD COLUMN IF NOT EXISTS` statements; rollback requires no destructive data
operation. If this branch is deployed to Sandbox for later review, stop that deployment and
return the worker to the pre-change deployment recorded in the staging log. Do not roll
back PR #39 or touch production as part of this procedure.
