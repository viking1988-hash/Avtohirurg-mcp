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

## State transitions and recovery
```text
OPEN -> READY -> APPROVED -> PROCESSING -> DONE
                         |          |  `- saved receipt after restart -> DONE
                         |          `---- uncertain send / no receipt -> RECONCILIATION_REQUIRED
                         `--------------- approval expires before send -> FAILED
PROCESSING -- lease expires before send, valid approval/budget --> APPROVED
RECONCILIATION_REQUIRED -- operator confirms delivered --> DONE
RECONCILIATION_REQUIRED -- operator confirms not delivered --> READY -> fresh APPROVAL required
```

`RECONCILIATION_REQUIRED` is a quarantine state: `claim()`, automatic recovery, and manual
requeue cannot send it. Recovery runs every 15 seconds and uses lease expiry plus stale/missing
heartbeat. Before the durable send marker, a still-valid approval and remaining attempt budget
may return the task to `APPROVED`. After the marker, no saved receipt requires reconciliation;
a durable receipt recovers directly to `DONE` without another send. Recovery never creates approval.

Manual reconciliation is operator-authenticated:
1. Query the provider's test channel using the task correlation/idempotency reference.
2. If delivery is confirmed, submit `outcome=delivered` with its receipt and optional external message ID.
3. If non-delivery is confirmed, submit `outcome=not_delivered`; the old approval is cleared and state becomes `READY`.
4. An operator must inspect the action again and issue a new fingerprint-bound approval before retry.
5. If the provider result remains unknown, leave the task quarantined. Do not retry.

The receipt and optional external message ID are persisted before `DONE`; unique indexes reject
duplicate identifiers. Lease owner plus attempt count form a fencing token. Expired owners cannot
heartbeat, send through the guarded repository, or finish. Fake receipt idempotency is enforced
by PostgreSQL. A real provider's exactly-once guarantee is not assumed.

## Prometheus metrics
Authenticated `/metrics` exposes current queue gauges plus DB-journal-backed counters. The
counters survive worker restarts and have no customer/payload labels.

| Counter | Meaning |
|---|---|
| `reconciliation_required_total` | Deliveries quarantined because the result is uncertain |
| `delivery_receipt_saved_total` | Durable receipts recorded before completion |
| `duplicate_delivery_prevented_total` | Fake idempotency-key collisions that returned the original receipt |
| `approval_expired_total` | Expired approvals rejected during lease recovery |
| `lease_recovery_total` | Expired processing leases recovered |

Queue gauges remain `queue_size`, `oldest_task`, `processing`, `failed`,
`reconciliation_required_queue`, `retry_count`, `recovery_count`, and `approval_count`.

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
