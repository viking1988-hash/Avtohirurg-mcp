#!/bin/sh
# Runs actual disposable launcher with the real Prometheus binary from CI.
set -eu
test -n "${PROMETHEUS_BIN:-}"
root="/tmp/prometheus-disposable-positive-$$"
pid=""
cleanup() {
  if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
  rm -rf "$root"
}
trap cleanup EXIT INT TERM
mkdir -p "$root"
cat >"$root/prometheus.yml" <<'YAML'
global:
  scrape_interval: 1s
scrape_configs:
  - job_name: synthetic
    static_configs:
      - targets: ['127.0.0.1:19091']
YAML
PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST=true \
PROMETHEUS_SNAPSHOT_TEST_DATA_DIR="$root" \
PROMETHEUS_DISPOSABLE_TEST_BIN="$PROMETHEUS_BIN" \
PROMETHEUS_DISPOSABLE_TEST_CONFIG="$root/prometheus.yml" \
sh ops/prometheus/start-disposable-snapshot-prometheus.sh >"$root/server.log" 2>&1 &
pid="$!"
ready=0
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:19091/-/ready >/dev/null 2>&1; then ready=1; break; fi
  if ! kill -0 "$pid" 2>/dev/null; then break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then cat "$root/server.log" >&2; exit 1; fi
curl -fsS -X POST http://127.0.0.1:19091/api/v1/admin/tsdb/snapshot >"$root/result.json"
python3 - "$root/result.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r.get("status") == "success" and r.get("data", {}).get("name"), r
print("DISPOSABLE_LAUNCHER_REAL_SNAPSHOT_PASS")
PY
