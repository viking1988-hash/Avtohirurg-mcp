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
    key = "pr47-private-" + uuid.uuid4().hex
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
    assert first[0] == 201, "initial request must create receipt"
    second = call("POST", "/messages", {"synthetic": True})
    assert second == (200, first[1]), "replay must return identical receipt"
    conflict = call("POST", "/messages", {"synthetic": False})
    assert conflict[0] == 409, "changed payload must conflict"
    receipt = call("GET", "/receipts/" + key)
    assert receipt == (200, first[1]), "stored receipt mismatch"
    print("PR47_PRIVATE_HTTP_E2E_PASS: 201/200/409/200; receipt stable; synthetic only")
    print("NOTE: persistence across Railway restart and effect count require separate verification")


if __name__ == "__main__":
    main()
