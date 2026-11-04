"""Offline unit tests for per-block promtool invocation."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "validate_restored_tsdb.py"
spec = importlib.util.spec_from_file_location("validate_restored_tsdb", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
BLOCK = "01M5NEYN71T8MY4RF27T1K2GN4"

class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name)
        self.block = self.db / BLOCK
        (self.block / "chunks").mkdir(parents=True)
        (self.block / "index").write_bytes(b"index")
        (self.block / "meta.json").write_text("{}", encoding="utf-8")

    @patch.object(mod.subprocess, "run")
    def test_uses_database_root_and_block_id(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        self.assertEqual(mod.validate(self.db, "/bin/promtool", 1), 1)
        self.assertEqual(run.call_args.args[0], ["/bin/promtool", "tsdb", "analyze", str(self.db), BLOCK])

    @patch.object(mod.subprocess, "run")
    def test_nonzero_exit_fails(self, run):
        run.return_value = subprocess.CompletedProcess([], 2, "", "corrupt")
        with self.assertRaisesRegex(RuntimeError, "promtool failed"):
            mod.validate(self.db, "/bin/promtool", 1)

    @patch.object(mod.subprocess, "run")
    def test_timeout_fails(self, run):
        run.side_effect = subprocess.TimeoutExpired("promtool", 1)
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            mod.validate(self.db, "/bin/promtool", 1)

    def test_wrong_block_count_fails(self):
        with self.assertRaisesRegex(ValueError, "Expected 2 blocks"):
            mod.validate(self.db, "/bin/promtool", 2)

    def test_missing_index_fails(self):
        (self.block / "index").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            mod.validate(self.db, "/bin/promtool", 1)

if __name__ == "__main__":
    unittest.main()
