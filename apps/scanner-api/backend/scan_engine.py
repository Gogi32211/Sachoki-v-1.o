"""
scan_engine.py — Phase 7A controlled Ultra scan engine.

Standalone module — zero imports from root backend/.
Uses Massive API (same provider as production) for OHLCV data.
Supports scoring_mode: "temporary" (rule-based), "real" (ultra_score),
or "compare" (both, stores comparison data).
No scheduler. No full-market scan. Max 500 symbols.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd
import requests

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_SYMBOLS: list[str] = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMD",
    "PLTR", "SOFI", "COIN", "META", "GOOGL",
]
MAX_SYMBOLS        = 20
ALLOWED_TIMEFRAMES = ["1d"]
SCORE_ENGINE_TEMP  = "temporary_phase_5A"
SCORE_ENGINE_REAL  = "real_ultra_score"
SCORE_ENGINE       = SCORE_ENGINE_TEMP  # kept for legacy reference; actual label set per run

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


# ── Candle fetch (Massive) ────────────────────────────────────────────────────

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

    time.sleep(0.08)  # gentle pacing
    return df


# ── Splits fetch (Massive — Phase F-1) ────────────────────────────────────────

def fetch_splits(
    history_days: int = 90,
    future_days:  int = 14,
    min_ratio:    float = 2.0,
) -> list[dict] | None:
    """
    Fetch reverse-stock-split events from Massive /v3/reference/splits.

    Replaces the legacy NASDAQ 100-HTTP loop with a single paginated call.
    Same shape as market-data-api's fetch_splits — duplicated here so
    scanner-api's in-process split_universe fallback doesn't need
    market-data-api running locally.

    Returns:
      list[dict]  on success (possibly empty)
      None        on transport / auth error
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
    while next_url and pages < 10:
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
                    return None if not out else out
                time.sleep(2 ** attempt)
        else:
            return None if not out else out

        for row in data.get("results") or []:
            try:
                sf = float(row.get("split_from") or 0)
                st = float(row.get("split_to")   or 0)
            except (TypeError, ValueError):
                continue
            if sf <= 0 or st <= 0:
                continue
            ratio = sf / st if st > 0 else 0
            if ratio < min_ratio:
                continue
            ticker     = (row.get("ticker") or "").upper().strip()
            split_date = row.get("execution_date") or ""
            if not ticker or not split_date:
                continue
            out.append({
                "ticker":       ticker,
                "split_date":   split_date,
                "ratio":        ratio,
                "ratio_str":    f"{int(sf)}:{int(st)}" if sf == int(sf) and st == int(st)
                                else f"{sf:g}:{st:g}",
                "source":       "massive",
                "companyName":  "",
                "securityName": "",
                "assetType":    "",
                "issueType":    "",
            })

        next_url = data.get("next_url")
        if next_url and "apiKey=" not in next_url:
            next_url = f"{next_url}&apiKey={key}"

    log.info("fetch_splits: %d events (%d pages, window=%s..%s)",
             len(out), pages, frm, to)
    return out


# ── Ticker reference (Massive — Phase F-2) ────────────────────────────────────

def fetch_tickers(
    exchange:  str | None = None,    # "XNAS" | "XNYS" | None=any
    ticker_type: str = "CS",         # CS = Common Stock; ETF / ADRC / WARRANT etc.
    market:    str = "stocks",
    active:    bool = True,
    max_pages: int = 12,             # 12 * 1000 = 12000 tickers ceiling
) -> list[str] | None:
    """
    Pull active ticker symbols from Massive /v3/reference/tickers.

    Default filter (CS + stocks + active=true) gives the real listed-stock
    universe — what most "scan everything" workflows actually want. ETFs,
    warrants, preferred shares, units etc. are excluded by default.

    Returns:
      list[str]   sorted, deduped tickers on success (possibly empty)
      None        on auth / transport error — caller falls back to curated sample
    """
    try:
        key = _massive_key()
    except EnvironmentError as exc:
        log.warning("fetch_tickers: %s", exc)
        return None

    url = f"{_MASSIVE_BASE}/v3/reference/tickers"
    params = {
        "market":  market,
        "active":  "true" if active else "false",
        "type":    ticker_type,
        "limit":   1000,
        "order":   "asc",
        "sort":    "ticker",
        "apiKey":  key,
    }
    if exchange:
        params["exchange"] = exchange

    out: list[str] = []
    pages = 0
    next_url: str | None = url
    while next_url and pages < max_pages:
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
                    log.warning("fetch_tickers: max retries (page %d): %s", pages, exc)
                    return None if not out else out
                time.sleep(2 ** attempt)
        else:
            return None if not out else out

        for row in data.get("results") or []:
            t = (row.get("ticker") or "").upper().strip()
            # Skip OTC / pink-sheet rubbish — Massive sometimes returns 5-char
            # OTC tickers ending in F/Y/Q. Limit to canonical 1–5 letter US
            # symbols, optionally with -A/-B share class suffix.
            if t and _VALID_TICKER_RE.match(t):
                out.append(t)

        next_url = data.get("next_url")
        if next_url and "apiKey=" not in next_url:
            next_url = f"{next_url}&apiKey={key}"

    out = sorted(set(out))
    log.info("fetch_tickers: %d active %s on %s (pages=%d)",
             len(out), ticker_type, exchange or "ANY", pages)
    return out


# ── Per-ticker details (Massive — Phase F-3) ──────────────────────────────────

def fetch_ticker_details(symbol: str) -> dict | None:
    """
    Fetch one ticker's full reference record from Massive
    `/v3/reference/tickers/{symbol}`.

    Returns a normalized dict with the fields we actually need for sector
    classification + display. None on transport / 404 / auth error.

    Shape:
      {ticker, name, primary_exchange, sic_code, sic_description,
       market_cap, total_employees, list_date, type, is_active, currency}
    """
    sym = symbol.upper().strip()
    if not _VALID_TICKER_RE.match(sym):
        return None
    try:
        key = _massive_key()
    except EnvironmentError as exc:
        log.warning("fetch_ticker_details: %s", exc)
        return None

    url = f"{_MASSIVE_BASE}/v3/reference/tickers/{sym}"
    params = {"apiKey": key}

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=(5, 10))
            if r.status_code == 429:
                time.sleep(1 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException as exc:
            if attempt == 2:
                log.debug("fetch_ticker_details %s: max retries: %s", sym, exc)
                return None
            time.sleep(2 ** attempt)
    else:
        return None

    res = data.get("results") or {}
    if not res:
        return None
    return {
        "ticker":           (res.get("ticker") or sym).upper(),
        "name":             res.get("name") or "",
        "primary_exchange": res.get("primary_exchange") or "",
        "sic_code":         res.get("sic_code") or "",
        "sic_description":  res.get("sic_description") or "",
        "market_cap":       res.get("market_cap"),
        "total_employees":  res.get("total_employees"),
        "list_date":        res.get("list_date") or "",
        "type":             res.get("type") or "",
        "is_active":        bool(res.get("active", True)),
        "currency":         res.get("currency_name") or "",
    }


# ── Signal computation ────────────────────────────────────────────────────────

def compute_signals(df: pd.DataFrame) -> dict:
    """
    Compute a minimal signal set from OHLCV DataFrame (lowercase columns).
    Returns empty dict on any computation error.
    """
    try:
        import numpy as np

        close  = df["close"]
        volume = df["volume"]

        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = 100 - (100 / (1 + rs))

        vol_avg = volume.rolling(20).mean()
        vol_avg_last = float(vol_avg.iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / vol_avg_last if vol_avg_last > 0 else 1.0

        mom5 = 0.0
        if len(close) >= 6:
            mom5 = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) * 100

        price      = float(close.iloc[-1])
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else None
        change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else None
        rsi_now    = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None
        ema20_v    = float(ema20.iloc[-1])
        ema50_v    = float(ema50.iloc[-1])

        ema_cross_up = False
        for i in range(max(1, len(ema20) - 5), len(ema20)):
            if (float(ema20.iloc[i]) > float(ema50.iloc[i]) and
                    float(ema20.iloc[i - 1]) <= float(ema50.iloc[i - 1])):
                ema_cross_up = True
                break

        return {
            "price":             round(price, 2),
            "prev_close":        round(prev_close, 2) if prev_close is not None else None,
            "change_pct":        change_pct,
            "rsi":               round(rsi_now, 1) if rsi_now is not None else None,
            "ema20":             round(ema20_v, 2),
            "ema50":             round(ema50_v, 2),
            "vol_ratio":         round(vol_ratio, 2),
            "mom5d_pct":         round(mom5, 2),
            "price_above_ema20": bool(price > ema20_v),
            "price_above_ema50": bool(price > ema50_v),
            "ema20_above_ema50": bool(ema20_v > ema50_v),
            "ema_cross_up_5d":   bool(ema_cross_up),
        }
    except Exception as exc:
        log.warning("compute_signals error: %s", exc)
        return {}


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_candidate(symbol: str, signals: dict) -> dict:
    """
    Minimal rule-based scorer. Returns 0-100 score.
    Explicitly marked score_engine=temporary_phase_5A.
    """
    score = 30
    why: list[str]   = []
    flags: list[str] = []

    pa20   = signals.get("price_above_ema20", False)
    e20e50 = signals.get("ema20_above_ema50", False)
    pa50   = signals.get("price_above_ema50", False)
    rsi    = signals.get("rsi") or 50.0
    vr     = signals.get("vol_ratio", 1.0) or 1.0
    mom5   = signals.get("mom5d_pct", 0.0) or 0.0
    cross  = signals.get("ema_cross_up_5d", False)

    if pa20 and e20e50:
        score += 20
        why.append("UPTREND:EMA_ALIGNED")
    elif pa20:
        score += 8
        why.append("ABOVE_EMA20")
    if not pa50 and not pa20:
        score -= 15
        flags.append("BELOW_EMA50")

    if cross:
        score += 12
        why.append("EMA20_CROSS_UP_5D")

    if vr >= 2.0:
        score += 12
        why.append(f"VOL_SURGE:{vr:.1f}x")
    elif vr >= 1.5:
        score += 6
        why.append(f"HIGH_VOLUME:{vr:.1f}x")

    if 55 <= rsi <= 70:
        score += 8
        why.append(f"RSI_HEALTHY:{rsi:.0f}")
    elif rsi > 75:
        score -= 10
        flags.append(f"RSI_EXTENDED:{rsi:.0f}")
    elif rsi < 40:
        score -= 8
        flags.append(f"RSI_WEAK:{rsi:.0f}")

    if mom5 >= 5.0:
        score += 10
        why.append(f"MOM5D:{mom5:.1f}%")
    elif mom5 >= 2.0:
        score += 5
        why.append(f"MOM5D:{mom5:.1f}%")
    elif mom5 <= -5.0:
        score -= 8
        flags.append(f"MOM5D_NEG:{mom5:.1f}%")

    score = max(0, min(100, score))

    if score >= 80:
        band, priority = "A+", "HIGH_PRIORITY"
    elif score >= 65:
        band, priority = "A",  "WATCH_A"
    elif score >= 50:
        band, priority = "B",  "STRONG_WATCH"
    elif score >= 35:
        band, priority = "C",  "CONTEXT_WATCH"
    else:
        band, priority = "D",  "LOW"

    return {
        # Identity
        "symbol":               symbol,
        "ticker":               symbol,
        "company":              "",
        "sector":               "",      # filled by run_controlled_scan via sector_map
        "industry":             "",      # filled by run_controlled_scan via sector_map
        # Price / market data
        "price":                signals.get("price"),
        "prev_close":           signals.get("prev_close"),
        "change_pct":           signals.get("change_pct"),
        "volume":               None,
        # Scores
        "ultra_score":          score,
        "ultra_score_band_v2":  band,
        "ultra_score_band":     band,
        "band":                 band,
        "ultra_score_priority": priority,
        "priority":             priority,
        # Signals
        "ema20":                signals.get("ema20"),
        "ema50":                signals.get("ema50"),
        "rsi":                  signals.get("rsi"),
        "vol_ratio":            signals.get("vol_ratio"),
        "mom5d_pct":            signals.get("mom5d_pct"),
        # Reasons / flags
        "ultra_score_reasons":  why[:5],
        "why_selected":         why[:5],
        "ultra_score_flags":    flags[:3],
        "risk_flags":           flags[:3],
        # Signal slots (empty — phase 5A engine)
        "final_signal":         "",
        "action_bucket":        "",
        "sequence_4bar":        "",
        "abr_category":         "",
        "wlnbb_bucket":         "",
        "ema_state":            "",
        # Metadata
        "score_engine":         SCORE_ENGINE,
        "data_provider":        "massive",
        "source":               "scanner-api-controlled-scan",
    }


# ── Main scan ─────────────────────────────────────────────────────────────────

def run_controlled_scan(
    symbols:           list[str],
    timeframe:         str  = "1d",
    universe:          str  = "manual_test",
    scan_mode:         str  = "controlled_test",
    scoring_mode:      str  = "temporary",   # temporary | real | compare
    progress_callback  = None,  # callable(i, sym, results, errors) → None
    cancel_event       = None,  # threading.Event; checked between symbols
) -> dict:
    """
    Run a controlled scan for the given symbol list via Massive API.
    Returns results dict — caller is responsible for DB writes.
    scoring_mode: "temporary" = rule-based, "real" = Ultra scorer, "compare" = both.
    progress_callback is called before each symbol with current counts.
    cancel_event is checked between symbols; sets result["cancelled"]=True if fired.
    """
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()

    results: list[dict] = []
    errors:  list[dict] = []
    cancelled = False

    for i, symbol in enumerate(symbols):
        # ── Cancel check ──────────────────────────────────────────────────────
        if cancel_event and cancel_event.is_set():
            log.info("Scan cancel_event set at symbol %d/%d — stopping.", i, len(symbols))
            cancelled = True
            break

        # ── Progress callback ─────────────────────────────────────────────────
        if progress_callback:
            try:
                progress_callback(i, symbol, results, errors)
            except Exception:
                pass

        sym = symbol.upper().strip()
        try:
            # Phase C-1: read-through cache. First scan of a symbol/tf hits
            # Massive once and writes market_bars; subsequent scans (same day,
            # same window) read straight from Postgres → score iterations are
            # nearly free. If DATABASE_URL is missing or the cache table is
            # absent, get_bars falls back transparently to direct Massive
            # fetch_bars (legacy behavior).
            try:
                # Phase C-3: route through market_data_client. When
                # MARKET_DATA_API_URL is set, this becomes an HTTP call to
                # the standalone market-data-api service; otherwise it
                # falls back to the in-process market_data.py module.
                from . import market_data_client as _mkt
                df = _mkt.get_bars(sym, tf=timeframe, days=180)
            except Exception as exc:
                log.warning("market_data_client.get_bars failed for %s, falling back to fetch_bars: %s", sym, exc)
                df = fetch_bars(sym, interval=timeframe)
            if df is None:
                errors.append({"symbol": sym, "stage": "fetch_bars", "error": "No candles returned"})
                continue
            signals = compute_signals(df)
            if not signals:
                errors.append({"symbol": sym, "stage": "compute_signals", "error": "Signal computation failed"})
                continue

            # Phase 8G commit 3: run the unified engine registry so we have a
            # real per-bar signal payload to persist alongside the score.
            # Failures are non-fatal; we still produce a scored candidate.
            normalized_bars: list[dict] = []
            try:
                # Phase B-2: route through engine_client. When
                # ENGINE_API_URL is set, this becomes an HTTP call to the
                # standalone engine-api service; otherwise it falls back
                # to the in-process engine_api/ subpackage. Same return
                # shape either way.
                from .engine_client import run_engines as _run_engines
                try:
                    # Phase C-3: split flags also routed through client.
                    from . import market_data_client as _mkt_split
                    _split_flags = _mkt_split.get_split_flags_for_ticker(sym)
                except Exception as exc:
                    log.debug("split flag lookup failed for %s: %s", sym, exc)
                    _split_flags = None
                normalized_bars = _run_engines(
                    ticker=sym, timeframe=timeframe, df=df,
                    split_flags=_split_flags,
                )
            except Exception as exc:
                log.warning("engine_registry failed for %s: %s", sym, exc)

            latest_bar = normalized_bars[-1] if normalized_bars else None

            if scoring_mode == "real":
                from .scoring_adapter import compute_scanner_ultra_candidate
                candidate = compute_scanner_ultra_candidate(
                    sym, signals, timeframe=timeframe, df=df, latest_bar=latest_bar,
                )
            elif scoring_mode == "compare":
                from .scoring_adapter import compute_scanner_ultra_candidate
                temp = score_candidate(sym, signals)
                real = compute_scanner_ultra_candidate(
                    sym, signals, timeframe=timeframe, df=df,
                    temp_candidate=temp, latest_bar=latest_bar,
                )
                candidate = real.copy()
                candidate["compare"] = {
                    "temp_score":  temp["ultra_score"],
                    "temp_band":   temp["band"],
                    "temp_why":    temp.get("why_selected", []),
                    "real_score":  real["ultra_score"],
                    "real_band":   real["band"],
                    "real_why":    real.get("why_selected", []),
                    "delta_score": real["ultra_score"] - temp["ultra_score"],
                }
            else:  # temporary (default)
                candidate = score_candidate(sym, signals)

            # Always attach the normalized scanner payload so Ultra latest,
            # filters, and exports read the same shape Super Chart reads.
            if latest_bar is not None:
                candidate["signals"]    = latest_bar.get("signals")    or {}
                candidate["indicators"] = latest_bar.get("indicators") or {}
                candidate["ohlcv"]      = latest_bar.get("ohlcv")      or {}
                candidate["scores_obj"] = latest_bar.get("scores")     or {}
                candidate["roles"]      = latest_bar.get("roles")      or {}
                candidate["split"]      = latest_bar.get("split")      or {}
                candidate["engine_debug"] = latest_bar.get("engine_debug") or {}
                candidate["bar_date"]   = latest_bar.get("date")
                candidate["bar_datetime"] = latest_bar.get("datetime")
            else:
                # Engines couldn't run — empty signal slots, not silently absent.
                from .engine_api import (
                    empty_signals, empty_scores, empty_roles, empty_split,
                )
                candidate["signals"]    = empty_signals()
                candidate["indicators"] = {}
                candidate["ohlcv"]      = {}
                candidate["scores_obj"] = empty_scores()
                candidate["roles"]      = empty_roles()
                candidate["split"]      = empty_split()
                candidate["engine_debug"] = {"engines_ran": [], "engines_failed": [],
                                             "warnings": ["registry_unavailable"]}

            candidate["timeframe"] = timeframe
            # Sector enrichment — static map, never raises
            # Phase F-3: read from Postgres ticker_reference first (Massive-
            # sourced, ~5500 tickers covered), fall back to static sector_map.
            from .ticker_reference import get_sector_info
            sector_info = get_sector_info(sym)
            candidate["sector"]   = sector_info["sector"]
            candidate["industry"] = sector_info["industry"]
            results.append(candidate)
            log.info("scanned %s → score=%s band=%s sector=%s engine=%s",
                     sym, candidate["ultra_score"], candidate["band"],
                     sector_info["sector"], scoring_mode)
        except Exception as exc:
            log.warning("scan error for %s: %s", sym, exc)
            errors.append({"symbol": sym, "stage": "unknown", "error": type(exc).__name__})

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    results.sort(key=lambda x: x.get("ultra_score", 0), reverse=True)

    engine_label = {
        "real":    SCORE_ENGINE_REAL,
        "compare": f"{SCORE_ENGINE_TEMP}+{SCORE_ENGINE_REAL}",
    }.get(scoring_mode, SCORE_ENGINE_TEMP)

    return {
        "results":           results,
        "errors":            errors,
        "symbols_requested": len(symbols),
        "symbols_scanned":   len(results),
        "symbols_failed":    len(errors),
        "candidates_saved":  len(results),
        "elapsed_ms":        elapsed_ms,
        "started_at":        started.isoformat(),
        "score_engine":      engine_label,
        "scoring_mode":      scoring_mode,
        "universe":          universe,
        "timeframe":         timeframe,
        "scan_mode":         scan_mode,
        "cancelled":         cancelled,
    }
