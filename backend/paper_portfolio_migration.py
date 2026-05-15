"""
paper_portfolio_migration.py — Creates paper portfolio tables on startup.

Called from main.py lifespan. Safe to call multiple times (IF NOT EXISTS).
"""
from __future__ import annotations
import logging
from db import get_db, USE_PG

log = logging.getLogger(__name__)

_DDL_PG = """
CREATE TABLE IF NOT EXISTS paper_portfolio (
    id                      SERIAL PRIMARY KEY,
    signal_date             DATE NOT NULL,
    ticker                  VARCHAR(20) NOT NULL,
    exchange                VARCHAR(10) NOT NULL DEFAULT 'NQ',
    ultra_score             INTEGER,
    ultra_band              VARCHAR(5),
    ultra_priority          VARCHAR(30),
    beta_score              INTEGER,
    beta_zone               VARCHAR(20),
    tz_sig                  VARCHAR(10),
    turbo_score             INTEGER,
    rtb_total               INTEGER,
    rtb_phase               VARCHAR(5),
    sweet_spot              BOOLEAN,
    tier                    VARCHAR(10),
    signal_reasons          TEXT,
    signal_price            NUMERIC(12,4),
    signal_change_pct       NUMERIC(8,4),
    entry_date              DATE,
    entry_price             NUMERIC(12,4),
    tp_parabolic            NUMERIC(12,4),
    sl_price                NUMERIC(12,4),
    tp_wide                 NUMERIC(12,4),
    hold_days               INTEGER DEFAULT 10,
    max_exit_date           DATE,
    exit_date_p             DATE,
    exit_price_p            NUMERIC(12,4),
    exit_reason_p           VARCHAR(20),
    realized_return_p       NUMERIC(8,4),
    exit_date_w             DATE,
    exit_price_w            NUMERIC(12,4),
    exit_reason_w           VARCHAR(20),
    realized_return_w       NUMERIC(8,4),
    status                  VARCHAR(20) DEFAULT 'PENDING',
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pp_signal_date  ON paper_portfolio(signal_date);
CREATE INDEX IF NOT EXISTS idx_pp_ticker       ON paper_portfolio(ticker);
CREATE INDEX IF NOT EXISTS idx_pp_status       ON paper_portfolio(status);
CREATE INDEX IF NOT EXISTS idx_pp_beta_zone    ON paper_portfolio(beta_zone);
CREATE INDEX IF NOT EXISTS idx_pp_tz_sig       ON paper_portfolio(tz_sig);

CREATE TABLE IF NOT EXISTS paper_daily_prices (
    id          SERIAL PRIMARY KEY,
    ticker      VARCHAR(20) NOT NULL,
    price_date  DATE NOT NULL,
    open        NUMERIC(12,4),
    high        NUMERIC(12,4),
    low         NUMERIC(12,4),
    close       NUMERIC(12,4),
    volume      BIGINT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ticker, price_date)
);

CREATE INDEX IF NOT EXISTS idx_pdp_ticker_date ON paper_daily_prices(ticker, price_date)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS paper_portfolio (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date             TEXT NOT NULL,
    ticker                  TEXT NOT NULL,
    exchange                TEXT NOT NULL DEFAULT 'NQ',
    ultra_score             INTEGER,
    ultra_band              TEXT,
    ultra_priority          TEXT,
    beta_score              INTEGER,
    beta_zone               TEXT,
    tz_sig                  TEXT,
    turbo_score             INTEGER,
    rtb_total               INTEGER,
    rtb_phase               TEXT,
    sweet_spot              INTEGER,
    tier                    TEXT,
    signal_reasons          TEXT,
    signal_price            REAL,
    signal_change_pct       REAL,
    entry_date              TEXT,
    entry_price             REAL,
    tp_parabolic            REAL,
    sl_price                REAL,
    tp_wide                 REAL,
    hold_days               INTEGER DEFAULT 10,
    max_exit_date           TEXT,
    exit_date_p             TEXT,
    exit_price_p            REAL,
    exit_reason_p           TEXT,
    realized_return_p       REAL,
    exit_date_w             TEXT,
    exit_price_w            REAL,
    exit_reason_w           TEXT,
    realized_return_w       REAL,
    status                  TEXT DEFAULT 'PENDING',
    notes                   TEXT,
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pp_signal_date  ON paper_portfolio(signal_date);
CREATE INDEX IF NOT EXISTS idx_pp_ticker       ON paper_portfolio(ticker);
CREATE INDEX IF NOT EXISTS idx_pp_status       ON paper_portfolio(status);
CREATE INDEX IF NOT EXISTS idx_pp_beta_zone    ON paper_portfolio(beta_zone);
CREATE INDEX IF NOT EXISTS idx_pp_tz_sig       ON paper_portfolio(tz_sig);

CREATE TABLE IF NOT EXISTS paper_daily_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    price_date  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, price_date)
);

CREATE INDEX IF NOT EXISTS idx_pdp_ticker_date ON paper_daily_prices(ticker, price_date)
"""


def ensure_paper_portfolio_tables() -> None:
    """Create paper_portfolio and paper_daily_prices tables if they don't exist."""
    ddl = _DDL_PG if USE_PG else _DDL_SQLITE
    try:
        with get_db() as db:
            db.executescript(ddl)
            db.commit()
        log.info("paper_portfolio tables ready")
    except Exception as exc:
        log.error("paper_portfolio migration failed: %s", exc)
        raise
