# Jarvis Monitoring & Alerting — Sandbox-first contract

Status: initial observability design, **not a production rollout**. Parent: Draft PR #44 at e5034d167c1ec13ddce45b77d3623ce317fff071.

## Objectives
Detect silent delivery failures, stuck queue/outbox work, unsafe retries, degraded worker and PostgreSQL health, and aging manual reconciliation. Do not trigger a retry or resolve reconciliation from an alert.

## Signals and proposed alerts
| Signal | Suggested trigger | Response |
| --- | --- | --- |
| `/ready` and worker heartbeat | readiness fails for 5 min | Check process, database and migrations; do not replay blindly |
| Oldest pending outbox age | > 5 min for 10 min | Inspect subscriber and replay lease; check processed markers |
| Outbox failed/dead-letter | any new permanent failure | Open incident and inspect event_id, subscriber, last_error |
| `RECONCILIATION_REQUIRED` age | > 15 min (warning), > 60 min (critical) | Operator verifies external receipt before any approval or resend |
| Delivery UNKNOWN rate | > 0 for 5 min | Review provider and connectivity; never automatically resend ambiguous writes |
| Expired processing lease | rising rate over 10 min | Investigate worker crashes and fencing |
| PostgreSQL connectivity | repeated errors for 2 min | Check Sandbox database and worker readiness |
| Replay backlog | sustained growth over 15 min | Inspect subscriber failures and processing throughput |

Thresholds are **proposals**, not measured SLOs. Calibrate with Sandbox baseline and workload before enabling paging.

## Required metric semantics
- Gauges: oldest pending outbox age seconds, outbox backlog count, oldest reconciliation age seconds, number awaiting manual review, worker heartbeat age seconds.
- Counters: delivery UNKNOWN, outbox replay failures, reconciliation decisions, lease recovery, provider failures.
- Histograms: replay lag, provider request latency, queue time-to-DONE.
- Cardinality: never label Prometheus metrics with task_id, email, chat_id, URL, token, message text, receipt, or external_message_id. Provider names and bounded status enums are allowed.
- Logs: structured correlation_id and event_id permitted; redact credentials and payloads.

## Operator playbook
1. Confirm Sandbox project/environment and transport mode before taking action.
2. Read current state, approval, send marker, receipt, events, outbox delivery and processed marker.
3. For UNKNOWN or ambiguous external side effects, keep `RECONCILIATION_REQUIRED`; no automatic retry.
4. Reconcile only against authoritative provider evidence. Confirmed non-delivery requires a fresh approval before resending.
5. Record actor, evidence, decision and timestamp in audit.
6. After recovery, verify the same event was not processed twice and receipt persisted across restart.

## Acceptance gates for implementation
1. Implement metrics from existing database state with bounded queries and no high-cardinality labels.
2. Add Prometheus rules and unit tests covering thresholds and counter resets.
3. Add authenticated, read-only operational views; do not expose database or metrics publicly.
4. Exercise alerts using synthetic Sandbox records, including process and PostgreSQL restart.
5. Confirm GitHub CI, Railway deployment ID, /ready and internal /metrics separately; healthcheck alone does not prove metrics.
6. Verify Production and CRM Booking deployment IDs unchanged.
7. Keep PR Draft and all real transports disabled.

## Known risks
Webhook PR #44 currently reports mergeable=false against its moving parent. Do not rebase, merge or resolve stacked PR conflicts automatically. Exactly-once external HTTP effects require receiver-side idempotency and reconciliation support. Live Replay smoke test has not yet been independently demonstrated on Railway Sandbox.
