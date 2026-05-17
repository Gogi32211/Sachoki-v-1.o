"""
chart_engine.py — Phase 8C: orchestration layer for /api/chart/* endpoints.

Wraps Massive fetch + chart_signal_engine + chart_wlnbb_engine.
No yfinance. No imports from old root backend.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from .scan_engine import fetch_bars
# Phase B-2: engine compute lives behind engine_client (HTTP or in-process).
# chart_engine is part of the SCANNER layer (history/snapshot endpoints),
# not the engine layer — so it imports the same client scan_engine uses.
from .engine_client import run_engines as _run_engines
# Per-engine imports kept for individual chart endpoints that need just one
# engine's output (e.g. /api/chart/score). These stay in-process always —
# they're light enough that an HTTP roundtrip isn't worth it.
from .engine_api.chart_signal_engine import compute_signals as _compute_tz
from .engine_api.chart_wlnbb_engine  import compute_wlnbb   as _compute_wlnbb
from .engine_api.chart_vabs_engine   import compute_vabs    as _compute_vabs, VABS_SIG_COLS
from .engine_api.chart_wick_engine   import compute_wick    as _compute_wick, WICK_SIG_COLS
from .engine_api.chart_combo_engine  import compute_combo   as _compute_combo, COMBO_SIG_COLS, COMBO_PREUP_COLS

log = logging.getLogger(__name__)

# ── Timeframe → calendar days needed to get `bars` completed candles ──────────
_TF_DAYS: dict[str, int] = {
    "1d":  2,    # multiplier applied to bars count
    "1wk": 10,
    "4h":  1,
    "1h":  1,
}
_MIN_FETCH_DAYS = 365


# Signal families not yet migrated to Phase 8C (P1/P2 reserved)
MISSING_GROUPS: list[str] = [
    "TZ Intel (ACTIVATION / BREAKING / SHAKEOUT)",
    "ABR Phase (ACTIVATION / BREAKING / RETEST)",
    "RTB Phase (TREND / BREAKOUT / RANGE)",
    "BUY_2809 / ROCKET composite",
    "SWEET_SPOT regime",
    "Rare Reversal (RR)",
    "Profile Score overlay",
]

# Signal display names — maps internal column → short badge label
_SIG_LABELS: dict[str, str] = {
    # T/Z signal
    "T1G": "T1G", "T1": "T1", "T2G": "T2G", "T2": "T2",
    "T3":  "T3",  "T4": "T4", "T5": "T5",   "T6": "T6",
    "T9":  "T9",  "T10": "T10", "T11": "T11", "T12": "T12",
    "Z1G": "Z1G", "Z1": "Z1", "Z2G": "Z2G", "Z2": "Z2",
    "Z3":  "Z3",  "Z4": "Z4", "Z5": "Z5",   "Z6": "Z6",
    "Z7":  "Z7",  "Z9": "Z9", "Z10": "Z10", "Z11": "Z11", "Z12": "Z12",
    # WLNBB
    "L34":  "L34",  "L43":  "L43",  "L64":  "L64",  "L22":  "L22",
    "L1L2": "L1L2", "L2L5": "L2L5", "L555": "L55",  "ONLY_L2L4": "L2L4",
    "FRI34": "FR34", "FRI43": "FR43", "FRI64": "FR64",
    "BLUE":  "BL",   "UI":   "UI",
    "FUCHSIA_RH": "F↑", "FUCHSIA_RL": "F↓",
    "PRE_PUMP": "PP",
    "CCI_READY": "CCI✓", "CCI_0_RETEST_OK": "CCIR", "CCI_BLUE_TURN": "CCIB",
    "BO_UP": "BO↑", "BO_DN": "BO↓",
    "BX_UP": "BX↑", "BX_DN": "BX↓",
    "BE_UP": "BE↑", "BE_DN": "BE↓",
}


# Signal display labels that belong to the L / WLNBB group
_L_DISPLAY: frozenset[str] = frozenset({
    "L34", "L43", "L64", "L22", "L1L2", "L2L5", "L55", "L2L4",
    "FR34", "FR43", "FR64", "BL", "UI",
    "BO↑", "BO↓", "BX↑", "BX↓", "BE↑", "BE↓",
})
# FUCHSIA display labels → F row
_F_DISPLAY: frozenset[str] = frozenset({"F↑", "F↓"})
# CCI context labels → CTX row
_CTX_DISPLAY: frozenset[str] = frozenset({"CCI✓", "CCIR", "CCIB"})
# PRE_PUMP → VOL row
_VOL_EXTRA: frozenset[str] = frozenset({"PP"})
# VABS display labels (from chart_vabs_engine) → vabs row
_VABS_DISPLAY: frozenset[str] = frozenset({lbl for _, lbl in VABS_SIG_COLS})
# WICK display labels → wick row
_WICK_DISPLAY: frozenset[str] = frozenset({lbl for _, lbl in WICK_SIG_COLS})
# PREUP labels (from combo engine) → z row (same as old SuperchartPanel)
_PREUP_DISPLAY: frozenset[str] = frozenset({"P2", "P3", "P50", "P89"})
# Combo/I labels → i row
_I_DISPLAY: frozenset[str] = frozenset({
    lbl for col, lbl in COMBO_SIG_COLS if col not in COMBO_PREUP_COLS
})


def _group_signals(signals: list[str], vol_bucket: str) -> dict[str, list[str]]:
    """
    Group per-bar display-label signals into named timeline rows.
    Works on the already-mapped labels (output of _active_signals).
    """
    groups: dict[str, list[str]] = {
        k: [] for k in (
            "z", "t", "l", "f", "fly", "g", "b", "i",
            "ult", "vol", "vabs", "wick", "setup", "gog", "ctx",
        )
    }
    # Vol bucket badge — omit "N" (normal) to reduce noise
    if vol_bucket and vol_bucket not in ("", "N"):
        groups["vol"].append(vol_bucket)

    for sig in signals:
        if sig.startswith("Z") or sig in _PREUP_DISPLAY:
            groups["z"].append(sig)
        elif sig.startswith("T"):
            groups["t"].append(sig)
        elif sig in _L_DISPLAY:
            groups["l"].append(sig)
        elif sig in _F_DISPLAY:
            groups["f"].append(sig)
        elif sig in _CTX_DISPLAY:
            groups["ctx"].append(sig)
        elif sig in _VOL_EXTRA:
            groups["vol"].append(sig)
        elif sig in _VABS_DISPLAY:
            groups["vabs"].append(sig)
        elif sig in _WICK_DISPLAY:
            groups["wick"].append(sig)
        elif sig in _I_DISPLAY:
            groups["i"].append(sig)
        else:
            groups["ctx"].append(sig)  # safe fallback

    return groups


def _active_extra_signals(
    vabs_row: pd.Series,
    wick_row: pd.Series,
    combo_row: pd.Series,
) -> list[str]:
    """Collect VABS / WICK / combo signal labels for a single bar."""
    sigs: list[str] = []
    for col, label in VABS_SIG_COLS:
        if vabs_row.get(col, False):
            sigs.append(label)
    for col, label in WICK_SIG_COLS:
        if wick_row.get(col, False):
            sigs.append(label)
    for col, label in COMBO_SIG_COLS:
        if combo_row.get(col, False):
            sigs.append(label)
    return sigs


def _fetch_for_chart(symbol: str, tf: str, bars: int) -> pd.DataFrame | None:
    """Fetch enough history so `bars` completed candles are available after slicing."""
    multiplier = _TF_DAYS.get(tf, 2)
    days = max(bars * multiplier, _MIN_FETCH_DAYS)
    return fetch_bars(symbol, interval=tf, days=days)


def _ts_to_date(ts) -> str:
    """Convert pandas Timestamp (UTC-aware) → 'YYYY-MM-DD' string."""
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d")
    return str(ts)[:10]


def _active_signals(tz_row: pd.Series, wl_row: pd.Series) -> list[str]:
    """Collect all active signal labels for a single bar."""
    sigs: list[str] = []
    # T/Z name
    sig_name = str(tz_row.get("sig_name", "NONE"))
    if sig_name and sig_name != "NONE":
        sigs.append(_SIG_LABELS.get(sig_name, sig_name))
    # WLNBB booleans
    for col in [
        "L34", "L43", "L64", "L22", "L1L2", "L2L5", "L555", "ONLY_L2L4",
        "FRI34", "FRI43", "FRI64", "BLUE", "UI",
        "FUCHSIA_RH", "FUCHSIA_RL", "PRE_PUMP",
        "CCI_READY", "CCI_0_RETEST_OK", "CCI_BLUE_TURN",
        "BO_UP", "BO_DN", "BX_UP", "BX_DN", "BE_UP", "BE_DN",
    ]:
        if wl_row.get(col, False):
            sigs.append(_SIG_LABELS.get(col, col))
    return sigs


def build_candles(
    df: pd.DataFrame,
    tz_df: pd.DataFrame,
    wl_df: pd.DataFrame,
    bars: int,
) -> list[dict]:
    """
    Merge OHLCV + signal frames into frontend-ready candle dicts.
    Returns the last `bars` completed candles (index 0 = oldest).
    """
    combined = pd.concat([df, tz_df, wl_df], axis=1)
    combined = combined.iloc[-bars:] if len(combined) > bars else combined

    out: list[dict] = []
    for ts, row in combined.iterrows():
        tz_row = tz_df.loc[ts] if ts in tz_df.index else pd.Series(dtype=object)
        wl_row = wl_df.loc[ts] if ts in wl_df.index else pd.Series(dtype=object)
        sigs   = _active_signals(tz_row, wl_row)
        out.append({
            "time":        _ts_to_date(ts),
            "open":        round(float(row["open"]),   4),
            "high":        round(float(row["high"]),   4),
            "low":         round(float(row["low"]),    4),
            "close":       round(float(row["close"]),  4),
            "volume":      int(row["volume"]),
            "vol_bucket":  str(row.get("vol_bucket", "")),
            "rsi":         round(float(row.get("rsi", 50)), 2),
            "cci_sma":     round(float(row.get("cci_sma", 0)), 2),
            "sig_name":    str(row.get("sig_name", "NONE")),
            "is_bull":     bool(row.get("is_bull", False)),
            "is_bear":     bool(row.get("is_bear", False)),
            "signals":     sigs,
        })
    return out


def build_markers(candles: list[dict]) -> list[dict]:
    """
    Build lightweight-charts marker objects for T/Z signals.
    Bull → belowBar arrowUp green; Bear → aboveBar arrowDown red.
    """
    markers: list[dict] = []
    for c in candles:
        if c["is_bull"]:
            markers.append({
                "time":     c["time"],
                "position": "belowBar",
                "color":    "#3fb950",
                "shape":    "arrowUp",
                "text":     c["sig_name"],
            })
        elif c["is_bear"]:
            markers.append({
                "time":     c["time"],
                "position": "aboveBar",
                "color":    "#f85149",
                "shape":    "arrowDown",
                "text":     c["sig_name"],
            })
    return markers


def _score_panel(symbol: str, signals: dict, tf: str, df: pd.DataFrame) -> dict:
    """Run compute_scanner_ultra_candidate and return score panel dict."""
    try:
        from .scoring_adapter import compute_scanner_ultra_candidate
        from .sector_map import get_sector_info
        candidate = compute_scanner_ultra_candidate(symbol, signals, timeframe=tf, df=df)
        sector_info = get_sector_info(symbol)
        return {
            "ultra_score":  candidate.get("ultra_score"),
            "band":         candidate.get("band", ""),
            "why_selected": candidate.get("why_selected", []),
            "risk_flags":   candidate.get("risk_flags", []),
            "final_signal": candidate.get("final_signal", ""),
            "score_engine": candidate.get("score_engine", ""),
            "sector":       sector_info.get("sector", ""),
            "industry":     sector_info.get("industry", ""),
        }
    except Exception as exc:
        log.warning("_score_panel error for %s: %s", symbol, exc)
        return {"ultra_score": None, "band": "", "why_selected": [], "risk_flags": []}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Public API ────────────────────────────────────────────────────────────────

def get_chart_candles(symbol: str, tf: str = "1d", bars: int = 150) -> dict:
    """
    Fetch OHLCV + compute T/Z + WLNBB signals. Return candles + markers.
    """
    sym = symbol.upper().strip()
    df = _fetch_for_chart(sym, tf, bars)
    if df is None:
        return {"ok": False, "error": f"No data for {sym} ({tf})"}

    try:
        tz_df = _compute_tz(df)
        wl_df = _compute_wlnbb(df)
    except Exception as exc:
        log.warning("Signal compute error for %s: %s", sym, exc)
        return {"ok": False, "error": "Signal computation failed"}

    candles = build_candles(df, tz_df, wl_df, bars)
    markers = build_markers(candles)

    return {
        "ok":           True,
        "symbol":       sym,
        "timeframe":    tf,
        "bars_returned": len(candles),
        "candles":      candles,
        "markers":      markers,
        "generated_at": _now_iso(),
    }


def get_chart_score(symbol: str, tf: str = "1d") -> dict:
    """
    Fetch bars and compute Ultra score panel for a single symbol.
    """
    sym = symbol.upper().strip()
    df = _fetch_for_chart(sym, tf, bars=200)
    if df is None:
        return {"ok": False, "error": f"No data for {sym} ({tf})"}

    try:
        from .scan_engine import compute_signals as _scan_signals
        signals = _scan_signals(df)
    except Exception as exc:
        log.warning("compute_signals error for %s: %s", sym, exc)
        signals = {}

    panel = _score_panel(sym, signals, tf, df)
    panel.update({
        "ok":           True,
        "symbol":       sym,
        "timeframe":    tf,
        "price":        signals.get("price"),
        "change_pct":   signals.get("change_pct"),
        "rsi":          signals.get("rsi"),
        "generated_at": _now_iso(),
    })
    return panel


def get_chart_history(symbol: str, tf: str = "1d", lookback: int = 60) -> dict:
    """
    Historical per-bar signal timeline for the Super Chart History view.

    Phase 8G commit 2: this endpoint now consumes the unified engine_registry
    output. Chart history no longer runs its own engines independently — it
    flattens the normalized scanner bars into the legacy history-row shape.
    """
    sym  = symbol.upper().strip()
    bars = min(max(lookback, 10), 120)

    df = _fetch_for_chart(sym, tf, bars)
    if df is None:
        return {
            "ok": False, "ticker": sym, "timeframe": tf, "lookback": bars,
            "bars": [],
            "meta": {"source": "unified_scanner_engine_pipeline",
                     "generated_at": _now_iso(),
                     "engines_enabled": [], "engines_failed": [],
                     "warning": f"no data returned for {sym} ({tf})"},
        }

    # Phase B-1 + C-3: engine_api is pure compute. Resolve split flags via
    # market_data_client (HTTP to market-data-api, or in-process fallback).
    try:
        from . import market_data_client as _mkt
        _split_flags = _mkt.get_split_flags_for_ticker(sym)
    except Exception as exc:
        log.debug("split flag lookup failed for %s: %s", sym, exc)
        _split_flags = None
    all_bars = _run_engines(ticker=sym, timeframe=tf, df=df, split_flags=_split_flags)
    if not all_bars:
        return {
            "ok": False, "ticker": sym, "timeframe": tf, "lookback": bars,
            "bars": [],
            "meta": {"source": "unified_scanner_engine_pipeline",
                     "generated_at": _now_iso(),
                     "engines_enabled": [], "engines_failed": [],
                     "warning": "engine pipeline returned no bars"},
        }

    sliced = all_bars[-bars:] if len(all_bars) > bars else all_bars
    last_debug = sliced[-1].get("engine_debug", {}) if sliced else {}

    out_bars: list[dict] = []
    for bar in sliced:
        ohlcv = bar.get("ohlcv") or {}
        ind   = bar.get("indicators") or {}
        out_bars.append({
            "date":         bar.get("date"),
            "display_date": bar.get("display_date"),
            "datetime":     bar.get("datetime"),
            "close":        _round(ohlcv.get("close"), 4),
            "rsi":          _round(ind.get("rsi"), 2),
            "cci":          _round(ind.get("cci"), 2),
            # Per-bar scoring is plumbed via the normalized scores dict; we
            # surface the headline values flat for back-compat with the existing
            # frontend table.
            "score":        (bar.get("scores") or {}).get("ultra_score"),
            "turbo":        (bar.get("scores") or {}).get("turbo_score"),
            "rtb":          (bar.get("scores") or {}).get("rtb_phase"),
            "category":     (bar.get("scores") or {}).get("category"),
            "signals":      bar.get("signals") or {},
            "scores":       bar.get("scores") or {},
            "roles":        bar.get("roles") or {},
            "split":        bar.get("split") or {},
            "ohlcv":        ohlcv,
            "indicators":   ind,
        })

    return {
        "ok":        True,
        "ticker":    sym,
        "timeframe": tf,
        "lookback":  bars,
        "bars":      out_bars,
        "meta": {
            "source":          "unified_scanner_engine_pipeline",
            "generated_at":    _now_iso(),
            "engines_enabled": last_debug.get("engines_ran", []),
            "engines_failed":  last_debug.get("engines_failed", []),
            "warning":         None,
        },
    }


def _round(v, ndigits: int):
    if v is None:
        return None
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def get_chart_snapshot(symbol: str, tf: str = "1d", bars: int = 150) -> dict:
    """
    Full chart snapshot: candles + markers + score panel.
    Single Massive fetch shared across all computations.
    """
    sym = symbol.upper().strip()
    df = _fetch_for_chart(sym, tf, bars)
    if df is None:
        return {"ok": False, "error": f"No data for {sym} ({tf})"}

    try:
        tz_df = _compute_tz(df)
        wl_df = _compute_wlnbb(df)
    except Exception as exc:
        log.warning("Signal compute error for %s: %s", sym, exc)
        return {"ok": False, "error": "Signal computation failed"}

    candles = build_candles(df, tz_df, wl_df, bars)
    markers = build_markers(candles)

    try:
        from .scan_engine import compute_signals as _scan_signals
        signals = _scan_signals(df)
    except Exception:
        signals = {}

    panel = _score_panel(sym, signals, tf, df)

    # Latest-bar WLNBB summary
    last_wl = wl_df.iloc[-1] if len(wl_df) else pd.Series(dtype=object)
    wlnbb_summary = {
        "vol_bucket": str(last_wl.get("vol_bucket", "")),
        "rsi":        round(float(last_wl.get("rsi", 50)), 2),
        "cci_sma":    round(float(last_wl.get("cci_sma", 0)), 2),
        "BLUE":       bool(last_wl.get("BLUE", False)),
        "L34":        bool(last_wl.get("L34", False)),
        "L43":        bool(last_wl.get("L43", False)),
        "FRI34":      bool(last_wl.get("FRI34", False)),
        "BO_UP":      bool(last_wl.get("BO_UP", False)),
        "BE_UP":      bool(last_wl.get("BE_UP", False)),
        "PRE_PUMP":   bool(last_wl.get("PRE_PUMP", False)),
    }

    last_tz = tz_df.iloc[-1] if len(tz_df) else pd.Series(dtype=object)
    tz_summary = {
        "sig_name": str(last_tz.get("sig_name", "NONE")),
        "is_bull":  bool(last_tz.get("is_bull", False)),
        "is_bear":  bool(last_tz.get("is_bear", False)),
    }

    return {
        "ok":            True,
        "symbol":        sym,
        "timeframe":     tf,
        "bars_returned": len(candles),
        "candles":       candles,
        "markers":       markers,
        "score":         panel,
        "tz":            tz_summary,
        "wlnbb":         wlnbb_summary,
        "missing_groups": MISSING_GROUPS,
        "generated_at":  _now_iso(),
    }
