import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from jarvis_return_queue_claims import claim, complete, initialize

class AtomicClaimTests(unittest.TestCase):
    def test_one_winner_across_concurrent_connections(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "claims.sqlite")
            with sqlite3.connect(path) as c:
                initialize(c)
            barrier = threading.Barrier(2)
            results = []
            def worker(name):
                with sqlite3.connect(path, timeout=5) as c:
                    barrier.wait()
                    results.append(claim(c, "task-1", "fingerprint", name,
                                         datetime.now(timezone.utc)))
            threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(2)]
            for t in threads: t.start()
            for t in threads: t.join()
            self.assertEqual(sorted(results), [False, True])

    def test_receipt_and_owner_required(self):
        with sqlite3.connect(":memory:") as c:
            initialize(c)
            self.assertTrue(claim(c, "task", "fp", "worker-1", datetime.now(timezone.utc)))
            self.assertFalse(claim(c, "task", "fp", "worker-2", datetime.now(timezone.utc)))
            self.assertFalse(complete(c, "task", "fp", "worker-1", ""))
            self.assertFalse(complete(c, "task", "fp", "worker-2", "receipt"))
            self.assertTrue(complete(c, "task", "fp", "worker-1", "receipt"))
            self.assertFalse(complete(c, "task", "fp", "worker-1", "receipt"))

if __name__ == "__main__":
    unittest.main()
