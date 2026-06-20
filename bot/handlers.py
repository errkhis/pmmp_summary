import logging
import os
from typing import Any

from database import (
    DatabaseNotConfigured,
    count_users,
    grant_premium,
    set_daily_summary_enabled,
    set_free,
    upsert_telegram_user,
)

from .messages import (
    account_reply_markup,
    account_status_message,
    database_error_message,
    esc,
    help_message,
    premium_only_message,
    welcome_message,
)
from .telegram import answer_callback, configure_public_commands, send


log = logging.getLogger(__name__)

TELEGRAM_ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID", "").strip()
TELEGRAM_ADMIN_USERNAME = os.environ.get("TELEGRAM_ADMIN_USERNAME", "").strip().lstrip("@")


def process_update(update: dict[str, Any]) -> None:
    callback = update.get("callback_query")
    if callback:
        process_callback(callback)
        return

    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if handle_admin_command(chat_id, text, message):
        return
    if text.startswith("/start"):
        handle_start(chat_id, message)
        return
    if text.startswith("/help"):
        send(chat_id, help_message(TELEGRAM_ADMIN_USERNAME, is_admin(message)))
        return
    if text.startswith("/me") or text.startswith("/subscription"):
        handle_account(chat_id, message)
        return


def handle_start(chat_id: int, message: dict[str, Any]) -> None:
    user = None
    try:
        user = upsert_telegram_user(message.get("from") or {"id": chat_id})
    except DatabaseNotConfigured:
        log.warning("Database not configured during /start")
    except Exception:
        log.exception("Failed to upsert user during /start")
    try:
        configure_public_commands()
    except Exception:
        log.exception("Failed to configure Telegram commands")
    send(chat_id, welcome_message(user, TELEGRAM_ADMIN_USERNAME))


def handle_account(chat_id: int, message: dict[str, Any]) -> None:
    sender = message.get("from") or {}
    if not sender.get("id"):
        send(chat_id, "❌ Unable to identify your Telegram user id.")
        return
    try:
        user = upsert_telegram_user(sender)
        if not user.is_premium:
            send(chat_id, account_status_message(user))
            send(chat_id, premium_only_message(TELEGRAM_ADMIN_USERNAME))
            return
        send(chat_id, account_status_message(user), reply_markup=account_reply_markup(user))
    except DatabaseNotConfigured:
        send(chat_id, database_error_message())
    except Exception as exc:
        log.exception("Account command error")
        send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")


def handle_admin_command(chat_id: int, text: str, message: dict[str, Any]) -> bool:
    admin_commands = ("/premium", "/free", "/users")
    if not text.startswith(admin_commands):
        return False
    if not is_admin(message):
        send(chat_id, "⛔ This command is reserved for the administrator.")
        return True
    if text.startswith("/users"):
        try:
            send(chat_id, f"👥 Registered users: <b>{count_users()}</b>")
        except DatabaseNotConfigured:
            send(chat_id, database_error_message())
        except Exception as exc:
            log.exception("Users command error")
            send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")
        return True

    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        send(chat_id, "Format: <code>/premium TELEGRAM_ID [years]</code> or <code>/free TELEGRAM_ID</code>")
        return True
    telegram_id = int(parts[1])
    admin_id = int((message.get("from") or {}).get("id"))
    try:
        if text.startswith("/premium"):
            years = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
            user = grant_premium(telegram_id, years, admin_telegram_id=admin_id)
            send(
                chat_id,
                "✅ Premium activated\n"
                f"User ID: <code>{user.telegram_id}</code>\n"
                f"Valid until: <b>{user.premium_expires_at.strftime('%Y-%m-%d')}</b>",
            )
        else:
            user = set_free(telegram_id, admin_telegram_id=admin_id)
            send(chat_id, f"✅ Premium removed\nUser ID: <code>{user.telegram_id}</code>")
    except DatabaseNotConfigured:
        send(chat_id, database_error_message())
    except Exception as exc:
        log.exception("Admin command error")
        send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")
    return True


def process_callback(callback: dict[str, Any]) -> None:
    callback_id = callback.get("id")
    message = callback.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    sender = callback.get("from") or {}
    data = callback.get("data") or ""
    if callback_id:
        answer_callback(callback_id, "Processing...")
    if not chat_id:
        return

    if data in ("daily_summary:on", "daily_summary:off"):
        try:
            user = upsert_telegram_user(sender or {"id": chat_id})
            if not user.is_premium:
                send(chat_id, premium_only_message(TELEGRAM_ADMIN_USERNAME))
                return
            user = set_daily_summary_enabled(user.telegram_id, True)
            send(
                chat_id,
                "✅ Daily summary <b>enabled</b>.",
                reply_markup=account_reply_markup(user),
            )
        except DatabaseNotConfigured:
            send(chat_id, database_error_message())
        except Exception as exc:
            log.exception("Daily summary toggle error")
            send(chat_id, f"❌ <b>Error:</b> {esc(str(exc)[:400])}")


def is_admin(message: dict[str, Any]) -> bool:
    if not TELEGRAM_ADMIN_ID:
        return False
    sender = message.get("from") or {}
    return str(sender.get("id", "")) == TELEGRAM_ADMIN_ID
