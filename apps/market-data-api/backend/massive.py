"""
massive.py — sole Massive API client for market-data-api.

This is the ONE place in the architecture that talks to Massive. Lift-and-
shift of fetch_bars from scanner-api/backend/scan_engine.py — verbatim, only
the module location changes. scanner-api keeps its own copy for in-process
fallback when MARKET_DATA_API_URL is unset.

After Phase C-3 verification on staging, MASSIVE_API_KEY should be removed
from scanner-api's env vars entirely (only market-data-api needs it).

No yfinance. No other providers. No fallbacks to alternative data sources.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests

log = logging.getLogger(__name__)

_MASSIVE_BASE = os.environ.get("MASSIVE_BASE", "https://api.massive.com")
_SPAN = {
    "1d":  (1, "day"),
    "1wk": (1, "week"),
    "4h":  (4, "hour"),
    "1h":  (1, "hour"),
}
_VALID_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _massive_key() -> str:
    k = os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY") or ""
    if not k:
        raise EnvironmentError("MASSIVE_API_KEY not set")
    return k


def massive_available() -> bool:
    return bool(os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY"))


def fetch_bars(symbol: str, interval: str = "1d", days: int = 180) -> pd.DataFrame | None:
    """
    Fetch OHLCV bars from Massive API. Returns None on any error.
    Columns: open, high, low, close, volume (lowercase). Index: UTC datetime.
    """
    sym = symbol.upper().strip()
    if not _VALID_TICKER_RE.match(sym):
        log.warning("fetch_bars: invalid ticker format: %s", sym)
        return None

    mult, span = _SPAN.get(interval, (1, "day"))
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    to  = now.strftime("%Y-%m-%d")
    url = f"{_MASSIVE_BASE}/v2/aggs/ticker/{sym}/range/{mult}/{span}/{frm}/{to}"

    try:
        key = _massive_key()
    except EnvironmentError as exc:
        log.warning("fetch_bars: %s", exc)
        return None

    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key}

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=(5, 10))
            if r.status_code == 429:
                time.sleep(1 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException as exc:
            if attempt == 2:
                log.warning("fetch_bars: max retries for %s: %s", sym, exc)
                return None
            time.sleep(2 ** attempt)
    else:
        log.warning("fetch_bars: max retries exhausted for %s", sym)
        return None

    results = data.get("results") or []
    if not results:
        log.warning("fetch_bars: no data for %s", sym)
        return None

    df = pd.DataFrame(results).rename(columns={
        "o": "open", "h": "high", "l": "low",
        "c": "close", "v": "volume", "t": "timestamp",
    })
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated()].sort_index()

    if len(df) < 10:
        log.warning("fetch_bars: insufficient rows (%d) for %s", len(df), sym)
        return None

    time.sleep(0.08)
    return df


__all__ = ["fetch_bars", "massive_available"]
