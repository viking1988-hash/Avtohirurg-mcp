# Jarvis Worker private monitoring — evidence and outstanding gates

Environment: Railway project Avtohirurg-Jarvis-Sandbox, staging only.
Repository branch: test/worker-private-monitoring-20261017; no main/production writes.

## Evidence already obtained

- Real Worker scrape: Prometheus API `up{job="jarvis-sandbox-worker"} = 1` (confirmed by operator).
- `/api/v1/rules`: `JarvisWorkerUnavailable` loaded, `health=ok`, `state=inactive`, 30-second evaluation, 2-minute for.
- `/api/v1/alertmanagers`: private Alertmanager present in `activeAlertmanagers`, no dropped managers.
- Private Alertmanager `/-/ready`: `OK`.
- Railway sink log 2026-10-17 14:45:01Z: `JarvisPrivateAlertPipelineE2E` `firing`, POST /alerts HTTP 200.
- Railway sink log 2026-10-17 14:46:01Z: same alert `resolved`, POST /alerts HTTP 200.
- Alert was submitted directly to Alertmanager API, not produced by Prometheus rule evaluation.

## Gate 1: automatic alerting (NOT YET VERIFIED LIVE)

Rule test fixture: `worker-alerts.test.yml`. Run `promtool test rules` in a disposable container or CI. The synthetic input changes up 1 -> 0 -> 1 and verifies delayed firing and recovery. A passing promtool test is **unit-level** evidence, not a live end-to-end proof. For live E2E, use a separate synthetic scrape target/job and a separate alert rule in disposable staging; do not stop the actual Worker or change its target.

## Gate 2: resolved delivery (PASS for direct API injection)

Confirmed from receiver logs above. Live Prometheus-originated resolved event remains unverified.

## Gate 3: Return Queue metrics (DISCOVERY REQUIRED)

Before adding alert expressions, inventory the exact metric names and types from authenticated Worker `/metrics`. Check presence of queue backlog, oldest pending age, retries/failures, and reconciliation-required counters. Define thresholds only after observing baseline, scrape consistency, and expected business behavior. Do not deploy speculative metric names or fabricate values. Prefer staging-only alert rules with explicit `environment=staging` labels and separate evaluation tests.

## Gate 4: restart/replay (NOT YET VERIFIED)

Use disposable Prometheus/Alertmanager replicas with dedicated persistent volumes; confirm retention across an actual container restart, target scrape recovery, and resolved notification receipts. Current services have no verified durable-volume evidence. Restarting production or the real Worker is out of scope. Do not claim restart PASS from a service SUCCESS status.

## Gate 5: GitHub traceability

This file records concrete observations and limitations. Keep the branch unmerged until test evidence and safety review are complete.

## Read-only verification commands in Prometheus shell

```sh
wget -qO- 'http://127.0.0.1:9090/api/v1/rules'
wget -qO- 'http://127.0.0.1:9090/api/v1/alertmanagers'
wget -qO- 'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22jarvis-sandbox-worker%22%7D'
```
