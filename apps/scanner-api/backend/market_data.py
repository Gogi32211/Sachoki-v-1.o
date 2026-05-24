"""
market_data.py — Phase C-1: read-through cache for OHLCV candles.

Architecture role (per docs/ARCHITECTURE_TARGET.md):
    This module is the eventual market-data-api service running as a module
    inside scanner-api today. Its public surface (get_bars, sync_bars) is the
    same surface that will be exposed via HTTP when this is extracted into
    apps/market-data-api/ later.

Storage:
    PostgreSQL table `market_bars` (schema in main.py _DDL_SCHEMA).
    Primary key (symbol, tf, ts, provider, adjusted) — same bar from
    different providers / adjusted modes is allowed; idempotent re-sync of
    the same bar overwrites OHLCV via ON CONFLICT.

Two public functions:

    sync_bars(symbols, tf, days, force=False) -> dict
        Fetch missing bars from Massive, write to market_bars. Idempotent.
        force=True wipes the cache window for those symbols/tf and re-fetches.

    get_bars(symbol, tf, days) -> pd.DataFrame | None
        Read-through cache. Returns DataFrame with same shape as
        scan_engine.fetch_bars: lowercase ohlcv cols, UTC datetime index.
        On cache miss for the requested window, calls Massive once, writes
        to cache, returns. On Massive failure with NO cached data, returns
        None.

This is the ONE place fetch-from-Massive lives. Everything else in scanner-api
goes through get_bars().
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Iterable

import pandas as pd

from . import db as _db
from . import scan_engine as _scan

log = logging.getLogger(__name__)


# ── Read ──────────────────────────────────────────────────────────────────────

def get_bars(
    symbol: str,
    tf: str = "1d",
    days: int = 180,
    *,
    provider: str = "massive",
    adjusted: bool = True,
    allow_fetch: bool = True,
) -> pd.DataFrame | None:
    """
    Return OHLCV for the last `days` calendar days as a pd.DataFrame indexed
    by UTC timestamp with columns open/high/low/close/volume.

    Read-through behavior:
      1. Read everything we have for (symbol, tf, provider, adjusted) from DB
         within the window.
      2. If we have ANY bars, return them as-is. Background sync handles
         catch-up. This is the hot path for re-scans.
      3. If we have NO bars and allow_fetch=True, call Massive once,
         write what came back, return it.
      4. If Massive fails and we have no cache, return None.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return None

    if not _db.DATABASE_URL:
        # No DB → fall back to direct Massive fetch (legacy behavior).
        return _scan.fetch_bars(sym, interval=tf, days=days) if allow_fetch else None

    cached = _read_window(sym, tf, days, provider, adjusted)
    if cached is not None and len(cached) > 0:
        return cached

    if not allow_fetch:
        return None

    # Cold cache → fetch + write + return.
    df = _scan.fetch_bars(sym, interval=tf, days=days)
    if df is None or len(df) == 0:
        return None
    try:
        _upsert(sym, tf, df, provider=provider, adjusted=adjusted)
    except Exception as exc:
        log.warning("market_bars upsert failed for %s/%s: %s", sym, tf, exc)
    return df


def _read_window(
    symbol: str, tf: str, days: int, provider: str, adjusted: bool,
) -> pd.DataFrame | None:
    """SELECT bars for the trailing `days`-day window. Returns None on DB error."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        with _db.get_conn() as cur:
            cur.execute(
                """
                SELECT ts, open, high, low, close, volume
                FROM market_bars
                WHERE symbol=%s AND tf=%s AND provider=%s AND adjusted=%s AND ts >= %s
                ORDER BY ts ASC
                """,
                (symbol, tf, provider, adjusted, since),
            )
            rows = cur.fetchall()
    except Exception as exc:
        log.debug("market_bars read failed for %s/%s: %s", symbol, tf, exc)
        return None

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]) \
        if rows and not isinstance(rows[0], dict) else pd.DataFrame(rows)
    if "ts" not in df.columns:
        return None
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


# ── Write ─────────────────────────────────────────────────────────────────────

def _upsert(
    symbol: str,
    tf: str,
    df: pd.DataFrame,
    *,
    provider: str = "massive",
    adjusted: bool = True,
) -> int:
    """
    UPSERT bars into market_bars. Returns count of rows written.

    Uses ON CONFLICT to make re-sync idempotent: same bar re-fetched from
    Massive overwrites the cached OHLCV (in case Massive corrects a bar).
    """
    if df is None or df.empty:
        return 0
    if not _db.DATABASE_URL:
        return 0

    rows = []
    for ts, r in df.iterrows():
        try:
            rows.append((
                symbol, tf,
                pd.Timestamp(ts).to_pydatetime(),
                float(r.get("open",   0) or 0),
                float(r.get("high",   0) or 0),
                float(r.get("low",    0) or 0),
                float(r.get("close",  0) or 0),
                float(r.get("volume", 0) or 0),
                adjusted, provider,
            ))
        except Exception:
            continue
    if not rows:
        return 0

    import psycopg2.extras
    with _db.get_write_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO market_bars
                    (symbol, tf, ts, open, high, low, close, volume, adjusted, provider)
                VALUES %s
                ON CONFLICT (symbol, tf, ts, provider, adjusted) DO UPDATE SET
                    open       = EXCLUDED.open,
                    high       = EXCLUDED.high,
                    low        = EXCLUDED.low,
                    close      = EXCLUDED.close,
                    volume     = EXCLUDED.volume,
                    updated_at = NOW()
                """,
                rows,
            )
        conn.commit()
    return len(rows)


# ── Bulk sync ────────────────────────────────────────────────────────────────

def sync_bars(
    symbols: Iterable[str],
    tf: str = "1d",
    days: int = 180,
    *,
    provider: str = "massive",
    adjusted: bool = True,
    force: bool = False,
) -> dict:
    """
    Sync OHLCV for `symbols`/tf into the `market_bars` cache.

    For each symbol:
      - if force=True, fetch from Massive and overwrite the window.
      - else, fetch only if cache for that window is empty.

    Returns a summary dict.
    """
    syms = [s.upper().strip() for s in symbols if s]
    summary = {
        "symbols_requested": len(syms),
        "synced_from_massive": 0,
        "cache_hit": 0,
        "failed": 0,
        "rows_written": 0,
        "tf": tf,
        "days": days,
        "force": bool(force),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    if not _db.DATABASE_URL:
        summary["error"] = "DATABASE_URL not configured"
        return summary

    for sym in syms:
        if not force:
            cached = _read_window(sym, tf, days, provider, adjusted)
            if cached is not None and len(cached) > 0:
                summary["cache_hit"] += 1
                continue

        df = _scan.fetch_bars(sym, interval=tf, days=days)
        if df is None or df.empty:
            summary["failed"] += 1
            continue
        try:
            n = _upsert(sym, tf, df, provider=provider, adjusted=adjusted)
            summary["synced_from_massive"] += 1
            summary["rows_written"]        += n
        except Exception as exc:
            log.warning("sync_bars upsert failed for %s: %s", sym, exc)
            summary["failed"] += 1

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary


__all__ = ["get_bars", "sync_bars"]
