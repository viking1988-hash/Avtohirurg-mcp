"""Run disposable DR preflight regressions in the repository's standard CI suite."""
import importlib.util
from pathlib import Path
import unittest

_SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "prometheus" / "test_disposable_dr_preflight.py"
_spec = importlib.util.spec_from_file_location("dr_preflight_regression", _SCRIPT)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

DRPathPreflightTests = _module.DRPathPreflightTests
