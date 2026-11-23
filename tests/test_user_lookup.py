import unittest
from unittest.mock import patch

import supabase_store as store


class EmailLookupTests(unittest.TestCase):
    @patch("supabase_store.call")
    def test_email_lookup_checks_later_pages_case_insensitively(self, call):
        call.side_effect = [{"users": [{"email": f"user{i}@example.com", "id": str(i)} for i in range(1000)]},
                            {"users": [{"email": "FRIEND@example.com", "id": "target"}]}]
        self.assertEqual(store.user_id_by_email("friend@example.com"), "target")
        self.assertEqual(call.call_count, 2)

    @patch("supabase_store.call", return_value={"users": []})
    def test_missing_user(self, call):
        self.assertIsNone(store.user_id_by_email("missing@example.com"))


if __name__ == "__main__":
    unittest.main()
