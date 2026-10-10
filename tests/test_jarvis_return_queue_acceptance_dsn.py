"""Fail-closed CI DSN validation: never connect to a remote database."""
import unittest
from scripts.run_jarvis_return_queue_acceptance import validate_ci_dsn


class AcceptanceDsnGuards(unittest.TestCase):
    def test_exact_local_dsn_accepted(self):
        validate_ci_dsn("postgresql://jarvis_sandbox:ci_only_not_a_real_secret@127.0.0.1:5432/jarvis_return_queue_test")

    def test_remote_hostname_with_localhost_in_username_rejected(self):
        with self.assertRaises(SystemExit):
            validate_ci_dsn("postgresql://localhost:pw@remote.example:5432/jarvis_return_queue_test")

    def test_remote_hostname_with_localhost_in_password_rejected(self):
        with self.assertRaises(SystemExit):
            validate_ci_dsn("postgresql://jarvis_sandbox:localhost@remote.example:5432/jarvis_return_queue_test")

    def test_lookalike_database_rejected(self):
        with self.assertRaises(SystemExit):
            validate_ci_dsn("postgresql://jarvis_sandbox:pw@127.0.0.1:5432/jarvis_return_queue_test_prod")

    def test_wrong_role_rejected(self):
        with self.assertRaises(SystemExit):
            validate_ci_dsn("postgresql://other:jarvis_sandbox@127.0.0.1:5432/jarvis_return_queue_test")

    def test_query_override_rejected(self):
        with self.assertRaises(SystemExit):
            validate_ci_dsn("postgresql://jarvis_sandbox:pw@127.0.0.1:5432/jarvis_return_queue_test?host=remote.example")

    def test_missing_port_rejected(self):
        with self.assertRaises(SystemExit):
            validate_ci_dsn("postgresql://jarvis_sandbox:pw@127.0.0.1/jarvis_return_queue_test")


if __name__ == "__main__":
    unittest.main()
