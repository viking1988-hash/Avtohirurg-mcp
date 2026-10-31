"""Bounded extraction preflight regression tests; no live backups required."""
import ast
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "ops" / "prometheus" / "disposable_dr_promql.py"


def load_check():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check_extracted_size")
    namespace = {"tarfile": tarfile, "MAX_EXTRACTED_BYTES": 2 * 1024 * 1024 * 1024}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["check_extracted_size"]


class DRExtractionLimitTests(unittest.TestCase):
    def test_rejects_expanded_size_above_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "compressed.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                payload = b"x" * 64
                for index in range(3):
                    member = tarfile.TarInfo(f"root/file{index}")
                    member.size = len(payload)
                    stream.addfile(member, io.BytesIO(payload))
            with self.assertRaisesRegex(ValueError, "extracted size limit"):
                load_check()(archive, limit=128)

    def test_accepts_within_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "small.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                payload = b"safe"
                member = tarfile.TarInfo("root/file")
                member.size = len(payload)
                stream.addfile(member, io.BytesIO(payload))
            self.assertEqual(load_check()(archive, limit=4), 4)


if __name__ == "__main__":
    unittest.main()
