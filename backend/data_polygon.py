"""
data_polygon.py — Massive (formerly Polygon.io) OHLCV data fetcher.

API key is read from the MASSIVE_API_KEY environment variable
(POLYGON_API_KEY also accepted for backwards compatibility).

Base URL: https://api.massive.com  (fallback: https://api.polygon.io)

Interval mapping:
  "1d"  → 1 day       "1wk" / "1w" → 1 week
  "4h"  → 4 hour      "1h"  → 1 hour
  "30m" → 30 minute   "15m" → 15 minute

Usage:
    from data_polygon import fetch_bars
    df = fetch_bars("AAPL", interval="1d", days=180)
"""
from __future__ import annotations

import os
import time
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests

log = logging.getLogger(__name__)

# Massive (new name) first, Polygon (old name) as fallback
_BASE = os.environ.get("MASSIVE_BASE", "https://api.massive.com")

# Valid US common-stock ticker: 1-5 uppercase letters, optional hyphen+letter
# Rejects: "CFLT B" (space = secondary class), "BF.B" (dot = preferred),
#          "NWS/A" (slash = unit), pure numbers, warrants, etc.
import re as _re
_VALID_TICKER_RE = _re.compile(r'^[A-Z]{1,5}(-[A-Z]{1,2})?$')

def _is_valid_stock_ticker(sym: str) -> bool:
    """Return True only for plain US common-stock primary-listing tickers."""
    return bool(_VALID_TICKER_RE.match(sym))

_SPAN = {
    "1d":  (1, "day"),
    "1wk": (1, "week"),
    "1w":  (1, "week"),
    "4h":  (4, "hour"),
    "1h":  (1, "hour"),
    "30m": (30, "minute"),
    "15m": (15, "minute"),
}

# conservative: Starter plan is unlimited but burst-friendly
_RATE_DELAY = 0.08   # ~12 req/s across workers


def _key() -> str:
    # check both env var names
    k = (os.environ.get("MASSIVE_API_KEY") or
         os.environ.get("POLYGON_API_KEY") or "")
    if not k:
        raise EnvironmentError("MASSIVE_API_KEY not set")
    return k


def fetch_bars(
    ticker: str,
    interval: str = "1d",
    days: int = 180,
) -> pd.DataFrame:
    """
    Fetch OHLCV bars from Massive/Polygon.
    Returns a DataFrame with columns open/high/low/close/volume indexed by UTC datetime.
    Raises on network error or empty result.
    """
    mult, span = _SPAN.get(interval, (1, "day"))
    now  = datetime.now(timezone.utc)
    frm  = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    to   = now.strftime("%Y-%m-%d")
    url  = f"{_BASE}/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{frm}/{to}"

    params = {
        "adjusted": "true",
        "sort":     "asc",
        "limit":    50000,
        "apiKey":   _key(),
    }

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=(5, 10))
            if r.status_code == 429:
                wait = 1 * (attempt + 1)  # 1s, 2s, 3s — keep workers moving
                log.debug("Polygon rate-limit %s, waiting %ds", ticker, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException as exc:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"Polygon: max retries reached for {ticker}")

    results = data.get("results") or []
    if not results:
        raise ValueError(f"Polygon: no data for {ticker} ({interval})")

    df = pd.DataFrame(results)
    df = df.rename(columns={
        "o": "open", "h": "high", "l": "low",
        "c": "close", "v": "volume", "t": "timestamp",
    })
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated()].sort_index()

    time.sleep(_RATE_DELAY)  # gentle pacing across concurrent workers
    return df


def polygon_available() -> bool:
    """True if MASSIVE_API_KEY (or POLYGON_API_KEY) is set in environment."""
    return bool(os.environ.get("MASSIVE_API_KEY") or
                os.environ.get("POLYGON_API_KEY"))


def _fetch_ticker_page(url: str, params: dict, timeout: int = 15) -> dict:
    """GET one page of ticker results with up to 3 retries on timeout/connection error."""
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=(5, timeout))
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if attempt == 2:
                raise
            wait = 2 ** attempt  # 1s, 2s
            log.warning("Massive ticker page timeout (attempt %d/3), retry in %ds: %s", attempt + 1, wait, exc)
            time.sleep(wait)


def get_all_us_tickers(market: str = "stocks", limit: int = 10_000) -> list[str]:
    """
    Fetch active US common stocks from Massive API sorted by market cap desc.
    Excludes ETFs, warrants, preferred shares (type=CS only).
    Requires MASSIVE_API_KEY.
    """
    tickers: list[str] = []
    url = f"{_BASE}/v3/reference/tickers"
    params = {
        "market":  market,
        "locale":  "us",
        "active":  "true",
        "type":    "CS",
        "limit":   1000,
        "apiKey":  _key(),
    }
    while url and len(tickers) < limit:
        data = _fetch_ticker_page(url, params)
        for t in data.get("results", []):
            sym = t.get("ticker", "")
            if sym and _is_valid_stock_ticker(sym):
                tickers.append(sym)
        url    = data.get("next_url")
        params = {"apiKey": _key()}
        time.sleep(0.15)

    log.info("Massive: fetched %d tickers (All US)", len(tickers))
    return tickers[:limit]


def get_exchange_tickers(exchange: str, limit: int = 5_000) -> list[str]:
    """
    Fetch active US common stocks from a specific exchange via Massive API.
    exchange examples: "XNAS" (NASDAQ), "XNYS" (NYSE), "XASE" (NYSE American).
    Requires MASSIVE_API_KEY.
    """
    tickers: list[str] = []
    url = f"{_BASE}/v3/reference/tickers"
    params = {
        "market":   "stocks",
        "locale":   "us",
        "active":   "true",
        "type":     "CS",
        "exchange": exchange,
        "limit":    1000,
        "apiKey":   _key(),
    }
    while url and len(tickers) < limit:
        data = _fetch_ticker_page(url, params)
        for t in data.get("results", []):
            sym = t.get("ticker", "")
            if sym and _is_valid_stock_ticker(sym):
                tickers.append(sym)
        url    = data.get("next_url")
        params = {"apiKey": _key()}
        time.sleep(0.15)

    log.info("Massive: fetched %d tickers (exchange=%s)", len(tickers), exchange)
    return tickers[:limit]
