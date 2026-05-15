"""
scan_engine.py — Phase 5A controlled Ultra scan engine.

Standalone module — zero imports from root backend/.
Uses yfinance for candle data and computes a minimal signal set.
All scores are explicitly marked score_engine="temporary_phase_5A".
No scheduler. No full-market scan. Max 20 symbols.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_SYMBOLS: list[str] = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMD",
    "PLTR", "SOFI", "COIN", "META", "GOOGL",
]
MAX_SYMBOLS      = 20
ALLOWED_TIMEFRAMES = ["1d"]
SCORE_ENGINE     = "temporary_phase_5A"


# ── Candle fetch ──────────────────────────────────────────────────────────────

def fetch_candles(symbol: str, interval: str = "1d") -> Any | None:
    """
    Fetch OHLCV DataFrame via yfinance (~60 bars). Returns None on any error.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo", interval=interval)
        if df is None or df.empty or len(df) < 10:
            log.warning("fetch_candles: insufficient data for %s", symbol)
            return None
        return df
    except Exception as exc:
        log.warning("fetch_candles error for %s: %s", symbol, exc)
        return None


# ── Signal computation ────────────────────────────────────────────────────────

def compute_signals(df: Any) -> dict:
    """
    Compute a minimal signal set from OHLCV DataFrame.
    All values are plain Python floats/bools — safe to JSON-serialize.
    Returns empty dict on any computation error.
    """
    try:
        import numpy as np

        close  = df["Close"]
        volume = df["Volume"]

        # EMAs
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        # RSI-14
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = 100 - (100 / (1 + rs))

        # Volume ratio vs 20-day average
        vol_avg = volume.rolling(20).mean()
        vol_avg_last = float(vol_avg.iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / vol_avg_last if vol_avg_last > 0 else 1.0

        # 5-bar momentum %
        mom5 = 0.0
        if len(close) >= 6:
            mom5 = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) * 100

        price    = float(close.iloc[-1])
        rsi_now  = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None
        ema20_v  = float(ema20.iloc[-1])
        ema50_v  = float(ema50.iloc[-1])

        # EMA20 crossed above EMA50 in last 5 bars
        ema_cross_up = False
        for i in range(max(1, len(ema20) - 5), len(ema20)):
            if (float(ema20.iloc[i]) > float(ema50.iloc[i]) and
                    float(ema20.iloc[i - 1]) <= float(ema50.iloc[i - 1])):
                ema_cross_up = True
                break

        return {
            "price":              round(price, 2),
            "rsi":                round(rsi_now, 1) if rsi_now is not None else None,
            "ema20":              round(ema20_v, 2),
            "ema50":              round(ema50_v, 2),
            "vol_ratio":          round(vol_ratio, 2),
            "mom5d_pct":          round(mom5, 2),
            "price_above_ema20":  bool(price > ema20_v),
            "price_above_ema50":  bool(price > ema50_v),
            "ema20_above_ema50":  bool(ema20_v > ema50_v),
            "ema_cross_up_5d":    bool(ema_cross_up),
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
    why: list[str] = []
    flags: list[str] = []

    pa20  = signals.get("price_above_ema20", False)
    pa50  = signals.get("price_above_ema50", False)
    e20e50 = signals.get("ema20_above_ema50", False)
    rsi   = signals.get("rsi") or 50.0
    vr    = signals.get("vol_ratio", 1.0) or 1.0
    mom5  = signals.get("mom5d_pct", 0.0) or 0.0
    cross = signals.get("ema_cross_up_5d", False)

    # Trend alignment
    if pa20 and e20e50:
        score += 20
        why.append("UPTREND:EMA_ALIGNED")
    elif pa20:
        score += 8
        why.append("ABOVE_EMA20")
    if not pa50 and not pa20:
        score -= 15
        flags.append("BELOW_EMA50")

    # EMA cross
    if cross:
        score += 12
        why.append("EMA20_CROSS_UP_5D")

    # Volume surge
    if vr >= 2.0:
        score += 12
        why.append(f"VOL_SURGE:{vr:.1f}x")
    elif vr >= 1.5:
        score += 6
        why.append(f"HIGH_VOLUME:{vr:.1f}x")

    # RSI zone
    if 55 <= rsi <= 70:
        score += 8
        why.append(f"RSI_HEALTHY:{rsi:.0f}")
    elif rsi > 75:
        score -= 10
        flags.append(f"RSI_EXTENDED:{rsi:.0f}")
    elif rsi < 40:
        score -= 8
        flags.append(f"RSI_WEAK:{rsi:.0f}")

    # Momentum
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
        band, priority = "A", "WATCH_A"
    elif score >= 50:
        band, priority = "B", "STRONG_WATCH"
    elif score >= 35:
        band, priority = "C", "CONTEXT_WATCH"
    else:
        band, priority = "D", "LOW"

    return {
        "symbol":       symbol,
        "ticker":       symbol,
        "ultra_score":  score,
        "band":         band,
        "ultra_score_band_v2": band,
        "priority":     priority,
        "ultra_score_priority": priority,
        "price":        signals.get("price"),
        "rsi":          signals.get("rsi"),
        "vol_ratio":    signals.get("vol_ratio"),
        "mom5d_pct":    signals.get("mom5d_pct"),
        "ema20":        signals.get("ema20"),
        "ema50":        signals.get("ema50"),
        "ultra_score_reasons": why[:5],
        "ultra_score_flags":   flags[:3],
        "score_engine":        SCORE_ENGINE,
        "source":              "scanner-api-controlled-scan",
        "sector":              "",
        "company":             "",
        "final_signal":        "",
        "action_bucket":       "",
        "why_selected":        why[:5],
        "risk_flags":          flags[:3],
    }


# ── Main scan ─────────────────────────────────────────────────────────────────

def run_controlled_scan(
    symbols:    list[str],
    timeframe:  str = "1d",
    universe:   str = "manual_test",
    scan_mode:  str = "controlled_test",
) -> dict:
    """
    Run a controlled scan for the given symbol list.
    Returns a results dict — caller is responsible for DB writes.
    """
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()

    results: list[dict] = []
    errors:  list[dict] = []

    for symbol in symbols:
        sym = symbol.upper().strip()
        try:
            df = fetch_candles(sym, interval=timeframe)
            if df is None:
                errors.append({"symbol": sym, "error": "no_data"})
                continue
            signals  = compute_signals(df)
            if not signals:
                errors.append({"symbol": sym, "error": "signal_computation_failed"})
                continue
            candidate = score_candidate(sym, signals)
            results.append(candidate)
            log.info("scanned %s → score=%s band=%s", sym, candidate["ultra_score"], candidate["band"])
        except Exception as exc:
            log.warning("scan error for %s: %s", sym, exc)
            errors.append({"symbol": sym, "error": type(exc).__name__})

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Sort by score descending
    results.sort(key=lambda x: x.get("ultra_score", 0), reverse=True)

    return {
        "results":       results,
        "errors":        errors,
        "symbols_requested": len(symbols),
        "symbols_scanned":   len(results),
        "candidates_saved":  len(results),
        "elapsed_ms":        elapsed_ms,
        "started_at":        started.isoformat(),
        "score_engine":      SCORE_ENGINE,
        "universe":          universe,
        "timeframe":         timeframe,
        "scan_mode":         scan_mode,
    }
