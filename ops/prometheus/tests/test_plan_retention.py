"""Retention planner never deletes any backup."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "plan_retention.py"
spec = importlib.util.spec_from_file_location("plan_retention", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files = []
        for day in range(1, 5):
            file = self.root / f"prometheus-202610{day:02d}T010000Z-snapshot.tar.gz"
            file.write_bytes(b"keep")
            self.files.append(file)

    def test_oldest_only_candidate(self):
        self.assertEqual(mod.plan(self.root, keep_latest=3), [self.files[0]])
        self.assertTrue(all(p.exists() for p in self.files))

    def test_protected_oldest_never_candidate(self):
        self.assertEqual(mod.plan(self.root, keep_latest=2, protected=[self.files[0].name]), [self.files[1]])

    def test_invalid_names_ignored(self):
        other = self.root / "arbitrary.tar.gz"
        other.write_bytes(b"keep")
        self.assertNotIn(other, mod.plan(self.root, keep_latest=1))

    def test_invalid_retention_rejected(self):
        with self.assertRaises(ValueError):
            mod.plan(self.root, keep_latest=0)

    def test_missing_dir_rejected(self):
        with self.assertRaises(ValueError):
            mod.plan(self.root / "missing")

if __name__ == "__main__":
    unittest.main()
