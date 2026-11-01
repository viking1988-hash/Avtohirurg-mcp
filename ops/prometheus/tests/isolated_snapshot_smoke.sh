#!/bin/sh
# Disposable, synthetic-only Prometheus snapshot feasibility test.
# NEVER mount a live Railway TSDB volume or attach to an existing service.
set -eu
umask 077
ROOT="$(mktemp -d)"
PROM_PID=""
cleanup() {
  if [ -n "$PROM_PID" ]; then kill "$PROM_PID" 2>/dev/null || true; wait "$PROM_PID" 2>/dev/null || true; fi
  rm -rf "$ROOT"
}
trap cleanup EXIT INT TERM
mkdir -p "$ROOT/tsdb" "$ROOT/snapshots"
cat > "$ROOT/prometheus.yml" <<'YAML'
global:
  scrape_interval: 1s
scrape_configs:
  - job_name: synthetic
    static_configs:
      - targets: ['127.0.0.1:19091']
YAML
"${PROMETHEUS_BIN:-/bin/prometheus}" \
  --config.file="$ROOT/prometheus.yml" \
  --storage.tsdb.path="$ROOT/tsdb" \
  --web.listen-address=127.0.0.1:19091 \
  --web.enable-admin-api \
  --storage.tsdb.min-block-duration=2s \
  --storage.tsdb.max-block-duration=2s \
  --storage.tsdb.retention.time=1h >"$ROOT/prometheus.log" 2>&1 &
PROM_PID="$!"
READY=0
for i in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:19091/-/ready >/dev/null; then READY=1; break; fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  cat "$ROOT/prometheus.log" >&2
  echo "ISOLATED_SNAPSHOT_FAIL: not ready" >&2
  exit 1
fi
sleep 12
curl --silent --show-error --fail --request POST \
  'http://127.0.0.1:19091/api/v1/admin/tsdb/snapshot' >"$ROOT/result.json"
python3 - "$ROOT/result.json" <<'PY'
import json, pathlib, sys
response = json.loads(pathlib.Path(sys.argv[1]).read_text())
if response.get("status") != "success" or not response.get("data", {}).get("name"):
    raise SystemExit("ISOLATED_SNAPSHOT_FAIL: missing successful snapshot response")
print("ISOLATED_SNAPSHOT_API_PASS")
PY
SNAPSHOT_COUNT="$(find "$ROOT/tsdb/snapshots" -mindepth 1 -maxdepth 1 -type d | wc -l)"
if [ "$SNAPSHOT_COUNT" -ne 1 ]; then
  echo "ISOLATED_SNAPSHOT_FAIL: expected one snapshot directory" >&2
  exit 1
fi
echo "ISOLATED_SNAPSHOT_PASS: one synthetic snapshot created on disposable TSDB"

if [ -n "${PROMTOOL_BIN:-}" ]; then
  SNAPSHOT_DIR="$(find "$ROOT/tsdb/snapshots" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  sh ops/prometheus/tests/isolated_receiver_restore_e2e.sh "$SNAPSHOT_DIR" "$ROOT/tsdb/snapshots" "$PROMTOOL_BIN"
fi
