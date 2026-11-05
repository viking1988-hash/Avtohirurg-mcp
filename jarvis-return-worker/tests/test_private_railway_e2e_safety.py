"""Fail-closed safety tests for opt-in Railway private E2E runner."""
import importlib.util
import os
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pr47_private_e2e", ROOT / "private_railway_e2e.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrivateRailwayE2ESafety(unittest.TestCase):
    def invoke(self, values):
        names = ("JARVIS_PR47_DISPOSABLE_ONLY", "JARVIS_PR47_PRIVATE_E2E_APPROVED",
                 "JARVIS_RECEIVER_E2E_URL", "JARVIS_RECEIVER_E2E_TOKEN")
        with patch.dict(os.environ, {name: "" for name in names} | values):
            with patch.object(MODULE.urllib.request, "build_opener",
                              side_effect=AssertionError("network must not be accessed")):
                with self.assertRaises(SystemExit) as error:
                    MODULE.main()
        return str(error.exception)

    def test_no_approval(self):
        self.assertIn("opt-in", self.invoke({"JARVIS_PR47_DISPOSABLE_ONLY": "YES"}))

    def test_no_disposable_guard(self):
        self.assertIn("disposable-only", self.invoke({}))

    def test_rejects_external_and_public_hosts(self):
        base = {"JARVIS_PR47_DISPOSABLE_ONLY": "YES",
                "JARVIS_PR47_PRIVATE_E2E_APPROVED": "YES",
                "JARVIS_RECEIVER_E2E_TOKEN": "x" * 32}
        for url in ("https://example.com", "http://127.0.0.1:8080",
                    "http://jarvis-pr47-http-sink-disposable.railway.internal:80",
                    "http://jarvis-pr47-http-sink-disposable.railway.internal:8080@evil.example",
                    "http://jarvis-pr47-http-sink-disposable.railway.internal:8080/path"):
            with self.subTest(url=url):
                self.assertIn("private DNS", self.invoke(base | {"JARVIS_RECEIVER_E2E_URL": url}))

    def test_missing_token(self):
        self.assertIn("token missing", self.invoke({
            "JARVIS_PR47_DISPOSABLE_ONLY": "YES",
            "JARVIS_PR47_PRIVATE_E2E_APPROVED": "YES",
            "JARVIS_RECEIVER_E2E_URL": "http://jarvis-pr47-http-sink-disposable.railway.internal:8080",
        }))


if __name__ == "__main__":
    unittest.main()
