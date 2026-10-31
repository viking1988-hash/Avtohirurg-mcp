#!/bin/sh
# Disposable isolated Railway snapshot and block integrity probe.
set -eu
url=http://127.0.0.1:9090
ready=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
  if wget -q -O /dev/null "$url/-/ready" 2>/dev/null; then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then echo 'SNAPSHOT_PROBE_NOT_READY'; exit 1; fi
if ! wget -q -O /tmp/disposable_snapshot_result.json --post-data='' "$url/api/v1/admin/tsdb/snapshot"; then echo 'SNAPSHOT_PROBE_REQUEST_FAILED'; exit 1; fi
if ! grep -q '"status":"success"' /tmp/disposable_snapshot_result.json; then echo 'SNAPSHOT_PROBE_INVALID_RESPONSE'; exit 1; fi
snapshot_dir=$(sed -n 's/.*"name":"\([^"]*\)".*/\1/p' /tmp/disposable_snapshot_result.json)
case "$snapshot_dir" in ''|*/*|*..*) echo 'SNAPSHOT_PROBE_INVALID_NAME'; exit 1;; esac
snapshot_path="/prometheus/snapshots/$snapshot_dir"
if [ ! -d "$snapshot_path" ]; then echo 'SNAPSHOT_PROBE_MISSING_DIRECTORY'; exit 1; fi
block_count=0
for block in "$snapshot_path"/*; do
  [ -d "$block" ] || continue
  [ -f "$block/meta.json" ] || continue
  block_count=$((block_count + 1))
  if ! /bin/promtool tsdb analyze "$snapshot_path" "${block##*/}" >/tmp/disposable_promtool.log 2>&1; then
    echo 'SNAPSHOT_PROBE_BLOCK_ANALYSIS_FAILED'; tail -n 8 /tmp/disposable_promtool.log; exit 1
  fi
done
if [ "$block_count" -lt 1 ]; then echo 'SNAPSHOT_PROBE_NO_BLOCKS'; exit 1; fi
echo 'SNAPSHOT_PROBE_SUCCESS'
echo 'SNAPSHOT_PROBE_DIRECTORY_PRESENT'
echo "SNAPSHOT_PROBE_BLOCKS_ANALYZED=$block_count"
