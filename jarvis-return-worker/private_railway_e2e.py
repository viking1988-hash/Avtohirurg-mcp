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
    print("PR47_EFFECT_COUNT_PASS: exactly one persisted effect for synthetic key")
    print("PR47_PRIVATE_HTTP_E2E_PASS: phase=" + phase + "; first_status=" + str(first[0]) + "; replay=200; conflict=409; receipt_get=200; receipt_id=" + str(first[1].get("receipt_id")) + "; synthetic only")
    print("NOTE: effect count requires independent database-side verification")


if __name__ == "__main__":
    main()
