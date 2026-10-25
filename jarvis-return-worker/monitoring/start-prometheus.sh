#!/bin/sh
set -eu
# The isolated synthetic-only Sandbox must not require production Worker credentials.
# The configured Worker scrape job will stay DOWN until a dedicated test token is provided.
if [ -n "${JARVIS_METRICS_TOKEN:-}" ]; then
  umask 077
  printf '%s' "$JARVIS_METRICS_TOKEN" > /tmp/worker-metrics-token
else
  # Prometheus validates credentials_file during startup. Provide a deliberately
  # unusable synthetic token rather than any real Worker secret.
  umask 077
  printf '%s' 'synthetic-sandbox-no-access' > /tmp/worker-metrics-token
fi
set -- --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus

# The automated mode keeps Prometheus on loopback and exposes the private
# service endpoint through a proxy that allows only authenticated snapshot POSTs.
if [ "${PROMETHEUS_SNAPSHOT_ADMIN_API:-false}" = "true" ]; then
  if [ -z "${PROMETHEUS_BACKUP_TOKEN:-}" ] || [ -z "${PROMETHEUS_BACKUP_URL:-}" ]; then
    echo "Snapshot automation requires PROMETHEUS_BACKUP_TOKEN and PROMETHEUS_BACKUP_URL" >&2
    exit 1
  fi
  exec python3 /usr/local/bin/prometheus_snapshot_runtime.py \
    --prometheus /bin/prometheus \
    --config /etc/prometheus/prometheus.yml \
    --storage /prometheus
fi

# Default mode: no Admin API and Prometheus keeps its regular private listener.
exec /bin/prometheus "$@"
