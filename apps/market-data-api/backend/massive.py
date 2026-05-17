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


def fetch_splits(
    history_days: int = 90,
    future_days:  int = 14,
    min_ratio:    float = 2.0,
) -> list[dict] | None:
    """
    Fetch reverse-stock-split history from Massive `/v3/reference/splits`.

    One paginated endpoint replaces the legacy 100-HTTP NASDAQ loop. Returns
    a normalized list of {ticker, split_date, ratio, ratio_str, source}
    dicts compatible with split_universe's downstream pipeline.

    Massive's /v3/reference/splits filter semantics:
      execution_date.gte  = inclusive lower bound (YYYY-MM-DD)
      execution_date.lte  = inclusive upper bound
      reverse_split=true  = only reverse splits (split_to < split_from)

    Pagination: response.next_url is followed until exhausted. Page size is
    1000 (Massive max). Typical reverse-split history of 90 days returns
    300–2000 events worldwide, so usually 1–2 pages.

    Returns:
      list[dict]  on success (possibly empty)
      None        on transport error — caller falls back to stale cache
    """
    try:
        key = _massive_key()
    except EnvironmentError as exc:
        log.warning("fetch_splits: %s", exc)
        return None

    now = datetime.now(timezone.utc).date()
    frm = (now - timedelta(days=history_days)).isoformat()
    to  = (now + timedelta(days=future_days)).isoformat()
    url = f"{_MASSIVE_BASE}/v3/reference/splits"
    params = {
        "execution_date.gte": frm,
        "execution_date.lte": to,
        "reverse_split":      "true",
        "limit":              1000,
        "order":              "desc",
        "sort":               "execution_date",
        "apiKey":             key,
    }

    out: list[dict] = []
    pages = 0
    next_url: str | None = url

    while next_url and pages < 10:   # safety cap — 10*1000 = 10k events
        pages += 1
        for attempt in range(3):
            try:
                r = requests.get(next_url, params=params if pages == 1 else None,
                                 timeout=(5, 15))
                if r.status_code == 429:
                    time.sleep(1 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except requests.RequestException as exc:
                if attempt == 2:
                    log.warning("fetch_splits: max retries (page %d): %s", pages, exc)
                    return None if not out else out   # return partial on later-page failure
                time.sleep(2 ** attempt)
        else:
            return None if not out else out

        for row in data.get("results") or []:
            # Massive shape: { ticker, execution_date, split_from, split_to, ... }
            try:
                sf = float(row.get("split_from") or 0)
                st = float(row.get("split_to")   or 0)
            except (TypeError, ValueError):
                continue
            if sf <= 0 or st <= 0:
                continue
            # Reverse split: new shares < old shares, ratio = old/new.
            # /v3/reference/splits with reverse_split=true filters server-side,
            # but we re-check here defensively for old API versions.
            ratio = sf / st if st > 0 else 0
            if ratio < min_ratio:
                continue
            ticker     = (row.get("ticker") or "").upper().strip()
            split_date = row.get("execution_date") or ""
            if not ticker or not split_date:
                continue
            out.append({
                "ticker":     ticker,
                "split_date": split_date,
                "ratio":      ratio,
                "ratio_str":  f"{int(sf)}:{int(st)}" if sf == int(sf) and st == int(st)
                              else f"{sf:g}:{st:g}",
                "source":     "massive",
                # Reference data fields not in /v3/reference/splits — left
                # empty so the downstream stock filter falls through to its
                # name-based heuristics. /v3/reference/tickers can fill these
                # in a follow-up (Phase F-2).
                "companyName":  "",
                "securityName": "",
                "assetType":    "",
                "issueType":    "",
            })

        next_url = data.get("next_url")
        # Massive's next_url already carries the apiKey; don't double-send.
        if next_url and "apiKey=" not in next_url:
            next_url = f"{next_url}&apiKey={key}"

    log.info("fetch_splits: %d reverse-split events over %d pages "
             "(window=%s..%s, min_ratio=%.1f)",
             len(out), pages, frm, to, min_ratio)
    return out


__all__ = ["fetch_bars", "fetch_splits", "massive_available"]
