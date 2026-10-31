#!/bin/sh
# Synthetic isolated Railway only. Trigger snapshot through loopback, no external access.
set -eu
url=http://127.0.0.1:9090
ready=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
  if wget -q -O /dev/null "$url/-/ready"; then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then echo 'SNAPSHOT_PROBE_NOT_READY'; exit 1; fi
if ! wget -q -O /tmp/disposable_snapshot_result.json --post-data='' "$url/api/v1/admin/tsdb/snapshot"; then
  echo 'SNAPSHOT_PROBE_REQUEST_FAILED'; exit 1
fi
if ! grep -q '"status":"success"' /tmp/disposable_snapshot_result.json; then
  echo 'SNAPSHOT_PROBE_INVALID_RESPONSE'; exit 1
fi
echo 'SNAPSHOT_PROBE_SUCCESS'
