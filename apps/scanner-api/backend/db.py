"""
db.py — Read-only PostgreSQL helper for scanner-api.

Opens one connection per call. No writes. No migrations.
Falls back cleanly if DATABASE_URL is absent or unreachable.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

log = logging.getLogger(__name__)

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")


def _available() -> bool:
    return bool(DATABASE_URL)


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
