#!/bin/sh
set -eu
if [ "${JARVIS_PR47_DISPOSABLE_ONLY:-}" != "YES" ]; then
  echo "disposable-only guard missing" >&2
  exit 1
fi
if [ ! -d /data ] || [ -L /data ]; then
  echo "persistent volume missing or unsafe" >&2
  exit 1
fi
chown sink:sink /data
exec gosu sink python /app/disposable_sink.py
