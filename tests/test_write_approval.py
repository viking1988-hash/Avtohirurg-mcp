import unittest
from unittest.mock import Mock

from write_approval import execute_approved_write


class WriteApprovalTests(unittest.TestCase):
    def test_external_write_callback_is_not_invoked_without_approval(self):
        for operation_name in ("wordpress_update_page", "wordpress_cf7_feedback"):
            with self.subTest(operation=operation_name):
                operation = Mock()
                with self.assertRaisesRegex(PermissionError, "server_side_human_approval_required"):
                    execute_approved_write(operation, {"synthetic": True})
                operation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
