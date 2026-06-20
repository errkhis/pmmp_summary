import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row


USERS_TABLE = "summary_users"
ADMIN_ACTIONS_TABLE = "summary_admin_actions"
PREMIUM_YEARS_DEFAULT = 1
_DB_INITIALIZED = False


class DatabaseNotConfigured(RuntimeError):
    pass


@dataclass
class User:
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    plan: str
    premium_expires_at: Optional[datetime]
    daily_summary_enabled: bool = False

    @property
    def is_premium(self) -> bool:
        return (
            self.plan == "premium"
            and self.premium_expires_at is not None
            and self.premium_expires_at > datetime.now(timezone.utc)
        )


def _load_local_env() -> None:
    env_path = Path(__file__).with_name(".env.local")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _database_url() -> str:
    _load_local_env()
    for name in ("DATABASE_URL", "POSTGRES_URL", "SUPABASE_DB_URL"):
        url = os.environ.get(name, "").strip()
        if url:
            return _clean_database_url(url)
    raise DatabaseNotConfigured("DATABASE_URL, POSTGRES_URL, or SUPABASE_DB_URL is not configured")


def _clean_database_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    allowed = {
        "application_name",
        "connect_timeout",
        "gssencmode",
        "keepalives",
        "keepalives_count",
        "keepalives_idle",
        "keepalives_interval",
        "sslcert",
        "sslcompression",
        "sslcrl",
        "sslkey",
        "sslmode",
        "sslrootcert",
        "target_session_attrs",
    }
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key in allowed]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _connect():
    return psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row)


def init_db() -> None:
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    with _connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
                telegram_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                plan TEXT NOT NULL DEFAULT 'free'
                    CHECK (plan IN ('free', 'premium')),
                premium_expires_at TIMESTAMPTZ,
                daily_summary_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ADMIN_ACTIONS_TABLE} (
                id BIGSERIAL PRIMARY KEY,
                admin_telegram_id BIGINT,
                target_telegram_id BIGINT NOT NULL,
                action TEXT NOT NULL,
                payload JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    _DB_INITIALIZED = True


def _row_to_user(row) -> User:
    return User(
        telegram_id=row["telegram_id"],
        username=row["username"],
        first_name=row["first_name"],
        plan=row["plan"],
        premium_expires_at=row["premium_expires_at"],
        daily_summary_enabled=bool(row.get("daily_summary_enabled", False)),
    )


def _record_admin_action(conn, admin_telegram_id: Optional[int], target_telegram_id: int, action: str, payload: dict) -> None:
    conn.execute(
        f"""
        INSERT INTO {ADMIN_ACTIONS_TABLE} (admin_telegram_id, target_telegram_id, action, payload)
        VALUES (%s, %s, %s, %s::jsonb)
        """,
        (
            admin_telegram_id,
            target_telegram_id,
            action,
            json.dumps(payload, ensure_ascii=True),
        ),
    )


def upsert_telegram_user(tg_user: dict) -> User:
    init_db()
    telegram_id = int(tg_user["id"])
    username = tg_user.get("username")
    first_name = tg_user.get("first_name")
    with _connect() as conn:
        row = conn.execute(
            f"""
            INSERT INTO {USERS_TABLE} (telegram_id, username, first_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                updated_at = NOW()
            RETURNING telegram_id, username, first_name, plan, premium_expires_at, daily_summary_enabled
            """,
            (telegram_id, username, first_name),
        ).fetchone()
    return _row_to_user(row)


def count_users() -> int:
    init_db()
    with _connect() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS total FROM {USERS_TABLE}").fetchone()
    return int(row["total"])


def list_daily_summary_recipients() -> list[int]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT telegram_id
            FROM {USERS_TABLE}
            WHERE daily_summary_enabled = TRUE
              AND plan = 'premium'
              AND premium_expires_at IS NOT NULL
              AND premium_expires_at > NOW()
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [int(row["telegram_id"]) for row in rows]


def grant_premium(telegram_id: int, years: int = PREMIUM_YEARS_DEFAULT, admin_telegram_id: Optional[int] = None) -> User:
    init_db()
    years = max(1, years)
    with _connect() as conn:
        with conn.transaction():
            row = conn.execute(
                f"""
                INSERT INTO {USERS_TABLE} (telegram_id, plan, premium_expires_at)
                VALUES (%s, 'premium', NOW() + (%s || ' years')::interval)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    plan = 'premium',
                    premium_expires_at = CASE
                        WHEN {USERS_TABLE}.premium_expires_at IS NOT NULL
                         AND {USERS_TABLE}.premium_expires_at > NOW()
                        THEN {USERS_TABLE}.premium_expires_at + (%s || ' years')::interval
                        ELSE NOW() + (%s || ' years')::interval
                    END,
                    updated_at = NOW()
                RETURNING telegram_id, username, first_name, plan, premium_expires_at, daily_summary_enabled
                """,
                (telegram_id, years, years, years),
            ).fetchone()
            _record_admin_action(conn, admin_telegram_id, telegram_id, "grant_premium", {"years": years})
    return _row_to_user(row)


def set_free(telegram_id: int, admin_telegram_id: Optional[int] = None) -> User:
    init_db()
    with _connect() as conn:
        with conn.transaction():
            row = conn.execute(
                f"""
                INSERT INTO {USERS_TABLE} (telegram_id, plan)
                VALUES (%s, 'free')
                ON CONFLICT (telegram_id) DO UPDATE SET
                    plan = 'free',
                    premium_expires_at = NULL,
                    daily_summary_enabled = FALSE,
                    updated_at = NOW()
                RETURNING telegram_id, username, first_name, plan, premium_expires_at, daily_summary_enabled
                """,
                (telegram_id,),
            ).fetchone()
            _record_admin_action(conn, admin_telegram_id, telegram_id, "set_free", {})
    return _row_to_user(row)


def set_daily_summary_enabled(telegram_id: int, enabled: bool) -> User:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            f"""
            UPDATE {USERS_TABLE}
            SET daily_summary_enabled = %s,
                updated_at = NOW()
            WHERE telegram_id = %s
            RETURNING telegram_id, username, first_name, plan, premium_expires_at, daily_summary_enabled
            """,
            (enabled, telegram_id),
        ).fetchone()
    if row is None:
        raise ValueError("Telegram user not found")
    return _row_to_user(row)
