#!/bin/sh
# DISPOSABLE TEST ONLY: Prometheus admin API on loopback, never on service port.
# Do not use as the Railway production/staging service entrypoint.
set -eu
if [ "${PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST:-}" != "true" ]; then
  echo "REFUSED: disposable snapshot test flag is not true" >&2
  exit 2
fi
if [ "${PROMETHEUS_SNAPSHOT_TEST_DATA_DIR:-}" = "" ]; then
  echo "REFUSED: explicit disposable TSDB directory required" >&2
  exit 2
fi
case "$PROMETHEUS_SNAPSHOT_TEST_DATA_DIR" in
  /tmp/prometheus-disposable-*) ;;

  *) echo "REFUSED: TSDB path must start with /tmp/prometheus-disposable-" >&2; exit 2 ;;
esac
case "$PROMETHEUS_SNAPSHOT_TEST_DATA_DIR" in
  *"/../"*|*"/./"*|*"/.."|*"/."|*"/" ) echo "REFUSED: noncanonical TSDB path" >&2; exit 2 ;;
esac
if [ ! -d "$PROMETHEUS_SNAPSHOT_TEST_DATA_DIR" ] || [ -L "$PROMETHEUS_SNAPSHOT_TEST_DATA_DIR" ]; then
  echo "REFUSED: disposable TSDB directory must exist and not be symlinked" >&2
  exit 2
fi
# Test override is allowed only when the executable is a regular file.
PROM_BIN="${PROMETHEUS_DISPOSABLE_TEST_BIN:-/bin/prometheus}"
if [ ! -f "$PROM_BIN" ] || [ -L "$PROM_BIN" ] || [ ! -x "$PROM_BIN" ]; then
  echo "REFUSED: invalid disposable Prometheus executable" >&2
  exit 2
fi
exec "$PROM_BIN" --config.file="${PROMETHEUS_DISPOSABLE_TEST_CONFIG:-/etc/prometheus/prometheus.yml}" \
  --storage.tsdb.path="$PROMETHEUS_SNAPSHOT_TEST_DATA_DIR" \
  --web.listen-address=127.0.0.1:19091 \
  --web.enable-admin-api
