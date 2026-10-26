"""Safety tests for opt-in single-cycle backup lock and failure behavior."""
import fcntl
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_cycle

class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshots = self.root / "snapshots"
        self.snapshots.mkdir()
        self.snapshot = self.snapshots / "snapshot_1"
        self.snapshot.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        self.lock = self.root / "cycle.lock"

    def test_lock_rejects_concurrent_cycle_without_upload(self):
        with self.lock.open("a+b") as locked:
            fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(backup_cycle, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "Another backup cycle"):
                    backup_cycle.execute(self.snapshot, self.snapshots, self.output, "http://example.invalid/v1/upload", "x"*32, self.lock)
                run.assert_not_called()

    def test_success_passes_arguments_to_one_shot(self):
        expected = {"archive": "saved.tar.gz"}
        with patch.object(backup_cycle, "run", return_value=expected) as run:
            result = backup_cycle.execute(self.snapshot, self.snapshots, self.output, "http://example.invalid/v1/upload", "x"*32, self.lock)
        self.assertEqual(result, expected)
        self.assertEqual(run.call_args.args[:2], (self.snapshot, self.snapshots))
        self.assertEqual(run.call_args.args[3:], ("http://example.invalid/v1/upload", "x"*32))

    def test_symlink_lock_rejected(self):
        target = self.root / "target"
        target.write_text("untouched")
        self.lock.symlink_to(target)
        with patch.object(backup_cycle, "run") as run:
            with self.assertRaisesRegex(ValueError, "symlink"):
                backup_cycle.execute(self.snapshot, self.snapshots, self.output, "http://example.invalid/v1/upload", "x"*32, self.lock)
            run.assert_not_called()
        self.assertEqual(target.read_text(), "untouched")

    def test_missing_output_dir_rejected(self):
        with patch.object(backup_cycle, "run") as run:
            with self.assertRaisesRegex(ValueError, "Output directory"):
                backup_cycle.execute(self.snapshot, self.snapshots, self.root / "missing", "http://example.invalid/v1/upload", "x"*32, self.lock)
            run.assert_not_called()

    def test_opt_in_disabled_without_any_execution(self):
        with patch.dict(os.environ, {"PROMETHEUS_BACKUP_SCHEDULER_ENABLED": "false"}):
            with patch.object(sys, "argv", ["backup_cycle", str(self.snapshot), str(self.snapshots), str(self.output), "--url", "http://example.invalid/v1/upload", "--lock", str(self.lock)]):
                with patch.object(backup_cycle, "execute") as execute:
                    self.assertEqual(backup_cycle.main(), 2)
                    execute.assert_not_called()

if __name__ == "__main__":
    unittest.main()
