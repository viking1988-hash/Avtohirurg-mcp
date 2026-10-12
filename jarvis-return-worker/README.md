# Jarvis Return Worker — Sandbox only

Continues accepted queue contract at a2f0f3c96eb65a81c521c57e799eb0d96523a3f9.
Does not import server.py, touch CRM, production or n8n. Only Fake Transport is implemented.
Real transport interface slots raise on construction; no message SDK is installed.

## Runtime
Railway project Avtohirurg-Jarvis-Sandbox, environment staging. Exact project/environment
IDs are configured privately via JARVIS_SANDBOX_PROJECT_ID and JARVIS_SANDBOX_ENVIRONMENT_ID.
Identity is checked at startup.
Private DB jarvis-sandbox-postgres.railway.internal / jarvis_return_queue_test / jarvis_sandbox.
Both DSN and server identity must match. No DSN query/fragment overrides accepted.
PORT=8080. Root directory /jarvis-return-worker. Dockerfile in this directory.
The additive migration runs at startup under advisory lock; existing prototype rows
without payload/correlation are retained, excluded from claims, never reconstructed.

## API and approval
GET /health and /ready expose only status; other endpoints require Bearer authentication.
JARVIS_READ_TOKEN grants queue/metrics reads and preparation via POST /enqueue.
JARVIS_APPROVAL_TOKEN is a separate operator credential for /approve, /cancel, /requeue, /reconcile.
The worker does not possess a code path that automatically approves tasks.
Human actor is server-configured JARVIS_OPERATOR_ID, never taken from caller JSON.
Approval binds SHA256 of canonical task+destination+payload and expires in <=24 hours.
API JSON: enqueue {task_id,destination,payload,correlation_id};
approve {task_id,fingerprint,expires_at}; cancel/requeue {task_id};
reconcile {task_id,outcome,receipt_id,message_id}.
Do not automate the human credential in n8n or client integrations.
Docs and OpenAPI are disabled; access logs disabled to avoid logging tokens/identifiers.

## Loop and recovery
FOR UPDATE SKIP LOCKED claim -> approval check -> heartbeat -> durable send marker -> transport -> durable receipt -> DONE.
Lease owner and attempt_count form a fencing token. An expired owner cannot heartbeat,
deliver or finish. Every change and duplicate preparation is audited in the same transaction.
Fake receipts are PostgreSQL-persisted and deduplicated by task+fingerprint. Provider receipt
and message IDs are stored on the task before DONE. No assertion of exactly-once real provider delivery.
Recovery runs every 15s and requires an expired lease and absent/stale heartbeat. Before the
send marker, a still-valid approval and remaining attempt budget may return the task to APPROVED.
After the marker, no saved receipt means RECONCILIATION_REQUIRED; a saved receipt means DONE.
The reconciliation endpoint is operator-only: confirmed delivery records identifiers and closes
DONE; confirmed non-delivery clears the old approval and returns READY, requiring fresh approval.
Unknown results remain non-sendable. Recovery never recreates an approval.
Cancel of PROCESSING is rejected: receipt reconciliation is required before cancellation.
Uncertain delivery is excluded from claims and manual requeue; only explicit operator reconciliation can resolve it.

## Metrics
Authenticated /metrics: queue_size, oldest_task (seconds), processing, failed, retry_count,
recovery_count, approval_count. Values derive from PostgreSQL and survive process restarts.
Prometheus gauges are used for persisted snapshot totals. No client payload metric labels.

## Shutdown and rollback
SIGTERM stops claims/recovery and waits up to JARVIS_SHUTDOWN_SECONDS (20 default).
Railway drainingSeconds=40. An interrupted attempt stays fenced until recovery.
Rollback: stop/redeploy only worker to prior commit. Preserve database/volume and audit.
Migration is additive; do not run DROP. Old prototype APIs are not changed.

## New verification
pytest jarvis-return-worker/tests. PostgreSQL integration requires an ephemeral CI database
via JARVIS_WORKER_CI_DATABASE_URL. This suite verifies new runtime behavior and does not
rerun the previous 29 acceptance tests. No real customer destination is permitted.

## Sandbox interruption verification
JARVIS_FAKE_DELAY_SECONDS (0 default, maximum 180) delays only Fake delivery.
Use temporarily for an operator-controlled worker interruption while PROCESSING;
restore to 0 after verification. Repository checks ownership again after delay.
No forced-exit API or real transport is exposed.
