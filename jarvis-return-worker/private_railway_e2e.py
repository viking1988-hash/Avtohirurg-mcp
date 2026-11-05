"""Opt-in synthetic E2E from inside the isolated Railway private network.

Run only in PR47 staging test client with explicit target and token.
Never expose a public endpoint or send real customer data.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    if os.environ.get("JARVIS_PR47_DISPOSABLE_ONLY") != "YES":
        raise SystemExit("STOP: disposable-only guard required")
    if os.environ.get("JARVIS_PR47_PRIVATE_E2E_APPROVED") != "YES":
        raise SystemExit("STOP: explicit private E2E opt-in required")
    base = os.environ.get("JARVIS_RECEIVER_E2E_URL", "").rstrip("/")
    token = os.environ.get("JARVIS_RECEIVER_E2E_TOKEN", "")
    parsed = urllib.parse.urlsplit(base)
    if (parsed.scheme != "http" or parsed.hostname !=
            "jarvis-pr47-http-sink-disposable.railway.internal" or
            parsed.port != 8080 or parsed.path or parsed.query or
            parsed.fragment or parsed.username or parsed.password):
        raise SystemExit("STOP: target must be exact isolated sink private DNS on port 8080")
    if len(token) < 24:
        raise SystemExit("STOP: synthetic token missing")
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    key = os.environ.get("JARVIS_PR47_E2E_KEY", "pr47-durable-20261029-a")
    if not key.startswith("pr47-durable-"):
        raise SystemExit("STOP: deterministic key must use the synthetic pr47-durable prefix")
    phase = os.environ.get("JARVIS_PR47_E2E_PHASE", "seed")
    if phase not in ("seed", "verify"):
        raise SystemExit("STOP: phase must be seed or verify")
    headers = {"Authorization": "Bearer " + token,
               "Idempotency-Key": key, "Content-Type": "application/json"}

    def call(method, path, body=None):
        req = urllib.request.Request(
            base + path, data=None if body is None else
            json.dumps(body).encode("utf-8"), headers=headers, method=method)
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    first = call("POST", "/messages", {"synthetic": True})
    if phase == "seed":
        assert first[0] in (200, 201), "seed must create or replay an identical synthetic receipt"
    else:
        assert first[0] == 200, "verify phase must replay the durable receipt after restart"
    second = call("POST", "/messages", {"synthetic": True})
    assert second == (200, first[1]), "replay must return identical receipt"
    conflict = call("POST", "/messages", {"synthetic": False})
    assert conflict[0] == 409, "changed payload must conflict"
    receipt = call("GET", "/receipts/" + key)
    assert receipt == (200, first[1]), "stored receipt mismatch"
    audit = call("GET", "/audit/effects/" + key)
    assert audit == (200, {"effect_count": 1}), "exactly one durable effect required"
    # Independent synthetic key: eight real overlapping requests to the deployed
    # private HTTP receiver, not just concurrent in-process SQLite calls.
    concurrent_key = key + "-concurrent-v1"
    concurrent_headers = dict(headers, **{"Idempotency-Key": concurrent_key})
    gate = threading.Barrier(8, timeout=15)

    def concurrent_send(_):
        gate.wait()
        req = urllib.request.Request(
            base + "/messages", data=b'{"concurrent":true}',
            headers=concurrent_headers, method="POST")
        # Each concurrent request owns its opener. Never replay an ambiguous
        # network failure: audit the durable receiver state instead.
        thread_opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.ProxyHandler({}))
        try:
            with thread_opener.open(req, timeout=15) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
        except (OSError, TimeoutError) as error:
            return None, {"transport_error": type(error).__name__}

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(concurrent_send, range(8)))
    # Always inspect durable state, including after connection resets. No
    # automatic POST retry of an ambiguous external effect.
    concurrent_audit = call("GET", "/audit/effects/" + concurrent_key)
    concurrent_receipt = call("GET", "/receipts/" + concurrent_key)
    assert concurrent_audit == (200, {"effect_count": 1}), (
        "concurrent receiver audit failed: " + repr(concurrent_audit))
    assert concurrent_receipt[0] == 200, (
        "concurrent durable receipt missing: " + repr(concurrent_receipt))
    failures = [item for item in outcomes if item[0] is None]
    if failures:
        print("PR47_CONCURRENT_HTTP_AMBIGUOUS: transport_failures=" +
              str(len(failures)) + "; persisted_effects=1; manual reconciliation required")
        raise AssertionError("ambiguous concurrent HTTP response; no automatic resend")
    statuses = sorted(status for status, _ in outcomes)
    # A repeat run may replay an already committed synthetic operation.
    assert statuses in ([200] * 7 + [201], [200] * 8), (
        "concurrent duplicate HTTP statuses invalid: " + repr(statuses))
    receipts = {payload.get("receipt_id") for _, payload in outcomes}
    assert len(receipts) == 1 and None not in receipts, (
        "concurrent requests did not share one durable receipt")
    assert all(payload == concurrent_receipt[1] for _, payload in outcomes), (
        "concurrent HTTP responses disagree with durable receipt")
    print("PR47_CONCURRENT_PRIVATE_HTTP_E2E_PASS: requests=8; persisted_effects=1; synthetic only")
    print("PR47_EFFECT_COUNT_PASS: exactly one persisted effect for synthetic key")
    print("PR47_PRIVATE_HTTP_E2E_PASS: phase=" + phase + "; first_status=" + str(first[0]) + "; replay=200; conflict=409; receipt_get=200; receipt_id=" + str(first[1].get("receipt_id")) + "; synthetic only")
    print("NOTE: effect count verified via authenticated private HTTP endpoint querying mounted SQLite; no separate out-of-band SQL session")


if __name__ == "__main__":
    main()
