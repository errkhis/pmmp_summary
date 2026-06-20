import unittest
from datetime import datetime, timedelta, timezone

from database import User


class DatabaseLogicTests(unittest.TestCase):
    def test_premium_property(self):
        user = User(
            telegram_id=1,
            username="demo",
            first_name="Demo",
            plan="premium",
            premium_expires_at=datetime.now(timezone.utc) + timedelta(days=10),
            daily_summary_enabled=True,
        )
        self.assertTrue(user.is_premium)

    def test_non_premium_property(self):
        user = User(
            telegram_id=1,
            username="demo",
            first_name="Demo",
            plan="free",
            premium_expires_at=None,
            daily_summary_enabled=False,
        )
        self.assertFalse(user.is_premium)


if __name__ == "__main__":
    unittest.main()
