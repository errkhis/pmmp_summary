from database import User


def esc(value) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_date(value):
    return value.strftime("%Y-%m-%d") if value else "—"


def admin_contact(admin_username: str) -> str:
    return f"@{admin_username.lstrip('@')}" if admin_username else "the administrator"


def welcome_message(user: User | None, admin_username: str) -> str:
    lines = [
        "📋 <b>PMMP Daily Summary Bot</b>",
        "",
        "This bot only sends the daily HTML summary of simplified open tenders.",
        "Premium users can activate or deactivate delivery from <b>/me</b>.",
        "",
        f"Premium contact: <b>{esc(admin_contact(admin_username))}</b>.",
    ]
    if user:
        lines.extend(["", account_status_message(user)])
    else:
        lines.extend(["", "Daily summary access: <b>Premium only</b>."])
    return "\n".join(lines)


def help_message(admin_username: str, is_admin: bool) -> str:
    lines = [
        "📖 <b>Commands</b>",
        "/start - Show welcome message",
        "/help - Show commands",
        "/me - Show your account",
        "/subscription - Alias of /me",
        "",
        "This bot is dedicated only to the HTML daily summary.",
        "Daily summary access: <b>Premium only</b>.",
        "",
        f"Premium contact: <b>{esc(admin_contact(admin_username))}</b>",
    ]
    if is_admin:
        lines.extend(["", "<b>Admin</b>", "/premium TELEGRAM_ID [years]", "/free TELEGRAM_ID", "/users"])
    return "\n".join(lines)


def account_status_message(user: User) -> str:
    if user.is_premium:
        status = "enabled" if user.daily_summary_enabled else "disabled"
        return (
            "👤 <b>Your account</b>\n"
            "Plan: <b>Premium</b>\n"
            f"Valid until: <b>{fmt_date(user.premium_expires_at)}</b>\n"
            f"Daily summary: <b>{status}</b>"
        )
    return (
        "👤 <b>Your account</b>\n"
        "Plan: <b>Inactive</b>\n"
        "Daily summary: <b>Premium only</b>"
    )


def account_reply_markup(user: User) -> dict | None:
    if not user.is_premium:
        return None
    if user.daily_summary_enabled:
        button = {
            "text": "Disable daily summary",
            "callback_data": "daily_summary:off",
        }
    else:
        button = {
            "text": "Enable daily summary",
            "callback_data": "daily_summary:on",
        }
    return {"inline_keyboard": [[button]]}


def database_error_message() -> str:
    return (
        "❌ <b>Database is not configured.</b>\n"
        "Add `DATABASE_URL` or `POSTGRES_URL` to your deployment environment."
    )


def premium_only_message(admin_username: str) -> str:
    return (
        "🔒 <b>Daily summary is Premium only</b>\n\n"
        "Contact "
        f"<b>{esc(admin_contact(admin_username))}</b> to activate Premium."
    )
