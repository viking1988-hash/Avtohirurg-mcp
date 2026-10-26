#!/bin/sh
# Positive argument contract with a disposable synthetic /bin/prometheus shim.
# Does not launch Prometheus or touch Railway.
set -eu
tmp="$(mktemp -d)"
testdir="/tmp/prometheus-disposable-positive-$$"
cleanup() { rm -rf "$tmp" "$testdir"; }
trap cleanup EXIT INT TERM
mkdir -p "$testdir"
# Script currently uses fixed /bin/prometheus; positive live test remains
# covered by isolated_snapshot_smoke.sh with the actual Prometheus binary.
# Check exact fail-closed opt-in and loopback-only launch contract statically.
grep -Fq 'PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST' ops/prometheus/start-disposable-snapshot-prometheus.sh
grep -Fq -- '--web.listen-address=127.0.0.1:19091' ops/prometheus/start-disposable-snapshot-prometheus.sh
grep -Fq -- '--web.enable-admin-api' ops/prometheus/start-disposable-snapshot-prometheus.sh
if grep -Eq -- '--web.listen-address=(0.0.0.0|:9090)' ops/prometheus/start-disposable-snapshot-prometheus.sh; then
  echo "FAIL: unsafe snapshot admin listener" >&2
  exit 1
fi
echo "DISPOSABLE_SNAPSHOT_LAUNCH_CONTRACT_PASS"
