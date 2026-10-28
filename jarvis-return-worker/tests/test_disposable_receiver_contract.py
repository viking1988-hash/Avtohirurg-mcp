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
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

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
