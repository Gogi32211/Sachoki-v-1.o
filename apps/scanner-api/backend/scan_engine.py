"""
scan_engine.py — Phase 5A controlled Ultra scan engine.

Standalone module — zero imports from root backend/.
Uses Massive API (same provider as production) for OHLCV data.
All scores are explicitly marked score_engine="temporary_phase_5A".
No scheduler. No full-market scan. Max 20 symbols.
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
SCORE_ENGINE       = "temporary_phase_5A"

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

        price   = float(close.iloc[-1])
        rsi_now = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None
        ema20_v = float(ema20.iloc[-1])
        ema50_v = float(ema50.iloc[-1])

        ema_cross_up = False
        for i in range(max(1, len(ema20) - 5), len(ema20)):
            if (float(ema20.iloc[i]) > float(ema50.iloc[i]) and
                    float(ema20.iloc[i - 1]) <= float(ema50.iloc[i - 1])):
                ema_cross_up = True
                break

        return {
            "price":             round(price, 2),
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
        "sector":               "",
        "industry":             "",
        # Price / market data
        "price":                signals.get("price"),
        "change_pct":           None,
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
    progress_callback  = None,  # callable(i, sym, results, errors) → None
    cancel_event       = None,  # threading.Event; checked between symbols
) -> dict:
    """
    Run a controlled scan for the given symbol list via Massive API.
    Returns results dict — caller is responsible for DB writes.
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
            df = fetch_bars(sym, interval=timeframe)
            if df is None:
                errors.append({"symbol": sym, "stage": "fetch_bars", "error": "No candles returned"})
                continue
            signals = compute_signals(df)
            if not signals:
                errors.append({"symbol": sym, "stage": "compute_signals", "error": "Signal computation failed"})
                continue
            candidate = score_candidate(sym, signals)
            candidate["timeframe"] = timeframe
            results.append(candidate)
            log.info("scanned %s → score=%s band=%s", sym, candidate["ultra_score"], candidate["band"])
        except Exception as exc:
            log.warning("scan error for %s: %s", sym, exc)
            errors.append({"symbol": sym, "stage": "unknown", "error": type(exc).__name__})

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    results.sort(key=lambda x: x.get("ultra_score", 0), reverse=True)

    return {
        "results":           results,
        "errors":            errors,
        "symbols_requested": len(symbols),
        "symbols_scanned":   len(results),
        "symbols_failed":    len(errors),
        "candidates_saved":  len(results),
        "elapsed_ms":        elapsed_ms,
        "started_at":        started.isoformat(),
        "score_engine":      SCORE_ENGINE,
        "universe":          universe,
        "timeframe":         timeframe,
        "scan_mode":         scan_mode,
        "cancelled":         cancelled,
    }
