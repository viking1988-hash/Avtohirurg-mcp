# Railway Sandbox Alertmanager retry E2E evidence — 2026-10-17

**Result: PASS** for isolated automatic HTTP 503 retry and eventual resolution.

## Scope

Project: `Avtohirurg-Alerts-Sandbox` (`d2cdf2e5-6ca0-46ec-96e8-dcbc11ea53a0`).
Environment: `staging` (`c883ef53-9f3f-4abf-b328-fac5ab9f69d3`).
No Production, main, live CRM or existing monitoring services modified.

Disposable services:
- Sender: `jarvis-retry-e2e-sender-disposable` (`3f862c02-a6c6-407e-82dd-832a6b4e270b`), deployment `fc8a7122-e9b9-4bd3-b10c-4cf521916dc4`, SUCCESS.
- Alertmanager: `jarvis-retry-e2e-alertmanager-disposable` (`573df617-024b-4f9a-99eb-f508baaade01`), deployment `3f1a3ea6-86ae-476f-b8e0-31c61d8bdedf`, SUCCESS.
- Receiver: `jarvis-retry-e2e-receiver-disposable` (`ca0fdaa8-1938-4e53-88b6-71054561af95`), deployment `8165464b-c5f8-42a6-ad2c-25d57dd193d9`, SUCCESS.

## Evidence from Railway deploy logs

Synthetic alert labels: `alertname=JarvisIsolatedRetryE2E`,
`environment=retry-e2e`, `run_id=retry-e2e-20261017`.

1. Sender: `RETRY_E2E_ALERT_SUBMITTED retry-e2e-20261017 200`.
2. Receiver: `SYNTHETIC_ALERT_REJECTED_503 {"attempt": 1}`.
3. Receiver: `SYNTHETIC_ALERT_REJECTED_503 {"attempt": 2}`.
4. Alertmanager: `Notify attempt failed, will retry later`, `err="unexpected status code 503"`.
5. Alertmanager: `Notify success`, `attempts=3`.
6. Receiver: `SYNTHETIC_ALERT_RECEIVED [{"name": "JarvisIsolatedRetryE2E", "status": "firing"}]`.
7. Receiver: `SYNTHETIC_ALERT_RECEIVED [{"name": "JarvisIsolatedRetryE2E", "status": "resolved"}]`.

This proves the **Alertmanager**, rather than the sender, performed automatic retries.
The sender is a one-shot process with restart policy NEVER.

No accepted duplicate firing event appeared in the reviewed receiver logs.
The resolved event arrived after the alert's three-minute end time.

## Cleanup

Disposable services may be deleted after recording this evidence. Their deletion
must be scoped to staging by explicit environment ID. Do not delete any existing
non-disposable service.
