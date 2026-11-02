#!/usr/bin/env python3
"""Fail-closed regression tests for disposable DR path preflight (no real backups)."""
import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).with_name("disposable_dr_promql.py")


def load_preflight():
    # Extract main without importing the receiver or requiring Prometheus binaries.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    namespace = {"Path": Path, "os": os, "json": json}
    exec(compile(ast.Module(body=[main], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["main"]


class DRPathPreflightTests(unittest.TestCase):
    def test_rejects_symlinked_backup_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            actual = Path(tmp) / "real"
            actual.mkdir()
            alias = Path(tmp) / "linked"
            alias.symlink_to(actual, target_is_directory=True)
            with patch.dict(os.environ, {"PROMETHEUS_BACKUP_DIR": str(alias)}):
                with self.assertRaisesRegex(ValueError, "Unsafe backup directory"):
                    load_preflight()()

    def test_rejects_symlinked_verification_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "backups"
            root.mkdir()
            metadata = Path(tmp) / "metadata.json"
            metadata.write_text('{"archive": "unused.tar.gz"}', encoding="utf-8")
            (root / "last-verified.json").symlink_to(metadata)
            with patch.dict(os.environ, {"PROMETHEUS_BACKUP_DIR": str(root)}):
                with self.assertRaisesRegex(ValueError, "Unsafe backup status file"):
                    load_preflight()()

    def test_rejects_missing_verification_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PROMETHEUS_BACKUP_DIR": tmp}):
                with self.assertRaisesRegex(ValueError, "Unsafe backup status file"):
                    load_preflight()()


if __name__ == "__main__":
    unittest.main()
