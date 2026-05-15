"""
ultra_scan_migration.py — Creates Ultra Scan live-results persistence tables.

Two tables:
  ultra_scan_runs       — one row per completed scan run (universe/tf/nasdaq_batch)
  ultra_scan_candidates — all candidate rows from a scan run (full row_json)

Only one run per (universe, tf, nasdaq_batch) has is_latest=true at a time.
Atomic replace: old is_latest row stays visible until the new run succeeds,
then a single transaction flips is_latest.

This is separate from the ULTRA Pump Research archive (ultra_pump_runs /
ultra_research_episodes / …). This table holds live scanner snapshots only.
"""
from __future__ import annotations
import logging
from db import get_db, USE_PG

log = logging.getLogger(__name__)

_DDL_PG = """
CREATE TABLE IF NOT EXISTS ultra_scan_runs (
    id               SERIAL PRIMARY KEY,
    universe         VARCHAR(20) NOT NULL DEFAULT 'sp500',
    tf               VARCHAR(10) NOT NULL DEFAULT '1d',
    nasdaq_batch     VARCHAR(20) NOT NULL DEFAULT '',
    status           VARCHAR(20) NOT NULL DEFAULT 'running',
    is_latest        BOOLEAN NOT NULL DEFAULT FALSE,
    total_candidates INTEGER DEFAULT 0,
    last_turbo_scan  TEXT,
    sources_json     TEXT,
    warnings_json    TEXT,
    meta_json        TEXT,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usr_univ_tf    ON ultra_scan_runs(universe, tf, nasdaq_batch);
CREATE INDEX IF NOT EXISTS idx_usr_is_latest  ON ultra_scan_runs(is_latest);
CREATE INDEX IF NOT EXISTS idx_usr_status     ON ultra_scan_runs(status);
CREATE INDEX IF NOT EXISTS idx_usr_created_at ON ultra_scan_runs(created_at);

CREATE TABLE IF NOT EXISTS ultra_scan_candidates (
    id          BIGSERIAL PRIMARY KEY,
    scan_run_id INTEGER NOT NULL REFERENCES ultra_scan_runs(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    ultra_score REAL DEFAULT 0,
    row_json    TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usc_run_id ON ultra_scan_candidates(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_usc_ticker ON ultra_scan_candidates(ticker);
CREATE INDEX IF NOT EXISTS idx_usc_score  ON ultra_scan_candidates(scan_run_id, ultra_score DESC);
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS ultra_scan_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    universe         TEXT NOT NULL DEFAULT 'sp500',
    tf               TEXT NOT NULL DEFAULT '1d',
    nasdaq_batch     TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'running',
    is_latest        INTEGER NOT NULL DEFAULT 0,
    total_candidates INTEGER DEFAULT 0,
    last_turbo_scan  TEXT,
    sources_json     TEXT,
    warnings_json    TEXT,
    meta_json        TEXT,
    started_at       TEXT DEFAULT (datetime('now')),
    finished_at      TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_usr_univ_tf    ON ultra_scan_runs(universe, tf, nasdaq_batch);
CREATE INDEX IF NOT EXISTS idx_usr_is_latest  ON ultra_scan_runs(is_latest);
CREATE INDEX IF NOT EXISTS idx_usr_status     ON ultra_scan_runs(status);
CREATE INDEX IF NOT EXISTS idx_usr_created_at ON ultra_scan_runs(created_at);

CREATE TABLE IF NOT EXISTS ultra_scan_candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id INTEGER NOT NULL REFERENCES ultra_scan_runs(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    ultra_score REAL DEFAULT 0,
    row_json    TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_usc_run_id ON ultra_scan_candidates(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_usc_ticker ON ultra_scan_candidates(ticker);
CREATE INDEX IF NOT EXISTS idx_usc_score  ON ultra_scan_candidates(scan_run_id, ultra_score DESC);
"""


def ensure_ultra_scan_tables() -> None:
    """Create Ultra Scan persistence tables if missing. Safe to call repeatedly."""
    ddl = _DDL_PG if USE_PG else _DDL_SQLITE
    try:
        with get_db() as db:
            db.executescript(ddl)
            db.commit()
        log.info("ultra_scan tables ready")
    except Exception as exc:
        log.error("ultra_scan migration failed: %s", exc)
        raise
