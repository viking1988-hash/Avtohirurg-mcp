import unittest

from write_approval import require_human_approval


class WriteApprovalTests(unittest.TestCase):
    def test_external_write_is_denied_until_approval_flow_exists(self):
        with self.assertRaisesRegex(PermissionError, "server_side_human_approval_required"):
            require_human_approval()


if __name__ == "__main__":
    unittest.main()
