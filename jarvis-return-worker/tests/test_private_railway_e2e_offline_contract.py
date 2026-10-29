"""Offline contract test for the guarded private Railway E2E client.

Mocks HTTP entirely: no Railway access, DNS, customer data or secrets.
"""
import importlib.util
import io
import json
import os
import pathlib
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pr47_private_client", ROOT / "private_railway_e2e.py")
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)
URL = "http://jarvis-pr47-http-sink-disposable.railway.internal:8080"


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, statuses=(201, 200, 409, 200, 200), mismatch=False):
        self.statuses = list(statuses)
        self.mismatch = mismatch
        self.calls = []

    def open(self, req, timeout):
        self.calls.append(req)
        index = len(self.calls) - 1
        status = self.statuses[index]
        receipt = {"receipt_id": "synthetic-1", "status": "COMMITTED"}
        if self.mismatch and index == 1:
            receipt["receipt_id"] = "unexpected"
        if status == 409:
            raise CLIENT.urllib.error.HTTPError(req.full_url, 409, "Conflict", {},
                                                io.BytesIO(b'{"error":"idempotency_conflict"}'))
        return FakeResponse(status, receipt)

    # json.load() needs read(), not just a payload.
    def __getattribute__(self, name):
        return object.__getattribute__(self, name)


class ReadableResponse(FakeResponse):
    def read(self, *args):
        return json.dumps(self.payload).encode("utf-8")


class OfflineContract(unittest.TestCase):
    def run_client(self, statuses=(201, 200, 409, 200, 200), mismatch=False, phase="seed"):
        class Opener(FakeOpener):
            def open(self, req, timeout):
                self.calls.append(req)
                index = len(self.calls) - 1
                status = self.statuses[index]
                receipt = {"receipt_id": "synthetic-1", "status": "COMMITTED"}
                if mismatch and index == 1:
                    receipt["receipt_id"] = "unexpected"
                if status == 409:
                    raise CLIENT.urllib.error.HTTPError(
                        req.full_url, 409, "Conflict", {},
                        io.BytesIO(b'{"error":"idempotency_conflict"}'))
                return ReadableResponse(status, {"effect_count": 1} if index == 4 else receipt)
        opener = Opener(statuses)
        env = {"JARVIS_PR47_DISPOSABLE_ONLY": "YES",
               "JARVIS_PR47_PRIVATE_E2E_APPROVED": "YES",
               "JARVIS_PR47_E2E_PHASE": phase,
               "JARVIS_RECEIVER_E2E_URL": URL,
               "JARVIS_RECEIVER_E2E_TOKEN": "synthetic-only-0123456789-abcdef"}
        with patch.dict(os.environ, env):
            with patch.object(CLIENT.urllib.request, "build_opener", return_value=opener):
                with redirect_stdout(io.StringIO()) as output:
                    CLIENT.main()
        return opener, output.getvalue()

    def test_full_contract_and_same_key(self):
        opener, output = self.run_client()
        self.assertEqual(len(opener.calls), 5)
        keys = {req.get_header("Idempotency-key") for req in opener.calls}
        self.assertEqual(len(keys), 1)
        self.assertNotIn("synthetic-only-0123456789-abcdef", output)
        self.assertIn("PR47_PRIVATE_HTTP_E2E_PASS", output)

    def test_rejects_unexpected_initial_status(self):
        with self.assertRaises(AssertionError):
            self.run_client(statuses=(202, 200, 409, 200, 200))

    def test_seed_replay_allowed(self):
        _, output = self.run_client(statuses=(200, 200, 409, 200, 200))
        self.assertIn("first_status=200", output)

    def test_verify_requires_existing_receipt(self):
        with self.assertRaises(AssertionError):
            self.run_client(phase="verify", statuses=(201, 200, 409, 200, 200))

    def test_verify_existing_receipt(self):
        _, output = self.run_client(phase="verify", statuses=(200, 200, 409, 200, 200))
        self.assertIn("phase=verify", output)

    def test_rejects_receipt_mismatch(self):
        with self.assertRaises(AssertionError):
            self.run_client(mismatch=True)


if __name__ == "__main__":
    unittest.main()
