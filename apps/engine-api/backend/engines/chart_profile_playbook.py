"""
Sachoki Profile Playbook — single source of truth for profile scoring.

This module is the ONLY place where the following are defined:
  - PROFILE_PLAYBOOK_VERSION
  - SIGNAL_ALIASES / normalize_signal_name()
  - Profile definitions (universes, price buckets, weights, thresholds)
  - BEAR_CONTEXT_SIGNALS, BULL_CONFIRM_SIGNALS, SEQUENCE_BONUSES
  - compute_profile_playbook_for_row()   ← unified public API
  - signal extraction helpers
  - unscored-signal audit helper
  - config-snapshot helper

It must NOT replace FINAL_BULL_SCORE or any canonical score.
It must NOT mutate canonical score columns.
All consumers (SuperChart, stock_stat, replay, API) must import from here.
"""

from __future__ import annotations

import re
import json
import time as _time
from typing import Any, Dict, List, Optional, Set, Tuple

# ── Version ───────────────────────────────────────────────────────────────────
PROFILE_PLAYBOOK_VERSION = "2026-05-04-btb-calibration-v3"

# ── Signal aliases ─────────────────────────────────────────────────────────────
SIGNAL_ALIASES: Dict[str, str] = {
    # VBO
    "VBO↑": "VBO_UP", "VBOUP": "VBO_UP", "VBO_UP": "VBO_UP",
    "VBO↓": "VBO_DN", "VBODN": "VBO_DN", "VBO_DN": "VBO_DN",
    # HILO
    "HILO↑": "HILO_UP", "HILO_UP": "HILO_UP",
    "HILO↓": "HILO_DN", "HILO_DN": "HILO_DN",
    # BB / BX / BO / BE
    "BB↑": "BB_UP", "BB_UP": "BB_UP",
    "BB↓": "BB_DN", "BB_DN": "BB_DN",
    "BX↑": "BX_UP", "BX_UP": "BX_UP",
    "BX↓": "BX_DN", "BX_DN": "BX_DN",
    "BO↑": "BO_UP", "BO_UP": "BO_UP",
    "BO↓": "BO_DN", "BO_DN": "BO_DN",
    "BE↑": "BE_UP", "BE_UP": "BE_UP",
    "BE↓": "BE_DN", "BE_DN": "BE_DN",
    # EB / FBO
    "EB↑": "EB_UP", "EB_UP": "EB_UP",
    "EB↓": "EB_DN", "EB_DN": "EB_DN",
    "FBO↑": "FBO_UP", "FBO_UP": "FBO_UP",
    "FBO↓": "FBO_DN", "FBO_DN": "FBO_DN",
    # Wick
    "WC↑": "WC_UP", "WC_UP": "WC_UP",
    "WC↓": "WC_DN", "WC_DN": "WC_DN",
    "WP↑": "WP_UP", "WP_UP": "WP_UP",
    "WP↓": "WP_DN", "WP_DN": "WP_DN",
    # FLY — bar dict uses dash notation; "FLY" alone means fly_abcd=True
    "FLY-BD": "FLY_BD", "FLY_BD": "FLY_BD",
    "FLY-CD": "FLY_CD", "FLY_CD": "FLY_CD",
    "FLY-AD": "FLY_AD", "FLY_AD": "FLY_AD",
    "FLY": "FLY_ABCD", "FLY_ABCD": "FLY_ABCD",
    # Ultra / combo display names
    "BEST↑": "BEST_UP", "BEST_UP": "BEST_UP",
    "3↑": "THREE_UP", "THREE_UP": "THREE_UP",
    "4BF": "BF_BUY", "BF_BUY": "BF_BUY",
    "4BF↓": "4BF_DN", "4BF_DN": "4BF_DN",
    "ATR↑": "ATR_BRK", "ATR_BRK": "ATR_BRK",
    # Star / vol
    "BEST★": "BEST_STAR", "BEST*": "BEST_STAR", "BEST_STAR": "BEST_STAR",
    "5×": "VOL_5X",  "5X": "VOL_5X",  "VOL_5X":  "VOL_5X",
    "10×": "VOL_10X", "10X": "VOL_10X", "VOL_10X": "VOL_10X",
    "20×": "VOL_20X", "20X": "VOL_20X", "VOL_20X": "VOL_20X",
    # Bias
    "↓BIAS": "BIAS_DN", "BIAS↓": "BIAS_DN", "BIAS_DN": "BIAS_DN",
    "↑BIAS": "BIAS_UP", "BIAS↑": "BIAS_UP", "BIAS_UP": "BIAS_UP",
    # Misc
    "CONS": "CONSO", "CONSO": "CONSO",
}

# ── Bear-context signals (exhaustion / absorption context) ────────────────────
# Standalone weight: small (cap applied per row). Not large enough to enter
# SWEET_SPOT alone. Become meaningful only when followed by bull confirmation.
BEAR_CONTEXT_SIGNALS: Dict[str, int] = {
    "EB_DN":  1,
    "BE_DN":  1,
    "VBO_DN": 1,
    "BO_DN":  1,
    "4BF_DN": 1,
    "WC_DN":  1,
    "WP_DN":  0,
    "FBO_DN": 0,
}
BEAR_CONTEXT_STANDALONE_CAP = 3

# ── Bullish confirmation signals ──────────────────────────────────────────────
BULL_CONFIRM_SIGNALS: Set[str] = {
    "BUY", "SVS", "ABS", "LOAD", "CLM", "L34",
    "VBO_UP", "BO_UP", "BX_UP", "BB_UP", "EB_UP", "BE_UP",
    "Z2", "Z4", "T4", "FRI43", "CCIB", "SC", "SQ",
}

# ── Bear-to-bull sequence bonuses (recalibrated 2026-05-04) ──────────────────
# Reduced to prevent BTB alone from flooding SWEET_SPOT.
# Strong pairs: BUY/SVS/BO_UP confirmations.
# Weak/noisy pairs: L34/VBO_UP/BO_UP from VBO_DN/BO_DN/WC_DN — significantly reduced.
SEQUENCE_BONUSES: Dict[str, Dict[str, int]] = {
    "EB_DN": {
        "BUY": 5, "SVS": 4, "ABS": 4, "L34": 3, "VBO_UP": 3, "BO_UP": 4,
    },
    "BE_DN": {
        "BUY": 5, "SVS": 4, "ABS": 3, "L34": 2, "VBO_UP": 4,
    },
    "VBO_DN": {
        "BUY": 4, "SVS": 2, "ABS": 3, "L34": 1, "VBO_UP": 2,
    },
    "BO_DN": {
        "BUY": 4, "SVS": 3, "L34": 1, "BO_UP": 2,
    },
    "4BF_DN": {
        "BUY": 4, "SVS": 3, "L34": 1,
    },
    "WC_DN": {
        "BUY": 1, "SVS": 2, "L34": 1,
    },
}
SEQUENCE_BONUS_CAP = 5  # reduced from 8 — BTB is a booster, not a primary driver

# Per-profile BTB caps (overrides SEQUENCE_BONUS_CAP for specific profiles)
# SP500_300_PLUS: high-price stocks need strong organic signals; BTB alone is too noisy
PROFILE_BTB_CAPS: Dict[str, int] = {
    "SP500_300_PLUS": 3,
}

# Profiles where BTB confirmations via L34/VBO_UP/BO_UP are scaled down by 50%
# (these are noisier on high-price stocks where momentum signals matter more)
PROFILE_BTB_WEAK_CONFIRM_PROFILES: Set[str] = {"SP500_300_PLUS"}

# Profiles where BTB is allowed to upgrade a row to SWEET_SPOT.
# Validated on v2 replay: only mid-range SP500 profiles show positive BTB-SWEET_SPOT perf.
BTB_SWEET_SPOT_ALLOWED_PROFILES: Set[str] = {"SP500_20_50", "SP500_50_150", "SP500_150_300"}

# ── Profile definitions ───────────────────────────────────────────────────────
PROFILES: Dict[str, dict] = {
    "SP500_LT20": {
        "universe": "sp500",
        "price_range": (0.0, 20.0),
        "description": "High-volatility S&P low-price bucket. More breakout potential but high fail/drawdown.",
        "role": "watch/high-risk",
        "preferred_preset": "STRUCTURAL_BUILD",
        "suggested_tp": 0.05,
        "suggested_sl": 0.05,
        "max_hold": 20,
        "signal_weights": {
            "BUY": 4, "SVS": 4, "BX_UP": 4, "FLY_BD": 4,
            "BB_UP": 3, "FRI43": 3, "ABS": 3, "LOAD": 3, "RTV": 3,
            "VBO_UP": 3, "G4": 3, "G11": 3, "F9": 2, "F10": 2, "CLM": 2,
        },
        "pair_bonuses": {
            ("BUY", "Z3"): 6, ("LOAD", "T10"): 6,
            ("SVS", "T10"): 6, ("FRI43", "T10"): 6,
        },
        "sweet_spot": (12, 36),
        "late_threshold": 36,
    },
    "SP500_20_50": {
        "universe": "sp500",
        "price_range": (20.0, 50.0),
        "description": "Small-price structural S&P bucket. Good breakout potential, still noisy.",
        "role": "structural-watch-or-entry",
        "preferred_preset": "STRUCTURAL_BUILD",
        "suggested_tp": 0.05,
        "suggested_sl": 0.05,
        "max_hold": 20,
        "signal_weights": {
            "BUY": 4, "SVS": 4, "260308": 4, "BX_UP": 4, "FLY_BD": 4,
            "BB_UP": 3, "FRI43": 3, "ABS": 3, "LOAD": 3, "VBO_UP": 3,
            "CCI": 3, "G11": 3, "G4": 3, "F9": 2, "F10": 2, "CLM": 2,
        },
        "pair_bonuses": {
            ("BL", "F10"): 10, ("CCI", "G11"): 9, ("CCI", "T5"): 8,
            ("260308", "F3"): 8, ("260308", "F4"): 8, ("B1", "CLM"): 8,
            ("LOAD", "T10"): 7, ("SVS", "T10"): 7, ("FRI43", "T10"): 7,
            ("G1", "SVS"): 7, ("G4", "LOAD"): 7,
        },
        "sweet_spot": (12, 40),
        "late_threshold": 40,
    },
    "SP500_50_150": {
        "universe": "sp500",
        "price_range": (50.0, 150.0),
        "description": "Best balanced actionable S&P bucket.",
        "role": "best-balanced-actionable",
        "preferred_preset": "STRUCTURAL_BUILD",
        "suggested_tp": 0.05,
        "suggested_sl": 0.05,
        "max_hold": 20,
        "signal_weights": {
            "FLY_BD": 5, "BB_UP": 4, "FRI43": 4, "BUY": 4, "SVS": 4,
            "260308": 4, "BX_UP": 4, "G4": 3, "G11": 3, "ABS": 3,
            "LOAD": 3, "F9": 2, "F10": 2, "CLM": 2, "SQ": 2, "SC": 2,
            "FLY_ABCD": 3, "EB_UP": 3,
        },
        "pair_bonuses": {
            ("BL", "F10"): 10, ("CCI", "G11"): 9, ("CCI", "T5"): 8,
            ("260308", "F3"): 8, ("260308", "F4"): 8, ("B1", "CLM"): 8,
            ("CCI", "HILO_DN"): 7, ("LOAD", "T10"): 7, ("SVS", "T10"): 7,
            ("FRI43", "T10"): 7, ("G1", "SVS"): 7, ("G4", "LOAD"): 7,
        },
        "sweet_spot": (12, 42),
        "late_threshold": 42,
    },
    "SP500_150_300": {
        "universe": "sp500",
        "price_range": (150.0, 300.0),
        "description": "High-quality setup only. Works with strong clean/shakeout setups.",
        "role": "quality-setup-only",
        "preferred_preset": "STRUCTURAL_BUILD",
        "suggested_tp": 0.05,
        "suggested_sl": 0.05,
        "max_hold": 20,
        "signal_weights": {
            "FLY_BD": 5, "BB_UP": 4, "BUY": 4, "SVS": 4, "BX_UP": 4,
            "FRI43": 4, "FLY_ABCD": 3, "G4": 3, "G11": 3,
            "ABS": 3, "LOAD": 3, "EB_UP": 3, "STRONG": 2,
            "F9": 2, "F10": 2, "CLM": 2,
        },
        "pair_bonuses": {
            ("BL", "F10"): 10, ("CCI", "G11"): 9, ("CCI", "T5"): 8,
            ("LOAD", "T10"): 7, ("SVS", "T10"): 7,
            ("FRI43", "T10"): 7, ("G1", "SVS"): 7,
        },
        "sweet_spot": (12, 42),
        "late_threshold": 42,
    },
    "SP500_300_PLUS": {
        "universe": "sp500",
        "price_range": (300.0, float("inf")),
        "description": "High-price quality bucket. Strong signals required; combo-first.",
        "role": "combo-first-quality",
        "preferred_preset": "STRUCTURAL_BUILD",
        "suggested_tp": 0.05,
        "suggested_sl": 0.05,
        "max_hold": 20,
        "signal_weights": {
            "FLY_BD": 4, "FLY_ABCD": 3, "BUY": 3, "SVS": 3, "BB_UP": 3,
            "BX_UP": 3, "ABS": 3, "LOAD": 3, "G4": 3, "G11": 3,
            "EB_UP": 3, "STRONG": 2, "CLM": 2, "F9": 2, "F10": 2, "F11": 2,
            "FRI43": 2, "FRI34": 2,
        },
        "pair_bonuses": {
            ("BL", "F10"): 10, ("CCI", "G11"): 9,
            ("LOAD", "T10"): 8, ("SVS", "T10"): 8, ("FRI43", "T10"): 8,
            ("G4", "SVS"): 7, ("G11", "BUY"): 7,
        },
        "sweet_spot": (12, 36),
        "late_threshold": 36,
    },
    "NASDAQ_PENNY": {
        "universe": "nasdaq",
        "price_range": (0.0, 5.0),
        "description": "NASDAQ penny/micro-cap experimental profile. High volatility; combo/momentum only.",
        "role": "experimental-high-risk",
        "experimental": True,
        "preferred_preset": "WIDE_MOMENTUM",
        "suggested_tp": 0.15,
        "suggested_sl": 0.05,
        "max_hold": 20,
        "signal_weights": {
            "VOL_20X": 6, "VOL_10X": 5, "VOL_5X": 4, "CONSO": 4, "SC": 4,
            "T11": 3, "Z7": 3, "BX_DN": 3, "FRI64": 2, "VBO_UP": 3,
        },
        "pair_bonuses": {
            ("SC", "T1"): 10, ("VOL_5X", "CONSO"): 8, ("BIAS_DN", "VBO_UP"): 8,
        },
        "sweet_spot": (10, 32),
        "late_threshold": 32,
    },
    "NASDAQ_REAL": {
        "universe": "nasdaq",
        "price_range": (5.0, float("inf")),
        "description": "NASDAQ real-company experimental profile. Combo-first; score is context only.",
        "role": "experimental-combo-first",
        "experimental": True,
        "preferred_preset": "STRUCTURAL_BUILD",
        "suggested_tp": 0.05,
        "suggested_sl": 0.05,
        "max_hold": 20,
        "signal_weights": {
            "FLY_BD": 4, "BUY": 3, "SVS": 3, "BB_UP": 3, "BX_UP": 3,
            "FLY_ABCD": 3, "G4": 3, "G11": 3, "ABS": 3, "LOAD": 3,
            "T11": 3, "BX_DN": 3, "B11": 3, "CONSO": 2, "L43": 2,
            "F9": 2, "F10": 2, "CLM": 2, "HILO_UP": 3, "L64": 3,
        },
        "pair_bonuses": {
            ("B10", "BEST_STAR"): 10, ("B2", "RL"): 8, ("BUY", "Z3"): 8,
            ("BB_UP", "FRI34"): 8, ("BX_UP", "F5"): 8, ("BX_DN", "L555"): 8,
            ("VOL_5X", "Z3"): 7, ("CCIB", "UM"): 7, ("STRONG", "T11"): 7,
            ("B11", "T3"): 7, ("T11", "WC_UP"): 7,
            ("LOAD", "T10"): 7, ("SVS", "T10"): 7, ("G4", "BUY"): 7,
        },
        "sweet_spot": (12, 36),
        "late_threshold": 36,
    },
}

# ── Turbo scan column → canonical signal name ─────────────────────────────────
_TURBO_SIGNAL_MAP: Dict[str, str] = {
    "buy_2809": "BUY",
    "svs_2809": "SVS",
    "um_2809":  "UM",
    "conso_2809": "CONSO",
    "bx_up": "BX_UP", "bx_dn": "BX_DN",
    "bo_up": "BO_UP", "bo_dn": "BO_DN",
    "be_up": "BE_UP", "be_dn": "BE_DN",
    "fri34": "FRI34", "fri43": "FRI43", "fri64": "FRI64",
    "l22": "L22", "l34": "L34", "l43": "L43", "l64": "L64", "l555": "L555",
    "blue": "BL",
    "cci_ready": "CCI",
    "cci_blue_turn": "CCIB",
    "abs_sig": "ABS",
    "climb_sig": "CLM",
    "load_sig": "LOAD",
    "best_sig": "BEST_STAR",
    "strong_sig": "STRONG",
    "vbo_up": "VBO_UP", "vbo_dn": "VBO_DN",
    "vol_spike_5x": "VOL_5X", "vol_spike_10x": "VOL_10X", "vol_spike_20x": "VOL_20X",
    "fly_bd": "FLY_BD", "fly_abcd": "FLY_ABCD",
    "fly_cd": "FLY_CD", "fly_ad": "FLY_AD",
    "eb_bull": "EB_UP", "eb_bear": "EB_DN",
    "fbo_bull": "FBO_UP", "fbo_bear": "FBO_DN",
    "bf_buy": "BF_BUY", "bf_sell": "4BF_DN",
    "hilo_buy": "HILO_UP", "hilo_sell": "HILO_DN",
    "sc": "SC", "bc": "BC", "sq": "SQ", "ns": "NS", "nd": "ND",
    "rtv": "RTV", "rocket": "ROCKET", "sig3g": "3G",
    "sig_260308": "260308", "sig_l88": "L88",
    "g1": "G1", "g2": "G2", "g4": "G4", "g6": "G6", "g11": "G11",
    "b1": "B1", "b2": "B2", "b3": "B3", "b4": "B4", "b5": "B5",
    "b6": "B6", "b7": "B7", "b8": "B8", "b9": "B9", "b10": "B10", "b11": "B11",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5",
    "f6": "F6", "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10", "f11": "F11",
    "bias_up": "BIAS_UP", "bias_down": "BIAS_DN",
    "bb_brk": "BB_UP",
    "wick_bull": "WC_UP", "wick_bear": "WC_DN",
    "rs": "RS", "rs_strong": "RS_STRONG",
    "va": "VA",
    "para_prep": "PARA_PREP", "para_start": "PARA_START",
    "para_plus": "PARA_PLUS", "para_retest": "PARA_RETEST",
    "fuchsia_rl": "RL", "fuchsia_rh": "RH",
    "pre_pump": "PRE_PUMP",
    "atr_brk": "ATR_BRK",
}


# ── Signal token parsing ──────────────────────────────────────────────────────

def normalize_signal_name(token: str) -> str:
    """Normalize a raw signal token to its canonical name.

    Single source of truth for all signal name normalization.
    Use this everywhere — SuperChart, stock_stat, replay, export, tests.
    """
    if token is None:
        return ""
    t = str(token).strip()
    if not t:
        return ""
    t = t.replace(",", " ").replace("|", " ").strip()
    return SIGNAL_ALIASES.get(t, t)


# Keep old name as alias for backward compatibility
normalize_signal_token = normalize_signal_name


def parse_signal_cell(value: Any) -> Set[str]:
    """Parse a signal cell (string) into a set of normalized tokens."""
    if value is None or not isinstance(value, str):
        return set()
    raw = value.strip()
    if not raw:
        return set()
    out: Set[str] = set()
    for p in re.split(r"[\s,|;]+", raw):
        n = normalize_signal_name(p)
        if n:
            out.add(n)
    return out


# ── Stat-row signal extraction ────────────────────────────────────────────────
# Maps bar-dict list column → stock_stat CSV string column
_STAT_COL_PAIRS: List[Tuple[str, str]] = [
    ("l",     "L"),
    ("f",     "F"),
    ("fly",   "FLY"),
    ("g",     "G"),
    ("b",     "B"),
    ("combo", "Combo"),
    ("ultra", "ULT"),
    ("vol",   "VOL"),
    ("vabs",  "VABS"),
    ("wick",  "WICK"),
]


def extract_profile_signals_from_stat_row(row: dict) -> Set[str]:
    """Extract normalized profile signals from a bar dict OR a stock_stat CSV row.

    Handles:
    - Bar dict from api_bar_signals (list columns: l, f, fly, g, b, combo, ultra, vol, vabs, wick)
    - CSV row from stock_stat export (string columns: L, F, FLY, G, B, Combo, ULT, VOL, VABS, WICK)

    Returns canonical signal names ready for compute_profile_playbook_for_row().
    """
    signals: Set[str] = set()
    for bar_key, csv_key in _STAT_COL_PAIRS:
        val = row.get(bar_key)
        if val is None:
            val = row.get(csv_key)
        if val is None:
            continue
        if isinstance(val, list):
            for tok in val:
                n = normalize_signal_name(str(tok).strip())
                if n:
                    signals.add(n)
        elif isinstance(val, str) and val.strip():
            signals |= parse_signal_cell(val)

    for tz_key in ("tz", "Z", "T"):
        tz = str(row.get(tz_key) or "").strip()
        if not tz:
            continue
        n = normalize_signal_name(tz)
        if n:
            signals.add(n)
            base = re.sub(r"G$", "", tz)
            if base and base != tz:
                signals.add(base)
    return signals


def extract_signals_from_turbo_row(row: dict) -> Set[str]:
    """Convert turbo_scan_results boolean columns to a set of canonical signal names."""
    signals: Set[str] = set()
    for col, name in _TURBO_SIGNAL_MAP.items():
        v = row.get(col)
        if v and v != 0:
            signals.add(name)
    tz = (row.get("tz_sig") or "").strip().upper()
    if tz:
        signals.add(tz)
        base = re.sub(r"G$", "", tz)
        if base and base != tz:
            signals.add(base)
    return signals


def get_signals_5bar(rows_for_ticker: List[dict]) -> Set[str]:
    """Union of normalized signal tokens across last 6 rows."""
    STRING_SIG_COLS = ["T", "Z", "L", "F", "B", "Combo", "ULT", "VOL", "VABS", "G", "FLY", "WICK"]
    window = rows_for_ticker[-6:]
    signals: Set[str] = set()
    for row in window:
        signals |= extract_signals_from_turbo_row(row)
        for col in STRING_SIG_COLS:
            if col in row:
                signals |= parse_signal_cell(row.get(col))
    return signals


# ── Profile selection ─────────────────────────────────────────────────────────

def get_profile(row: dict, universe: str) -> str:
    """Assign profile based on universe and current row close."""
    price = (
        row.get("close") or row.get("Close")
        or row.get("last_price") or row.get("Last_Price")
        or row.get("median_price") or 50.0
    )
    try:
        price = float(price)
    except (TypeError, ValueError):
        price = 50.0

    uni = (universe or "").lower()
    if uni == "sp500":
        if price < 20:   return "SP500_LT20"
        if price < 50:   return "SP500_20_50"
        if price < 150:  return "SP500_50_150"
        if price < 300:  return "SP500_150_300"
        return "SP500_300_PLUS"
    if price < 5:
        return "NASDAQ_PENNY"
    return "NASDAQ_REAL"


# ── Sequence decay ────────────────────────────────────────────────────────────

def sequence_decay_bonus(base_bonus: int, bars_ago: int) -> int:
    """Apply time decay to a bear-to-bull sequence bonus."""
    if bars_ago <= 1: return base_bonus
    if bars_ago <= 3: return round(base_bonus * 0.60)
    if bars_ago <= 5: return round(base_bonus * 0.25)
    return 0


# ── Category computation ──────────────────────────────────────────────────────

def _score_to_category(score: int, profile: dict) -> Tuple[str, bool, bool]:
    """Return (category, sweet_spot_active, late_warning) for a given score and profile."""
    sweet_low, sweet_high = profile["sweet_spot"]
    late_threshold = profile["late_threshold"]
    late_warning      = score > late_threshold
    sweet_spot_active = sweet_low <= score <= sweet_high
    if late_warning:
        cat = "LATE"
    elif sweet_spot_active:
        cat = "SWEET_SPOT"
    elif score >= sweet_low * 0.70:
        cat = "BUILDING"
    else:
        cat = "WATCH"
    return cat, sweet_spot_active, late_warning


# ── Unified public API ────────────────────────────────────────────────────────

def compute_profile_playbook_for_row(
    row: dict,
    universe: str,
    history_context: Optional[List[Set[str]]] = None,
) -> dict:
    """Single unified function for all Profile Playbook scoring.

    This is the ONLY function that should be called by:
      - api_bar_signals (SuperChart)
      - run_stock_stat
      - replay_engine step 1d
      - any other consumer

    Args:
        row: bar dict (list-column format from api_bar_signals, or CSV row from stock_stat)
        universe: "sp500", "nasdaq", etc.
        history_context: list of up to 5 prior bars' active signal sets,
                         ordered [most_recent_first] i.e. [1_bar_ago, 2_bars_ago, ...]

    Returns:
        Full profile playbook result dict.
    """
    profile_name = get_profile(row, universe)
    profile      = PROFILES.get(profile_name)

    # Extract current bar's active signals
    # Union stat-row (list/string columns) and turbo-row (boolean columns) to handle both formats
    active_signals: Set[str] = (
        extract_profile_signals_from_stat_row(row) | extract_signals_from_turbo_row(row)
    )

    # ── Base profile score (signal_weights + pair_bonuses) ────────────────────
    base_score = 0
    matched_signals: List[str] = []
    matched_pairs:   List[str] = []

    if profile:
        for sig, weight in profile["signal_weights"].items():
            if sig in active_signals:
                base_score += int(weight)
                matched_signals.append(sig)
        for pair, bonus in profile.get("pair_bonuses", {}).items():
            a, b = pair
            if a in active_signals and b in active_signals:
                base_score += int(bonus)
                matched_pairs.append(f"{a}+{b}")

    # ── Bear-context standalone score (global, capped) ────────────────────────
    bear_standalone = 0
    for sig, weight in BEAR_CONTEXT_SIGNALS.items():
        if sig in active_signals:
            bear_standalone += weight
    bear_standalone = min(bear_standalone, BEAR_CONTEXT_STANDALONE_CAP)

    # ── Sequence scoring (requires history context) ───────────────────────────
    bear_context_last_3   = False
    bear_context_last_5   = False
    bull_confirm_now      = False
    bear_to_bull_confirmed = False
    bear_to_bull_bars_ago  = 0
    bear_to_bull_bonus     = 0
    bear_to_bull_pairs:    List[str] = []

    bull_sigs_now = active_signals & BULL_CONFIRM_SIGNALS
    if bull_sigs_now:
        bull_confirm_now = True

    # Per-profile BTB cap (SP500_300_PLUS = 3, others = SEQUENCE_BONUS_CAP)
    btb_cap = PROFILE_BTB_CAPS.get(profile_name, SEQUENCE_BONUS_CAP)
    # Weak confirm signals scaled ×0.5 for high-price profiles (L34/VBO_UP/BO_UP too noisy)
    _weak_btb_confirms: Set[str] = {"L34", "VBO_UP", "BO_UP"}

    if history_context:
        raw_bonus = 0
        _min_bars_ago: Optional[int] = None
        is_weak_profile = profile_name in PROFILE_BTB_WEAK_CONFIRM_PROFILES
        for bars_ago, hist_sigs in enumerate(history_context, start=1):
            if bars_ago > 5:
                break
            bear_sigs = hist_sigs & set(BEAR_CONTEXT_SIGNALS.keys())
            if bear_sigs:
                if bars_ago <= 3:
                    bear_context_last_3 = True
                bear_context_last_5 = True
                if bull_confirm_now:
                    for bear_sig in bear_sigs:
                        seq_map = SEQUENCE_BONUSES.get(bear_sig, {})
                        for bull_sig in bull_sigs_now:
                            base = seq_map.get(bull_sig, 0)
                            if base > 0:
                                if is_weak_profile and bull_sig in _weak_btb_confirms:
                                    base = round(base * 0.5)
                                bonus = sequence_decay_bonus(base, bars_ago)
                                if bonus > 0:
                                    bear_to_bull_confirmed = True
                                    raw_bonus += bonus
                                    if _min_bars_ago is None or bars_ago < _min_bars_ago:
                                        _min_bars_ago = bars_ago
                                    bear_to_bull_pairs.append(f"{bear_sig}->{bull_sig}@{bars_ago}")
        bear_to_bull_bonus    = min(raw_bonus, btb_cap)
        bear_to_bull_bars_ago = _min_bars_ago or 0

    # ── Total score + category (with and without BTB) ─────────────────────────
    base_profile_score_without_btb = base_score + bear_standalone
    btb_late_clamped               = False
    btb_sweet_spot_allowed_profile = int(profile_name in BTB_SWEET_SPOT_ALLOWED_PROFILES)

    if profile:
        category_without_btb, _, _ = _score_to_category(base_profile_score_without_btb, profile)
        total_score = base_profile_score_without_btb + bear_to_bull_bonus
        category_with_btb_raw, sweet_spot_active, late_warning = _score_to_category(total_score, profile)
        cat = category_with_btb_raw
        # Gate 1: BTB must never create LATE — clamp to SWEET_SPOT first
        if category_without_btb != "LATE" and cat == "LATE":
            cat = "SWEET_SPOT"
            sweet_spot_active = True
            late_warning      = False
            btb_late_clamped  = True
        # Gate 2: BTB-created SWEET_SPOT only allowed for specific profiles
        if category_without_btb != "SWEET_SPOT" and cat == "SWEET_SPOT":
            if profile_name not in BTB_SWEET_SPOT_ALLOWED_PROFILES:
                cat = "BUILDING"
                sweet_spot_active = False
        category_with_btb = cat
    else:
        category_without_btb  = "WATCH"
        category_with_btb     = "WATCH"
        total_score           = base_profile_score_without_btb + bear_to_bull_bonus
        cat, sweet_spot_active, late_warning = "WATCH", False, False

    btb_category_upgrade    = cat != category_without_btb
    btb_created_sweet_spot  = (cat == "SWEET_SPOT" and category_without_btb != "SWEET_SPOT")

    # ── Unscored signals ──────────────────────────────────────────────────────
    _known: Set[str] = set(BEAR_CONTEXT_SIGNALS.keys()) | BULL_CONFIRM_SIGNALS
    for p in PROFILES.values():
        _known.update(p["signal_weights"].keys())
        for pair in p["pair_bonuses"].keys():
            _known.update(pair)
    for seq_map in SEQUENCE_BONUSES.values():
        _known.update(seq_map.keys())
    unscored = sorted(active_signals - _known)

    return {
        "profile_playbook_version":        PROFILE_PLAYBOOK_VERSION,
        "profile_name":                    profile_name,
        "profile_score":                   total_score,
        "profile_category":                cat,
        "sweet_spot_active":               sweet_spot_active,
        "late_warning":                    late_warning,
        "active_signals":                  sorted(active_signals),
        "matched_profile_signals":         matched_signals,
        "matched_profile_pairs":           matched_pairs,
        "unscored_signals":                unscored,
        "bear_context_last_3":             int(bear_context_last_3),
        "bear_context_last_5":             int(bear_context_last_5),
        "bull_confirm_now":                int(bull_confirm_now),
        "bear_to_bull_confirmed":          int(bear_to_bull_confirmed),
        "bear_to_bull_bars_ago":           bear_to_bull_bars_ago,
        "bear_to_bull_bonus":              bear_to_bull_bonus,
        "bear_to_bull_pairs":              bear_to_bull_pairs,
        # BTB calibration audit fields
        "base_profile_score_without_btb":  base_profile_score_without_btb,
        "category_without_btb":            category_without_btb,
        "category_with_btb":               category_with_btb,
        "btb_category_upgrade":            int(btb_category_upgrade),
        "btb_created_sweet_spot":          int(btb_created_sweet_spot),
        "btb_late_clamped":                int(btb_late_clamped),
        "btb_sweet_spot_allowed_profile":  btb_sweet_spot_allowed_profile,
        # legacy fields (kept for backward compatibility)
        "profile_role":              profile.get("role")              if profile else None,
        "profile_description":       profile.get("description")       if profile else None,
        "profile_experimental":      bool(profile.get("experimental", False)) if profile else False,
        "profile_preferred_preset":  profile.get("preferred_preset")  if profile else None,
        "profile_suggested_tp":      profile.get("suggested_tp")      if profile else None,
        "profile_suggested_sl":      profile.get("suggested_sl")      if profile else None,
        "profile_max_hold":          profile.get("max_hold")          if profile else None,
    }


# ── Legacy wrapper (backward compatibility for older call sites) ──────────────

def compute_profile_score(signals_5bar: Set[str], profile_name: str) -> dict:
    """Legacy wrapper — use compute_profile_playbook_for_row() for new code.

    Returns a result compatible with old callers that expected just base score.
    Bear-context standalone cap is applied. Sequence bonus is NOT applied
    (no history context). For full scoring, use compute_profile_playbook_for_row().
    """
    profile = PROFILES.get(profile_name)
    if not profile:
        return {
            "profile_name": profile_name, "profile_score": 0,
            "profile_category": "WATCH", "sweet_spot_active": False,
            "late_warning": False, "matched_profile_signals": [],
            "matched_profile_pairs": [],
        }

    score = 0
    matched_signals: List[str] = []
    for sig, weight in profile["signal_weights"].items():
        if sig in signals_5bar:
            score += int(weight)
            matched_signals.append(sig)

    matched_pairs: List[str] = []
    for pair, bonus in profile.get("pair_bonuses", {}).items():
        a, b = pair
        if a in signals_5bar and b in signals_5bar:
            score += int(bonus)
            matched_pairs.append(f"{a}+{b}")

    bear_standalone = 0
    for sig, weight in BEAR_CONTEXT_SIGNALS.items():
        if sig in signals_5bar:
            bear_standalone += weight
    score += min(bear_standalone, BEAR_CONTEXT_STANDALONE_CAP)

    cat, sweet_spot_active, late_warning = _score_to_category(score, profile)
    return {
        "profile_name":            profile_name,
        "profile_score":           score,
        "profile_category":        cat,
        "sweet_spot_active":       sweet_spot_active,
        "late_warning":            late_warning,
        "matched_profile_signals": matched_signals,
        "matched_profile_pairs":   matched_pairs,
    }


# ── Row enrichment (legacy helper) ───────────────────────────────────────────

_CANONICAL_FIELDS = frozenset({
    "FINAL_BULL_SCORE", "TURBO_SCORE", "turbo_score",
    "SIGNAL_SCORE", "signal_score", "RTB_SCORE", "rtb_total",
    "FINAL_REGIME", "final_regime",
    "HARD_BEAR_SCORE", "ROCKET_SCORE", "GOG_SCORE",
})


def enrich_row_with_profile(
    row: dict,
    universe: str,
    signals: Optional[Set[str]] = None,
    history_context: Optional[List[Set[str]]] = None,
) -> dict:
    """Add profile playbook fields to a row dict. Does NOT overwrite canonical score columns."""
    if signals is not None:
        # Legacy: signals passed explicitly — use legacy compute_profile_score path
        profile_name = get_profile(row, universe)
        result       = compute_profile_score(signals, profile_name)
    else:
        result = compute_profile_playbook_for_row(row, universe, history_context)

    out = dict(row)
    for k, v in result.items():
        if k not in _CANONICAL_FIELDS:
            out[k] = v
    return out


# ── Unscored signal audit ──────────────────────────────────────────────────────

def profile_unscored_signals(rows: List[dict]) -> List[dict]:
    """Find signal tokens in rows not covered by any profile scoring rule.

    CSV columns: generated_at, raw_signal, normalized_signal, count,
                 source_columns, example_tickers, example_dates
    """
    # Build the full "known" set — signals covered by any scoring rule
    _known: Set[str] = set(BEAR_CONTEXT_SIGNALS.keys()) | BULL_CONFIRM_SIGNALS
    for p in PROFILES.values():
        _known.update(p["signal_weights"].keys())
        for pair in p["pair_bonuses"].keys():
            _known.update(pair)
    for seq_map in SEQUENCE_BONUSES.values():
        _known.update(seq_map.keys())

    sig_data: Dict[str, dict] = {}

    def _track(raw_tok: str, norm: str, col_label: str, ticker: str, date: str) -> None:
        if not norm or norm in _known:
            return
        if norm not in sig_data:
            sig_data[norm] = {"raw_signal": raw_tok, "count": 0,
                              "source_columns": set(), "tickers": [], "dates": []}
        d = sig_data[norm]
        d["count"] += 1
        d["source_columns"].add(col_label)
        if len(d["tickers"]) < 3:
            d["tickers"].append(ticker)
            d["dates"].append(date)

    for r in rows:
        ticker = str(r.get("ticker", ""))
        date   = str(r.get("date", ""))
        for bar_key, csv_key in _STAT_COL_PAIRS:
            val = r.get(bar_key)
            col_label = bar_key
            if val is None:
                val = r.get(csv_key)
                col_label = csv_key
            if val is None:
                continue
            if isinstance(val, list):
                for tok in val:
                    raw = str(tok).strip()
                    if raw:
                        _track(raw, normalize_signal_name(raw), col_label, ticker, date)
            elif isinstance(val, str) and val.strip():
                for raw in re.split(r"[\s,|;]+", val.strip()):
                    if raw:
                        _track(raw, normalize_signal_name(raw), col_label, ticker, date)
        for tz_key in ("tz", "Z", "T"):
            tz = str(r.get(tz_key) or "").strip()
            if tz:
                _track(tz, normalize_signal_name(tz), tz_key, ticker, date)

    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    out = [
        {
            "generated_at":      now,
            "raw_signal":        d["raw_signal"],
            "normalized_signal": norm,
            "count":             d["count"],
            "source_columns":    "|".join(sorted(d["source_columns"])),
            "example_tickers":   "|".join(d["tickers"]),
            "example_dates":     "|".join(d["dates"]),
        }
        for norm, d in sig_data.items()
        if d["count"] >= 5
    ]
    out.sort(key=lambda x: -x["count"])
    return out


# ── Config snapshot ───────────────────────────────────────────────────────────

def get_playbook_config_snapshot() -> dict:
    """Return a JSON-serialisable snapshot of the currently active scoring config.

    Write to profile_playbook_config_snapshot.json to record which config was
    used for a given stock_stat / replay run.
    """
    profiles_snap = {}
    for name, p in PROFILES.items():
        profiles_snap[name] = {
            "universe":      p["universe"],
            "price_range":   list(p["price_range"]) if p["price_range"][1] != float("inf")
                             else [p["price_range"][0], None],
            "sweet_spot":    list(p["sweet_spot"]),
            "late_threshold": p["late_threshold"],
            "signal_weights": dict(p["signal_weights"]),
            "pair_bonuses":  {f"{a}+{b}": v for (a, b), v in p["pair_bonuses"].items()},
        }
    return {
        "profile_playbook_version": PROFILE_PLAYBOOK_VERSION,
        "generated_at":             _time.strftime("%Y-%m-%dT%H:%M:%S"),
        "aliases_count":            len(SIGNAL_ALIASES),
        "profiles":                 profiles_snap,
        "bear_context_signals":     dict(BEAR_CONTEXT_SIGNALS),
        "bear_context_standalone_cap": BEAR_CONTEXT_STANDALONE_CAP,
        "bull_confirm_signals":     sorted(BULL_CONFIRM_SIGNALS),
        "sequence_bonuses":         {k: dict(v) for k, v in SEQUENCE_BONUSES.items()},
        "sequence_bonus_cap":       SEQUENCE_BONUS_CAP,
        "profile_btb_caps":         dict(PROFILE_BTB_CAPS),
        "profile_btb_weak_confirm_profiles": sorted(PROFILE_BTB_WEAK_CONFIRM_PROFILES),
        "btb_sweet_spot_allowed_profiles":   sorted(BTB_SWEET_SPOT_ALLOWED_PROFILES),
    }
