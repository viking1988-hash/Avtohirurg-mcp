#!/bin/sh
set -eu
script="ops/prometheus/start-disposable-snapshot-prometheus.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
expect_refusal() {
  if "$@" >"$tmp/out" 2>&1; then
    echo "FAIL: unsafe snapshot launcher invocation accepted" >&2
    exit 1
  fi
  if ! grep -q "REFUSED" "$tmp/out"; then
    echo "FAIL: unsafe invocation did not fail closed" >&2
    cat "$tmp/out" >&2
    exit 1
  fi
}
expect_refusal env -u PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST sh "$script"
expect_refusal env PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST=true PROMETHEUS_SNAPSHOT_TEST_DATA_DIR=/prometheus sh "$script"
expect_refusal env PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST=true PROMETHEUS_SNAPSHOT_TEST_DATA_DIR=/tmp/prometheus-disposable-nonexistent-should-not-exist sh "$script"
ln -s "$tmp" "$tmp/link"
# A symlink under the accepted prefix must be rejected.
ln -s "$tmp" /tmp/prometheus-disposable-ci-link-$$
trap 'rm -f /tmp/prometheus-disposable-ci-link-$$; rm -rf "$tmp"' EXIT
expect_refusal env PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST=true PROMETHEUS_SNAPSHOT_TEST_DATA_DIR=/tmp/prometheus-disposable-ci-link-$$ sh "$script"
mkdir -p /tmp/prometheus-disposable-ci-path-$
trap 'rm -rf /tmp/prometheus-disposable-ci-path-$; rm -f /tmp/prometheus-disposable-ci-link-$; rm -rf "$tmp"' EXIT
expect_refusal env PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST=true PROMETHEUS_SNAPSHOT_TEST_DATA_DIR=/tmp/prometheus-disposable-ci-path-$/../prometheus-disposable-ci-path-$ sh "$script"
expect_refusal env PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST=true PROMETHEUS_SNAPSHOT_TEST_DATA_DIR=/tmp/prometheus-disposable-ci-path-$/. sh "$script"
expect_refusal env PROMETHEUS_DISPOSABLE_SNAPSHOT_TEST=true PROMETHEUS_SNAPSHOT_TEST_DATA_DIR=/tmp/prometheus-disposable-ci-path-$/ sh "$script"
echo "DISPOSABLE_SNAPSHOT_LAUNCHER_NEGATIVE_PASS"
