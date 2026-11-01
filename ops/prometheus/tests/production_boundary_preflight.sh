#!/bin/sh
# Offline preflight only. Does not start Prometheus or change Railway.
set -eu
script="${1:-jarvis-return-worker/monitoring/start-prometheus.sh}"
dockerfile="${2:-Dockerfile.worker-private-monitoring-sandbox}"
test -f "$script"
test -f "$dockerfile"
if grep -Eq -- '--web.enable-admin-api|PROMETHEUS_SNAPSHOT_ADMIN_API' "$script"; then
  echo "FAIL: existing Prometheus launcher must not enable admin API" >&2
  exit 1
fi
if grep -Eq -- '--web.enable-admin-api' "$dockerfile"; then
  echo "FAIL: existing image must not enable admin API" >&2
  exit 1
fi
if ! grep -Fq 'EXPOSE 9090' "$dockerfile"; then
  echo "FAIL: expected existing monitoring port 9090" >&2
  exit 1
fi
echo "PASS: existing Prometheus launcher unchanged and admin API disabled"
