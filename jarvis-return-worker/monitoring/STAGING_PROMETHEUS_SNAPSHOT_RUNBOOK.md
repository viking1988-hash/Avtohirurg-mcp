# Staging-only Prometheus snapshot runbook (DRAFT — not executed)

## Safety gates
- Target Railway project: `73400534-5ebc-4619-86d9-f036e1028db9`, environment: `96dc8ace-8884-47b1-b529-ee2c317c7904`.
- Prometheus service: `10589df1-2963-4094-9050-7ec11a09006e`, TSDB volume mounted at `/prometheus`.
- Backup helper: `43f16966-9d83-4563-b6c9-b68a339dd840`, separate volume mounted at `/backup`.
- Preserve the existing archive `/backup/prometheus-blocks/blocks-20261019.tar.gz`.
- No production changes, no automatic merge/deploy, no deletion of existing TSDB or backup.
- Verify current Railway live source branch and commit, deployment health, mounted volume, available disk, and rollback image before deploying.
- Admin API can mutate/delete TSDB: never expose it beyond loopback. During the temporary snapshot mode Prometheus will stop serving peers on its private Railway endpoint; obtain a short maintenance window.
- Before deployment, verify that the source branch is not auto-deploying changes unexpectedly. Prepare rollback to the exact prior image/configuration.

## Build and preflight
1. Review PR diff, run `sh -n jarvis-return-worker/monitoring/start-prometheus.sh` and `sh jarvis-return-worker/monitoring/test-start-prometheus-snapshot.sh` on a checkout.
2. Verify default behavior in a throwaway container: no admin API flag, original listen address, and valid synthetic token fallback. Do not use production secrets.
3. Test enabled behavior in a throwaway container: `PROMETHEUS_SNAPSHOT_ADMIN_API=true` adds both `--web.enable-admin-api` and `--web.listen-address=127.0.0.1:9090`.
4. Validate that the same Railway deployment preserves the original TSDB volume and the existing `JARVIS_METRICS_TOKEN` variable name. Do not print the token.

## Snapshot window (manual, staging only)
1. Deploy reviewed candidate to staging only with `PROMETHEUS_SNAPSHOT_ADMIN_API=true` and observe successful startup and local readiness. No other services can reach Prometheus while it listens only on loopback.
2. From an authenticated SSH session inside the Prometheus container, use a **POST**, not GET:
   `wget -qO- --post-data='' 'http://127.0.0.1:9090/api/v1/admin/tsdb/snapshot?skip_head=false'`
   If BusyBox wget does not support this form, use an available HTTP client that can send POST; do not switch to a destructive endpoint.
3. Capture the returned snapshot name, verify `/prometheus/snapshots/<name>` exists, and inspect all blocks. Do not assume the snapshot includes a restorable live WAL: Prometheus snapshot API with `skip_head=false` flushes head data into snapshot blocks.
4. Transfer the immutable snapshot archive to the **separate** Backup Helper volume, using an explicitly verified supported transfer path (Railway service-files download/upload or streamed SSH); do not assume cross-service mounts. Verify checksum and archive listing on the backup volume.
5. Deploy the normal/default configuration without `PROMETHEUS_SNAPSHOT_ADMIN_API=true` immediately after snapshot. Confirm `web.enable-admin-api=false`, normal listen address, local ready, target UP, and peer query probe.

## Restore validation
1. Provision a **disposable isolated staging test service and separate test volume** with no scrape jobs, notifications, external write endpoints, or production credentials.
2. Extract the archived snapshot into an empty TSDB directory (not into the live `/prometheus` volume). Verify block metadata and open it with Prometheus or promtool, then query expected samples.
3. Record archive SHA-256, block count, sample/query checks, snapshot timestamp, restore logs, and result.
4. Remove disposable restore resources only after validation and explicit identification; keep the verified backup.

## Rollback and failure criteria
- On unexpected startup failure, inability to connect, scrape regression, or unplanned exposure: immediately restore the previously verified staging deployment/configuration.
- If snapshot creation, transfer, or restore verification fails, report **INCOMPLETE**, not PASS.
- Never switch Prometheus to non-root before backup and restore are proven; ownership and volume migration require a separate rollback plan.
