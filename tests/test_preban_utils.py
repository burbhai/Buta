import unittest

from core import can_attempt_preban, parse_target_identifier


class TestParseTargetIdentifier(unittest.TestCase):
    def test_parse_id_access_hash(self) -> None:
        user_id, access_hash, username = parse_target_identifier("12345:67890")
        self.assertEqual(user_id, 12345)
        self.assertEqual(access_hash, 67890)
        self.assertIsNone(username)

    def test_parse_numeric_id(self) -> None:
        user_id, access_hash, username = parse_target_identifier("54321")
        self.assertEqual(user_id, 54321)
        self.assertIsNone(access_hash)
        self.assertIsNone(username)

    def test_parse_username(self) -> None:
        user_id, access_hash, username = parse_target_identifier("@SomeUser")
        self.assertIsNone(user_id)
        self.assertIsNone(access_hash)
        self.assertEqual(username, "someuser")

    def test_parse_empty(self) -> None:
        user_id, access_hash, username = parse_target_identifier(" ")
        self.assertIsNone(user_id)
        self.assertIsNone(access_hash)
        self.assertIsNone(username)


class TestCanAttemptPreban(unittest.TestCase):
    def test_group_with_id(self) -> None:
        allowed, reason = can_attempt_preban("group", 123, None)
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_channel_missing_access_hash(self) -> None:
        allowed, reason = can_attempt_preban("channel", 123, None)
        self.assertFalse(allowed)
        self.assertEqual(reason, "missing access_hash for channel ban")

    def test_missing_user_id(self) -> None:
        allowed, reason = can_attempt_preban("group", None, None)
        self.assertFalse(allowed)
        self.assertEqual(reason, "missing user_id")


if __name__ == "__main__":
    unittest.main()
