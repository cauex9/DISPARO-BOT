from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "database" / "bot.db"))
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ── Connection pool (reused across requests) ────────────────────────────────
_pg_pool: "psycopg2.pool.ThreadedConnectionPool | None" = None

def _get_pool() -> "psycopg2.pool.ThreadedConnectionPool":
    global _pg_pool
    if _pg_pool is None or _pg_pool.closed:
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=DATABASE_URL,
            cursor_factory=psycopg2.extras.DictCursor,
        )
        logger.info("PostgreSQL connection pool initialized.")
    return _pg_pool


def get_dialect() -> str:
    if DATABASE_URL and DATABASE_URL.startswith("postgres"):
        return "postgres"
    return "sqlite"


# ── Wrapper classes ──────────────────────────────────────────────────────────

class DBCursor:
    def __init__(self, cursor, dialect):
        self._cursor = cursor
        self.dialect = dialect

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> Any:
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)

    @property
    def lastrowid(self):
        if self.dialect == "sqlite":
            return self._cursor.lastrowid
        return None


class DBConnection:
    def __init__(self, conn, dialect):
        self._conn = conn
        self.dialect = dialect

    def execute(self, query: str, params: tuple | list | None = None) -> DBCursor:
        t0 = time.perf_counter()
        if self.dialect == "postgres":
            query = query.replace("?", "%s")
        cursor = self._conn.cursor()
        cursor.execute(query, params or ())
        elapsed = (time.perf_counter() - t0) * 1000
        if elapsed > 200:
            logger.warning("SLOW QUERY (%.0fms): %s", elapsed, query[:120])
        return DBCursor(cursor, self.dialect)

    def executemany(self, query: str, params_list: list[tuple]) -> DBCursor:
        if self.dialect == "postgres":
            query = query.replace("?", "%s")
        cursor = self._conn.cursor()
        cursor.executemany(query, params_list)
        return DBCursor(cursor, self.dialect)

    def executescript(self, script: str) -> None:
        if self.dialect == "postgres":
            script = script.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cursor = self._conn.cursor()
            cursor.execute(script)
        else:
            self._conn.executescript(script)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ── Context manager ──────────────────────────────────────────────────────────

@contextmanager
def get_connection() -> Iterator[DBConnection]:
    dialect = get_dialect()
    pool = None
    conn = None
    if dialect == "postgres":
        if not HAS_PSYCOPG2:
            raise RuntimeError("psycopg2-binary not installed but DATABASE_URL is set to postgres.")
        pool = _get_pool()
        conn = pool.getconn()
        conn.autocommit = False
    else:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

    wrapper = DBConnection(conn, dialect)
    try:
        yield wrapper
        wrapper.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if pool is not None:
            pool.putconn(conn)   # return connection to pool — NOT closed
        else:
            conn.close()


# ── Schema ───────────────────────────────────────────────────────────────────

def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                email TEXT UNIQUE,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS groups_table (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                reference TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                price TEXT NOT NULL,
                product_url TEXT NOT NULL,
                image_url TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ad_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ad_id INTEGER NOT NULL REFERENCES ads(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL REFERENCES groups_table(id),
                ad_id INTEGER NOT NULL REFERENCES ads(id),
                variant_id INTEGER REFERENCES ad_variants(id),
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                prepared_text TEXT,
                published_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                provider_subscription_id TEXT,
                transaction_id TEXT,
                identifier TEXT UNIQUE NOT NULL,
                plan TEXT NOT NULL,
                amount NUMERIC(10, 2) NOT NULL,
                status TEXT NOT NULL,
                webhook_token TEXT,
                current_period_start TIMESTAMP,
                current_period_end TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS subscriptions_user_status_idx
                ON subscriptions(user_id, status);
            CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_provider_subscription_idx
                ON subscriptions(provider, provider_subscription_id);
            CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_provider_transaction_idx
                ON subscriptions(provider, transaction_id);
            CREATE TABLE IF NOT EXISTS subscription_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                provider_event_id TEXT NOT NULL,
                subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
                received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP,
                UNIQUE(provider, provider_event_id)
            );
            INSERT INTO settings(key, value) VALUES
                ('interval_seconds', '30'), ('queue_state', 'stopped')
            ON CONFLICT(key) DO NOTHING;
            """
        )
        _migrate_users(connection)


def _migrate_users(connection: DBConnection) -> None:
    """Add registration fields to databases created by older app versions."""
    if connection.dialect == "sqlite":
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("users",),
        ).fetchone()
    else:
        table = connection.execute(
            """SELECT 1 FROM information_schema.tables
               WHERE table_schema = current_schema() AND table_name = ?""",
            ("users",),
        ).fetchone()

    if not table:
        return

    def get_columns() -> set[str]:
        if connection.dialect == "sqlite":
            return {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        return {
            row["column_name"]
            for row in connection.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = ?""",
                ("users",),
            )
        }

    columns = get_columns()
    missing_columns = {
        "name": "TEXT NOT NULL DEFAULT ''",
        "email": "TEXT",
        "username": "TEXT",
        "password_hash": "TEXT",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    }
    for column, definition in missing_columns.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    # Re-read the schema so index creation can never run against a stale column set.
    columns = get_columns()
    if "email" in columns:
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email)")
    if "username" in columns:
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users(username)")


# ── Helpers ──────────────────────────────────────────────────────────────────

def row_dict(row: Any) -> dict[str, Any] | None:
    return dict(row) if row else None


def as_bool(value: Any) -> bool:
    return bool(int(value))


def group_dict(row: Any) -> dict[str, Any] | None:
    item = row_dict(row)
    if item:
        item["active"] = as_bool(item["active"])
    return item


def ad_dict(row: Any, connection: DBConnection | None = None) -> dict[str, Any] | None:
    """Convert an ads row to dict, fetching variants.

    Pass the current `connection` to avoid opening an extra round-trip to the DB.
    When called without a connection (e.g. from external code) it opens one itself.
    """
    item = row_dict(row)
    if not item:
        return None
    item["active"] = as_bool(item["active"])

    def _fetch_variants(conn: DBConnection) -> list:
        rows = conn.execute(
            "SELECT id, text, active FROM ad_variants WHERE ad_id = ? ORDER BY id",
            (item["id"],),
        ).fetchall()
        return [{**dict(v), "active": as_bool(v["active"])} for v in rows]

    if connection is not None:
        item["variants"] = _fetch_variants(connection)
    else:
        with get_connection() as conn:
            item["variants"] = _fetch_variants(conn)

    return item


def setting(key: str, default: str | None = None) -> str | None:
    with get_connection() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: Any) -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
            (key, str(value)),
        )

