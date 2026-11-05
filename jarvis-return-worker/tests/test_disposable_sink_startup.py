"""Disposable sink startup safety checks; never starts a listener."""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "disposable_sink.py"


class DisposableSinkStartupTests(unittest.TestCase):
    def run_sink(self, overrides):
        env = dict(os.environ)
        for name in ("JARVIS_PR47_DISPOSABLE_ONLY", "JARVIS_RECEIVER_E2E_TOKEN", "JARVIS_RECEIVER_DB"):
            env.pop(name, None)
        env.update(overrides)
        return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=5)

    def test_missing_disposable_guard_fails_closed(self):
        result = self.run_sink({"JARVIS_RECEIVER_E2E_TOKEN": "x" * 32})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disposable-only guard missing", result.stderr)

    def test_missing_token_fails_closed(self):
        result = self.run_sink({"JARVIS_PR47_DISPOSABLE_ONLY": "YES"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit synthetic token required", result.stderr)

    def test_missing_persistent_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            result = self.run_sink({
                "JARVIS_PR47_DISPOSABLE_ONLY": "YES",
                "JARVIS_RECEIVER_E2E_TOKEN": "x" * 32,
                "JARVIS_RECEIVER_DB": str(pathlib.Path(root) / "absent" / "inbox.sqlite3"),
            })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("persistent database directory missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
