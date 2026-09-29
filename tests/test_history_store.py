import json
import os
import unittest
from unittest.mock import patch

import history_store


class HistoryStoreTests(unittest.TestCase):
    def test_client_key_is_stable_and_not_plain_phone(self):
        a = history_store._client_key("+79990000000")
        b = history_store._client_key("+79990000000")
        self.assertEqual(a, b)
        self.assertNotEqual(a, "+79990000000")
        self.assertEqual(len(a), 64)

    def test_missing_database_is_explicit(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                history_store._connect()

    def test_return_task_status_validation(self):
        with self.assertRaises(ValueError):
            history_store.update_return_task_status(1, "BAD")

    def test_visit_payload_is_json_serializable(self):
        card = {"car": "Toyota Camry", "confirmed_faults": ["Рулевая тяга"]}
        payload = json.dumps(card, ensure_ascii=False)
        self.assertIn("Рулевая тяга", payload)


if __name__ == "__main__":
    unittest.main()
