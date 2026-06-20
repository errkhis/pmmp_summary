import unittest
from datetime import datetime, timedelta, timezone

from bot.messages import account_reply_markup, account_status_message, premium_only_message, welcome_message
from database import User


class MessageTests(unittest.TestCase):
    def test_premium_account_message(self):
        user = User(
            telegram_id=1,
            username="demo",
            first_name="Demo",
            plan="premium",
            premium_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            daily_summary_enabled=True,
        )
        self.assertIn("Premium", account_status_message(user))
        self.assertIn("enabled", account_status_message(user))

    def test_account_reply_markup(self):
        user = User(
            telegram_id=1,
            username="demo",
            first_name="Demo",
            plan="premium",
            premium_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            daily_summary_enabled=False,
        )
        markup = account_reply_markup(user)
        self.assertIn("daily_summary:on", str(markup))

    def test_premium_only_message(self):
        text = premium_only_message("summary_admin")
        self.assertIn("@summary_admin", text)

    def test_welcome_message_mentions_summary(self):
        text = welcome_message(None, "summary_admin")
        self.assertIn("daily HTML summary", text)


if __name__ == "__main__":
    unittest.main()
