"""
db.py — SQLite / PostgreSQL abstraction layer.

Auto-selects backend based on DATABASE_URL env variable:
  DATABASE_URL set  → psycopg2 (PostgreSQL, Railway addon)
  DATABASE_URL unset → sqlite3 at DB_PATH (local dev / fallback)

All fetchone() / fetchall() calls return dicts regardless of backend.
Parameter style: SQLite :name / ? placeholders are auto-converted to
%(name)s / %s for psycopg2.
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

# Validate PG connection at import — fall back to SQLite if DATABASE_URL is
# a placeholder or the host is unreachable.
_pg_ok = False
if DATABASE_URL:
    try:
        import psycopg2 as _psycopg2_test
        _c = _psycopg2_test.connect(DATABASE_URL, connect_timeout=5)
        _c.close()
        _pg_ok = True
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).warning(
            "DATABASE_URL set but PG connection failed (%s) — using SQLite fallback.", _e
        )

def _resolve_db_path() -> str:
    """Return SQLite path: explicit DB_PATH env → /data volume → /tmp fallback."""
    explicit = os.environ.get("DB_PATH")
    if explicit:
        return explicit
    # Auto-detect Railway persistent volume mounted at /data
    if os.path.isdir("/data"):
        return "/data/scanner.db"
    return "/tmp/scanner.db"

DB_PATH: str = _resolve_db_path()
USE_PG: bool = _pg_ok


# ── SQL helpers ───────────────────────────────────────────────────────────────

def _to_pg(sql: str) -> str:
    """Convert SQLite-style placeholders to psycopg2 style.
    :name  →  %(name)s
    ?      →  %s
    """
    sql = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", sql)
    sql = sql.replace("?", "%s")
    return sql


def pk_col() -> str:
    """Auto-increment primary key column definition."""
    return "BIGSERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


# ── Connection wrapper ────────────────────────────────────────────────────────

class Conn:
    """
    Unified connection providing a sqlite3-like API over both sqlite3 and psycopg2.

    execute(sql, params) → self
    executemany(sql, rows)
    executescript(sql)       multi-statement DDL
    fetchone()               → dict | None
    fetchall()               → list[dict]
    lastrowid                → int | None
    table_columns(table)     → set[str]
    commit()
    close()
    """

    def __init__(self) -> None:
        if USE_PG:
            import psycopg2
            import psycopg2.extras
            self._pg  = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            self._pg.autocommit = False
            self._cur = self._pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            self._sqlite = None
        else:
            # Ensure parent directory exists (e.g. /data volume on Railway)
            _db_dir = os.path.dirname(DB_PATH)
            if _db_dir:
                os.makedirs(_db_dir, exist_ok=True)
            self._sqlite = sqlite3.connect(DB_PATH, timeout=30)
            self._sqlite.execute("PRAGMA journal_mode=WAL")
            self._sqlite.execute("PRAGMA busy_timeout=30000")
            self._sqlite.row_factory = sqlite3.Row
            self._pg  = None
            self._cur = None
        self._last: Any = None   # last SQLite cursor result

    # ── Core ──────────────────────────────────────────────────────────────────

    def execute(self, sql: str, params=None) -> "Conn":
        if USE_PG:
            self._cur.execute(_to_pg(sql), params or ())
        else:
            self._last = self._sqlite.execute(sql, params or ())
        return self

    def executemany(self, sql: str, rows) -> "Conn":
        if USE_PG:
            import psycopg2.extras
            psycopg2.extras.execute_batch(self._cur, _to_pg(sql), rows, page_size=200)
        else:
            self._sqlite.executemany(sql, rows)
        return self

    def executescript(self, sql: str) -> None:
        """Execute one or more DDL statements (';' separated)."""
        if USE_PG:
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                self._cur.execute(stmt)
        else:
            self._sqlite.executescript(sql)

    # ── Results ───────────────────────────────────────────────────────────────

    def fetchone(self) -> dict | None:
        if USE_PG:
            row = self._cur.fetchone()
            return dict(row) if row else None
        else:
            row = self._last.fetchone()
            return dict(row) if row else None

    def fetchall(self) -> list[dict]:
        if USE_PG:
            return [dict(r) for r in self._cur.fetchall()]
        else:
            return [dict(r) for r in self._last.fetchall()]

    @property
    def lastrowid(self) -> int | None:
        """
        For PostgreSQL the INSERT must end with RETURNING id.
        For SQLite uses the native lastrowid.
        """
        if USE_PG:
            row = self._cur.fetchone()
            return row["id"] if row else None
        else:
            return self._last.lastrowid

    # ── Schema helpers ────────────────────────────────────────────────────────

    def table_columns(self, table: str) -> set:
        """Return set of existing column names for the given table."""
        if USE_PG:
            self._cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s",
                (table,),
            )
            return {row["column_name"] for row in self._cur.fetchall()}
        else:
            rows = self._sqlite.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
            return {r[1] for r in rows}

    def table_exists(self, table: str) -> bool:
        if USE_PG:
            self._cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                (table,),
            )
            return self._cur.fetchone() is not None
        else:
            rows = self._sqlite.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchall()
            return len(rows) > 0

    # ── Transaction ───────────────────────────────────────────────────────────

    def commit(self) -> None:
        if USE_PG:
            self._pg.commit()
        else:
            self._sqlite.commit()

    def close(self) -> None:
        try:
            if USE_PG:
                self._cur.close()
                self._pg.close()
            else:
                self._sqlite.close()
        except Exception:
            pass

    def __enter__(self) -> "Conn":
        return self

    def __exit__(self, *_) -> None:
        self.close()


def get_db() -> Conn:
    """Open and return a new database connection."""
    return Conn()
