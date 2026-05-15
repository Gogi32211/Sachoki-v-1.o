"""
signal_replay_migration.py — Creates Signal Replay metadata tables.

Hybrid storage architecture:
  signal_replay_runs   — one row per run (metadata + summary)
  replay_artifacts     — artifact registry (paths + sizes for parquet/json files on disk)

Heavy data (events, outcomes, statistics) lives on disk as Parquet files.
Set REPLAY_STORAGE_DIR env var to a persistent mount (e.g. /data/replay_runs).
"""
from __future__ import annotations
import logging
from db import get_db, USE_PG

log = logging.getLogger(__name__)

_DDL_RUNS_PG = """
CREATE TABLE IF NOT EXISTS signal_replay_runs (
    id                    SERIAL PRIMARY KEY,
    status                VARCHAR(20) NOT NULL DEFAULT 'running',
    mode                  VARCHAR(20) NOT NULL,
    universe              VARCHAR(20) NOT NULL,
    timeframe             VARCHAR(8)  NOT NULL DEFAULT '1d',
    as_of_date            DATE,
    start_date            DATE,
    end_date              DATE,
    event_scope           VARCHAR(40) DEFAULT 'all_signals',
    min_price             NUMERIC(10,4),
    min_volume            BIGINT,
    min_dollar_volume     NUMERIC(18,2),
    benchmark_symbol      VARCHAR(10) DEFAULT 'QQQ',
    total_days            INTEGER DEFAULT 0,
    days_completed        INTEGER DEFAULT 0,
    total_symbols         INTEGER DEFAULT 0,
    symbols_completed     INTEGER DEFAULT 0,
    total_events          INTEGER DEFAULT 0,
    total_outcomes        INTEGER DEFAULT 0,
    total_statistics_rows INTEGER DEFAULT 0,
    settings_json         TEXT,
    error_message         TEXT,
    storage_mode             VARCHAR(20) DEFAULT 'parquet',
    fetch_bars               INTEGER,
    outcome_forward_bars     INTEGER,
    warmup_bars              INTEGER,
    artifact_status_json     TEXT,
    context_limitations_json TEXT,
    started_at               TIMESTAMPTZ DEFAULT NOW(),
    finished_at              TIMESTAMPTZ,
    created_at               TIMESTAMPTZ DEFAULT NOW(),
    updated_at               TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_srr_status     ON signal_replay_runs(status);
CREATE INDEX IF NOT EXISTS idx_srr_created_at ON signal_replay_runs(created_at);
"""

_DDL_ARTIFACTS_PG = """
CREATE TABLE IF NOT EXISTS replay_artifacts (
    id             SERIAL PRIMARY KEY,
    run_id         INTEGER NOT NULL,
    artifact_type  VARCHAR(40) NOT NULL,
    file_path      TEXT NOT NULL,
    format         VARCHAR(20) DEFAULT 'parquet',
    row_count      BIGINT DEFAULT 0,
    size_bytes     BIGINT DEFAULT 0,
    schema_version INTEGER DEFAULT 1,
    sha256         VARCHAR(64),
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ra_run          ON replay_artifacts(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ra_run_type ON replay_artifacts(run_id, artifact_type);
"""

_DDL_RUNS_SQLITE = """
CREATE TABLE IF NOT EXISTS signal_replay_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    status                TEXT NOT NULL DEFAULT 'running',
    mode                  TEXT NOT NULL,
    universe              TEXT NOT NULL,
    timeframe             TEXT NOT NULL DEFAULT '1d',
    as_of_date            TEXT,
    start_date            TEXT,
    end_date              TEXT,
    event_scope           TEXT DEFAULT 'all_signals',
    min_price             REAL,
    min_volume            INTEGER,
    min_dollar_volume     REAL,
    benchmark_symbol      TEXT DEFAULT 'QQQ',
    total_days            INTEGER DEFAULT 0,
    days_completed        INTEGER DEFAULT 0,
    total_symbols         INTEGER DEFAULT 0,
    symbols_completed     INTEGER DEFAULT 0,
    total_events          INTEGER DEFAULT 0,
    total_outcomes        INTEGER DEFAULT 0,
    total_statistics_rows INTEGER DEFAULT 0,
    settings_json         TEXT,
    error_message         TEXT,
    storage_mode             TEXT DEFAULT 'parquet',
    fetch_bars               INTEGER,
    outcome_forward_bars     INTEGER,
    warmup_bars              INTEGER,
    artifact_status_json     TEXT,
    context_limitations_json TEXT,
    started_at               TEXT DEFAULT (datetime('now')),
    finished_at              TEXT,
    created_at               TEXT DEFAULT (datetime('now')),
    updated_at               TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_srr_status     ON signal_replay_runs(status);
CREATE INDEX IF NOT EXISTS idx_srr_created_at ON signal_replay_runs(created_at);
"""

_DDL_ARTIFACTS_SQLITE = """
CREATE TABLE IF NOT EXISTS replay_artifacts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL,
    artifact_type  TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    format         TEXT DEFAULT 'parquet',
    row_count      INTEGER DEFAULT 0,
    size_bytes     INTEGER DEFAULT 0,
    schema_version INTEGER DEFAULT 1,
    sha256         TEXT,
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ra_run          ON replay_artifacts(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ra_run_type ON replay_artifacts(run_id, artifact_type);
"""


def _add_extended_columns_if_missing(db) -> None:
    """Add columns introduced for the fetch_bars / context_limitations feature."""
    new_cols = [
        ("fetch_bars",               "INTEGER"  if USE_PG else "INTEGER"),
        ("outcome_forward_bars",     "INTEGER"  if USE_PG else "INTEGER"),
        ("warmup_bars",              "INTEGER"  if USE_PG else "INTEGER"),
        ("artifact_status_json",     "TEXT"),
        ("context_limitations_json", "TEXT"),
    ]
    for col, dtype in new_cols:
        try:
            if USE_PG:
                db.execute(
                    f"ALTER TABLE signal_replay_runs "
                    f"ADD COLUMN IF NOT EXISTS {col} {dtype}"
                )
            else:
                db.execute(
                    f"ALTER TABLE signal_replay_runs ADD COLUMN {col} {dtype}"
                )
        except Exception:
            pass  # Column already exists (SQLite raises an error; PG uses IF NOT EXISTS)


def _add_storage_mode_column_if_missing(db) -> None:
    """Add storage_mode column to signal_replay_runs if it was created before this migration.

    Backfills existing rows with 'postgres' so history correctly identifies
    pre-migration runs that stored data in DB tables (no parquet files).
    """
    if USE_PG:
        try:
            db.execute(
                "ALTER TABLE signal_replay_runs "
                "ADD COLUMN IF NOT EXISTS storage_mode VARCHAR(20) DEFAULT 'postgres'"
            )
        except Exception:
            pass
    else:
        try:
            db.execute(
                "ALTER TABLE signal_replay_runs ADD COLUMN storage_mode TEXT DEFAULT 'postgres'"
            )
        except Exception:
            pass  # Column already exists in SQLite


def ensure_signal_replay_tables() -> None:
    """Create Signal Replay tables if they don't exist. Safe to call multiple times."""
    if USE_PG:
        ddl_runs      = _DDL_RUNS_PG
        ddl_artifacts = _DDL_ARTIFACTS_PG
    else:
        ddl_runs      = _DDL_RUNS_SQLITE
        ddl_artifacts = _DDL_ARTIFACTS_SQLITE

    try:
        with get_db() as db:
            db.executescript(ddl_runs)
            db.executescript(ddl_artifacts)
            _add_storage_mode_column_if_missing(db)
            _add_extended_columns_if_missing(db)
            db.commit()
        log.info("signal_replay tables ready (parquet storage mode)")
    except Exception as exc:
        log.error("signal_replay migration failed: %s", exc)
        raise
