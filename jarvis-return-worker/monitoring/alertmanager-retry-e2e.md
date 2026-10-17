# Isolated Alertmanager HTTP 503 retry E2E runbook

**Status: NOT VERIFIED until real Alertmanager delivery logs are captured.**

## Scope and safety

Only a disposable Railway Sandbox environment or separately isolated test services.
Do not change the existing staging Alertmanager, Prometheus, fixture or probe,
Production, GitHub main, CRM, or any real customer-facing endpoints.
Use a dedicated internal-only receiver and dedicated Alertmanager; never point
this experiment at an existing receiver. Record all service IDs and configuration
before the run, and delete only disposable services created for this experiment.

## Prerequisites

1. Verify the GitHub commit SHA and all three CI workflows pass.
2. Provision an isolated receiver from `Dockerfile.monitoring-probe` with
   `SYNTHETIC_REJECT_FIRST=2`, plus an isolated Alertmanager using a
   separate config that points **only** to that receiver's internal hostname.
3. Configure a dedicated synthetic alert source to send one unique alert
   to the isolated Alertmanager; avoid sharing the existing Prometheus.
4. Confirm internal networking and that the receiver logs each attempt
   without printing secrets or real event data.

## Procedure

1. Start log capture on the receiver and Alertmanager before firing.
2. Fire one synthetic alert with a unique run ID and stable labels.
3. Confirm the receiver logs exactly two
   `SYNTHETIC_ALERT_REJECTED_503` attempts.
4. Confirm a later `SYNTHETIC_ALERT_RECEIVED` for the **same alert**
   without manually resending it. This is the essential proof of
   Alertmanager automatic retry.
5. Resolve the synthetic alert; verify a `resolved` notification.
6. Confirm no unexpected duplicate accepted delivery and record
   Alertmanager retry errors, timestamps, alert fingerprint and deployment IDs.
7. Restore or delete disposable resources, verify existing staging services
   and GitHub PR state remain unchanged.

## Acceptance criteria

- Two HTTP 503 rejections followed by a 2xx acceptance of the same alert,
  **without a second client-side send**.
- Alertmanager retry evidence in its own logs.
- `resolved` notification received after clearing the alert.
- No real external notification, no customer data, no production mutation.
- All disposable resources cleaned up.

A local `503 -> 503 -> 202` test that explicitly sends three requests
is **not** sufficient evidence of Alertmanager retry behavior.
