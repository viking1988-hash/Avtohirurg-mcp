"""One-shot synthetic alert sender for the isolated Railway retry experiment."""
import datetime
import json
import os
import time
import urllib.error
import urllib.request

URL = "http://jarvis-retry-e2e-alertmanager.railway.internal:9093/api/v2/alerts"
RUN_ID = os.environ.get("RETRY_E2E_RUN_ID", "retry-e2e-20261017")
now = datetime.datetime.now(datetime.timezone.utc)
payload = [{
    "labels": {"alertname": "JarvisIsolatedRetryE2E", "environment": "retry-e2e",
               "run_id": RUN_ID},
    "annotations": {"summary": "Disposable synthetic alert only"},
    "startsAt": now.isoformat().replace("+00:00", "Z"),
    "endsAt": (now + datetime.timedelta(minutes=3)).isoformat().replace("+00:00", "Z"),
}]
request = urllib.request.Request(
    URL, data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)
for attempt in range(1, 16):
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            print("RETRY_E2E_ALERT_SUBMITTED", RUN_ID, response.status, flush=True)
            break
    except (urllib.error.URLError, TimeoutError) as error:
        print("RETRY_E2E_SUBMISSION_WAIT", attempt, type(error).__name__, flush=True)
        if attempt == 15:
            raise
        time.sleep(4)
