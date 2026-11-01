# Isolated snapshot source — acceptance criteria

CI has proved a **disposable** Prometheus can create a TSDB snapshot when
its admin API is bound to `127.0.0.1`. This does not imply the Railway service
can safely run with its admin API enabled.

## Required deployment design

1. Preserve the existing private Prometheus scrape/query listener on port 9090,
   but never expose the Admin API on that listener. Prometheus exposes admin
   operations on its own listener when enabled; a separate proxy or network
   namespace must enforce endpoint restrictions. Binding the entire Prometheus
   process to loopback requires a reverse proxy for scraping/querying.
2. Require a strong backup-only token and exact-method/path filtering on the
   internal snapshot controller. Reject all other admin and lifecycle endpoints.
   No externally reachable admin API, no secrets in logs.
3. Keep the snapshot controller in the same isolated container or security
   boundary as Prometheus. Do not mount the live TSDB into the backup helper.
4. Enforce one snapshot at a time, snapshot timeout, bounded archive size and
   free-space checks. Delete only completed temporary snapshots after transfer.
5. Send only completed, checksummed archives over the private network to the
   authenticated backup receiver. A failed upload must preserve older archives.
6. Run an independent restore from the saved archive, including per-block
   promtool checks and a query-level smoke test.
7. Confirm readiness, existing scrape targets and alerts after deployment;
   verify rollback to the old start command and image.

## Blocker

The current `start-prometheus.sh` and Dockerfile do not implement a proxy or
snapshot controller. **Do not set `PROMETHEUS_SNAPSHOT_ADMIN_API=true` on the
existing Railway service.** The current CI smoke test exercises only a
disposable, synthetic Prometheus instance.

## Proof required before merging

- Negative tests: GET snapshot rejected; other admin paths rejected; bad
  token rejected; external direct access to admin port rejected.
- Isolated integration: snapshot -> archive -> authenticated receiver ->
  separate restore -> promtool -> query-level verification.
- Staging rollout and rollback evidence attached to PR.
