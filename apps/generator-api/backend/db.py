"""
db.py — Read-only PostgreSQL helper for scanner-api.

Opens one connection per call. No writes. No migrations.
Falls back cleanly if DATABASE_URL is absent or unreachable.

Architecture rule: every endpoint that needs a database calls require_db()
at its top. That's the ONE place "is the DB usable?" is decided. Do not
sprinkle `if not _db.DATABASE_URL: return 503` across endpoints.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

from fastapi import HTTPException

log = logging.getLogger(__name__)

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")


def _available() -> bool:
    return bool(DATABASE_URL)


def require_db() -> None:
    """
    Endpoint guard. Raises 503 HTTPException if DATABASE_URL is not set.

    Endpoints that need a DB write or read should call this at the top —
    one line, no boilerplate response body. Centralized so the wording,
    HTTP code, and behavior (e.g. logging, retry-after header) live in
    one place forever.
    """
    if not DATABASE_URL:
        raise HTTPException(
            status_code=503,
            detail={
                "ok":         False,
                "error":      "DATABASE_URL not configured",
                "error_code": "DB_NOT_CONFIGURED",
            },
        )


def ping() -> tuple[bool, str]:
    """Return (connected, error_message). Never raises."""
    if not DATABASE_URL:
        return False, "DATABASE_URL not set"
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        conn.close()
        return True, ""
    except Exception as exc:
        return False, type(exc).__name__


@contextmanager
def get_conn() -> Generator:
    """
    Yield a psycopg2 RealDictCursor. Read-only — callers must not commit.
    Raises RuntimeError if DATABASE_URL is not set.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not configured")
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    conn.set_session(readonly=True, autocommit=True)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


def list_tables() -> list[dict]:
    """Return all tables in the public schema. [{schema, table}]"""
    with get_conn() as cur:
        cur.execute(
            """
            SELECT table_schema AS schema, table_name AS table
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
            """
        )
        return [dict(r) for r in cur.fetchall()]


def table_exists(name: str) -> bool:
    with get_conn() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s",
            (name,),
        )
        return cur.fetchone() is not None


@contextmanager
def get_write_conn() -> Generator:
    """
    Yield a writable psycopg2 connection (autocommit=False).
    Caller must explicitly commit or rollback. Used only by scan execution.
    Raises RuntimeError if DATABASE_URL is not set.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not configured")
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    conn.autocommit = False
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
