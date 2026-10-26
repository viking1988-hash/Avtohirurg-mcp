import json
import fcntl
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_coordinator as coordinator


class FakeResponse:
    def __init__(self, data, status=200):
        self.status = status
        self.data = data
    def read(self, limit):
        return self.data


class FakeConnection:
    response = None
    def __init__(self, host, port, timeout):
        assert (host, port) == ("127.0.0.1", 19092)
    def request(self, method, path, headers):
        assert (method, path) == ("POST", "/v1/snapshot")
        assert headers["Authorization"].startswith("Bearer ")
    def getresponse(self):
        return self.response
    def close(self):
        pass


class CoordinatorTests(unittest.TestCase):
    def test_opt_in_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys, "argv", ["coordinator", "/tmp", "/tmp", "--url", "http://127.0.0.1", "--lock", "/tmp/lock"]):
                self.assertEqual(coordinator.main(), 2)

    def test_controller_rejects_bad_names_and_errors(self):
        token = "x" * 32
        cases = [
            ({"status": "success", "data": {"name": "../escape"}}, 200),
            ({"status": "success", "data": {"name": "/tmp/foo"}}, 200),
            ({"status": "success", "data": {"name": ""}}, 200),
            ({"status": "error"}, 200),
            ({"status": "success", "data": {"name": "good"}}, 503),
        ]
        with patch.object(coordinator.http.client, "HTTPConnection", FakeConnection):
            for data, status in cases:
                FakeConnection.response = FakeResponse(json.dumps(data).encode(), status)
                with self.assertRaises((ValueError, RuntimeError)):
                    coordinator.request_snapshot(token)

    def test_controller_accepts_safe_name(self):
        FakeConnection.response = FakeResponse(b'{"status":"success","data":{"name":"20261027T123456Z-abc"}}')
        with patch.object(coordinator.http.client, "HTTPConnection", FakeConnection):
            self.assertEqual(coordinator.request_snapshot("x" * 32), "20261027T123456Z-abc")

    def test_existing_lock_prevents_snapshot_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "snapshots"
            root.mkdir()
            output = Path(temp) / "output"
            output.mkdir()
            lock_path = Path(temp) / "cycle.lock"
            with open(lock_path, "a+b") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(coordinator, "request_snapshot") as snapshot:
                    with self.assertRaises(RuntimeError):
                        coordinator.coordinate(root, output, "http://127.0.0.1", "b", "c", lock_path)
                    snapshot.assert_not_called()

    def test_coordinate_only_uploads_expected_completed_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "snapshots"
            root.mkdir()
            (root / "safe").mkdir()
            output = Path(temp) / "output"
            output.mkdir()
            with patch.object(coordinator, "request_snapshot", return_value="safe"), patch.object(coordinator, "execute", return_value={"verified": True}) as execute:
                self.assertEqual(coordinator.coordinate(root, output, "http://127.0.0.1", "b", "c", Path(temp) / "lock"), {"verified": True})
                self.assertEqual(execute.call_count, 1)
            with patch.object(coordinator, "request_snapshot", return_value="missing"), patch.object(coordinator, "execute") as execute:
                with self.assertRaises(RuntimeError):
                    coordinator.coordinate(root, output, "http://127.0.0.1", "b", "c", Path(temp) / "lock")
                execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
