#!/bin/sh
set -eu
: "${JARVIS_METRICS_TOKEN:?JARVIS_METRICS_TOKEN is required}"
umask 077
printf '%s' "$JARVIS_METRICS_TOKEN" > /tmp/worker-metrics-token
exec /bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus --web.console.libraries=/usr/share/prometheus/console_libraries --web.console.templates=/usr/share/prometheus/consoles
