"""Offline tests for single-host lock."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "run_locked.py"
spec = importlib.util.spec_from_file_location("run_locked", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

@unittest.skipUnless(sys.platform.startswith("linux"), "Linux flock only")
class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lock = Path(self.tmp.name) / "backup.lock"

    def test_success_and_return_code(self):
        self.assertEqual(mod.run_locked(self.lock, [sys.executable, "-c", "pass"]), 0)
        self.assertEqual(mod.run_locked(self.lock, [sys.executable, "-c", "import sys;sys.exit(4)"]), 4)

    def test_held_lock_rejected(self):
        import fcntl
        fd = os.open(self.lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "holds the lock"):
                mod.run_locked(self.lock, [sys.executable, "-c", "pass"])
        finally:
            os.close(fd)

    def test_symlink_rejected(self):
        target = Path(self.tmp.name) / "target"
        target.write_text("")
        self.lock.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            mod.run_locked(self.lock, [sys.executable, "-c", "pass"])

    def test_timeout_fails(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            mod.run_locked(self.lock, [sys.executable, "-c", "import time;time.sleep(2)"], timeout=1)

    def test_empty_command_fails(self):
        with self.assertRaisesRegex(ValueError, "Command"):
            mod.run_locked(self.lock, [])

if __name__ == "__main__":
    unittest.main()
