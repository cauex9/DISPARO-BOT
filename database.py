from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool

    HAS_PSYCOPG2 = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_PSYCOPG2 = False

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "database" / "bot.db"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

_pg_pool: "psycopg2.pool.ThreadedConnectionPool | None" = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


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
            pool.putconn(conn)
        else:
            conn.close()


def _table_columns(connection: DBConnection, table_name: str) -> set[str]:
    if connection.dialect == "sqlite":
        rows = connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        return {row[1] for row in rows}
    return {
        row["column_name"]
        for row in connection.execute(
            """SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?""",
            (table_name,),
        ).fetchall()
    }


def _migrate_users(connection: DBConnection) -> None:
    """Add the registration fields introduced after the initial release."""
    if connection.dialect == "sqlite":
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("users",),
        ).fetchone()
    else:
        table = connection.execute(
            """SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?""",
            ("users",),
        ).fetchone()

    if not table:
        return

    columns = _table_columns(connection, "users")
    for column, definition in {
        "name": "TEXT NOT NULL DEFAULT ''",
        "email": "TEXT",
        "username": "TEXT",
        "password_hash": "TEXT",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    }.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    columns = _table_columns(connection, "users")
    if "email" in columns:
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email)")
    if "username" in columns:
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users(username)")


def _migrate_publications_schema(connection: DBConnection) -> None:
    """Add queue/worker metadata without deleting existing rows or references."""
    if connection.dialect == "sqlite":
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("publications",),
        ).fetchone()
    else:
        table = connection.execute(
            """SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?""",
            ("publications",),
        ).fetchone()

    if not table:
        return

    columns = _table_columns(connection, "publications")
    required_columns = {
        "processing_started_at": "TIMESTAMP",
        "worker_id": "TEXT",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "lease_until": "TIMESTAMP",
        "idempotency_key": "TEXT",
        "external_reference": "TEXT",
        "requires_human_action_at": "TIMESTAMP",
    }
    for column, definition in required_columns.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE publications ADD COLUMN {column} {definition}")

    columns = _table_columns(connection, "publications")
    if "status" not in columns:
        connection.execute("ALTER TABLE publications ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'")
    if "idempotency_key" in columns:
        rows = connection.execute("SELECT id FROM publications WHERE idempotency_key IS NULL OR idempotency_key = ''").fetchall()
        for row in rows:
            connection.execute(
                "UPDATE publications SET idempotency_key = ? WHERE id = ?",
                (uuid.uuid4().hex, row["id"]),
            )
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS publications_idempotency_key_idx ON publications(idempotency_key)")
    connection.execute("UPDATE publications SET attempt_count = 0 WHERE attempt_count IS NULL")
    connection.execute("UPDATE publications SET status = 'queued' WHERE status IS NULL OR status = ''")
    connection.execute("CREATE INDEX IF NOT EXISTS publications_queue_worker_idx ON publications(status, worker_id, lease_until)")


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
                status TEXT NOT NULL DEFAULT 'queued',
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
            CREATE INDEX IF NOT EXISTS subscriptions_user_status_idx ON subscriptions(user_id, status);
            CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_provider_subscription_idx ON subscriptions(provider, provider_subscription_id);
            CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_provider_transaction_idx ON subscriptions(provider, transaction_id);
            CREATE TABLE IF NOT EXISTS subscription_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                provider_event_id TEXT NOT NULL,
                subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
                received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP,
                UNIQUE(provider, provider_event_id)
            );
            """
        )
        _migrate_users(connection)
        _migrate_publications_schema(connection)
        connection.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
            ("interval_seconds", "30"),
        )
        connection.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
            ("queue_state", "stopped"),
        )


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
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
            (key, str(value)),
        )


def create_publication_record(
    group_id: int,
    ad_id: int,
    variant_id: int | None = None,
    *,
    status: str = "queued",
    idempotency_key: str | None = None,
    result: str | None = None,
    error: str | None = None,
    prepared_text: str | None = None,
    published_at: str | None = None,
    external_reference: str | None = None,
) -> int:
    key = idempotency_key or uuid.uuid4().hex
    with get_connection() as connection:
        if connection.dialect == "postgres":
            existing = connection.execute(
                "SELECT id FROM publications WHERE idempotency_key = %s LIMIT 1",
                (key,),
            ).fetchone()
            if existing:
                return int(existing["id"])
            row = connection.execute(
                """
                INSERT INTO publications(
                    group_id, ad_id, variant_id, status, result, error, prepared_text,
                    published_at, idempotency_key, external_reference, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (group_id, ad_id, variant_id, status, result, error, prepared_text, published_at, key, external_reference),
            ).fetchone()
            if row:
                return int(row["id"])
        else:
            existing = connection.execute(
                "SELECT id FROM publications WHERE idempotency_key = ? LIMIT 1",
                (key,),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO publications(
                    group_id, ad_id, variant_id, status, result, error, prepared_text,
                    published_at, idempotency_key, external_reference, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (group_id, ad_id, variant_id, status, result, error, prepared_text, published_at, key, external_reference),
            )
            if cursor.lastrowid:
                return int(cursor.lastrowid)

        fallback = connection.execute(
            "SELECT id FROM publications WHERE idempotency_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        if fallback:
            return int(fallback["id"])
        return 0


def mark_publication_status(
    publication_id: int,
    status: str,
    error: str | None = None,
    result: str | None = None,
    prepared_text: str | None = None,
    published_at: str | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 60,
) -> int:
    with get_connection() as connection:
        normalized = (status or "queued").strip().lower()
        if normalized == "processing":
            connection.execute(
                """
                UPDATE publications
                SET status = ?, worker_id = ?, processing_started_at = CURRENT_TIMESTAMP,
                    lease_until = ?, attempt_count = COALESCE(attempt_count, 0) + 1,
                    error = NULL, result = COALESCE(result, ?), prepared_text = COALESCE(prepared_text, ?)
                WHERE id = ?
                """,
                (normalized, worker_id, (_utc_now() + timedelta(seconds=lease_seconds)).isoformat(), result or "Processamento em andamento", prepared_text or "", publication_id),
            )
            return publication_id

        if normalized == "published":
            connection.execute(
                """
                UPDATE publications
                SET status = ?, result = COALESCE(?, result), error = NULL,
                    published_at = CURRENT_TIMESTAMP, lease_until = NULL,
                    worker_id = ?, processing_started_at = processing_started_at
                WHERE id = ?
                """,
                (normalized, result or "Publicação concluída com sucesso", worker_id, publication_id),
            )
            return publication_id

        if normalized == "simulated":
            connection.execute(
                """
                UPDATE publications
                SET status = ?, result = COALESCE(?, result), error = NULL,
                    published_at = NULL, lease_until = NULL,
                    worker_id = ?, requires_human_action_at = NULL,
                    processing_started_at = processing_started_at
                WHERE id = ?
                """,
                (normalized, result or "Simulação concluída; nada foi publicado no Facebook.", worker_id, publication_id),
            )
            return publication_id

        if normalized == "requires_human_action":
            connection.execute(
                """
                UPDATE publications
                SET status = ?, error = COALESCE(?, error), result = COALESCE(?, result),
                    requires_human_action_at = CURRENT_TIMESTAMP, lease_until = NULL,
                    worker_id = ?, published_at = COALESCE(published_at, published_at)
                WHERE id = ?
                """,
                (normalized, error or "Requer revisão humana.", result or "Ação manual requerida.", worker_id, publication_id),
            )
            return publication_id

        connection.execute(
            """
            UPDATE publications
            SET status = ?, result = COALESCE(?, result), error = ?, prepared_text = COALESCE(?, prepared_text),
                published_at = COALESCE(?, published_at), lease_until = NULL, worker_id = ?, requires_human_action_at = NULL
            WHERE id = ?
            """,
            (normalized, result or "Falha no processamento", error or "Erro não informado.", prepared_text, published_at, worker_id, publication_id),
        )
        return publication_id


def reserve_next_publication(worker_id: str, lease_seconds: int = 60, max_attempts: int = 3) -> dict[str, Any] | None:
    lease_deadline = (_utc_now() + timedelta(seconds=int(lease_seconds))).isoformat()
    with get_connection() as connection:
        if connection.dialect == "postgres":
            blocked = connection.execute(
                """
                SELECT * FROM publications
                WHERE status = 'queued'
                  AND (attempt_count IS NOT NULL AND attempt_count >= %s)
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (int(max_attempts),),
            ).fetchone()
            if blocked:
                connection.execute(
                    """
                    UPDATE publications
                    SET status = 'requires_human_action', worker_id = %s, error = %s,
                        requires_human_action_at = CURRENT_TIMESTAMP, lease_until = NULL,
                        attempt_count = COALESCE(attempt_count, 0)
                    WHERE id = %s
                    """,
                    (worker_id, "Máximo de tentativas atingido. A publicação foi bloqueada para revisão manual.", blocked["id"]),
                )
                return None
            row = connection.execute(
                """
                SELECT * FROM publications
                WHERE status = 'queued'
                  AND (attempt_count IS NULL OR attempt_count < %s)
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (int(max_attempts),),
            ).fetchone()
            if not row:
                return None
            next_attempt = int(row["attempt_count"] or 0) + 1
            connection.execute(
                """
                UPDATE publications
                SET status = 'processing', worker_id = %s, processing_started_at = CURRENT_TIMESTAMP,
                    lease_until = %s, attempt_count = %s, error = NULL
                WHERE id = %s
                """,
                (worker_id, lease_deadline, next_attempt, row["id"]),
            )
            updated = connection.execute("SELECT * FROM publications WHERE id = %s", (row["id"],)).fetchone()
            return row_dict(updated)

        connection._conn.execute("BEGIN IMMEDIATE")
        blocked = connection.execute(
            "SELECT * FROM publications WHERE status = 'queued' AND attempt_count >= ? ORDER BY id LIMIT 1",
            (int(max_attempts),),
        ).fetchone()
        if blocked:
            connection.execute(
                """
                UPDATE publications
                SET status = 'requires_human_action', worker_id = ?, error = ?,
                    requires_human_action_at = CURRENT_TIMESTAMP, lease_until = NULL,
                    attempt_count = COALESCE(attempt_count, 0)
                WHERE id = ?
                """,
                (worker_id, "Máximo de tentativas atingido. A publicação foi bloqueada para revisão manual.", blocked["id"]),
            )
            connection._conn.commit()
            return None
        row = connection.execute(
            "SELECT * FROM publications WHERE status = 'queued' AND (attempt_count IS NULL OR attempt_count < ?) ORDER BY id LIMIT 1",
            (int(max_attempts),),
        ).fetchone()
        if not row:
            connection._conn.rollback()
            return None
        next_attempt = int(row["attempt_count"] or 0) + 1
        connection.execute(
            """
            UPDATE publications
            SET status = 'processing', worker_id = ?, processing_started_at = CURRENT_TIMESTAMP,
                lease_until = ?, attempt_count = ?, error = NULL
            WHERE id = ?
            """,
            (worker_id, lease_deadline, next_attempt, row["id"]),
        )
        updated = connection.execute("SELECT * FROM publications WHERE id = ?", (row["id"],)).fetchone()
        connection._conn.commit()
        return row_dict(updated)


def recover_expired_publication_leases(worker_id: str, lease_seconds: int = 60, max_attempts: int = 3) -> dict[str, Any] | None:
    cutoff = _utc_now().isoformat()
    with get_connection() as connection:
        if connection.dialect == "postgres":
            row = connection.execute(
                """
                SELECT * FROM publications
                WHERE status = 'processing'
                  AND lease_until IS NOT NULL
                  AND lease_until <= %s
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (cutoff,),
            ).fetchone()
            if not row:
                return None
            next_attempt = int(row["attempt_count"] or 0) + 1
            if next_attempt > int(max_attempts):
                connection.execute(
                    """
                    UPDATE publications
                    SET status = 'requires_human_action', worker_id = %s, error = %s,
                        lease_until = NULL, requires_human_action_at = CURRENT_TIMESTAMP,
                        attempt_count = %s
                    WHERE id = %s
                    """,
                    (worker_id, "Lease expirado e limite de tentativas alcançado. Requer revisão manual.", next_attempt, row["id"]),
                )
                return None
            new_lease = (_utc_now() + timedelta(seconds=int(lease_seconds))).isoformat()
            connection.execute(
                """
                UPDATE publications
                SET status = 'processing', worker_id = %s, processing_started_at = CURRENT_TIMESTAMP,
                    lease_until = %s, attempt_count = %s, error = NULL
                WHERE id = %s
                """,
                (worker_id, new_lease, next_attempt, row["id"]),
            )
            updated = connection.execute("SELECT * FROM publications WHERE id = %s", (row["id"],)).fetchone()
            return row_dict(updated)

        connection._conn.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM publications WHERE status = 'processing' AND lease_until IS NOT NULL AND lease_until <= ? ORDER BY id LIMIT 1",
            (cutoff,),
        ).fetchone()
        if not row:
            connection._conn.rollback()
            return None
        next_attempt = int(row["attempt_count"] or 0) + 1
        if next_attempt > int(max_attempts):
            connection.execute(
                """
                UPDATE publications
                SET status = 'requires_human_action', worker_id = ?, error = ?,
                    lease_until = NULL, requires_human_action_at = CURRENT_TIMESTAMP,
                    attempt_count = ?
                WHERE id = ?
                """,
                (worker_id, "Lease expirado e limite de tentativas alcançado. Requer revisão manual.", next_attempt, row["id"]),
            )
            connection._conn.commit()
            return None
        new_lease = (_utc_now() + timedelta(seconds=int(lease_seconds))).isoformat()
        connection.execute(
            """
            UPDATE publications
            SET status = 'processing', worker_id = ?, processing_started_at = CURRENT_TIMESTAMP,
                lease_until = ?, attempt_count = ?, error = NULL
            WHERE id = ?
            """,
            (worker_id, new_lease, next_attempt, row["id"]),
        )
        updated = connection.execute("SELECT * FROM publications WHERE id = ?", (row["id"],)).fetchone()
        connection._conn.commit()
        return row_dict(updated)
