#!/bin/sh
# Static guardrails for the temporary staging snapshot switch.
set -eu
script="${1:-jarvis-return-worker/monitoring/start-prometheus.sh}"
grep -Fq 'set -eu' "$script"
grep -Fq 'printf '\''%s'\'' "$JARVIS_METRICS_TOKEN" > /tmp/worker-metrics-token' "$script"
grep -Fq 'synthetic-sandbox-no-access' "$script"
grep -Fq 'PROMETHEUS_SNAPSHOT_ADMIN_API:-false' "$script"
grep -Fq -- '--web.enable-admin-api --web.listen-address=127.0.0.1:9090' "$script"
grep -Fq 'exec /bin/prometheus "$@"' "$script"
if grep -Eq '^exec /bin/prometheus .*--web.enable-admin-api' "$script"; then
  echo 'FAIL: admin API enabled unconditionally' >&2
  exit 1
fi
echo 'PASS: staging snapshot launch guardrails'
