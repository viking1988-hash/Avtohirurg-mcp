#!/bin/sh
# Synthetic isolated Railway only. Trigger snapshot through loopback, no external access.
set -eu
url=http://127.0.0.1:9090
if ! wget -q -O /dev/null "$url/-/ready"; then
  echo 'SNAPSHOT_PROBE_NOT_READY'; exit 1
fi
if ! wget -q -O /tmp/disposable_snapshot_result.json --post-data='' "$url/api/v1/admin/tsdb/snapshot"; then
  echo 'SNAPSHOT_PROBE_REQUEST_FAILED'; exit 1
fi
if ! grep -q '"status":"success"' /tmp/disposable_snapshot_result.json; then
  echo 'SNAPSHOT_PROBE_INVALID_RESPONSE'; exit 1
fi
echo 'SNAPSHOT_PROBE_SUCCESS'
