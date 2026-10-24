# Prometheus Staging backup automation — gated rollout

## Verified baseline (2026-10-24)

Manual snapshot `20261023T224352Z-5414e93b16d99cb9` was copied to
`/backup/prometheus-snapshots/` on the separate backup helper volume.
SHA-256: `f96f4ae41b381b41f476547026889ae943be56ae5d0a7ce39cbd5688280456b5`.
Restored into a temporary directory; all 11 TSDB blocks passed
`promtool tsdb analyze <restored-db-path> <block-id>`.
The live Prometheus admin API was then disabled and Staging rolled back.
This evidence covers a manual recovery exercise, **not** scheduled backup.

## Current PR

- `validate_snapshot_archive.py`: offline, read-only SHA-256, safe tar member
  types/paths, expected block count, meta.json ULID and index/chunks checks.
- Offline unit tests and isolated GitHub Actions job.
- No deployment, scheduling, rotation or administrative API changes.

## Required before enabling unattended backup

1. **Snapshot consistency:** use a supported Prometheus snapshot operation.
   The admin API is currently disabled. Do not simply tar the live TSDB and
   do not permanently expose the admin API. Design an authenticated,
   least-privilege, localhost-only snapshot controller with explicit time-bound
   access and independent verification that the admin API is disabled outside
   the maintenance window. Alternative: validated offline snapshot from a
   stopped *replica*, not the live process.
2. **Transfer:** move completed snapshots from the Prometheus instance to
   the separate backup helper volume using a restricted transfer channel;
   write to `.partial`, verify SHA-256, fsync as applicable, then atomically
   rename. Never share/move the live TSDB volume to the helper.
3. **Scheduling:** run a dedicated staging-only job with no production
   credentials, a distributed lock and failure timeout. An unsuccessful job
   must never print PASS or trigger retention deletion.
4. **Retention:** keep at least 7 daily and 4 weekly snapshots as an initial
   proposal, adjustable for actual volume capacity. Delete only backups
   older than the policy after the new archive has passed verification.
   Always preserve the latest verified restore candidate.
5. **Recovery:** periodically unpack to a *new* isolated directory, validate
   every block with `promtool tsdb analyze <db> <block-id>`, and run a
   temporary Prometheus against that copy for query-level confidence.
6. **Alerting:** report last verified backup age, duration, errors, volume
   free space and last restore-test result. Alert on missed backups and
   verification failures. No secret values in logs.
7. **Release gates:** offline unit tests, negative tests, a staged dry-run,
   staging backup+restore, explicit rollback, and monitoring verification.
   Keep PR Draft until evidence is attached.

## Offline validation

```sh
python -m unittest discover -s ops/prometheus/tests -v
python ops/prometheus/validate_snapshot_archive.py /path/to/snapshot.tar.gz \
  --sha256 EXPECTED_SHA256 --blocks 11
```

This validator does **not** establish consistency or full TSDB readability:
the `promtool` and isolated restore tests are separate mandatory gates.
