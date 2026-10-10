import unittest
from scripts.apply_jarvis_sandbox_schema import validate_target, EXPECTED_DB, SQL_PATH

class SandboxMigrationGuardTests(unittest.TestCase):
    def test_expected_sandbox_url(self):
        validate_target("postgresql://jarvis_sandbox:example@jarvis-sandbox-postgres.railway.internal:5432/jarvis_return_queue_test", EXPECTED_DB)

    def test_reject_crm_and_production_database(self):
        for database in ("postgres", "crm", "avtohirurg", "jarvis_production"):
            with self.subTest(database=database), self.assertRaises(ValueError):
                validate_target(f"postgresql://jarvis_sandbox:example@db.internal/{database}", EXPECTED_DB)

    def test_reject_missing_password(self):
        with self.assertRaises(ValueError):
            validate_target("postgresql://jarvis_sandbox@db.internal/jarvis_return_queue_test", EXPECTED_DB)

    def test_ddl_has_no_python_docstring(self):
        sql = SQL_PATH.read_text(encoding="utf-8")
        self.assertNotIn('"""', sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS jarvis_return_actions", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS jarvis_return_action_events", sql)

if __name__ == "__main__":
    unittest.main()
