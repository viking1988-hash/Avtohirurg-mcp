#!/bin/sh
# Static safety guardrails; runtime behavior is covered by the container smoke workflow.
set -eu
script="${1:-jarvis-return-worker/monitoring/start-prometheus.sh}"
grep -Fq 'set -eu' "$script"
grep -Fq 'synthetic-sandbox-no-access' "$script"
grep -Fq 'PROMETHEUS_SNAPSHOT_ADMIN_API:-false' "$script"
grep -Fq 'PROMETHEUS_BACKUP_TOKEN' "$script"
grep -Fq 'PROMETHEUS_BACKUP_URL' "$script"
grep -Fq 'prometheus_snapshot_runtime.py' "$script"
grep -Fq 'exec /bin/prometheus "$@"' "$script"
if grep -Eq '^exec /bin/prometheus .*--web.enable-admin-api' "$script"; then
  echo 'FAIL: Admin API enabled unconditionally' >&2
  exit 1
fi
echo 'PASS: snapshot API is opt-in and guarded'
