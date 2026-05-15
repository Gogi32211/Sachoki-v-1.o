"""
chart_obs_migration.py — Creates chart_observations table on startup.

Called from main.py lifespan. Safe to call multiple times (IF NOT EXISTS).
"""
from __future__ import annotations
import logging
from db import get_db, USE_PG

log = logging.getLogger(__name__)

_DDL_PG = """
CREATE TABLE IF NOT EXISTS chart_observations (
    id              SERIAL PRIMARY KEY,
    obs_date        DATE NOT NULL,
    ticker          VARCHAR(20) NOT NULL,
    exchange        VARCHAR(10) NOT NULL DEFAULT 'NQ',
    t_signal        VARCHAR(10),
    z_prev_1        VARCHAR(10),
    z_prev_2        VARCHAR(10),
    t_prev_1        VARCHAR(10),
    sequence_label  VARCHAR(60),
    l_signal        VARCHAR(10),
    gog_signal      VARCHAR(10),
    f_signal        VARCHAR(10),
    lvbo_present    BOOLEAN DEFAULT FALSE,
    eb_reversal     BOOLEAN DEFAULT FALSE,
    vbo_present     BOOLEAN DEFAULT FALSE,
    score_before    INTEGER,
    score_at        INTEGER,
    score_delta     INTEGER,
    turbo_score     INTEGER,
    rtb_phase       VARCHAR(5),
    rtb_total       INTEGER,
    beta_zone       VARCHAR(20),
    sweet_spot      BOOLEAN DEFAULT FALSE,
    entry_price     NUMERIC(12,4),
    k_signal_match  VARCHAR(10),
    k_fired         BOOLEAN DEFAULT FALSE,
    entry_quality   VARCHAR(10),
    notes           TEXT,
    result_5d       NUMERIC(8,4),
    result_10d      NUMERIC(8,4),
    result_outcome  VARCHAR(10),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(obs_date, ticker, t_signal)
);

CREATE INDEX IF NOT EXISTS idx_co_obs_date ON chart_observations(obs_date);
CREATE INDEX IF NOT EXISTS idx_co_ticker   ON chart_observations(ticker);
CREATE INDEX IF NOT EXISTS idx_co_t_signal ON chart_observations(t_signal);
CREATE INDEX IF NOT EXISTS idx_co_k_match  ON chart_observations(k_signal_match);

CREATE TABLE IF NOT EXISTS stock_stat (
    ticker                 VARCHAR(20) NOT NULL,
    date                   DATE        NOT NULL,
    t                      VARCHAR(10),
    z                      VARCHAR(10),
    l                      VARCHAR(10),
    f                      VARCHAR(10),
    g                      VARCHAR(10),
    b                      VARCHAR(10),
    turbo_score            INTEGER,
    rtb_total              INTEGER,
    rtb_phase              VARCHAR(10),
    beta_score             INTEGER,
    beta_zone              VARCHAR(30),
    sweet_spot_active      BOOLEAN,
    signal_score           INTEGER,
    last_price             NUMERIC(14,4),
    ultra_score            INTEGER,
    ultra_score_band_v2    VARCHAR(4),
    ultra_score_priority   VARCHAR(20),
    ultra_score_reasons    TEXT,
    rolling_score_max_5d   NUMERIC(8,2),
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_ss_ticker ON stock_stat(ticker);
CREATE INDEX IF NOT EXISTS idx_ss_date   ON stock_stat(date);

ALTER TABLE stock_stat ADD COLUMN IF NOT EXISTS rolling_score_max_5d NUMERIC(8,2)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS chart_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    obs_date        TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'NQ',
    t_signal        TEXT,
    z_prev_1        TEXT,
    z_prev_2        TEXT,
    t_prev_1        TEXT,
    sequence_label  TEXT,
    l_signal        TEXT,
    gog_signal      TEXT,
    f_signal        TEXT,
    lvbo_present    INTEGER DEFAULT 0,
    eb_reversal     INTEGER DEFAULT 0,
    vbo_present     INTEGER DEFAULT 0,
    score_before    INTEGER,
    score_at        INTEGER,
    score_delta     INTEGER,
    turbo_score     INTEGER,
    rtb_phase       TEXT,
    rtb_total       INTEGER,
    beta_zone       TEXT,
    sweet_spot      INTEGER DEFAULT 0,
    entry_price     REAL,
    k_signal_match  TEXT,
    k_fired         INTEGER DEFAULT 0,
    entry_quality   TEXT,
    notes           TEXT,
    result_5d       REAL,
    result_10d      REAL,
    result_outcome  TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(obs_date, ticker, t_signal)
);

CREATE INDEX IF NOT EXISTS idx_co_obs_date ON chart_observations(obs_date);
CREATE INDEX IF NOT EXISTS idx_co_ticker   ON chart_observations(ticker);
CREATE INDEX IF NOT EXISTS idx_co_t_signal ON chart_observations(t_signal);
CREATE INDEX IF NOT EXISTS idx_co_k_match  ON chart_observations(k_signal_match);

CREATE TABLE IF NOT EXISTS stock_stat (
    ticker                 TEXT NOT NULL,
    date                   TEXT NOT NULL,
    t                      TEXT,
    z                      TEXT,
    l                      TEXT,
    f                      TEXT,
    g                      TEXT,
    b                      TEXT,
    turbo_score            INTEGER,
    rtb_total              INTEGER,
    rtb_phase              TEXT,
    beta_score             INTEGER,
    beta_zone              TEXT,
    sweet_spot_active      INTEGER,
    signal_score           INTEGER,
    last_price             REAL,
    ultra_score            INTEGER,
    ultra_score_band_v2    TEXT,
    ultra_score_priority   TEXT,
    ultra_score_reasons    TEXT,
    rolling_score_max_5d   REAL,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_ss_ticker ON stock_stat(ticker);
CREATE INDEX IF NOT EXISTS idx_ss_date   ON stock_stat(date)
"""


def ensure_chart_obs_tables() -> None:
    """Create chart_observations table if it doesn't exist."""
    ddl = _DDL_PG if USE_PG else _DDL_SQLITE
    try:
        with get_db() as db:
            db.executescript(ddl)
            db.commit()
        log.info("chart_observations table ready")
    except Exception as exc:
        log.error("chart_observations migration failed: %s", exc)
        raise
