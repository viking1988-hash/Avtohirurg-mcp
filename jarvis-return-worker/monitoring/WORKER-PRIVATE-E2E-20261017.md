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

## Metrics inventory confirmed from live Prometheus (2026-10-17)

The operator ran `/api/v1/label/__name__/values` and confirmed these metric names are present: `outbox_pending_total`, `outbox_failed_total`, `outbox_replayed_total`, `queue_size`, `replay_queue_size`, `oldest_task`, `reconciliation_required_queue`, `reconciliation_required_total`, `retry_count`, `replay_latency_seconds_bucket`, `replay_latency_seconds_count`, `replay_latency_seconds_sum`, `up`. Names alone do not establish current values, metric types, units, or thresholds. Follow up with instant-vector queries and inspect HELP/TYPE at authenticated Worker /metrics before setting alert thresholds. 

## Live baseline (Prometheus instant-vector, 2026-10-17)

All from job `jarvis-sandbox-worker`: `queue_size=0`, `oldest_task=0`, `reconciliation_required_queue=0`, `outbox_pending_total=0`, `replay_queue_size=0`. These are a point-in-time observation only, not proof of continuous health or queue draining. `oldest_task` unit/type still unknown.

## Additional observed counters (2026-10-17)

Prometheus instant-vector from `jarvis-sandbox-worker`: `retry_count=1`, `outbox_replayed_total=16`, `outbox_failed_total=0`, `reconciliation_required_total=0`. These are observed absolute samples, not verified rate/counter semantics; do not alert on historical nonzero values alone. Verify metric HELP/TYPE before using `increase()`.

## Operator-verified promtool tests (2026-10-18)

- `promtool --version`: Prometheus promtool 3.5.0.
- `promtool check rules /etc/prometheus/worker-alerts.yml`: `SUCCESS: 1 rules found`.
- `promtool test rules /tmp/jarvis-worker-alert.test.yml`: `SUCCESS`. Test simulates Worker `up` 1 -> 0 -> 1 and checks firing after 2-minute hold and resolution after recovery.
- This is rule-level simulation only; Prometheus-originated live Alertmanager delivery remains unverified.

- `promtool test rules /tmp/jarvis-queue-alerts.test.yml`: `SUCCESS` (operator output). Synthetic `queue_size` 0 -> 1 -> 0; alert pending at 15m, firing at 16m, resolved at 17m. This validates the 15m backlog rule at unit-test level, not a live Railway incident.

## Real isolated Prometheus redeployment (2026-10-18)

- Railway service `jarvis-worker-private-prometheus-e2e`, staging only, deployment `58e79386-5e1d-4963-a519-aa36e4ae8ba3`: `SUCCESS`.
- Deployment log 10:20:57Z: `TSDB started`, `Completed loading of configuration file`, `Server is ready to receive web requests.`.
- TSDB replayed WAL segment 0, but **no attached persistent Railway volume** is configured; WAL replay is NOT proof of pre-restart history retention. Previous samples may be lost. No post-restart Prometheus API query yet; scrape and alertmanager connection must be rechecked.
- Alertmanager and synthetic sink latest deployments also `SUCCESS`, neither has an attached volume.

- Operator verified after redeploy: Prometheus API `up{job="jarvis-sandbox-worker"}` returned `1` (sample timestamp 1792319296.852). Worker scrape recovery PASS; alert rule and Alertmanager discovery post-redeploy still need independent verification.
