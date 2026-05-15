"""
ultra_pump_migration.py — Creates ULTRA Pump Research metadata tables.

Hybrid storage: Postgres holds the lightweight `ultra_pump_runs` row + reuses
`replay_artifacts` for artifact registry. Heavy parquet data lives on disk
under REPLAY_STORAGE_DIR/run_XXXXXX.

v2: adds persistent research archive tables:
  - ultra_research_episodes   (one row per pump episode per run)
  - ultra_research_patterns   (pattern stats per run)
  - ultra_research_bundles    (full research_bundle JSON per run)
"""
from __future__ import annotations
import logging
from db import get_db, USE_PG

log = logging.getLogger(__name__)

_DDL_PG = """
CREATE TABLE IF NOT EXISTS ultra_pump_runs (
    id                              SERIAL PRIMARY KEY,
    status                          VARCHAR(20) NOT NULL DEFAULT 'running',
    universe                        VARCHAR(20) NOT NULL DEFAULT 'all_us',
    mode                            VARCHAR(20) NOT NULL DEFAULT 'date_range',
    pump_target                     VARCHAR(20) NOT NULL DEFAULT 'X2_TO_X4',
    start_date                      DATE,
    end_date                        DATE,
    pump_horizon                    INTEGER NOT NULL DEFAULT 60,
    pre_pump_window_bars            INTEGER NOT NULL DEFAULT 14,
    scanner_detection_window_bars   INTEGER NOT NULL DEFAULT 14,
    detection_reference             VARCHAR(40) NOT NULL DEFAULT 'before_first_x2_else_before_peak',
    lookback_bars                   INTEGER NOT NULL DEFAULT 500,
    split_impact_window_days        INTEGER NOT NULL DEFAULT 30,
    research_mode                   VARCHAR(20) DEFAULT 'standard',
    event_scope                     VARCHAR(40) DEFAULT 'pumps_only',
    min_price                       NUMERIC(10,4),
    min_volume                      BIGINT,
    min_dollar_volume               NUMERIC(18,2),
    benchmark_symbol                VARCHAR(10) DEFAULT 'QQQ',
    settings_json                   TEXT,
    summary_json                    TEXT,
    artifact_manifest_json          TEXT,
    error_message                   TEXT,
    storage_mode                    VARCHAR(20) DEFAULT 'parquet',
    total_episodes                  INTEGER DEFAULT 0,
    total_caught                    INTEGER DEFAULT 0,
    total_missed                    INTEGER DEFAULT 0,
    symbols_total                   INTEGER DEFAULT 0,
    symbols_completed               INTEGER DEFAULT 0,
    started_at                      TIMESTAMPTZ DEFAULT NOW(),
    finished_at                     TIMESTAMPTZ,
    created_at                      TIMESTAMPTZ DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_upr_status     ON ultra_pump_runs(status);
CREATE INDEX IF NOT EXISTS idx_upr_created_at ON ultra_pump_runs(created_at);

CREATE TABLE IF NOT EXISTS ultra_research_episodes (
    id                              BIGSERIAL PRIMARY KEY,
    run_id                          INTEGER NOT NULL REFERENCES ultra_pump_runs(id) ON DELETE CASCADE,
    episode_id                      TEXT,
    symbol                          TEXT,
    universe                        TEXT,
    category                        TEXT,
    anchor_date                     TEXT,
    anchor_close                    NUMERIC(18,6),
    peak_date                       TEXT,
    peak_high                       NUMERIC(18,6),
    max_gain_pct                    NUMERIC(12,4),
    first_x2_date                   TEXT,
    days_to_peak                    INTEGER,
    days_to_first_x2                INTEGER,
    max_drawdown_before_peak_pct    NUMERIC(12,4),
    pre_pump_window_start_date      TEXT,
    pre_pump_window_end_date        TEXT,
    pre_pump_window_bars            INTEGER,
    scanner_detection_window_bars   INTEGER,
    pump_horizon                    INTEGER,
    caught_status                   TEXT,
    caught_bar_offset_from_anchor   INTEGER,
    strongest_pre_pump_score        NUMERIC(12,4),
    split_status                    TEXT,
    extra_json                      TEXT,
    created_at                      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ure_run_id    ON ultra_research_episodes(run_id);
CREATE INDEX IF NOT EXISTS idx_ure_symbol    ON ultra_research_episodes(symbol);
CREATE INDEX IF NOT EXISTS idx_ure_category  ON ultra_research_episodes(category);

CREATE TABLE IF NOT EXISTS ultra_research_patterns (
    id                              BIGSERIAL PRIMARY KEY,
    run_id                          INTEGER NOT NULL REFERENCES ultra_pump_runs(id) ON DELETE CASCADE,
    pattern_key                     TEXT,
    pattern_type                    TEXT,
    pump_count                      INTEGER,
    pump_episode_coverage_pct       NUMERIC(12,4),
    baseline_count                  INTEGER,
    baseline_frequency_pct          NUMERIC(12,4),
    lift_vs_baseline                NUMERIC(12,4),
    odds_ratio                      NUMERIC(12,4),
    precision                       NUMERIC(12,6),
    recall                          NUMERIC(12,6),
    false_positive_rate             NUMERIC(12,6),
    median_future_gain              NUMERIC(12,4),
    median_days_to_peak             NUMERIC(12,4),
    median_drawdown_before_peak     NUMERIC(12,4),
    lift_all                        NUMERIC(12,4),
    lift_clean_non_split            NUMERIC(12,4),
    lift_split_related              NUMERIC(12,4),
    lift_post_reverse_split         NUMERIC(12,4),
    extra_json                      TEXT,
    created_at                      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_urp_run_id ON ultra_research_patterns(run_id);

CREATE TABLE IF NOT EXISTS ultra_research_bundles (
    run_id                          INTEGER PRIMARY KEY REFERENCES ultra_pump_runs(id) ON DELETE CASCADE,
    bundle_json                     TEXT NOT NULL,
    created_at                      TIMESTAMPTZ DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ DEFAULT NOW()
);
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS ultra_pump_runs (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    status                          TEXT NOT NULL DEFAULT 'running',
    universe                        TEXT NOT NULL DEFAULT 'all_us',
    mode                            TEXT NOT NULL DEFAULT 'date_range',
    pump_target                     TEXT NOT NULL DEFAULT 'X2_TO_X4',
    start_date                      TEXT,
    end_date                        TEXT,
    pump_horizon                    INTEGER NOT NULL DEFAULT 60,
    pre_pump_window_bars            INTEGER NOT NULL DEFAULT 14,
    scanner_detection_window_bars   INTEGER NOT NULL DEFAULT 14,
    detection_reference             TEXT NOT NULL DEFAULT 'before_first_x2_else_before_peak',
    lookback_bars                   INTEGER NOT NULL DEFAULT 500,
    split_impact_window_days        INTEGER NOT NULL DEFAULT 30,
    research_mode                   TEXT DEFAULT 'standard',
    event_scope                     TEXT DEFAULT 'pumps_only',
    min_price                       REAL,
    min_volume                      INTEGER,
    min_dollar_volume               REAL,
    benchmark_symbol                TEXT DEFAULT 'QQQ',
    settings_json                   TEXT,
    summary_json                    TEXT,
    artifact_manifest_json          TEXT,
    error_message                   TEXT,
    storage_mode                    TEXT DEFAULT 'parquet',
    total_episodes                  INTEGER DEFAULT 0,
    total_caught                    INTEGER DEFAULT 0,
    total_missed                    INTEGER DEFAULT 0,
    symbols_total                   INTEGER DEFAULT 0,
    symbols_completed               INTEGER DEFAULT 0,
    started_at                      TEXT DEFAULT (datetime('now')),
    finished_at                     TEXT,
    created_at                      TEXT DEFAULT (datetime('now')),
    updated_at                      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_upr_status     ON ultra_pump_runs(status);
CREATE INDEX IF NOT EXISTS idx_upr_created_at ON ultra_pump_runs(created_at);

CREATE TABLE IF NOT EXISTS ultra_research_episodes (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                          INTEGER NOT NULL REFERENCES ultra_pump_runs(id) ON DELETE CASCADE,
    episode_id                      TEXT,
    symbol                          TEXT,
    universe                        TEXT,
    category                        TEXT,
    anchor_date                     TEXT,
    anchor_close                    REAL,
    peak_date                       TEXT,
    peak_high                       REAL,
    max_gain_pct                    REAL,
    first_x2_date                   TEXT,
    days_to_peak                    INTEGER,
    days_to_first_x2                INTEGER,
    max_drawdown_before_peak_pct    REAL,
    pre_pump_window_start_date      TEXT,
    pre_pump_window_end_date        TEXT,
    pre_pump_window_bars            INTEGER,
    scanner_detection_window_bars   INTEGER,
    pump_horizon                    INTEGER,
    caught_status                   TEXT,
    caught_bar_offset_from_anchor   INTEGER,
    strongest_pre_pump_score        REAL,
    split_status                    TEXT,
    extra_json                      TEXT,
    created_at                      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ure_run_id    ON ultra_research_episodes(run_id);
CREATE INDEX IF NOT EXISTS idx_ure_symbol    ON ultra_research_episodes(symbol);
CREATE INDEX IF NOT EXISTS idx_ure_category  ON ultra_research_episodes(category);

CREATE TABLE IF NOT EXISTS ultra_research_patterns (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                          INTEGER NOT NULL REFERENCES ultra_pump_runs(id) ON DELETE CASCADE,
    pattern_key                     TEXT,
    pattern_type                    TEXT,
    pump_count                      INTEGER,
    pump_episode_coverage_pct       REAL,
    baseline_count                  INTEGER,
    baseline_frequency_pct          REAL,
    lift_vs_baseline                REAL,
    odds_ratio                      REAL,
    precision                       REAL,
    recall                          REAL,
    false_positive_rate             REAL,
    median_future_gain              REAL,
    median_days_to_peak             REAL,
    median_drawdown_before_peak     REAL,
    lift_all                        REAL,
    lift_clean_non_split            REAL,
    lift_split_related              REAL,
    lift_post_reverse_split         REAL,
    extra_json                      TEXT,
    created_at                      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_urp_run_id ON ultra_research_patterns(run_id);

CREATE TABLE IF NOT EXISTS ultra_research_bundles (
    run_id                          INTEGER PRIMARY KEY REFERENCES ultra_pump_runs(id) ON DELETE CASCADE,
    bundle_json                     TEXT NOT NULL,
    created_at                      TEXT DEFAULT (datetime('now')),
    updated_at                      TEXT DEFAULT (datetime('now'))
);
"""


def ensure_ultra_pump_tables() -> None:
    """Create ULTRA Pump Research tables if missing. Safe to call repeatedly."""
    ddl = _DDL_PG if USE_PG else _DDL_SQLITE
    try:
        with get_db() as db:
            db.executescript(ddl)
            db.commit()
        log.info("ultra_pump tables ready")
    except Exception as exc:
        log.error("ultra_pump migration failed: %s", exc)
        raise
