# Prometheus Staging backup automation

## What this draft implements

The non-Pro path is a two-service staging workflow:

1. Prometheus creates a consistent TSDB snapshot through its Admin API bound
   only to loopback (127.0.0.1:9091). Its private service port stays on 9090
   behind a proxy. The proxy allows only a bearer-authenticated POST to the
   exact snapshot endpoint; other Admin API and lifecycle routes are blocked.
2. A controller creates an archive from the completed snapshot, checks for
   symlinks and available disk space, computes SHA-256, then sends it to the
   private backup helper.
3. The helper writes to a temporary file, checks the digest, archive paths,
   block metadata and expected block count, extracts only regular files into a
   temporary restore directory, and runs promtool tsdb analyze for every block.
   It publishes the archive atomically only after all checks pass.
4. The helper exposes health and Prometheus metrics. It restores last-success
   timestamps from last-verified.json after restart. Retention is optional and
   disabled by default; when enabled it runs only after a verified upload and
   preserves the newest archive, 7 daily points and 4 weekly points.

The scheduler runs an initial backup after Prometheus is ready, then uses a
24-hour interval by default (minimum one hour). Failed runs log a failure event
without deleting older backups.

## Required staging wiring

These PRs remain Draft and are not deployed. On HOBBY, the Railway native
volume-backup schedule is unavailable; this draft uses an application-level
pipeline instead.

To activate the staging path after review:

- Build the Prometheus image from Dockerfile.worker-private-monitoring-sandbox
  and the helper image from ops/prometheus/Dockerfile.backup-helper.
- Configure the helper to mount its existing /backup volume and set
  PROMETHEUS_BACKUP_TOKEN to a unique random value of at least 32 characters.
- Set the same token in the Prometheus service, set PROMETHEUS_BACKUP_URL to the
  helper's private upload URL, and set PROMETHEUS_SNAPSHOT_ADMIN_API=true.
  Keep the helper private and do not attach a public domain.
- Before enabling retention, confirm restore-test and storage headroom against
  the expected archive size. Retention is off by default.
- Verify the helper's /healthz and /metrics, a successful backup, the restored
  blocks and scrape target health. Then rehearse rollback by disabling the
  opt-in flag.

Secrets and service configuration are not part of these PRs. No live service,
volume, deployment or production environment has been changed.

## Validation

    python -m unittest discover -s ops/prometheus/tests -v
    python ops/prometheus/validate_snapshot_archive.py /path/to/snapshot.tar.gz --sha256 EXPECTED_SHA256 --blocks 11

CI builds the helper image and runs synthetic receiver, archive and restore
checks. These checks do not prove that the current Railway services are wired
together or that a live staging restore has succeeded. Keep both PRs Draft
until the staging activation and rollback evidence is recorded.
