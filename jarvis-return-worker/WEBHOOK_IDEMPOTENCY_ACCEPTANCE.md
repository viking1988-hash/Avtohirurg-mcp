# Webhook receiver idempotency — P1 acceptance contract (Draft PR #47)

**Status: NOT VERIFIED. Deployment blocked.** This is a receiver-side requirement, not proof that any currently configured endpoint implements it.

## Current sender behavior (verified in source)

- `WebhookAdapter.adapt` creates `jarvis-webhook:<event_id>` as the stable idempotency key; replay inserts one PostgreSQL operation per event ID.
- `WebhookProvider.send` forwards that key to `HTTPClient.request`, which sets the `Idempotency-Key` HTTP header.
- Non-GET requests are not automatically retried inside `HTTPClient` after ambiguous network errors or 5xx responses.
- `Orchestrator.process_webhook_operation` verifies PostgreSQL lease immediately before provider lookup/send, but verification and external HTTP are **not atomic**. An in-flight request cannot be revoked by a later lease conflict.
- Unknown outcomes require reconciliation; a stale worker cannot commit a receipt. This protects local state, **not** remote side effects.

## Required receiver contract before any real send

1. Authenticate the sender and bind the idempotency namespace to that identity; never accept an untrusted caller's key as global authority.
2. For every mutating method (POST, PUT, PATCH, DELETE), require a nonempty `Idempotency-Key`. Associate it with the authenticated sender, HTTP method, route, and canonical request fingerprint. The same key with different content must be rejected without side effects.
3. Atomically claim the key and execute the business mutation, or use a durable transactional inbox/outbox pattern that guarantees one committed business effect. Two concurrent requests with the same key must not both perform the mutation.
4. Persist the final outcome and stable receipt, and return the same logical result on exact replay. If the first execution is still in progress, return an explicit non-success/in-progress response; never start a second mutation.
5. Preserve deduplication records for at least the full replay/reconciliation horizon (including operator-driven recovery). Expiry policy and disaster-recovery behavior must be documented and tested.
6. Provide an authenticated read-only status/receipt lookup keyed by the same operation identity, so an unknown response can be reconciled **without repeating the mutation**. Do not infer delivery from an HTTP 202 alone.

## Mandatory adversarial verification

- Concurrent duplicate requests with identical key and body: **one** committed receiver-side effect.
- Same key with changed method/path/body: rejection, **zero** additional effects.
- Receiver commits then drops connection before responding; subsequent status lookup returns the original receipt, no resend.
- Worker A verifies lease and pauses; lease expires or is recovered; worker B attempts delivery. At most one receiver-side effect, even if both requests reach receiver.
- Receiver restart during in-progress and completed operations; idempotency record and receipt survive.
- Reconciliation of UNKNOWN never triggers a mutating replay without a separately approved, receiver-proven safe path.

## Evidence required to close P1

Record receiver implementation location and version, endpoint identity, test harness and logs, actual request/receipt correlation, deduplication retention policy, and independent human security approval in issue #68. CI on this sender repository alone is insufficient.

**Operational guardrail:** until the receiver contract is proven, use fake-only isolated E2E. Do not enable real Webhook sends, merge PR #47, or deploy to the original Jarvis Sandbox or Production.


## Receiver discovery and decision gate (2026-10-28)

GitHub code search for `Idempotency-Key`, `webhook/status`, and `jarvis-webhook` returned no matches in the available indexed scope; this is **not evidence of absence** because feature branches and unindexed/private code may be omitted. The linked account exposes `Avtohirurg-mcp` and `Avtohirurg-CRM`, but neither is established as the actual receiving endpoint.

Before enabling any real HTTP transport, the operator must identify the configured `JARVIS_WEBHOOK_BASE_URL` **without disclosing credentials**, map its service to the authoritative source repository/deployment, and provide an isolated receiver test endpoint. Do not guess the receiver from repository names, change live configuration, or send a probing mutating request.

**Fail-closed decision:** if receiver identity or deduplication semantics cannot be established, retain fake-only delivery and manual reconciliation for uncertain outcomes. An unverified receiver must not be treated as idempotent merely because the sender supplies an `Idempotency-Key` header.
