# Deployment status: NOT READY (verified 2026-10-26)

**Do not activate the wiring below on the existing Railway Prometheus service.**
The checked-in `jarvis-return-worker/monitoring/start-prometheus.sh` still launches
Prometheus without a snapshot controller or admin API, and
`Dockerfile.worker-private-monitoring-sandbox` has no snapshot proxy/controller.
The private backup receiver exists, but no verified snapshot source has been
integrated in this branch. Enabling `PROMETHEUS_SNAPSHOT_ADMIN_API=true` by
itself does not implement a snapshot mechanism. Do not enable an unauthenticated
admin API on the private network listener.

GitHub Actions has passed the disposable synthetic snapshot -> archive -> real HTTP receiver -> extracted TSDB -> real promtool E2E (three blocks). This is **not** a live Railway backup or recovery. Keep PR Draft and do not deploy.

---

## Verified implementation status (2026-10-26)

- GitHub Actions at commit `4643eb7`: both workflows passed.
- Disposable Prometheus snapshot -> `backup_once.py` -> authenticated receiver -> restored blocks -> real `promtool` is covered by CI.
- `backup_cycle.py` is a **single invocation only**, disabled unless `PROMETHEUS_BACKUP_SCHEDULER_ENABLED=true`. It uses a nonblocking file lock, does not retry automatically, does not create snapshots, and does not delete local archives.
- `snapshot_controller.py` remains a loopback-only prototype, **not** integrated into the Railway Prometheus image or startup command.
- **Not verified:** live Railway TSDB snapshot, private authenticated transfer between Railway services, real restore from the live TSDB, scheduled invocation, retention policy, or safe admin-API isolation in deployed topology.
- Do **not** configure Railway cron, expose the Prometheus Admin API, change existing service branches, or deploy this draft until those items have independent acceptance evidence.

# Prometheus Staging backup automation

## What this draft implements

The non-Pro path is a two-service staging workflow:

1. Prometheus creates a consistent TSDB snapshot through its Admin API bound only to loopback (127.0.0.1:9091). Its private service port stays on 9090 behind a proxy. The proxy allows only a bearer-authenticated POST to the exact snapshot endpoint; other Admin API and lifecycle routes are blocked.
2. A controller creates an archive from the completed snapshot, checks for symlinks and available disk space, computes SHA-256, then sends it to the private backup helper.
3. The helper writes to a temporary file, checks the digest, archive paths, block metadata and expected block count, extracts only regular files into a temporary restore directory, and runs promtool tsdb analyze for every block. It publishes the archive atomically only after all checks pass.
4. The helper exposes health and Prometheus metrics. It restores last-success timestamps from last-verified.json after restart. Retention is optional and disabled by default; when enabled it runs only after a verified upload and preserves the newest archive, 7 daily points and 4 weekly points.

The scheduler runs an initial backup after Prometheus is ready, then uses a 24-hour interval by default (minimum one hour). Failed transfers retry every 15 minutes by default (minimum one minute). The temporary local snapshot tree is removed after each transfer attempt so snapshots do not accumulate on the Prometheus volume. Failed runs log a failure event without deleting older backups. The helper reports backup-volume free bytes; staging rules alert below 128 MiB.

## Read-only Railway inventory (2026-10-26)

- Staging Prometheus service `10589df1-2963-4094-9050-7ec11a09006e` currently tracks `chore/staging-prometheus-optional-snapshot-20261023` and mounts its own `/prometheus` TSDB volume.
- Backup helper service `43f16966-9d83-4563-b6c9-b68a339dd840` tracks `test/prometheus-backup-automation-safe-20261024` and mounts a **different** `/backup` volume.
- Both latest deployments reported SUCCESS; neither service has a Railway cron schedule, and no Railway changes were staged.
- The helper has no direct access to `/prometheus`; this is intentional. The current Prometheus startup does not provide the authenticated snapshot controller, nor the upload scheduler. **Do not switch its source branch or enable the Admin API as a shortcut.**
- Next gate: implement and test an opt-in loopback-only snapshot controller, packaging/upload scheduler and clean rollback in a disposable service first. Only then propose a staging deployment, with explicit verification of mounted volume, private networking, token handling and disk usage.

## Required staging wiring

These PRs remain Draft. On HOBBY, the Railway native volume-backup schedule is unavailable; this draft uses an application-level pipeline instead.

- Prometheus: build with Dockerfile builder using Dockerfile.worker-private-monitoring-sandbox.
- Helper: build with Dockerfile builder using ops/prometheus/Dockerfile.backup-helper; run python3 /opt/prometheus-backup/backup_receiver.py and configure /healthz as the healthcheck.
- Keep the existing helper volume mounted at /backup and keep the helper private with no public domain.
- Set the same unique PROMETHEUS_BACKUP_TOKEN (at least 32 characters) on both services. Set PROMETHEUS_BACKUP_URL on Prometheus to the helper's private upload URL and set PROMETHEUS_SNAPSHOT_ADMIN_API=true.
- Leave PROMETHEUS_BACKUP_RETENTION_ENABLED=false for the first run. Review free space and restore-test evidence before enabling retention.
- Verify /healthz, /metrics, a successful backup, the restored blocks and scrape target health. Then rehearse rollback by disabling the opt-in flag and restoring the prior deployment.

Secrets and live service configuration are not part of these PRs. No Production environment is changed.

## Validation

    python -m unittest discover -s ops/prometheus/tests -v
    python ops/prometheus/validate_snapshot_archive.py /path/to/snapshot.tar.gz --sha256 EXPECTED_SHA256 --blocks 11

CI builds the helper image and runs synthetic receiver, archive and restore checks. These checks do not prove that the current Railway services are wired together or that a live staging restore has succeeded. Keep both PRs Draft until the staging activation and rollback evidence is recorded.
