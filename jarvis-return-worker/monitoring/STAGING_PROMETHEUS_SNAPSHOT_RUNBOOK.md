# Staging-only Prometheus snapshot runbook (DRAFT — staging rollout)

## Safety gates
- Target Railway project: `73400534-5ebc-4619-86d9-f036e1028db9`, environment: `96dc8ace-8884-47b1-b529-ee2c317c7904`.
- Prometheus service: `10589df1-2963-4094-9050-7ec11a09006e`, TSDB volume mounted at `/prometheus`.
- Backup helper: `43f16966-9d83-4563-b6c9-b68a339dd840`, separate volume mounted at `/backup`.
- Preserve the existing archive `/backup/prometheus-blocks/blocks-20261019.tar.gz`.
- No production changes and no deletion of existing TSDB or backup.
- Before rollout, verify source commits, deployment health, mounted volumes, available disk and exact rollback deployment.
- The Admin API can mutate/delete TSDB: keep it bound to `127.0.0.1:9091`. The private Prometheus endpoint remains on `:9090` behind the authenticated proxy, so normal peer queries continue.

## Build and preflight
1. CI must pass for both Prometheus controller and receiver PR heads.
2. Confirm default mode still starts Prometheus without `--web.enable-admin-api`.
3. In snapshot mode, confirm Prometheus listens on loopback port 9091 for Admin API and the proxy serves the private endpoint on port 9090.
4. Preserve the existing TSDB and backup volumes and the `JARVIS_METRICS_TOKEN` variable name. Never print the token.

## Automated staging cycle
1. Deploy the pinned Prometheus controller and helper candidates only in staging. Keep the helper private with no public domain.
2. Set the same unique bearer token (at least 32 characters) on both services. Set `PROMETHEUS_BACKUP_URL` to the helper's private upload endpoint and enable `PROMETHEUS_SNAPSHOT_ADMIN_API=true`.
3. Wait for Prometheus readiness. The controller creates an initial snapshot, archives it, calculates SHA-256, and uploads it to the helper. It retries failures after 15 minutes and otherwise runs every 24 hours.
4. Confirm sanitized `prometheus_backup_verified` logs, HTTP 200 from helper `/healthz`, a positive last-success timestamp on `/metrics`, a saved archive on the separate backup volume, and the matching block count/checksum in the receiver response.
5. Confirm Prometheus remains ready on its private proxy and the helper scrape target becomes UP. Review free-space metrics before enabling optional retention; leave retention disabled for the first run.
6. Confirm a helper restart reloads the last verified timestamp from the backup volume. Do not delete existing archives.

The receiver restores the archive into a temporary directory, validates its digest and block metadata, and runs `promtool tsdb analyze` on every block before atomically publishing it. This is the automated restore validation. CI uses synthetic archives; it does not substitute for a real staging archive check.

## Rollback
- On failed startup, upload, verification, scrape regression or unexpected exposure, disable `PROMETHEUS_SNAPSHOT_ADMIN_API` and roll back both services to their recorded prior deployments/configuration.
- Keep the verified archive and both volumes. Do not delete or reformat either volume.
- If any backup/restore check fails, report **INCOMPLETE**, not PASS.
- Keep helper private. Do not change the Prometheus runtime user or volume ownership in this rollout.
