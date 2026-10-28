"""Receiver contract tests against a disposable HTTP target, never production.

This test is opt-in: provide JARVIS_RECEIVER_E2E_URL and JARVIS_RECEIVER_E2E_TOKEN.
It refuses non-loopback hosts, ensuring real Railway/Telegram/Webhook targets
cannot be contacted by this harness.
"""
import json
import os
import urllib.parse
import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_LOOPBACK_OPENER = urllib.request.build_opener(_NoRedirect())
import unittest
import uuid


class DisposableReceiverContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = os.environ.get("JARVIS_RECEIVER_E2E_URL", "").rstrip("/")
        cls.token = os.environ.get("JARVIS_RECEIVER_E2E_TOKEN", "")
        if not cls.base or not cls.token:
            raise unittest.SkipTest("disposable receiver endpoint not configured")
        url = urllib.parse.urlsplit(cls.base)
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("refusing non-loopback receiver E2E target")
        if url.username or url.password or url.query or url.fragment:
            raise RuntimeError("receiver E2E URL must not contain credentials/query/fragment")

    def request(self, method, path, body=None, key=None):
        url = self.base + path
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        if body is not None:
            headers["Content-Type"] = "application/json"
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _LOOPBACK_OPENER.open(req, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_loopback_contract_rejects_external_redirect(self):
        # A malicious/misconfigured local receiver must not redirect the harness
        # to a real webhook endpoint. The custom opener must not follow 30x.
        import http.server
        import threading
        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "https://example.invalid/never-contact")
                self.end_headers()
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:%d/" % server.server_port,
                headers={"Authorization": "Bearer disposable-test-only"},
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                _LOOPBACK_OPENER.open(request, timeout=3)
            self.assertEqual(caught.exception.code, 302)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_durable_replay_and_conflict(self):
        key = "disposable-" + uuid.uuid4().hex
        first = self.request("POST", "/messages", {"synthetic": True}, key)
        self.assertEqual(first[0], 201)
        replay = self.request("POST", "/messages", {"synthetic": True}, key)
        self.assertEqual(replay, (200, first[1]))
        self.assertEqual(self.request("POST", "/messages", {"synthetic": False}, key)[0], 409)
        self.assertEqual(self.request("GET", "/receipts/" + key), (200, first[1]))


if __name__ == "__main__":
    unittest.main()
