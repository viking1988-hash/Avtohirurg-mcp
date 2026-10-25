# Prometheus Staging backup automation

## What this draft implements

The non-Pro path is a two-service staging workflow:

1. Prometheus creates a consistent TSDB snapshot through its Admin API bound only to loopback (127.0.0.1:9091). Its private service port stays on 9090 behind a proxy. The proxy allows only a bearer-authenticated POST to the exact snapshot endpoint; other Admin API and lifecycle routes are blocked.
2. A controller creates an archive from the completed snapshot, checks for symlinks and available disk space, computes SHA-256, then sends it to the private backup helper.
3. The helper writes to a temporary file, checks the digest, archive paths, block metadata and expected block count, extracts only regular files into a temporary restore directory, and runs promtool tsdb analyze for every block. It publishes the archive atomically only after all checks pass.
4. The helper exposes health and Prometheus metrics. It restores last-success timestamps from last-verified.json after restart. Retention is optional and disabled by default; when enabled it runs only after a verified upload and preserves the newest archive, 7 daily points and 4 weekly points.

The scheduler runs an initial backup after Prometheus is ready, then uses a 24-hour interval by default (minimum one hour). Failed transfers retry every 15 minutes by default (minimum one minute). The temporary local snapshot tree is removed after each transfer attempt so snapshots do not accumulate on the Prometheus volume. Failed runs log a failure event without deleting older backups. The helper reports backup-volume free bytes; staging rules alert below 128 MiB.

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
