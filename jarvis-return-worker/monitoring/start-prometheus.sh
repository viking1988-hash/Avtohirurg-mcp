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
# Snapshot admin API is disabled by default. Enable only for a short,
# explicitly controlled staging backup window; disable again afterwards.
set -- --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --web.console.libraries=/usr/share/prometheus/console_libraries \
  --web.console.templates=/usr/share/prometheus/consoles
if [ "${PROMETHEUS_SNAPSHOT_ADMIN_API:-false}" = "true" ]; then
  set -- "$@" --web.enable-admin-api
fi
exec /bin/prometheus "$@"
