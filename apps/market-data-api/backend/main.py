"""
market-data-api — sole owner of Massive HTTP fetches + market_bars + split universe.

Phase C-3 extraction (per docs/ARCHITECTURE_TARGET.md): the market-data
layer from scanner-api is promoted to its own service. The Python surface
stays identical to the in-process module; the HTTP wrapper here is
intentionally thin.

Architectural law:
    OWNS Massive HTTP fetches (only place MASSIVE_API_KEY is needed).
    OWNS market_bars table writes.
    OWNS split_universe NASDAQ calendar fetch + lifecycle classification.
    NO engine compute. NO scan orchestration. NO dashboard views.

Endpoints:
    GET  /health
    GET  /version
    GET  /api/debug/status
    GET  /api/market-data/bars/{symbol}?tf=1d&days=180  — read cached/fetch
    POST /api/market-data/sync                          — bulk pre-warm
    GET  /api/market-data/split-universe                — NASDAQ split lifecycle list
    GET  /api/market-data/split-flags/{symbol}          — per-ticker split flags

Auth: POST /api/market-data/sync requires x-admin-token (same model as
scanner-api's existing /api/admin/sync-market-data — which now proxies here).
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from . import market_data as _market_data
from . import split_universe as _split
from . import db as _db
from . import massive as _massive

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())

app = FastAPI(title="market-data-api", version="0.1.0")

_VERSION = "0.1.0"
_PHASE   = "C-3 extraction"


# ── DDL (own only the tables this service writes) ────────────────────────────
# market_bars is shared read-only with scanner-api; both run idempotent
# CREATE TABLE IF NOT EXISTS on startup.

_DDL_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_bars (
    symbol     VARCHAR(16)  NOT NULL,
    tf         VARCHAR(8)   NOT NULL,
    ts         TIMESTAMPTZ  NOT NULL,
    open       DOUBLE PRECISION,
    high       DOUBLE PRECISION,
    low        DOUBLE PRECISION,
    close      DOUBLE PRECISION,
    volume     DOUBLE PRECISION,
    adjusted   BOOLEAN      NOT NULL DEFAULT TRUE,
    provider   VARCHAR(16)  NOT NULL DEFAULT 'massive',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, tf, ts, provider, adjusted)
);
CREATE INDEX IF NOT EXISTS idx_mb_symbol_tf_ts ON market_bars(symbol, tf, ts DESC);
CREATE INDEX IF NOT EXISTS idx_mb_updated_at   ON market_bars(updated_at);
"""


def _ensure_schema() -> None:
    if not _db.DATABASE_URL:
        return
    with _db.get_write_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL_SCHEMA)
        conn.commit()


@app.on_event("startup")
def _startup_ensure_schema() -> None:
    try:
        _ensure_schema()
    except Exception as exc:
        log.warning("market-data-api startup schema init failed: %s", exc)


# ── Auth helper ──────────────────────────────────────────────────────────────

def _require_admin(x_admin_token: str) -> None:
    expected = os.environ.get("ADMIN_TOKEN") or os.environ.get("SEED_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN not configured on this service")
    if not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Invalid admin token")


# ── Health / version / debug ─────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "market-data-api"}


@app.get("/version")
def version():
    return {"service": "market-data-api", "version": _VERSION, "phase": _PHASE}


@app.get("/api/debug/status")
def debug_status():
    massive_ok = _massive.massive_available()
    db_configured = bool(_db.DATABASE_URL)
    db_connected, db_error = (False, None)
    if db_configured:
        db_connected, db_error = _db.ping()
    split_cache_warm = bool(_split.split_service._is_cache_valid()
                            and _split.split_service._last_result)
    return {
        "service":              "market-data-api",
        "mode":                 "pure_data_layer",
        "database_configured":  db_configured,
        "database_connected":   db_connected,
        "database_error":       db_error,
        "massive_configured":   massive_ok,
        "split_cache_warm":     split_cache_warm,
        "owns_table":           "market_bars",
        "writes":               ["market_bars"],
        "reads":                ["market_bars"],
        "external_apis":        ["massive", "nasdaq_splits"],
    }


# ── Market bars: read + sync ─────────────────────────────────────────────────

@app.get("/api/market-data/bars/{symbol}")
def get_bars(
    symbol:      str,
    tf:          str  = Query(default="1d"),
    days:        int  = Query(default=180, ge=1, le=3650),
    provider:    str  = Query(default="massive"),
    adjusted:    bool = Query(default=True),
    allow_fetch: bool = Query(default=True),
):
    """Read OHLCV for `symbol`. Cache hit returns immediately. Cache miss
    triggers one Massive fetch + write (when allow_fetch=True), then returns.

    Response shape mirrors what scanner-api's old `from .scan_engine import
    fetch_bars` returned, serialized as records — caller can rebuild the
    DataFrame from {ts, open, high, low, close, volume}.
    """
    df = _market_data.get_bars(
        symbol, tf=tf, days=days,
        provider=provider, adjusted=adjusted, allow_fetch=allow_fetch,
    )
    if df is None or df.empty:
        return {
            "ok":     False,
            "symbol": symbol.upper().strip(),
            "tf":     tf,
            "days":   days,
            "bars":   [],
            "rows":   0,
            "source": "no_data",
        }
    bars = []
    for ts, row in df.iterrows():
        bars.append({
            "ts":     pd.Timestamp(ts).isoformat(),
            "open":   float(row.get("open",   0) or 0),
            "high":   float(row.get("high",   0) or 0),
            "low":    float(row.get("low",    0) or 0),
            "close":  float(row.get("close",  0) or 0),
            "volume": float(row.get("volume", 0) or 0),
        })
    return {
        "ok":     True,
        "symbol": symbol.upper().strip(),
        "tf":     tf,
        "days":   days,
        "rows":   len(bars),
        "bars":   bars,
        "source": "cache_or_massive",
    }


@app.post("/api/market-data/sync")
def post_sync(
    body: dict = Body(default={}),
    x_admin_token: str = Header(default=""),
):
    """
    Bulk sync OHLCV into market_bars. Same body shape as scanner-api's
    legacy /api/admin/sync-market-data (which now proxies here).

    Body (all optional):
      { "symbols": [...], "tf": "1d", "days": 180, "force": false }

    When `symbols` is missing, the operator's scanner-api proxy injects
    the union of its sample lists. This service doesn't know about
    sample lists — it just syncs whatever it's given.
    """
    _require_admin(x_admin_token)
    if not _db.DATABASE_URL:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    _ensure_schema()

    symbols = body.get("symbols")
    if not symbols:
        raise HTTPException(status_code=400, detail="symbols list is required")
    tf    = body.get("tf",    "1d")
    days  = int(body.get("days", 180))
    force = bool(body.get("force", False))

    summary = _market_data.sync_bars(symbols, tf=tf, days=days, force=force)
    summary["source"] = "market-data-api"
    return summary


# ── Split universe ───────────────────────────────────────────────────────────

@app.get("/api/market-data/split-universe")
def get_split_universe():
    """Full reverse-split universe with lifecycle metadata. Cache-warming
    happens lazily on first call (or returns existing cache)."""
    try:
        res = _split.split_service.get_split_universe_result()
        return {
            "ok":                    True,
            "tickers":               res.tickers,
            "rows":                  res.rows,
            "total_events":          res.total_events,
            "reverse_split_events":  res.reverse_split_events,
            "stock_like_events":     res.stock_like_events,
            "filtered_non_stock":    res.filtered_non_stock,
            "generated_at":          res.generated_at,
            "cache_key":             res.cache_key,
        }
    except Exception as exc:
        log.warning("split-universe endpoint failed: %s", exc)
        return {"ok": False, "tickers": [], "rows": [], "error": type(exc).__name__}


@app.get("/api/market-data/split-flags/{symbol}")
def get_split_flags(symbol: str):
    """Per-ticker split lifecycle flags. Used by scan_engine.run_controlled_scan
    via the bridge in scanner-api's market_data_client."""
    try:
        flags = _split.get_split_flags_for_ticker(symbol)
        return {"ok": True, "symbol": symbol.upper().strip(), "flags": flags}
    except Exception as exc:
        log.warning("split-flags failed for %s: %s", symbol, exc)
        return {"ok": False, "symbol": symbol.upper().strip(), "flags": None,
                "error": type(exc).__name__}
