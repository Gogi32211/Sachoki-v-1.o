"""
unified_schema.py — Phase 8G: normalized scanner output schema.

This module defines the single canonical per-bar object produced by the
engine_registry pipeline. All consumers — scan_engine (latest), chart_engine
(history), filters, exports — read this same shape so the scanner is the
single source of truth.

Nothing here computes anything. It's typed constants + builders + the row
groupings used by the Super Chart history table.
"""
from __future__ import annotations

from typing import Any


# ── Signal row keys (display row order in Super Chart History) ───────────────

ROW_ORDER: tuple[str, ...] = (
    "z", "t", "l", "f", "fly", "g", "b", "i", "ult",
    "vol", "vabs", "wick", "setup", "gog", "ctx",
)

SIGNAL_ROW_KEYS: frozenset[str] = frozenset(ROW_ORDER)


# ── Score field names (preserve old Ultra naming) ────────────────────────────

SCORE_FIELDS: tuple[str, ...] = (
    "ultra_score",
    "real_ultra_score",
    "final_bull_score",
    "final_bear_score",
    "signal_score",
    "turbo_score",
    "rtb_phase",
    "rtb_total",
    "pf",
    "category",
    "cat",
    "band",
    "sector_band",
    "score_reason",
)


# ── Role / classification field names ────────────────────────────────────────

ROLE_FIELDS: tuple[str, ...] = (
    "primary_role",
    "quality_band",
    "scanner_category",
    "pullback_status",
    "breakout_status",
    "abr_category",
    "fire_arm_base",
    "preup_status",
    "predn_status",
)


# ── Split / reverse-split field names ────────────────────────────────────────

SPLIT_FIELDS: tuple[str, ...] = (
    "has_split",
    "has_reverse_split",
    "split_ratio",
    "split_date",
    "split_contaminated",
    "stock_like_split_event",
    "split_filter_reason",
    "split_impact",
    "phase",
    "wave",
    "days_offset",
    "heat_score",
)


# ── Builders ─────────────────────────────────────────────────────────────────

def empty_signals() -> dict[str, list[str]]:
    """Return a fresh dict with every signal row key mapped to an empty list."""
    return {k: [] for k in ROW_ORDER}


def empty_scores() -> dict[str, Any]:
    return {k: None for k in SCORE_FIELDS}


def empty_roles() -> dict[str, Any]:
    return {k: None for k in ROLE_FIELDS}


def empty_split() -> dict[str, Any]:
    out: dict[str, Any] = {k: None for k in SPLIT_FIELDS}
    out["has_split"] = False
    out["has_reverse_split"] = False
    out["split_contaminated"] = False
    return out


def empty_indicators() -> dict[str, Any]:
    return {
        "rsi": None,
        "cci": None,
        "ema8": None,
        "ema13": None,
        "ema21": None,
        "ema34": None,
        "ema50": None,
        "ema89": None,
        "ema200": None,
        "atr": None,
        "bb_upper": None,
        "bb_mid": None,
        "bb_lower": None,
        "volume_ma": None,
        "volume_z": None,
        "volume_ratio": None,
        "body_pct": None,
        "upper_wick_pct": None,
        "lower_wick_pct": None,
    }


def empty_ohlcv() -> dict[str, Any]:
    return {"open": None, "high": None, "low": None, "close": None, "volume": None}


def build_bar(
    *,
    ticker: str,
    timeframe: str,
    date: str,
    display_date: str,
    datetime_iso: str,
) -> dict[str, Any]:
    """
    Build a fully-shaped, empty per-bar object. Engines fill in slots in-place.
    """
    return {
        "ticker":       ticker,
        "timeframe":    timeframe,
        "date":         date,
        "display_date": display_date,
        "datetime":     datetime_iso,

        "ohlcv":      empty_ohlcv(),
        "indicators": empty_indicators(),
        "signals":    empty_signals(),
        "scores":     empty_scores(),
        "roles":      empty_roles(),
        "split":      empty_split(),

        "filters_debug": {
            "passed_filters": [],
            "failed_filters": [],
            "filter_reasons": [],
        },
        "engine_debug": {
            "engines_ran":    [],
            "engines_failed": [],
            "warnings":       [],
        },
        "raw": {"old_ultra_fields": {}},
    }


# ── Helpers used by chart history flatteners ─────────────────────────────────

def bar_to_history_row(bar: dict[str, Any]) -> dict[str, Any]:
    """
    Flatten a unified bar object into the legacy history-row shape consumed by
    the Super Chart History timeline. Keeps top-level close/rsi/cci for backward
    compatibility but also exposes the full nested objects.
    """
    ohlcv = bar.get("ohlcv") or {}
    ind   = bar.get("indicators") or {}
    return {
        "date":         bar.get("date"),
        "display_date": bar.get("display_date"),
        "datetime":     bar.get("datetime"),
        "close":        ohlcv.get("close"),
        "rsi":          ind.get("rsi"),
        "cci":          ind.get("cci"),
        "ohlcv":        ohlcv,
        "indicators":   ind,
        "signals":      bar.get("signals") or empty_signals(),
        "scores":       bar.get("scores") or empty_scores(),
        "roles":        bar.get("roles") or empty_roles(),
        "split":        bar.get("split") or empty_split(),
        "filters_debug": bar.get("filters_debug") or {},
        "engine_debug":  bar.get("engine_debug") or {},
    }
