"""
turbo_engine.py — TURBO combined scan engine.

Runs ALL signal engines per ticker and produces a single turbo_score (0-100)
covering: VABS, Wyckoff, Combo (2809), T/Z patterns, WLNBB (L/FRI/BLUE),
Wick.

turbo_score tiers:
  55+  Fire   — multiple strong signals aligning
  40+  Strong — solid multi-engine confirmation
  25+  Bull   — base bullish setup
  <25  Weak   — sparse signals
"""
from __future__ import annotations

import gc
import os
import logging
import time
from datetime import datetime, timezone
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed, wait as _cf_wait, FIRST_COMPLETED

import json
import numpy as np
import pandas as pd

from db import get_db, USE_PG, pk_col

from indicators    import rsi as _rsi_ind, cci as _cci_ind
from signal_engine import compute_signals, compute_b_signals, compute_g_signals
from f_engine import compute_f_signals
from wlnbb_engine  import compute_wlnbb
from combo_engine  import compute_combo, compute_tz_state
from wick_engine   import compute_wick, compute_wick_x
from vabs_engine   import compute_vabs
from ultra_engine  import compute_260308_l88, compute_ultra_v2
from delta_engine   import compute_delta

log = logging.getLogger(__name__)

# ── Progress ──────────────────────────────────────────────────────────────────
_turbo_state: dict = {
    "running": False,
    "done": 0,
    "total": 0,
    "found": 0,
    "failed": 0,
    "fetched_from_massive": 0,
    "started_at": 0,
    "completed_at": None,
    "universe": None,
    "interval": None,
    "error": None,
    "tfs_total": 1,
    "tfs_done": 0,
}

_SCAN_TIMEOUT = 30 * 60  # 30 minutes max before auto-reset

# All main timeframes covered by a single scan
ALL_SCAN_TFS = ['1wk', '1d', '4h', '1h']


def get_turbo_progress() -> dict:
    state = dict(_turbo_state)
    now = time.time()
    # auto-reset if stuck longer than timeout
    if state["running"] and now - state.get("started_at", 0) > _SCAN_TIMEOUT:
        _turbo_state["running"] = False
        state["running"] = False
    # compute elapsed
    if state.get("started_at"):
        state["elapsed"] = round(now - state["started_at"], 1) if state["running"] else (
            round((state.get("completed_at") or now) - state["started_at"], 1)
        )
    else:
        state["elapsed"] = 0
    # estimate remaining
    done = state.get("done", 0)
    total = state.get("total", 0)
    elapsed = state["elapsed"]
    if state["running"] and done > 5 and total > done:
        rate = done / elapsed if elapsed > 0 else 0
        state["eta"] = round((total - done) / rate, 0) if rate > 0 else None
    else:
        state["eta"] = None
    return state


# ── T/Z signal weights (profile-specific) ────────────────────────────────────
_TZ_W_BASE = {
    "T4": 9, "T6": 9,
    "T1G": 6, "T2G": 8,
    "T1": 7, "T2": 5,
    "T9": 4, "T10": 4,
    "T3": 2, "T11": 2, "T5": 1,
}
# SP500: T4/T6 weaker as standalone signals on large-caps
_TZ_W_SP500  = {**_TZ_W_BASE, "T4": 6, "T6": 6}
# NASDAQ: T4/T6 are pre-breakout triggers; context combos give the full uplift
_TZ_W_NASDAQ = {**_TZ_W_BASE, "T4": 7, "T6": 7, "T1": 5, "T9": 3}
# ALL-US: T1 reclaim is the cleanest reversal; T4/T6 heavily demoted (combo block provides uplift)
_TZ_W_ALL_US = {**_TZ_W_BASE, "T4": 3, "T6": 3, "T1": 8, "T1G": 6, "T2G": 5, "T2": 4, "T9": 4}
# Backward-compat alias
_TZ_W = _TZ_W_BASE

# ── DB columns ────────────────────────────────────────────────────────────────
_TURBO_COLS = [
    "turbo_score",
    # VABS
    "best_sig", "strong_sig", "vbo_up", "vbo_dn",
    "abs_sig", "climb_sig", "load_sig",
    # Wyckoff (VABS legacy)
    "ns", "nd", "sc", "bc", "sq",
    # Combo / 2809
    "buy_2809", "rocket", "sig3g", "rtv",
    "hilo_buy", "hilo_sell", "atr_brk", "bb_brk",
    "bias_up", "bias_down", "cons_atr",
    "um_2809", "svs_2809", "conso_2809",
    # B signals (260321) — B1–B11, no RSI filter
    "b1", "b2", "b3", "b4", "b5",
    "b6", "b7", "b8", "b9", "b10", "b11",
    # F signals (260418 F Builder) — F1–F11 + any_f
    "f1", "f2", "f3", "f4", "f5", "f6",
    "f7", "f8", "f9", "f10", "f11", "any_f",
    # G signals (260410) — armed by Z10/Z11/Z12, no RSI filter
    "g1", "g2", "g4", "g6", "g11",
    # seqBContLite (260412) — continuation sequence T/Z patterns
    "seq_bcont",
    # VA — ATR Volume Confirm crossover (260402_COMBO_OSC)
    "va",
    # Volume spike signals (vs previous bar)
    "vol_spike_5x", "vol_spike_10x", "vol_spike_20x",
    # TZ state + confluences + transition signals (260412)
    "tz_state", "ca", "cd", "cw",
    "tz_bull_flip", "tz_attempt",
    # W signals — Bear Dominance→Weakening transition (260414)
    "tz_weak_bull", "tz_weak_bear",
    # T/Z
    "tz_sig", "tz_bull",
    # WLNBB
    "fri34", "fri43", "fri64",
    "l34", "l43", "l64", "l22", "l555", "only_l2l4",
    "blue", "cci_ready", "cci_0_retest", "cci_blue_turn",
    "bo_up", "bo_dn", "bx_up", "bx_dn",
    "be_up", "be_dn",
    "fuchsia_rh", "fuchsia_rl",
    "pre_pump",
    # Wick (3112_2C legacy confirm)
    "wick_bull", "wick_bear",
    # Wick X signals (260402_WICK)
    "x2g_wick", "x2_wick", "x1g_wick", "x1_wick", "x3_wick",
    # meta
    "vol_bucket",
    # Delta / order-flow (260403 V2)
    "d_strong_bull", "d_strong_bear",
    "d_absorb_bull", "d_absorb_bear",
    "d_div_bull",    "d_div_bear",
    "d_cd_bull",     "d_cd_bear",
    "d_surge_bull",  "d_surge_bear",
    "d_blast_bull",  "d_blast_bear",
    "d_vd_div_bull", "d_vd_div_bear",
    "d_spring",      "d_upthrust",
    "d_flip_bull",   "d_flip_bear",   "d_orange_bull",
    "d_blast_bull_red", "d_blast_bear_grn",
    "d_surge_bull_red", "d_surge_bear_grn",
    # RSI / CCI
    "rsi", "cci",
    # 260308 + L88
    "sig_260308", "sig_l88",
    # ULTRA v2
    "eb_bull", "eb_bear",
    "fbo_bull", "fbo_bear",
    "bf_buy", "bf_sell",
    "ultra_3up", "ultra_3dn",
    "best_long", "best_short",
    # RS / Relative Strength vs SPY+IWM
    "rs", "rs_strong",
    # RGTI (260404) + SMX (260402) — multi-TF EMA alignment signals
    "rgti_ll", "rgti_up", "rgti_upup", "rgti_upupup",
    "rgti_orange", "rgti_green", "rgti_greencirc", "smx",
    # PARA (260420) — Parabola Start Detector v3.6
    "para_prep", "para_start", "para_plus", "para_retest",
    # FLY (260424) — ABCD EMA DP pattern
    "fly_abcd", "fly_cd", "fly_bd", "fly_ad",
    # PREUP (EMA cross ↑)
    "preup66", "preup55", "preup89", "preup3", "preup2", "preup50",
    # PREDN (EMA drop ↓)
    "predn66", "predn55", "predn89", "predn3", "predn2", "predn50",
    # N=3, N=5 and N=10 scores (client-side N= switching without rescan)
    "turbo_score_n3",
    "turbo_score_n5",
    "turbo_score_n10",
    # Signal ages JSON {"signal_key": bars_since_last_fire, ...}
    "sig_ages",
    # Data source: "polygon" | "yfinance" (shown as badge in UI)
    "data_source",
    # Average daily volume (20-bar SMA) — used for volume filter
    "avg_vol",
    # EMA values for price-vs-EMA frontend filters
    "ema20", "ema50", "ema89", "ema200",
    # GICS sector (from yfinance info, cached)
    "sector",
    # RTB v4 — Reversal-To-Breakout phase scores
    "rtb_build", "rtb_turn", "rtb_ready", "rtb_bonus3",
    "rtb_late", "rtb_total",
    "rtb_phase", "rtb_transition",
    "rtb_phase_age",
    # Profile lookback flags — 5b window
    "_ztrap_recent_5b", "_l64_recent_5b", "_l43_recent_5b", "_l22_recent_5b",
    "_blue_recent_5b", "_l34_recent_3b", "_fri34_recent_3b", "_dabsorb_recent_5b",
    # Extended lookback flags — ALL-US profile (15b / 10b windows)
    "_ztrap_recent_15b", "_l64_recent_15b", "_l22_recent_15b",
    "_l43_recent_10b", "_ns_recent_5b", "_t10t11_recent_5b",
    # Composite setup signals — GOG Priority Engine (260501 FULL)
    "smx_sig", "akan_sig", "nnn_sig", "mx_sig", "gog_sig",
    "gog_tier", "gog_score", "already_extended",
    # GOG boosted tiers
    "gog_g1p", "gog_g2p", "gog_g3p",
    "gog_g1l", "gog_g2l", "gog_g3l",
    "gog_g1c", "gog_g2c", "gog_g3c",
    "gog_gog1", "gog_gog2", "gog_gog3",
    # Context signals
    "ctx_ld", "ctx_lds", "ctx_ldc", "ctx_ldp",
    "ctx_lrc", "ctx_lrp", "ctx_wrc", "ctx_f8c",
    "ctx_sqb", "ctx_bct", "ctx_svs",
]


# ── Helpers ───────────────────────────────────────────────────────────────────
def _db():
    return get_db()


def _col_def(c: str) -> str:
    _TEXT = {"tz_sig", "vol_bucket", "sig_ages", "data_source", "sector",
             "rtb_phase", "rtb_transition", "gog_tier"}
    _REAL = {"turbo_score", "turbo_score_n3", "turbo_score_n5", "turbo_score_n10", "rsi", "cci", "avg_vol",
             "ema20", "ema50", "ema89", "ema200",
             "rtb_build", "rtb_turn", "rtb_ready", "rtb_bonus3", "rtb_late", "rtb_total", "gog_score"}
    typ     = "TEXT"    if c in _TEXT else "REAL" if c in _REAL else "INTEGER"
    default = "''"      if c in _TEXT else "0"
    return f"    {c}  {typ}  DEFAULT {default},"


def _init_db() -> None:
    con = get_db()
    cols_sql = "\n".join(_col_def(c) for c in _TURBO_COLS)
    _pk = pk_col()
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS turbo_scan_runs (
            id           {_pk},
            tf           TEXT    DEFAULT '1d',
            universe     TEXT    DEFAULT 'sp500',
            started_at   TEXT,
            completed_at TEXT,
            result_count INTEGER DEFAULT 0,
            is_complete  INTEGER DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS turbo_scan_results (
            id         {_pk},
            scan_id    INTEGER,
            ticker     TEXT NOT NULL,
{cols_sql}
            last_price REAL,
            change_pct REAL,
            scanned_at TEXT
        );
    """)
    con.commit()
    # migration: add any missing columns
    _TEXT_COLS = {"tz_sig", "vol_bucket", "sig_ages", "data_source"}
    _REAL_COLS = {"turbo_score", "turbo_score_n3", "turbo_score_n5", "turbo_score_n10",
                  "rsi", "cci", "avg_vol"}
    try:
        existing = con.table_columns("turbo_scan_results")
        for col in _TURBO_COLS:
            if col not in existing:
                typ     = "TEXT" if col in _TEXT_COLS else "REAL" if col in _REAL_COLS else "INTEGER"
                default = "''"   if col in _TEXT_COLS else "0"
                # Use IF NOT EXISTS on PG to avoid race-condition failures across workers
                if_not = "IF NOT EXISTS " if USE_PG else ""
                con.execute(
                    f"ALTER TABLE turbo_scan_results ADD COLUMN {if_not}{col} {typ} DEFAULT {default}"
                )
        run_cols = con.table_columns("turbo_scan_runs")
        if "tf" not in run_cols:
            con.execute("ALTER TABLE turbo_scan_runs ADD COLUMN tf TEXT DEFAULT '1d'")
        if "universe" not in run_cols:
            con.execute("ALTER TABLE turbo_scan_runs ADD COLUMN universe TEXT DEFAULT 'sp500'")
        if "is_complete" not in run_cols:
            con.execute("ALTER TABLE turbo_scan_runs ADD COLUMN is_complete INTEGER DEFAULT NULL")
        con.commit()
    except Exception as _mig_exc:
        log.warning("_init_db migration warning (non-fatal): %s", _mig_exc)
        try:
            if USE_PG:
                con._pg.rollback()
        except Exception:
            pass
    finally:
        con.close()


# ── Score calculator ──────────────────────────────────────────────────────────
def _calc_turbo_score(r: dict, profile: str = "sp500") -> float:
    """
    Statistics-based scoring v3 (SP500 pooled stats, 500 tickers 2yr + co-occurrence analysis).
    Core backbone: conso_2809 (79% freq) → tz_bull (65%) → bf_buy (43%).
    Rarer signals score higher; redundant subsets don't double-count.
      Backbone      cap 18  — conso_2809, tz_bull, chain bonus
      Volume/accum  cap 22  — VABS atomic, Wyckoff, 260308/L88, svs_2809
      Breakout      cap 18  — ULTRA v2, BO/BX (rare→+5), RS
      Combo/trend   cap 14  — Combo signals
      L-structure   cap 13  — T/Z, WLNBB, RL Avg3=2.80, W Avg3=2.42
      Delta         cap 12  — Order-flow
      EMA cross     cap 8   — preup series
      G signals     cap 10  — G2 Avg3=2.64%/Win%=54.9% (best)
    Context (Wick+PARA+FLY+Vol×10) uncapped (max ~25).
    """
    has_conso   = bool(r.get("conso_2809"))
    has_tz_bull = bool(r.get("tz_bull"))
    has_bf_buy  = bool(r.get("bf_buy"))

    # ── Backbone / setup chain (cap 18) ───────────────────────────────────
    # Weights inverse-proportional to frequency: conso 79%→4, tz_bull 65%→6
    # Chain bonus rewards co-occurrence of the full 3-signal backbone
    bkb = 0.0
    if has_conso:   bkb += 4
    if has_tz_bull: bkb += 6
    if has_conso and has_tz_bull and has_bf_buy:
        bkb += 8   # full bullish chain — statistically most predictive combo
    elif has_conso and has_tz_bull:
        bkb += 3   # partial chain (setup without entry confirmation)
    s = min(bkb, 18)

    # ── Volume / accumulation family (cap 22) ─────────────────────────────
    # Weights v4 per optimization analysis (SP500 pooled stats):
    #   CLB Avg3=2.80 → +5; LD Avg3=2.69 → +5; VBO Avg3=2.37 → +4; NS Avg3=2.35 → +4
    vol = 0.0
    if r.get("abs_sig"):   vol += 5
    if r.get("climb_sig"): vol += 5   # Avg3=2.80
    if r.get("load_sig"):  vol += 5   # Avg3=2.69 (RAISE 4→5)
    if r.get("vbo_up"):    vol += 4   # Avg3=2.37 (LOWER 5→4)
    if r.get("ns"):        vol += 4   # Avg3=2.35 (LOWER 5→4)
    if r.get("sq"):        vol += 5   # Win%=57.5%, Avg3=2.63 (RAISE 4→5)
    if r.get("sc"):        vol += 2
    if r.get("svs_2809"):  vol += 3   # volume expansion within conso_2809 setup
    if r.get("um_2809"):   vol += 3   # NASDAQ: 67% A with tz_bull, 49% with bf_buy
    if r.get("sig_l88"):        vol += 5
    elif r.get("sig_260308"):   vol += 3
    if r.get("va"):             vol += 3
    s += min(vol, 22)

    # ── Breakout / expansion family (cap 18) ──────────────────────────────
    # bf_buy: 43% freq → raised +4→+6 (medium rarity, strong entry signal)
    # bo_up:  14% freq → raised +3→+5 (rare → higher information value)
    brk = 0.0
    if has_bf_buy:          brk += 6
    if r.get("fbo_bull"):   brk += 5
    if r.get("eb_bull"):    brk += 4   # Avg3=2.39 (RAISE 3→4)
    if r.get("be_up"):      brk += 10  # BE breakout — full-body engulf
    if r.get("ultra_3up"):  brk += 3   # Avg3=1.65 (RAISE 2→3)
    if r.get("bo_up") or r.get("bx_up"): brk += 5
    if r.get("rs_strong"):  brk += 5
    elif r.get("rs"):       brk += 3
    s += min(brk, 18)

    # ── Combo / momentum family (cap 14) ──────────────────────────────────
    combo = 0.0
    if r.get("rocket"):
        combo += 12
    elif r.get("buy_2809"):
        combo += 8
    if r.get("sig3g"):    combo += 4
    if r.get("rtv"):      combo += 3
    if r.get("hilo_buy"): combo += 4   # NASDAQ: 93% A with conso_2809, raised from +2
    if r.get("atr_brk") or r.get("bb_brk"): combo += 2
    if r.get("cd"):   combo += 5
    elif r.get("ca"): combo += 3
    elif r.get("cw"): combo += 2
    if r.get("seq_bcont"): combo += 3
    s += min(combo, 14)

    # ── L-structure / trend family (cap 13) ───────────────────────────────
    trend = 0.0
    if profile == "nasdaq":
        _tz_w = _TZ_W_NASDAQ
    elif profile == "all_us":
        _tz_w = _TZ_W_ALL_US
    else:
        _tz_w = _TZ_W_SP500
    trend += _tz_w.get(r.get("tz_sig", ""), 0)
    # tz_bull_flip: 100%A/98%C triple (FLP↑+4BF+T/Z↑) — strong pattern, raised +1→+3 with bf_buy
    if r.get("tz_bull_flip"):
        trend += 3 if has_bf_buy else 4
    elif r.get("tz_attempt"):
        trend += 2
    if r.get("fri34"):
        trend += 6
    elif r.get("fri43"):
        trend += 4
    if r.get("l34") and not r.get("fri34"): trend += 5
    if r.get("blue"):         trend += 5   # Avg3=2.76 (RAISE 3→5)
    if r.get("cci_ready"):    trend += 2
    if r.get("l43") and not r.get("fri43") and not r.get("fri34"): trend += 5  # Avg3=2.60 (RAISE 4→5)
    if r.get("fuchsia_rl"):   trend += 5   # Avg3=2.80% (RAISE 3→5)
    if r.get("tz_weak_bull"): trend += 2   # W — BearWeak↑ early turn (ADD v3)
    s += min(trend, 17)

    # ── Delta / order-flow family (cap 12) ───────────────────────────────
    # Delta weights v4: dSPR +6; Ab↑ +6; ΔΔ↑ +5; Δ↑ +5; T↓ +5; B/S↑ +4
    dlt = 0.0
    if r.get("d_blast_bull"):        dlt += 5   # Avg3=2.46
    elif r.get("d_surge_bull"):      dlt += 5   # Avg3=2.43 (RAISE 4→5)
    if r.get("d_strong_bull"):       dlt += 4   # Avg3=2.03 (RAISE 2→4)
    if r.get("d_absorb_bull"):       dlt += 6   # Avg3=2.99 #3 overall
    if r.get("d_spring"):            dlt += 6   # Avg3=3.36 #1 overall
    elif r.get("d_div_bull"):        dlt += 5   # Avg3=2.54 (RAISE 4→5)
    if r.get("d_vd_div_bull"):       dlt += 3
    elif r.get("d_cd_bull"):         dlt += 2
    s += min(dlt, 12)

    # ── EMA cross family (cap 8) ──────────────────────────────────────────
    ema_x = 0.0
    if r.get("preup66"):   ema_x += 8
    elif r.get("preup55"): ema_x += 6
    elif r.get("preup89"): ema_x += 5
    elif r.get("preup3"):  ema_x += 5   # RAISE 3→5 (pre-breakout attention)
    elif r.get("preup2"):  ema_x += 4   # RAISE 2→4 (pre-breakout attention)
    s += min(ema_x, 10)

    # ── G signals family (cap 10) ─────────────────────────────────────────
    # SP500 pooled: G2 Avg3=2.64% Win%=54.9% — best G (RAISE 4→5)
    g_sig = 0.0
    if r.get("g2"):   g_sig += 5   # Avg3=2.64% Win%=54.9% (RAISE 4→5)
    if r.get("g4"):   g_sig += 3
    if r.get("g1"):   g_sig += 3
    if r.get("g6"):   g_sig += 2
    if r.get("g11"):  g_sig += 2
    s += min(g_sig, 10)

    # ── Backtest-proven confluence bonuses (cap 18) ───────────────────────
    # Source: Run 25 (n=2254, Jan–Apr 2026). Signal mapping:
    #   D4 ≡ d_absorb_bull | d_spring   (institutional absorption)
    #   D6 ≡ d_surge_bull  | d_blast_bull (delta surge/blast)
    #   L34 ≡ l34 | fri34               (WLNBB setup bar)
    _d4  = bool(r.get("d_absorb_bull") or r.get("d_spring"))
    _d6  = bool(r.get("d_surge_bull")  or r.get("d_blast_bull"))
    _l34 = bool(r.get("l34") or r.get("fri34"))
    _be  = bool(r.get("be_up"))
    _l34_r3 = bool(r.get("_l34_recent_3b") or r.get("_fri34_recent_3b"))
    _dabs_r5 = bool(r.get("_dabsorb_recent_5b"))

    conf = 0.0
    # ── Tier 1: SAME-BAR confluences ──────────────────────────────────────
    # D6+BE_UP same bar: +6.26% avg 5d, 71% win rate  (n=32)
    if _d6 and _be:
        conf += 12
    # D4+L34 same bar:  +2.53% avg 5d, 70.8% win  (n=24) — LOWER 8→5
    if _d4 and _l34:
        conf += 5
    # D4+BE_UP same bar: +2.89% avg 5d  (n=52) — LOWER 6→5
    if _d4 and _be:
        conf += 5

    # ── Tier 2: SEQUENCE bonuses (prior setup → trigger fires) ────────────
    # L34 fired 1-3 bars ago, D4 fires now → L34_THEN_D4_3B: +7.87% (n=31) — RAISE 10→15
    if _l34_r3 and _d4 and not _l34:
        conf += 15
    # L34 fired 1-3 bars ago, BE_UP fires now → TRIGGER_AFTER_L34: +1.77% (n=55) — LOWER 5→3
    if _l34_r3 and _be and not _l34:
        conf += 3
    # D4 fired 1-5 bars ago, BE_UP fires now → D4_THEN_BEUP_5B: +5.33% (n=54) — RAISE 8→10
    if _dabs_r5 and _be and not _d4:
        conf += 10

    # ── Tier 3: State bonuses ─────────────────────────────────────────────
    # ACCUMULATION_READY analog (ns+tight range+l34): +1.17% avg, 66.1% win
    if r.get("ns") and r.get("cons_atr") and _l34:
        conf += 4

    s += min(conf, 18)

    # ── SP500 profile combo bonuses (cap 20) ──────────────────────────────────
    # Based on pooled SP500 1D analysis — strongest contextual combos
    if profile == "sp500":
        _ztrap_r = bool(r.get("_ztrap_recent_5b"))
        _l64_rp  = bool(r.get("_l64_recent_5b"))
        _l43_rp  = bool(r.get("_l43_recent_5b"))
        _l22_rp  = bool(r.get("_l22_recent_5b"))
        _t1_t1g  = r.get("tz_sig", "") in ("T1", "T1G")
        sp_cb = 0.0
        # SQ + CLM + Ztrap_recent: accumulation surge inside trap context
        if r.get("sq") and r.get("climb_sig") and _ztrap_r:
            sp_cb += 12
        # SQ + LOAD + Ztrap_recent: loading + squeeze in trap zone
        if r.get("sq") and r.get("load_sig") and _ztrap_r:
            sp_cb += 12
        # NS + UM: supply exhaustion + institutional ignition
        if r.get("ns") and r.get("um_2809"):
            sp_cb += 10
        # L43_recent + CLM + Ztrap: structural reset + climber in trap
        if _l43_rp and r.get("climb_sig") and _ztrap_r:
            sp_cb += 10
        # L64_recent + T1/T1G + SVS: long structural base + turn + volume expansion
        if _l64_rp and _t1_t1g and r.get("svs_2809"):
            sp_cb += 12
        # L22/L64_context + SQ + Ztrap: structural context + squeeze in trap
        if (_l22_rp or _l64_rp) and r.get("sq") and _ztrap_r:
            sp_cb += 8
        s += min(sp_cb, 20)

    # ── NASDAQ profile combo bonuses (cap 25) ─────────────────────────────────
    # Based on pooled NASDAQ 1D analysis — UM-centric, C-phase, B_TO_C combos
    elif profile == "nasdaq":
        _c_phase = r.get("rtb_phase") == "C"
        _btoc    = r.get("rtb_transition") == "B_TO_C"
        _ztrap_r = bool(r.get("_ztrap_recent_5b"))
        _l64_rp  = bool(r.get("_l64_recent_5b"))
        _l22_rp  = bool(r.get("_l22_recent_5b"))
        _blue_rp = bool(r.get("_blue_recent_5b"))
        _um      = bool(r.get("um_2809"))
        _tz_cur  = r.get("tz_sig", "")
        _t6      = (_tz_cur == "T6")
        _t4      = (_tz_cur == "T4")
        nq_cb = 0.0
        # Tier 1 — strongest NASDAQ combos
        # ZTRAP_T6_C: trap context + T6 trigger + C phase
        if _ztrap_r and _t6 and _c_phase:
            nq_cb += 14
        # L64_UM_T6: structural base + ignition + breakout trigger
        if _l64_rp and _um and _t6:
            nq_cb += 14
        # UM_BE: institutional ignition + full-body breakout engulf
        if _um and r.get("be_up"):
            nq_cb += 12
        # ZTRAP_UM_T4: trap + ignition + T4 reversal bar
        if _ztrap_r and _um and _t4:
            nq_cb += 12
        # T6_BTOC: T6 trigger exactly on B→C phase transition
        if _t6 and _btoc:
            nq_cb += 12
        # Tier 2 — supportive NASDAQ combos
        # SQ_BL_C: accumulation squeeze + bullish label + C phase
        if r.get("sq") and (r.get("blue") or _blue_rp) and _c_phase:
            nq_cb += 8
        # LOAD_BL_C: loading + bullish label + C phase
        if r.get("load_sig") and (r.get("blue") or _blue_rp) and _c_phase:
            nq_cb += 8
        # UM_VBO: institutional ignition + volume breakout
        if _um and r.get("vbo_up"):
            nq_cb += 8
        # UM_BX: institutional ignition + BB breakout
        if _um and r.get("bx_up"):
            nq_cb += 8
        # L22/L64 + SQ + LOAD: double volume activation in structural context
        if (_l22_rp or _l64_rp) and r.get("sq") and r.get("load_sig"):
            nq_cb += 8
        s += min(nq_cb, 25)

    # ── Kill / penalty conditions ─────────────────────────────────────────
    # ISOLATED G trigger (no L34/BE_UP/D4): -1.80% avg, 34.7% of all FPs
    if (r.get("g4") or r.get("g6")) and not _l34 and not _be and not _d4:
        s -= 4
    # d_strong_bull alone (no structure): IMPULSE_ONLY path -1.66% avg 5d
    if r.get("d_strong_bull") and not _l34 and not _be and not _d4 and not _d6:
        s -= 3
    # D6+L34 without BE_UP: avg_5d -2.52% (opposite of D6+BE_UP which is +6.26%)
    if _d6 and _l34 and not _be:
        s -= 5
    # OVERHEATED EXPANSION: RSI > 75 without delta absorption confirmation
    _rsi = float(r.get("rsi", 50.0))
    if _rsi > 80 and not _d4 and not _d6:
        s -= 6
    elif _rsi > 75 and not _d4 and not _d6 and not _be:
        s -= 3
    # Buying Climax (bc) without BE_UP: distribution risk, mean-reversion likely
    if r.get("bc") and not _be:
        s -= 3

    # ── NASDAQ-specific demotions ──────────────────────────────────────────────
    if profile == "nasdaq":
        # RL alone: weak on pooled NASDAQ without activation context
        if r.get("fuchsia_rl") and not r.get("um_2809") and not _be and not r.get("vbo_up") and not r.get("bx_up"):
            s -= 2
        # T4/T6 without any NASDAQ context: slight demotion (combos give the uplift)
        if r.get("tz_sig", "") in ("T4", "T6"):
            _has_nq_ctx = (
                bool(r.get("_ztrap_recent_5b")) or
                bool(r.get("_l64_recent_5b"))   or
                bool(r.get("um_2809"))           or
                r.get("rtb_phase") == "C"        or
                r.get("rtb_transition") == "B_TO_C"
            )
            if not _has_nq_ctx:
                s -= 2

    # ── ALL-US profile — combo bonuses + bearish breakdown (cap 22 bull) ────────
    # Design: combo-gated, background+activation model.
    # Background window ~15b (Ztrap, L64/L22/L43), trigger window ~5b (SQ/CLM/LOAD/T1/SVS).
    elif profile == "all_us":
        # Read extended lookback flags (populated by _scan_turbo_ticker)
        _ztrap_5  = bool(r.get("_ztrap_recent_5b"))
        _ztrap_15 = bool(r.get("_ztrap_recent_15b"))
        _l64_15   = bool(r.get("_l64_recent_15b"))
        _l22_15   = bool(r.get("_l22_recent_15b"))
        _l43_10   = bool(r.get("_l43_recent_10b"))
        _ns_5     = bool(r.get("_ns_recent_5b"))
        _ztrap_bg = _ztrap_5 or _ztrap_15         # any recent trap background
        _l_struct = _l64_15 or _l22_15            # broad structural support
        _sq       = bool(r.get("sq"))
        _clm      = bool(r.get("climb_sig"))
        _load     = bool(r.get("load_sig"))
        _svs      = bool(r.get("svs_2809"))
        _t1_cur   = r.get("tz_sig", "") == "T1"
        _t1g_cur  = r.get("tz_sig", "") == "T1G"
        _t1_any   = _t1_cur or _t1g_cur

        # ── BULLISH COMBO BLOCK ───────────────────────────────────────────
        au_bull = 0.0

        # Tier 1 — structure + activation + trap (all 3 present) [n production-ready]
        if _sq and _clm and _ztrap_bg:
            au_bull += 14  # SQ_CLM_ZTRAP: stop-action + climber inside trap
        if _sq and _load and _ztrap_bg:
            au_bull += 12  # SQ_LOAD_ZTRAP: squeeze + loading in trap context
        if _l64_15 and _t1_any and _svs:
            au_bull += 12  # L64_T1_SVS: structural base + reclaim + ignition
        if (_l22_15 or _l64_15) and _sq and _ztrap_bg:
            au_bull += 12  # L22L64_SQ_ZTRAP: support context + activation + trap

        # Tier 2 — two-element combos with strong context [production-ready]
        if _l43_10 and _clm and _ztrap_bg:
            au_bull += 10  # L43_CLM_ZTRAP: structural reset + climb in trap
        if _t1_any and _svs and _ns_5:
            au_bull += 10  # T1_SVS_NS: reclaim + ignition + supply drying up
        if _l64_15 and _clm and _load:
            au_bull += 9   # L64_CLM_LOAD: structural context + dual activation
        if _sq and bool(r.get("ns")) and _ztrap_bg:
            au_bull += 8   # SQ_NS_ZTRAP: squeeze + no-supply in trap zone
        if (_l22_15 or _l64_15) and _load and _clm and not _ztrap_bg:
            au_bull += 7   # L_LOAD_CLM: activation without trap (moderate)

        # Tier 3 — established backtest patterns (from Run 25, adjusted for ALL-US)
        _d4   = bool(r.get("d_absorb_bull") or r.get("d_spring"))
        _d6   = bool(r.get("d_surge_bull")  or r.get("d_blast_bull"))
        _l34  = bool(r.get("l34") or r.get("fri34"))
        _be   = bool(r.get("be_up"))
        _l34_r3  = bool(r.get("_l34_recent_3b") or r.get("_fri34_recent_3b"))
        _dabs_r5 = bool(r.get("_dabsorb_recent_5b"))
        if _d6 and _be:
            au_bull += 10   # D6+BE↑ same bar (conservative vs SP500 12)
        if _l34_r3 and _d4 and not _l34:
            au_bull += 12   # L34→D4 sequence (conservative vs SP500 15)
        if _dabs_r5 and _be and not _d4:
            au_bull += 8    # D4→BE↑ sequence

        s += min(au_bull, 22)

        # ── ALL-US SPECIFIC DEMOTIONS ─────────────────────────────────────
        # Single-signal noise reduction for the broad universe
        _has_struct = _l_struct or _l43_10 or _ztrap_bg or _l34
        _has_activ  = _sq or _clm or _load or _svs or _t1_any

        # UM alone (no structure or activation): very noisy in ALL-US
        if r.get("um_2809") and not _has_struct and not _has_activ:
            s -= 3
        # T4/T6 alone (no Ztrap or L-support): demoted in ALL-US (combo block = uplift)
        if r.get("tz_sig", "") in ("T4", "T6"):
            if not _ztrap_bg and not _l_struct:
                s -= 3
        # RL alone (no activation or structure): demoted
        if r.get("fuchsia_rl") and not _has_activ and not _be:
            s -= 2
        # B/S↑ alone (no L34/BE/D4/D6)
        if r.get("d_strong_bull") and not _l34 and not _be and not _d4 and not _d6:
            s -= 2
        # RSI overheated without institutional support
        _rsi_au = float(r.get("rsi", 50.0))
        if _rsi_au > 80 and not _d4 and not _d6:
            s -= 5
        elif _rsi_au > 75 and not _d4 and not _d6 and not _be:
            s -= 2

        # ── BEARISH BREAKDOWN BLOCK ───────────────────────────────────────
        # Separate from bullish logic; applied as negative score adjustment.
        _t10t11_cur    = r.get("tz_sig", "") in ("T10", "T11", "T12")
        _t10t11_r5     = bool(r.get("_t10t11_recent_5b"))
        _rh            = bool(r.get("fuchsia_rh"))
        _bo_dn         = bool(r.get("bo_dn"))
        _bx_dn         = bool(r.get("bx_dn"))
        _breakdown     = _bo_dn or _bx_dn

        # Rule B1: T10/T11 + breakdown confirmation — strongest bearish signal
        if (_t10t11_cur or _t10t11_r5) and _breakdown:
            s -= 8
        # Rule B2: RH + breakdown — reversal failure
        elif _rh and _breakdown:
            s -= 8
        # Rule B3: RH + T10/T11 recent — top-building context (no breakdown yet)
        if _rh and (_t10t11_cur or _t10t11_r5):
            s -= 5
        # Background weakness penalty (less severe, context only)
        if _t10t11_cur and not _breakdown:
            s -= 2
        if _rh and not _breakdown and not _t10t11_cur:
            s -= 2

    s = max(0.0, s)

    # ── Composite setup signals (SMX / AKAN / NNN / MX / GOG) — cap 12 ──────
    # Incremental value over already-scored primitives (VBO, T6, L-signals, etc.)
    # AKAN is a strict superset of SMX so only the higher score applies.
    _setup = 0.0
    if r.get("akan_sig"):  _setup += 8   # full sequence + final trigger + pressure
    elif r.get("smx_sig"): _setup += 6   # context + real/early trigger
    if r.get("nnn_sig"):   _setup += 6   # bottom Z/T compression + ignition
    if r.get("mx_sig"):    _setup += 5   # momentum continuation cluster
    if r.get("gog_sig"):   _setup += 4   # VBO confirmed in primed setup context
    s += min(_setup, 12)

    # ── Context / confirmation (uncapped, max ~18) ────────────────────────
    if r.get("x2g_wick"):      s += 5
    elif r.get("x2_wick"):     s += 4
    elif r.get("x1g_wick"):    s += 4
    elif r.get("x1_wick"):     s += 3
    elif r.get("x3_wick"):     s += 2
    if r.get("wick_bull"):     s += 5   # 94% C-anchor in WK↑+4BF+T/Z↑ triple, raised +3→+5

    # PARA 260420 — confirmed by pooled stats (RETEST False%=6.3% lowest)
    if r.get("para_retest"):                           s += 3
    elif r.get("para_plus") or r.get("para_start"):    s += 2

    # FLY 260424 — all sub-types 100% co-correlated; score once by strongest pattern
    if r.get("fly_abcd"):                              s += 4
    elif r.get("fly_cd") or r.get("fly_bd") or r.get("fly_ad"): s += 3

    # Vol spike — Avg3=5.51% Win%=61.8% — massively undervalued, RAISE 3→10
    if r.get("vol_spike_10x"): s += 10  # Avg3=5.51% Win%=61.8% (RAISE 3→10)

    return round(min(100.0, s), 1)


def _sig_age(frame: "pd.DataFrame | None", col: str, max_age: int = 30) -> int:
    """Bars since signal last fired (0 = current bar). Returns max_age if never."""
    if frame is None or col not in frame.columns:
        return max_age
    vals = frame[col].values.astype(bool)
    for i in range(len(vals) - 1, -1, -1):
        if vals[i]:
            return len(vals) - 1 - i
    return max_age


# ── RGTI/SMX multi-TF helper ──────────────────────────────────────────────────
def _fetch_htf(ticker: str, interval: str, days: int) -> "pd.DataFrame | None":
    """Fetch intraday OHLCV bars for RGTI/SMX multi-TF computation."""
    from data_polygon import fetch_bars, polygon_available
    df = None
    if polygon_available():
        try:
            df = fetch_bars(ticker, interval=interval, days=days)
        except Exception:
            pass
        return df if df is not None and len(df) >= 20 else None
    # Only try yfinance when Polygon is not configured
    try:
        import yfinance as yf
        period = "60d" if days <= 60 else "90d"
        raw = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if raw is not None and not raw.empty:
            raw.columns = [str(c).lower() for c in raw.columns]
            df = raw[["open", "high", "low", "close", "volume"]].dropna()
    except Exception:
        pass
    return df if df is not None and len(df) >= 20 else None


# ── Per-ticker worker ─────────────────────────────────────────────────────────
def _scan_turbo_ticker(
    ticker: str,
    interval: str,
    min_price: float = 0.0,
    max_price: float = 1e9,
    spy_chg: float | None = None,
    iwm_chg: float | None = None,
    partial_day: bool = False,
    min_volume: float = 0,
    profile: str = "sp500",
) -> dict | None:
    try:
        from data_polygon import fetch_bars, polygon_available

        if interval in ("1wk", "1w"):
            days = 400   # need 50+ weekly bars
        elif interval == "1d":
            days = 180
        else:
            days = 90    # 4h, 1h, 30m, 15m

        # ── Fetch OHLCV — Polygon first, yfinance fallback ─────────────────
        df = None
        _data_source = "polygon"
        if polygon_available():
            try:
                df = fetch_bars(ticker, interval=interval, days=days)
            except Exception as exc:
                log.debug("Polygon skip %s: %s — falling back to yfinance", ticker, exc)

        if df is None or df.empty:
            if polygon_available():
                return None  # Polygon configured but returned no data — skip ticker
            # Polygon not configured: try yfinance (no timeout guard — use only without Polygon)
            _data_source = "yfinance"
            import yfinance as yf
            period = "5y" if interval in ("1wk", "1w") else "180d" if interval == "1d" else "60d"
            raw = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
            if raw is None or raw.empty:
                return None
            raw.columns = [str(c).lower() for c in raw.columns]
            df = raw[["open", "high", "low", "close", "volume"]].dropna()

        if len(df) < 50:
            return None

        # Drop today's bar ONLY while US market is still open.
        # partial_day=True skips this — intentionally keeps the in-progress bar
        # so signals can be computed on the partial daily candle (intraday preview).
        if interval in ("1d", "1wk", "1w") and not partial_day:
            last_dt = df.index[-1]
            if hasattr(last_dt, "date"):
                last_dt = last_dt.date()
            import pytz as _pytz
            _et  = _pytz.timezone("America/New_York")
            now_utc = datetime.now(timezone.utc)
            now_et  = now_utc.astimezone(_et)
            today   = now_utc.date()
            mins_et = now_et.hour * 60 + now_et.minute
            # Mon–Fri, before 16:15 ET → market may still be open → bar incomplete
            market_open = (now_et.weekday() < 5) and (mins_et < 16 * 60 + 15)
            if last_dt == today and market_open:
                df = df.iloc[:-1]
        if len(df) < 40:
            return None

        row: dict = {"ticker": ticker, "data_source": _data_source}

        # ── Price / change ─────────────────────────────────────────────────
        price  = float(df["close"].iloc[-1])
        prev_p = float(df["close"].iloc[-2]) if len(df) >= 2 else price
        row["last_price"] = round(price, 2)
        row["change_pct"] = round((price - prev_p) / prev_p * 100, 2) if prev_p else 0.0

        # ── EMA values for price-vs-EMA filters ───────────────────────────
        try:
            _c = df["close"]
            row["ema20"]  = round(float(_c.ewm(span=20,  adjust=False).mean().iloc[-1]), 4)
            row["ema50"]  = round(float(_c.ewm(span=50,  adjust=False).mean().iloc[-1]), 4)
            row["ema89"]  = round(float(_c.ewm(span=89,  adjust=False).mean().iloc[-1]), 4)
            row["ema200"] = round(float(_c.ewm(span=200, adjust=False).mean().iloc[-1]), 4)
        except Exception:
            row["ema20"] = row["ema50"] = row["ema89"] = row["ema200"] = 0.0

        # ── Price range filter ─────────────────────────────────────────────
        if price < min_price or price > max_price:
            return None

        # ── Average volume filter (20-bar SMA) ────────────────────────────
        avg_vol = float(df["volume"].rolling(20, min_periods=5).mean().iloc[-1])
        row["avg_vol"] = round(avg_vol, 0)
        if min_volume > 0 and avg_vol < min_volume:
            return None

        # Helper: extract bool signal from last N bars (always N=1; ages stored separately)
        def _sig(frame, col, n=1):
            if frame is None or col not in frame.columns:
                return 0
            return int(bool(frame.iloc[-1][col]))

        _EDF = pd.DataFrame()  # empty fallback — _sig returns 0 for any col

        # ── T/Z signals ────────────────────────────────────────────────────
        try:
            sig_df  = compute_signals(df)
            last_s  = sig_df.iloc[-1]
            tz_bull = bool(last_s.get("is_bull", False))
            tz_name = str(last_s.get("sig_name", "")) if tz_bull else ""
        except Exception:
            sig_df = _EDF; tz_bull = False; tz_name = ""
        row["tz_bull"] = int(tz_bull)
        row["tz_sig"]  = tz_name

        # ── WLNBB (L signals, FRI, BLUE, BO, BX, BE, CCI) ─────────────────
        try:
            wlnbb  = compute_wlnbb(df)
            last_w = wlnbb.iloc[-1]
            bkt    = str(last_w.get("vol_bucket", ""))
        except Exception:
            wlnbb = _EDF; last_w = {}; bkt = ""
        row["fri34"]         = _sig(wlnbb, "FRI34")
        row["fri43"]         = _sig(wlnbb, "FRI43")
        row["fri64"]         = _sig(wlnbb, "FRI64")
        row["l34"]           = _sig(wlnbb, "L34")
        row["l43"]           = _sig(wlnbb, "L43")
        row["l64"]           = _sig(wlnbb, "L64")
        row["l22"]           = _sig(wlnbb, "L22")
        row["l555"]          = _sig(wlnbb, "L555")
        row["only_l2l4"]     = _sig(wlnbb, "ONLY_L2L4")
        row["blue"]          = _sig(wlnbb, "BLUE")
        row["cci_ready"]     = _sig(wlnbb, "CCI_READY")
        row["cci_0_retest"]  = _sig(wlnbb, "CCI_0_RETEST_OK")
        row["cci_blue_turn"] = _sig(wlnbb, "CCI_BLUE_TURN")
        row["bo_up"]         = _sig(wlnbb, "BO_UP")
        row["bo_dn"]         = _sig(wlnbb, "BO_DN")
        row["bx_up"]         = _sig(wlnbb, "BX_UP")
        row["bx_dn"]         = _sig(wlnbb, "BX_DN")
        row["be_up"]         = _sig(wlnbb, "BE_UP")
        row["be_dn"]         = _sig(wlnbb, "BE_DN")
        row["fuchsia_rh"]    = _sig(wlnbb, "FUCHSIA_RH")
        row["fuchsia_rl"]    = _sig(wlnbb, "FUCHSIA_RL")
        row["pre_pump"]      = _sig(wlnbb, "PRE_PUMP")
        row["vol_bucket"]    = bkt
        row["l_combo"]       = str(last_w.get("l_combo", "")) if isinstance(last_w, dict) else ""

        # ── Combo (2809, CONS, Bias, HILO, RTV, 3G, ROCKET, BRK, PREUP/DN) ─
        try:
            combo = compute_combo(df)
        except Exception:
            combo = _EDF
        row["buy_2809"]  = _sig(combo, "buy_2809")
        row["rocket"]    = _sig(combo, "rocket")
        row["sig3g"]     = _sig(combo, "sig3g")
        row["rtv"]       = _sig(combo, "rtv")
        row["hilo_buy"]  = _sig(combo, "hilo_buy")
        row["hilo_sell"] = _sig(combo, "hilo_sell")
        row["atr_brk"]   = _sig(combo, "atr_brk")
        row["bb_brk"]    = _sig(combo, "bb_brk")
        row["bias_up"]   = _sig(combo, "bias_up")
        row["bias_down"] = _sig(combo, "bias_down")
        row["cons_atr"]  = _sig(combo, "cons_atr")
        row["um_2809"]    = _sig(combo, "um_2809")
        row["svs_2809"]   = _sig(combo, "svs_2809")
        row["conso_2809"] = _sig(combo, "conso_2809")
        row["preup66"]   = _sig(combo, "preup66")
        row["preup55"]   = _sig(combo, "preup55")
        row["preup89"]   = _sig(combo, "preup89")
        row["preup3"]    = _sig(combo, "preup3")
        row["preup2"]    = _sig(combo, "preup2")
        row["preup50"]   = _sig(combo, "preup50")
        row["predn66"]   = _sig(combo, "predn66")
        row["predn55"]   = _sig(combo, "predn55")
        row["predn89"]   = _sig(combo, "predn89")
        row["predn3"]    = _sig(combo, "predn3")
        row["predn2"]    = _sig(combo, "predn2")
        row["predn50"]   = _sig(combo, "predn50")

        # ── B signals (260321) — B1–B11, no RSI filter ───────────────────
        try:
            b_sigs = compute_b_signals(df)
        except Exception:
            b_sigs = _EDF
        for _b in range(1, 12):
            row[f"b{_b}"] = _sig(b_sigs, f"b{_b}")
        # ── F signals (260418 F Builder) — F1–F11 + any_f ────────────────
        try:
            f_sigs = compute_f_signals(df)
            for _f in range(1, 12):
                row[f"f{_f}"] = _sig(f_sigs, f"f{_f}")
            row["any_f"] = _sig(f_sigs, "any_f")
        except Exception:
            for _f in range(1, 12):
                row[f"f{_f}"] = 0
            row["any_f"] = 0
        # ── G signals (260410) — armed by Z10/Z11/Z12, no RSI filter ─────
        try:
            g_sigs = compute_g_signals(df)
        except Exception:
            g_sigs = _EDF
        row["g1"]  = _sig(g_sigs, "g1")
        row["g2"]  = _sig(g_sigs, "g2")
        row["g4"]  = _sig(g_sigs, "g4")
        row["g6"]  = _sig(g_sigs, "g6")
        row["g11"] = _sig(g_sigs, "g11")
        # ── seqBContLite (260412) — continuation-lite sequence ────────────
        try:
            _sig_obj  = compute_signals(df)
            _bc_ser   = _sig_obj["bc"].fillna(0).astype(int)
            _bc_c  = int(_bc_ser.iloc[-1]) if len(_bc_ser) > 0 else 0
            _bc_p1 = int(_bc_ser.iloc[-2]) if len(_bc_ser) > 1 else 0
            _bc_p2 = int(_bc_ser.iloc[-3]) if len(_bc_ser) > 2 else 0
            row["seq_bcont"] = int(
                (_bc_p2 in {5, 3, 6, 4, 7} and _bc_c == 1) or   # T1/T1G/T2/T2G/T9 @-2 → T4
                (_bc_p1 in {9, 10, 11}      and _bc_c in {1, 2}) or  # T3/T11/T5 @-1 → T4/T6
                (_bc_p1 in {1, 4, 9}        and _bc_c == 2)          # T4/T2G/T3 @-1 → T6
            )
        except Exception:
            row["seq_bcont"] = 0
        # ── VA — ATR Volume Confirm (260402_COMBO_OSC) ────────────────────
        # volConfirmATR = ta.crossover(volume / ta.sma(volume, 20), 2.0)
        try:
            _avg_vol  = df["volume"].rolling(20, min_periods=1).mean()
            _vr       = df["volume"] / _avg_vol.replace(0, np.nan)
            _vr_now   = float(_vr.iloc[-1])  if not pd.isna(_vr.iloc[-1])  else 0.0
            _vr_prev  = float(_vr.iloc[-2])  if len(_vr) > 1 and not pd.isna(_vr.iloc[-2]) else 0.0
            row["va"] = int(_vr_now > 2.0 and _vr_prev <= 2.0)
        except Exception:
            row["va"] = 0
        # ── Volume spike vs previous bar ──────────────────────────────────
        try:
            _vs_cur  = float(df["volume"].iloc[-1])
            _vs_prev = float(df["volume"].iloc[-2]) if len(df) >= 2 else 0.0
            _vs_ratio = _vs_cur / _vs_prev if _vs_prev > 0 else 0.0
            row["vol_spike_5x"]  = int(_vs_ratio >= 5.0)
            row["vol_spike_10x"] = int(_vs_ratio >= 10.0)
            row["vol_spike_20x"] = int(_vs_ratio >= 20.0)
        except Exception:
            row["vol_spike_5x"] = row["vol_spike_10x"] = row["vol_spike_20x"] = 0

        # ── TZ state machine + CA/CD/CW + transition signals (260412) ─────
        _tz_weak_df  = None  # pre-init here so it stays valid after W block
        _tz_trans_df = None  # tz_bull_flip / tz_attempt series for N-scores
        tz_st = None         # pre-init so RTB block can safely reference it
        try:
            tz_st = compute_tz_state(df)
            row["tz_state"] = int(tz_st.iloc[-1]) if len(tz_st) else 0
            _any_b = any(row.get(f"b{_b}", 0) for _b in range(1, 12))
            _last_st = row["tz_state"]
            row["ca"] = int(_any_b and _last_st == 2)
            row["cd"] = int(_any_b and _last_st == 3)
            row["cw"] = int(_any_b and _last_st == 1)
            if len(tz_st) >= 2:
                _tz_prev = int(tz_st.iloc[-2])
                _tz_curr = int(tz_st.iloc[-1])
                row["tz_bull_flip"] = int(_tz_curr == 3 and _tz_prev != 3)
                row["tz_attempt"]   = int(_tz_curr == 2 and _tz_prev != 2)
                _ew = _tz_curr == 1 and _tz_prev == 0
                _bar_bull = df["close"].iloc[-1] > df["open"].iloc[-1]
                row["tz_weak_bull"] = int(_ew and _bar_bull)
                row["tz_weak_bear"] = int(_ew and not _bar_bull)
                _tz_st_shifted = tz_st.shift(1, fill_value=0).astype(int)
                _early_weak_ser = (tz_st.astype(int) == 1) & (_tz_st_shifted == 0)
                _bull_bar_ser   = df["close"] > df["open"]
                _tz_weak_df = pd.DataFrame({
                    "tz_weak_bull": _early_weak_ser & _bull_bar_ser,
                    "tz_weak_bear": _early_weak_ser & ~_bull_bar_ser,
                }, index=df.index)
                _tz_curr_i = tz_st.astype(int)
                _tz_trans_df = pd.DataFrame({
                    "tz_bull_flip": (_tz_curr_i == 3) & (_tz_st_shifted != 3),
                    "tz_attempt":   (_tz_curr_i == 2) & (_tz_st_shifted != 2),
                }, index=df.index)
            else:
                row["tz_bull_flip"] = row["tz_attempt"] = 0
                row["tz_weak_bull"] = row["tz_weak_bear"] = 0
        except Exception:
            row["tz_state"] = row["ca"] = row["cd"] = row["cw"] = 0
            row["tz_bull_flip"] = row["tz_attempt"] = 0
            row["tz_weak_bull"] = row["tz_weak_bear"] = 0

        # ── VABS (ABS, CLIMB, LOAD, Wyckoff, BEST, STRONG, VBO) ───────────
        try:
            vabs = compute_vabs(df)
        except Exception:
            vabs = _EDF
        row["abs_sig"]    = _sig(vabs, "abs_sig")
        row["climb_sig"]  = _sig(vabs, "climb_sig")
        row["load_sig"]   = _sig(vabs, "load_sig")
        row["ns"]         = _sig(vabs, "ns")
        row["nd"]         = _sig(vabs, "nd")
        row["sc"]         = _sig(vabs, "sc")
        row["bc"]         = _sig(vabs, "bc")
        row["sq"]         = _sig(vabs, "sq")
        row["best_sig"]   = _sig(vabs, "best_sig")
        row["strong_sig"] = _sig(vabs, "strong_sig")
        row["vbo_up"]     = _sig(vabs, "vbo_up")
        row["vbo_dn"]     = _sig(vabs, "vbo_dn")

        wx = ddf = u308 = uv2 = None  # pre-initialise for age computation (NOT _tz_weak_df)

        # ── Wick (3112_2C legacy) ─────────────────────────────────────────
        try:
            wick = compute_wick(df)
        except Exception:
            wick = _EDF
        row["wick_bull"] = _sig(wick, "WICK_BULL_CONFIRM")
        row["wick_bear"] = _sig(wick, "WICK_BEAR_CONFIRM")

        # ── Wick X (260402_WICK) ──────────────────────────────────────────
        try:
            wx = compute_wick_x(df)
            row["x2g_wick"] = _sig(wx, "x2g_wick")
            row["x2_wick"]  = _sig(wx, "x2_wick")
            row["x1g_wick"] = _sig(wx, "x1g_wick")
            row["x1_wick"]  = _sig(wx, "x1_wick")
            row["x3_wick"]  = _sig(wx, "x3_wick")
        except Exception:
            row["x2g_wick"] = row["x2_wick"] = row["x1g_wick"] = row["x1_wick"] = row["x3_wick"] = 0

        # ── Delta / order-flow (260403 V2) ────────────────────────────────
        try:
            ddf = compute_delta(df)
            for col in ("strong_bull","strong_bear","absorb_bull","absorb_bear",
                        "div_bull","div_bear","cd_bull","cd_bear",
                        "surge_bull","surge_bear","blast_bull","blast_bear",
                        "vd_div_bull","vd_div_bear","spring","upthrust",
                        "flip_bull","flip_bear","orange_bull",
                        "blast_bull_red","blast_bear_grn",
                        "surge_bull_red","surge_bear_grn"):
                row[f"d_{col}"] = _sig(ddf, col)
        except Exception:
            for col in ("strong_bull","strong_bear","absorb_bull","absorb_bear",
                        "div_bull","div_bear","cd_bull","cd_bear",
                        "surge_bull","surge_bear","blast_bull","blast_bear",
                        "vd_div_bull","vd_div_bear","spring","upthrust",
                        "flip_bull","flip_bear","orange_bull",
                        "blast_bull_red","blast_bear_grn",
                        "surge_bull_red","surge_bear_grn"):
                row[f"d_{col}"] = 0

        # ── RSI(14) ────────────────────────────────────────────────────────
        try:
            row["rsi"] = round(float(_rsi_ind(df["close"], 14).iloc[-1]), 1)
        except Exception:
            row["rsi"] = 0.0

        # ── CCI(20) ────────────────────────────────────────────────────────
        try:
            row["cci"] = round(float(_cci_ind(df["high"], df["low"], df["close"], 20).iloc[-1]), 1)
        except Exception:
            row["cci"] = 0.0

        # ── 260308 + L88 ───────────────────────────────────────────────────
        try:
            u308   = compute_260308_l88(df)
            last_u = u308.iloc[-1]
            row["sig_260308"] = int(bool(last_u.get("sig_260308", False)))
            row["sig_l88"]    = int(bool(last_u.get("sig_l88",    False)))
        except Exception:
            row["sig_260308"] = 0
            row["sig_l88"]    = 0

        # ── ULTRA v2 ───────────────────────────────────────────────────────
        try:
            uv2 = compute_ultra_v2(df)
            row["eb_bull"]    = _sig(uv2, "eb_bull")
            row["eb_bear"]    = _sig(uv2, "eb_bear")
            row["fbo_bull"]   = _sig(uv2, "fbo_bull")
            row["fbo_bear"]   = _sig(uv2, "fbo_bear")
            row["bf_buy"]     = _sig(uv2, "bf_buy")
            row["bf_sell"]    = _sig(uv2, "bf_sell")
            row["ultra_3up"]  = _sig(uv2, "ultra_3up")
            row["ultra_3dn"]  = _sig(uv2, "ultra_3dn")
            row["best_long"]  = _sig(uv2, "best_long")
            row["best_short"] = _sig(uv2, "best_short")
        except Exception:
            for k in ("eb_bull","eb_bear","fbo_bull","fbo_bear","bf_buy","bf_sell",
                      "ultra_3up","ultra_3dn","best_long","best_short"):
                row[k] = 0

        # ── RS / Relative Strength vs SPY + IWM ───────────────────────────
        # RS: ticker up ≥ 0.5% while SPY AND IWM both down ≥ 0.3%
        # RS+: RS AND high volume (bucket B or VB)
        try:
            if spy_chg is not None and iwm_chg is not None:
                rs_cond = (
                    row["change_pct"] >= 0.5
                    and spy_chg <= -0.3
                    and iwm_chg <= -0.3
                )
                row["rs"] = int(rs_cond)
                row["rs_strong"] = int(rs_cond and bkt in ("B", "VB"))
            else:
                row["rs"] = 0
                row["rs_strong"] = 0
        except Exception:
            row["rs"] = 0
            row["rs_strong"] = 0

        # ── RGTI (260404) + SMX (260402) — disabled (extra API calls slow scan) ──
        _RGTI_KEYS = ("rgti_ll", "rgti_up", "rgti_upup", "rgti_upupup",
                      "rgti_orange", "rgti_green", "rgti_greencirc", "smx")
        for _k in _RGTI_KEYS:
            row[_k] = 0

        # ── PARA (260420) — Parabola Start Detector v3.6 ─────────────────
        _PARA_KEYS = ("para_prep", "para_start", "para_plus", "para_retest")
        _para_ser = None
        try:
            from para_engine import compute_para_series as _cpara_ser
            _is_daily = interval in ("1d", "1wk", "1w")
            _para_ser = _cpara_ser(df, is_daily=_is_daily)
            if _para_ser is not None:
                _lp = _para_ser.iloc[-1]
                row["para_prep"]   = int(bool(_lp.get("para_prep",   False)))
                row["para_start"]  = int(bool(_lp.get("para_start",  False)))
                row["para_plus"]   = int(bool(_lp.get("para_plus",   False)))
                row["para_retest"] = int(bool(_lp.get("para_retest", False)))
            else:
                for _k in _PARA_KEYS:
                    row[_k] = 0
        except Exception:
            for _k in _PARA_KEYS:
                row[_k] = 0

        # ── FLY (260424) — ABCD EMA DP ───────────────────────────────────
        _FLY_KEYS = ("fly_abcd", "fly_cd", "fly_bd", "fly_ad")
        _fly_ser = None
        try:
            from fly_engine import compute_fly_series as _cfly_ser
            _fly_ser = _cfly_ser(df)
            _lf = _fly_ser.iloc[-1]
            for _k in _FLY_KEYS:
                row[_k] = int(bool(_lf.get(_k, False)))
        except Exception:
            for _k in _FLY_KEYS:
                row[_k] = 0

        # ── Sequence age flags for confluence scoring ─────────────────────
        # Computed here (wlnbb + ddf available) and passed into _calc_turbo_score.
        # _l34_recent_3b: L34/FRI34 fired 1-3 bars ago (not current bar)
        # _dabsorb_recent_5b: d_absorb_bull or d_spring fired 1-5 bars ago
        try:
            _l34_age_v   = _sig_age(wlnbb, "L34",   max_age=10)
            _fri34_age_v = _sig_age(wlnbb, "FRI34", max_age=10)
            _dabs_age_v  = _sig_age(ddf, "absorb_bull", max_age=10)
            _dspr_age_v  = _sig_age(ddf, "spring",      max_age=10)
            row["_l34_recent_3b"]     = int(0 < _l34_age_v   <= 3)
            row["_fri34_recent_3b"]   = int(0 < _fri34_age_v  <= 3)
            row["_dabsorb_recent_5b"] = int(0 < min(_dabs_age_v, _dspr_age_v) <= 5)
        except Exception:
            row["_l34_recent_3b"] = row["_fri34_recent_3b"] = row["_dabsorb_recent_5b"] = 0

        # ── Profile-scoring lookback flags (Z-trap, L-family, BLUE recent) ─────
        try:
            _ZTRAP_NAMES = {"Z9", "Z10", "Z11", "Z12"}
            _BEAR_TOP    = {"T10", "T11", "T12"}
            _ztrap_r = 0
            _ztrap_r15 = 0
            _t10t11_r = 0
            if sig_df is not None and not sig_df.empty and "sig_name" in sig_df.columns:
                for _zi in range(1, min(16, len(sig_df))):
                    try:
                        _sn_val = str(sig_df["sig_name"].iloc[-_zi] or "")
                        if _sn_val in _ZTRAP_NAMES:
                            _ztrap_r15 = 1
                            if _zi <= 5:
                                _ztrap_r = 1
                        if _sn_val in _BEAR_TOP and _zi <= 5:
                            _t10t11_r = 1
                    except Exception:
                        pass
            row["_ztrap_recent_5b"]  = _ztrap_r
            row["_ztrap_recent_15b"] = _ztrap_r15
            row["_t10t11_recent_5b"] = _t10t11_r
            row["_l64_recent_5b"]    = _sn(wlnbb, "L64",  5)
            row["_l43_recent_5b"]    = _sn(wlnbb, "L43",  5)
            row["_l22_recent_5b"]    = _sn(wlnbb, "L22",  5)
            row["_blue_recent_5b"]   = _sn(wlnbb, "BLUE", 5)
            # Extended windows for ALL-US profile
            row["_l64_recent_15b"]   = _sn(wlnbb, "L64",  15)
            row["_l22_recent_15b"]   = _sn(wlnbb, "L22",  15)
            row["_l43_recent_10b"]   = _sn(wlnbb, "L43",  10)
            row["_ns_recent_5b"]     = _sn(vabs,  "ns",    5)
        except Exception:
            row["_ztrap_recent_5b"]  = row["_l64_recent_5b"]  = row["_l43_recent_5b"] = 0
            row["_l22_recent_5b"]    = row["_blue_recent_5b"] = row["_ztrap_recent_15b"] = 0
            row["_t10t11_recent_5b"] = row["_l64_recent_15b"] = row["_l22_recent_15b"]  = 0
            row["_l43_recent_10b"]   = row["_ns_recent_5b"]   = 0

        # ── GOG Priority Engine (260501 FULL + F8) ───────────────────────────
        try:
            from gog_engine import compute_gog_signals as _cgog
            _gog_df  = _cgog(df, wlnbb, sig_df, f_sigs, vabs, u308, uv2, combo)
            _gl      = _gog_df.iloc[-1]
            row["akan_sig"]      = int(_gl.get("A",  0))
            row["smx_sig"]       = int(_gl.get("SM", 0))
            row["nnn_sig"]       = int(_gl.get("N",  0))
            row["mx_sig"]        = int(_gl.get("MX", 0))
            row["gog_sig"]       = int(bool(_gl.get("GOG_SCORE", 0)))
            row["gog_tier"]      = str(_gl.get("GOG_TIER", ""))
            row["gog_score"]     = float(_gl.get("GOG_SCORE", 0.0))
            row["already_extended"] = int(_gl.get("already_extended_flag", 0))
            # GOG boosted tiers
            for _gk in ("G1P","G2P","G3P","G1L","G2L","G3L","G1C","G2C","G3C","GOG1","GOG2","GOG3"):
                row[f"gog_{_gk.lower()}"] = int(_gl.get(_gk, 0))
            # Context signals
            for _ck in ("LD","LDS","LDC","LDP","LRC","LRP","WRC","F8C","SQB","BCT","SVS"):
                row[f"ctx_{_ck.lower()}"] = int(_gl.get(_ck, 0))
        except Exception:
            row["smx_sig"] = row["akan_sig"] = row["nnn_sig"] = row["mx_sig"] = row["gog_sig"] = 0
            row["gog_tier"] = ""; row["gog_score"] = 0.0; row["already_extended"] = 0
            for _gk in ("G1P","G2P","G3P","G1L","G2L","G3L","G1C","G2C","G3C","GOG1","GOG2","GOG3"):
                row[f"gog_{_gk.lower()}"] = 0
            for _ck in ("LD","LDS","LDC","LDP","LRC","LRP","WRC","F8C","SQB","BCT","SVS"):
                row[f"ctx_{_ck.lower()}"] = 0

        # ── TURBO SCORE ────────────────────────────────────────────────────
        row["turbo_score"] = _calc_turbo_score(row, profile)

        # ── RTB v4 score ───────────────────────────────────────────────────
        try:
            from rtb_engine import calc_rtb_v4

            # tz_st is from compute_tz_state(df) computed earlier in the W block.
            # Build 5-bar history dicts; index i means -(i+1) bars from last bar.
            def _hv(frame, col, i):
                if frame is None or col not in frame.columns: return 0
                try: return int(bool(frame.iloc[-(i+1)][col]))
                except Exception: return 0

            def _rtb_hist_bar(i: int) -> dict:
                # tz_sig for bar i: read from sig_df (is_bull + sig_name)
                tz_s = ""
                if sig_df is not None and len(sig_df) > i:
                    try:
                        s = sig_df.iloc[-(i+1)]
                        tz_s = str(s.get("sig_name","")) if s.get("is_bull") else ""
                    except Exception: pass
                # tz_bull_flip / tz_attempt from tz_st series
                tzf = tza = 0
                try:
                    if tz_st is not None and len(tz_st) > i + 1:
                        cur_st  = int(tz_st.iloc[-(i+1)])
                        prev_st = int(tz_st.iloc[-(i+2)])
                        tzf = int(cur_st == 3 and prev_st != 3)
                        tza = int(cur_st == 2 and prev_st != 2)
                except Exception: pass
                cl = float(df["close"].iloc[-(i+1)]) if len(df) > i else 0.0
                op = float(df["open"].iloc[-(i+1)])  if len(df) > i else 0.0
                hi = float(df["high"].iloc[-(i+1)])  if len(df) > i else 0.0
                bkt = ""
                if wlnbb is not None and "vol_bucket" in wlnbb.columns:
                    try: bkt = str(wlnbb.iloc[-(i+1)]["vol_bucket"])
                    except Exception: pass
                return {
                    "vol_bucket": bkt, "tz_sig": tz_s,
                    "tz_bull_flip": tzf, "tz_attempt": tza,
                    "close": cl, "open": op, "high": hi,
                    "l34": _hv(wlnbb,"L34",i),   "fri34": _hv(wlnbb,"FRI34",i),
                    "l43": _hv(wlnbb,"L43",i),   "l64":   _hv(wlnbb,"L64",i),
                    "l22": _hv(wlnbb,"L22",i),
                    "abs_sig":   _hv(vabs,"abs_sig",i),
                    "climb_sig": _hv(vabs,"climb_sig",i),
                    "load_sig":  _hv(vabs,"load_sig",i),
                    "ns":        _hv(vabs,"ns",i),
                    "sq":        _hv(vabs,"sq",i),
                    "conso_2809":    _hv(combo,"conso_2809",i),
                    "d_spring":      _hv(ddf,"spring",i),
                    "d_absorb_bull": _hv(ddf,"absorb_bull",i),
                    "f1": _hv(f_sigs,"f1",i) if f_sigs is not None else 0,
                    "g1": _hv(g_sigs,"g1",i) if g_sigs is not None else 0,
                }

            rtb_history = [_rtb_hist_bar(i) for i in range(1, 6)]
            row["close"] = float(df["close"].iloc[-1])
            row["open"]  = float(df["open"].iloc[-1])
            row["high"]  = float(df["high"].iloc[-1])

            # Sequential warm-up: replay last 10 bars to derive correct
            # prev_phase / soft_streak / pending state before final bar.
            # Without this, hysteresis always starts from "0" and blocks
            # most non-A phases, producing empty RTB in the scan.
            _wp = "0"; _wa = 0; _ws = 0; _wpp = ""; _wpc = 0
            _n_warmup = min(10, len(df) - 1)
            for _wi in range(_n_warmup, 0, -1):
                _wb = _rtb_hist_bar(_wi)
                _wh = [_rtb_hist_bar(_wi + k) for k in range(1, 6)]
                _wr = calc_rtb_v4(_wb, _wh, _wp, _wa, _ws, _wpp, _wpc)
                _wp  = _wr["rtb_phase"]
                _wa  = _wr["rtb_phase_age"]
                _ws  = _wr["_soft_streak"]
                _wpp = _wr["_pending_phase"]
                _wpc = _wr["_pending_phase_count"]

            rtb = calc_rtb_v4(row, rtb_history, _wp, _wa, _ws, _wpp, _wpc)
            row["rtb_build"]      = rtb["rtb_build"]
            row["rtb_turn"]       = rtb["rtb_turn"]
            row["rtb_ready"]      = rtb["rtb_ready"]
            row["rtb_bonus3"]     = rtb["rtb_bonus3"]
            row["rtb_late"]       = rtb["rtb_late"]
            row["rtb_total"]      = rtb["rtb_total"]
            row["rtb_phase"]      = rtb["rtb_phase"]
            row["rtb_transition"] = rtb["rtb_transition"]
            row["rtb_phase_age"]  = rtb["rtb_phase_age"]
        except Exception:
            row["rtb_build"] = row["rtb_turn"] = row["rtb_ready"] = 0.0
            row["rtb_bonus3"] = row["rtb_late"] = row["rtb_total"] = 0.0
            row["rtb_phase"] = row["rtb_transition"] = ""
            row["rtb_phase_age"] = 0

        # ── Signal ages + N=5 / N=10 scores (for client N= switching) ──────
        def _sa(frame, col):
            return _sig_age(frame, col)

        def _sn(frame, col, n):
            if frame is None or col not in frame.columns:
                return 0
            return int(frame[col].iloc[-n:].any())

        row["sig_ages"] = json.dumps({
            # WLNBB
            "fri34":         _sa(wlnbb, "FRI34"),
            "fri43":         _sa(wlnbb, "FRI43"),
            "fri64":         _sa(wlnbb, "FRI64"),
            "l34":           _sa(wlnbb, "L34"),
            "l43":           _sa(wlnbb, "L43"),
            "l64":           _sa(wlnbb, "L64"),
            "l22":           _sa(wlnbb, "L22"),
            "l555":          _sa(wlnbb, "L555"),
            "only_l2l4":     _sa(wlnbb, "ONLY_L2L4"),
            "blue":          _sa(wlnbb, "BLUE"),
            "cci_ready":     _sa(wlnbb, "CCI_READY"),
            "cci_0_retest":  _sa(wlnbb, "CCI_0_RETEST_OK"),
            "cci_blue_turn": _sa(wlnbb, "CCI_BLUE_TURN"),
            "bo_up":         _sa(wlnbb, "BO_UP"),
            "bo_dn":         _sa(wlnbb, "BO_DN"),
            "bx_up":         _sa(wlnbb, "BX_UP"),
            "bx_dn":         _sa(wlnbb, "BX_DN"),
            "be_up":         _sa(wlnbb, "BE_UP"),
            "be_dn":         _sa(wlnbb, "BE_DN"),
            "fuchsia_rh":    _sa(wlnbb, "FUCHSIA_RH"),
            "fuchsia_rl":    _sa(wlnbb, "FUCHSIA_RL"),
            "pre_pump":      _sa(wlnbb, "PRE_PUMP"),
            # Combo
            "buy_2809":   _sa(combo, "buy_2809"),
            "rocket":     _sa(combo, "rocket"),
            "sig3g":      _sa(combo, "sig3g"),
            "rtv":        _sa(combo, "rtv"),
            "hilo_buy":   _sa(combo, "hilo_buy"),
            "hilo_sell":  _sa(combo, "hilo_sell"),
            "atr_brk":    _sa(combo, "atr_brk"),
            "bb_brk":     _sa(combo, "bb_brk"),
            "bias_up":    _sa(combo, "bias_up"),
            "bias_down":  _sa(combo, "bias_down"),
            "cons_atr":   _sa(combo, "cons_atr"),
            "um_2809":    _sa(combo, "um_2809"),
            "svs_2809":   _sa(combo, "svs_2809"),
            "conso_2809": _sa(combo, "conso_2809"),
            "preup66":    _sa(combo, "preup66"),
            "preup55":    _sa(combo, "preup55"),
            "preup89":    _sa(combo, "preup89"),
            "preup3":     _sa(combo, "preup3"),
            "preup2":     _sa(combo, "preup2"),
            "preup50":    _sa(combo, "preup50"),
            "predn66":    _sa(combo, "predn66"),
            "predn55":    _sa(combo, "predn55"),
            "predn89":    _sa(combo, "predn89"),
            "predn3":     _sa(combo, "predn3"),
            "predn2":     _sa(combo, "predn2"),
            "predn50":    _sa(combo, "predn50"),
            # B signals
            **{f"b{i}": _sa(b_sigs, f"b{i}") for i in range(1, 12)},
            # G signals
            "g1":  _sa(g_sigs, "g1"),
            "g2":  _sa(g_sigs, "g2"),
            "g4":  _sa(g_sigs, "g4"),
            "g6":  _sa(g_sigs, "g6"),
            "g11": _sa(g_sigs, "g11"),
            # seqBContLite + VA (stored as 0/1, age = 0 if fired today)
            "seq_bcont": 0 if row.get("seq_bcont") else 999,
            "va":        0 if row.get("va")        else 999,
            # VABS
            "abs_sig":    _sa(vabs, "abs_sig"),
            "climb_sig":  _sa(vabs, "climb_sig"),
            "load_sig":   _sa(vabs, "load_sig"),
            "ns":         _sa(vabs, "ns"),
            "nd":         _sa(vabs, "nd"),
            "sc":         _sa(vabs, "sc"),
            "bc":         _sa(vabs, "bc"),
            "sq":         _sa(vabs, "sq"),
            "best_sig":   _sa(vabs, "best_sig"),
            "strong_sig": _sa(vabs, "strong_sig"),
            "vbo_up":     _sa(vabs, "vbo_up"),
            "vbo_dn":     _sa(vabs, "vbo_dn"),
            # Wick
            "wick_bull": _sa(wick, "WICK_BULL_CONFIRM"),
            "wick_bear": _sa(wick, "WICK_BEAR_CONFIRM"),
            # Wick X
            "x2g_wick": _sa(wx, "x2g_wick"),
            "x2_wick":  _sa(wx, "x2_wick"),
            "x1g_wick": _sa(wx, "x1g_wick"),
            "x1_wick":  _sa(wx, "x1_wick"),
            "x3_wick":  _sa(wx, "x3_wick"),
            # 260308 / L88
            "sig_260308": _sa(u308, "sig_260308"),
            "sig_l88":    _sa(u308, "sig_l88"),
            # Ultra v2
            "eb_bull":    _sa(uv2, "eb_bull"),
            "eb_bear":    _sa(uv2, "eb_bear"),
            "fbo_bull":   _sa(uv2, "fbo_bull"),
            "fbo_bear":   _sa(uv2, "fbo_bear"),
            "bf_buy":     _sa(uv2, "bf_buy"),
            "bf_sell":    _sa(uv2, "bf_sell"),
            "ultra_3up":  _sa(uv2, "ultra_3up"),
            "ultra_3dn":  _sa(uv2, "ultra_3dn"),
            "best_long":  _sa(uv2, "best_long"),
            "best_short": _sa(uv2, "best_short"),
            # Delta
            "d_strong_bull": _sa(ddf, "strong_bull"),
            "d_strong_bear": _sa(ddf, "strong_bear"),
            "d_absorb_bull": _sa(ddf, "absorb_bull"),
            "d_absorb_bear": _sa(ddf, "absorb_bear"),
            "d_div_bull":    _sa(ddf, "div_bull"),
            "d_div_bear":    _sa(ddf, "div_bear"),
            "d_cd_bull":     _sa(ddf, "cd_bull"),
            "d_cd_bear":     _sa(ddf, "cd_bear"),
            "d_surge_bull":  _sa(ddf, "surge_bull"),
            "d_surge_bear":  _sa(ddf, "surge_bear"),
            "d_blast_bull":  _sa(ddf, "blast_bull"),
            "d_blast_bear":  _sa(ddf, "blast_bear"),
            "d_vd_div_bull": _sa(ddf, "vd_div_bull"),
            "d_vd_div_bear": _sa(ddf, "vd_div_bear"),
            "d_spring":      _sa(ddf, "spring"),
            "d_upthrust":    _sa(ddf, "upthrust"),
            "d_flip_bull":       _sa(ddf, "flip_bull"),
            "d_flip_bear":       _sa(ddf, "flip_bear"),
            "d_orange_bull":     _sa(ddf, "orange_bull"),
            "d_blast_bull_red":  _sa(ddf, "blast_bull_red"),
            "d_blast_bear_grn":  _sa(ddf, "blast_bear_grn"),
            "d_surge_bull_red":  _sa(ddf, "surge_bull_red"),
            "d_surge_bear_grn":  _sa(ddf, "surge_bear_grn"),
            # W signals
            "tz_weak_bull":  _sa(_tz_weak_df, "tz_weak_bull"),
            "tz_weak_bear":  _sa(_tz_weak_df, "tz_weak_bear"),
            # TZ transition signals
            "tz_bull_flip":  _sa(_tz_trans_df, "tz_bull_flip"),
            "tz_attempt":    _sa(_tz_trans_df, "tz_attempt"),
            # F signals (260418) — previously missing from age tracking
            **({f"f{_fi}": _sa(f_sigs, f"f{_fi}") for _fi in range(1, 12)} if f_sigs is not None else {f"f{_fi}": 30 for _fi in range(1, 12)}),
            "any_f": (_sa(f_sigs, "any_f") if f_sigs is not None else 30),
            # PARA signals
            "para_prep":    _sa(_para_ser, "para_prep"),
            "para_start":   _sa(_para_ser, "para_start"),
            "para_plus":    _sa(_para_ser, "para_plus"),
            "para_retest":  _sa(_para_ser, "para_retest"),
            # FLY signals
            "fly_abcd": _sa(_fly_ser, "fly_abcd"),
            "fly_cd":   _sa(_fly_ser, "fly_cd"),
            "fly_bd":   _sa(_fly_ser, "fly_bd"),
            "fly_ad":   _sa(_fly_ser, "fly_ad"),
            # GOG Priority Engine signals
            "smx_sig":  0 if row.get("smx_sig")  else 999,
            "akan_sig": 0 if row.get("akan_sig") else 999,
            "nnn_sig":  0 if row.get("nnn_sig")  else 999,
            "mx_sig":   0 if row.get("mx_sig")   else 999,
            "gog_sig":  0 if row.get("gog_sig")  else 999,
            "gog_tier": row.get("gog_tier", ""),
            "gog_score": row.get("gog_score", 0),
        }, separators=(',', ':'))

        # N=3, N=5 and N=10 turbo scores
        for _n, _key in ((3, "turbo_score_n3"), (5, "turbo_score_n5"), (10, "turbo_score_n10")):
            _r = {
                # Volume / accum
                "abs_sig":    _sn(vabs,  "abs_sig",    _n),
                "climb_sig":  _sn(vabs,  "climb_sig",  _n),
                "load_sig":   _sn(vabs,  "load_sig",   _n),
                "vbo_up":     _sn(vabs,  "vbo_up",     _n),
                "ns":         _sn(vabs,  "ns",          _n),
                "sq":         _sn(vabs,  "sq",          _n),
                "sc":         _sn(vabs,  "sc",          _n),
                "sig_l88":    _sn(u308,  "sig_l88",    _n),
                "sig_260308": _sn(u308,  "sig_260308", _n),
                # Breakout
                "fbo_bull":     _sn(uv2,   "fbo_bull",   _n),
                "eb_bull":      _sn(uv2,   "eb_bull",    _n),
                "bf_buy":       _sn(uv2,   "bf_buy",     _n),
                "ultra_3up":    _sn(uv2,   "ultra_3up",  _n),
                "bo_up":        _sn(wlnbb, "BO_UP",      _n),
                "bx_up":        _sn(wlnbb, "BX_UP",      _n),
                "rs_strong":    row.get("rs_strong", 0),
                "rs":           row.get("rs", 0),
                # Combo
                "rocket":   _sn(combo, "rocket",   _n),
                "buy_2809": _sn(combo, "buy_2809", _n),
                "sig3g":    _sn(combo, "sig3g",    _n),
                "rtv":      _sn(combo, "rtv",      _n),
                "hilo_buy": _sn(combo, "hilo_buy", _n),
                "atr_brk":  _sn(combo, "atr_brk",  _n),
                "bb_brk":   _sn(combo, "bb_brk",   _n),
                "cd":  row.get("cd", 0),
                "ca":  row.get("ca", 0),
                "cw":  row.get("cw", 0),
                # L-structure
                "tz_sig":    tz_name,
                "fri34":     _sn(wlnbb, "FRI34",     _n),
                "fri43":     _sn(wlnbb, "FRI43",     _n),
                "l34":       _sn(wlnbb, "L34",       _n),
                "blue":      _sn(wlnbb, "BLUE",      _n),
                "cci_ready": _sn(wlnbb, "CCI_READY", _n),
                # Delta
                "d_blast_bull":  _sn(ddf, "blast_bull",  _n),
                "d_surge_bull":  _sn(ddf, "surge_bull",  _n),
                "d_strong_bull": _sn(ddf, "strong_bull", _n),
                "d_absorb_bull": _sn(ddf, "absorb_bull", _n),
                "d_spring":      _sn(ddf, "spring",      _n),
                "d_div_bull":    _sn(ddf, "div_bull",    _n),
                "d_vd_div_bull": _sn(ddf, "vd_div_bull", _n),
                "d_cd_bull":     _sn(ddf, "cd_bull",     _n),
                "d_flip_bull":   _sn(ddf, "flip_bull",   _n),
                "d_orange_bull":    _sn(ddf, "orange_bull",    _n),
                "d_blast_bull_red": _sn(ddf, "blast_bull_red", _n),
                "d_surge_bull_red": _sn(ddf, "surge_bull_red", _n),
                # EMA cross
                "preup66": _sn(combo, "preup66", _n),
                "preup55": _sn(combo, "preup55", _n),
                "preup89": _sn(combo, "preup89", _n),
                "preup3":  _sn(combo, "preup3",  _n),
                "preup2":  _sn(combo, "preup2",  _n),
                "preup50": _sn(combo, "preup50", _n),
                # Context (wick)
                "x2g_wick": _sn(wx,   "x2g_wick",          _n),
                "x2_wick":  _sn(wx,   "x2_wick",           _n),
                "x1g_wick": _sn(wx,   "x1g_wick",          _n),
                "x1_wick":  _sn(wx,   "x1_wick",           _n),
                "x3_wick":  _sn(wx,   "x3_wick",           _n),
                "wick_bull": _sn(wick, "WICK_BULL_CONFIRM", _n),
                # Backbone (were missing — high-weight signals)
                "conso_2809": _sn(combo,  "conso_2809", _n),
                "tz_bull":    _sn(sig_df, "is_bull",    _n),
                # Breakout additions
                "be_up":    _sn(wlnbb, "BE_UP", _n),
                # Volume additions
                "svs_2809": _sn(combo, "svs_2809", _n),
                "um_2809":  _sn(combo, "um_2809",  _n),
                # L-structure additions
                "l43":          _sn(wlnbb,        "L43",        _n),
                "fuchsia_rl":   _sn(wlnbb,        "FUCHSIA_RL", _n),
                "tz_bull_flip": _sn(_tz_trans_df,  "tz_bull_flip", _n),
                "tz_attempt":   _sn(_tz_trans_df,  "tz_attempt",   _n),
                "tz_weak_bull": _sn(_tz_weak_df,   "tz_weak_bull", _n),
                # G signals
                "g1":  _sn(g_sigs, "g1",  _n),
                "g2":  _sn(g_sigs, "g2",  _n),
                "g4":  _sn(g_sigs, "g4",  _n),
                "g6":  _sn(g_sigs, "g6",  _n),
                "g11": _sn(g_sigs, "g11", _n),
                # Context additions (para + fly)
                "para_retest": _sn(_para_ser, "para_retest", _n),
                "para_plus":   _sn(_para_ser, "para_plus",   _n),
                "para_start":  _sn(_para_ser, "para_start",  _n),
                "fly_abcd": _sn(_fly_ser, "fly_abcd", _n),
                "fly_cd":   _sn(_fly_ser, "fly_cd",   _n),
                "fly_bd":   _sn(_fly_ser, "fly_bd",   _n),
                "fly_ad":   _sn(_fly_ser, "fly_ad",   _n),
                # Composite setup signals — use current-bar value for all N windows
                "smx_sig":  row.get("smx_sig",  0),
                "akan_sig": row.get("akan_sig", 0),
                "nnn_sig":  row.get("nnn_sig",  0),
                "mx_sig":   row.get("mx_sig",   0),
                "gog_sig":  row.get("gog_sig",  0),
            }
            row[_key] = _calc_turbo_score(_r, profile)

        row.setdefault("sector", "")  # not fetched in bulk scan; column exists in DB

        return row

    except Exception as exc:
        log.debug("Turbo skip %s: %s", ticker, exc)
        return None


# ── Fast-path: copy sub-universe results from a recent all_us scan ────────────
def _copy_from_allus_if_recent(interval: str, universe: str) -> int | None:
    """
    If a recent all_us scan exists for this interval, copy + filter its results
    for the requested sub-universe without re-scanning. Returns row count or None.
    Cutoff: 36 h for daily/weekly, 2 h for intraday.
    """
    cutoff_h = 36 if interval in ("1d", "1wk") else 2
    con = _db()
    try:
        row = con.execute(
            "SELECT id, completed_at, result_count FROM turbo_scan_runs "
            "WHERE tf=? AND universe='all_us' AND completed_at IS NOT NULL "
            "AND is_complete = 1 "
            "ORDER BY id DESC LIMIT 1",
            (interval,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    try:
        ca_str = row["completed_at"].rstrip("Z").split("+")[0]
        ca = datetime.fromisoformat(ca_str).replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - ca).total_seconds() / 3600 > cutoff_h:
            return None
    except Exception:
        return None

    all_us_scan_id = row["id"]

    # Build sub-universe ticker set from static fallback lists (instant, no network)
    try:
        from scanner import _FALLBACK, _NASDAQ_EXTRA, _RUSSELL2K_FALLBACK
        if universe == "sp500":
            tickers_set = set(_FALLBACK)
        elif universe == "nasdaq":
            tickers_set = set(list(_FALLBACK) + list(_NASDAQ_EXTRA))
        else:  # russell2k
            tickers_set = set(list(_FALLBACK) + list(_RUSSELL2K_FALLBACK))
    except Exception:
        return None

    # Augment with pickle cache if available (populated by previous full scans)
    import tempfile as _tmp, pickle as _pkl, os as _os
    cache_key = universe if universe != "russell2k" else "russell2k"
    _cp = _os.path.join(_tmp.gettempdir(), f"sachoki_tickers_{cache_key}.pkl")
    try:
        if _os.path.exists(_cp):
            with open(_cp, "rb") as f:
                cached = _pkl.load(f)
            if cached:
                tickers_set = tickers_set | set(cached)
    except Exception:
        pass

    now_iso = datetime.now(timezone.utc).isoformat()
    con = _db()
    try:
        _ret = " RETURNING id" if USE_PG else ""
        scan_id = con.execute(
            f"INSERT INTO turbo_scan_runs (tf, universe, started_at, completed_at, is_complete) VALUES (?, ?, ?, ?, 1){_ret}",
            (interval, universe, now_iso, now_iso),
        ).lastrowid
        con.commit()

        rows = con.execute(
            "SELECT * FROM turbo_scan_results WHERE scan_id=?",
            (all_us_scan_id,),
        ).fetchall()

        batch: list[dict] = []
        for r in rows:
            if r["ticker"] not in tickers_set:
                continue
            d = {k: v for k, v in r.items() if k != "id"}
            d["scan_id"] = scan_id
            d["scanned_at"] = now_iso
            batch.append(d)

        if batch:
            cols = list(batch[0].keys())
            ph = ", ".join(f":{c}" for c in cols)
            con.executemany(
                f"INSERT INTO turbo_scan_results ({', '.join(cols)}) VALUES ({ph})",
                batch,
            )

        found = len(batch)
        con.execute(
            "UPDATE turbo_scan_runs SET result_count=? WHERE id=?",
            (found, scan_id),
        )
        con.execute("""
            DELETE FROM turbo_scan_results WHERE scan_id IN (
                SELECT id FROM turbo_scan_runs WHERE tf=? AND universe=?
            ) AND scan_id NOT IN (
                SELECT id FROM turbo_scan_runs
                WHERE tf=? AND universe=? AND is_complete=1
                ORDER BY id DESC LIMIT 3
            )
        """, (interval, universe, interval, universe))
        con.commit()
    finally:
        con.close()

    log.info("Fast-copy %d results all_us→%s tf=%s (age %.1fh)",
             found, universe, interval,
             (datetime.now(timezone.utc) - ca).total_seconds() / 3600)
    return found


# ── Scan runner ───────────────────────────────────────────────────────────────
def run_turbo_scan(
    interval: str = "1d",
    universe: str = "sp500",
    workers: int = 4,
    lookback_n: int = 5,
    partial_day: bool = False,
    min_volume: float = 0,
    _keep_running: bool = False,  # internal: skip running=False at end (used by all-TF wrapper)
    min_store_score: float = 5,   # D+A=5 (localStorage), C=0 (IndexedDB — store all)
) -> int:
    from scanner import get_universe_tickers, UNIVERSE_CONFIGS
    global _turbo_state

    _init_db()
    cfg = UNIVERSE_CONFIGS.get(universe, UNIVERSE_CONFIGS["sp500"])
    min_price = float(cfg["min_price"])
    max_price = float(cfg["max_price"])

    _turbo_state.update({
        "running": True, "done": 0, "total": 0, "found": 0, "failed": 0,
        "fetched_from_massive": 0, "universe": universe, "interval": interval,
        "partial_day": partial_day,
        "started_at": time.time(), "completed_at": None, "error": None,
    })

    # ── Fast path: derive sub-universe from a recent all_us scan ─────────────
    if universe in ("sp500", "nasdaq", "russell2k"):
        found = _copy_from_allus_if_recent(interval, universe)
        if found is not None:
            _turbo_state.update({
                "done": found, "total": found, "found": found,
            })
            if not _keep_running:
                _turbo_state["running"] = False
                _turbo_state["completed_at"] = time.time()
            return found

    try:
        tickers = get_universe_tickers(universe)
    except Exception as exc:
        _turbo_state.update({"running": False, "error": str(exc)})
        log.error("Failed to fetch tickers for universe=%s: %s", universe, exc)
        return 0

    # ── Filter: keep only plain US common-stock primary-listing tickers ────
    # Removes "CFLT B" (space=secondary class), "BF.B" (dot=preferred), etc.
    from data_polygon import _is_valid_stock_ticker
    tickers = [t for t in tickers if _is_valid_stock_ticker(t)]

    # ── Fetch SPY + IWM once for RS computation ────────────────────────────
    spy_chg: float | None = None
    iwm_chg: float | None = None
    try:
        from data_polygon import fetch_bars, polygon_available
        days = 5
        for _sym, _attr in (("SPY", "spy_chg"), ("IWM", "iwm_chg")):
            _df = None
            if polygon_available():
                try:
                    _df = fetch_bars(_sym, interval=interval, days=days)
                except Exception:
                    pass
            if (_df is None or _df.empty) and not polygon_available():
                import yfinance as yf
                _raw = yf.Ticker(_sym).history(period="5d", interval=interval, auto_adjust=True)
                if _raw is not None and not _raw.empty:
                    _raw.columns = [str(c).lower() for c in _raw.columns]
                    _df = _raw[["close"]].dropna()
            if _df is not None and len(_df) >= 2:
                _chg = (_df["close"].iloc[-1] - _df["close"].iloc[-2]) / _df["close"].iloc[-2] * 100
                if _attr == "spy_chg":
                    spy_chg = round(float(_chg), 3)
                else:
                    iwm_chg = round(float(_chg), 3)
    except Exception as exc:
        log.debug("Could not fetch SPY/IWM for RS: %s", exc)

    _turbo_state["total"] = len(tickers)
    _turbo_state["fetched_from_massive"] = len(tickers)
    now_iso = datetime.now(timezone.utc).isoformat()

    con = _db()
    _ret = " RETURNING id" if USE_PG else ""
    scan_id = con.execute(
        f"INSERT INTO turbo_scan_runs (tf, universe, started_at) VALUES (?, ?, ?){_ret}",
        (interval, universe, now_iso),
    ).lastrowid
    con.commit()
    con.close()

    cols = ["scan_id", "ticker", "last_price", "change_pct", "scanned_at"] + _TURBO_COLS
    ph   = ", ".join(f":{c}" for c in cols)
    found = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        _profile = (
            "nasdaq"  if universe == "nasdaq"  else
            "all_us"  if universe == "all_us"  else
            "sp500"
        )
        futures = {
            pool.submit(
                _scan_turbo_ticker, t, interval, min_price, max_price,
                spy_chg, iwm_chg, partial_day, min_volume, _profile
            ): t
            for t in tickers
        }
        batch: list[dict] = []
        remaining = set(futures.keys())
        aborted = False

        # Poll with a 90-second timeout per round: if no worker finishes in 90s
        # all active threads are stuck on a hung socket — abort immediately
        # instead of waiting forever (old `as_completed` + `with pool` combo
        # would block the process until the stuck threads finally timeout).
        while remaining:
            if not _turbo_state.get("running", True):
                log.info("Turbo scan stopped by Force Stop")
                for f in remaining:
                    f.cancel()
                aborted = True
                break
            done_futs, remaining = _cf_wait(
                remaining, timeout=90, return_when=FIRST_COMPLETED
            )
            if not done_futs:
                log.error(
                    "Turbo scan: no progress in 90s, %d workers stuck — aborting",
                    len(remaining),
                )
                _turbo_state["error"] = f"Aborted: {len(remaining)} workers stuck (network hang)"
                for f in remaining:
                    f.cancel()
                aborted = True
                break
            for fut in done_futs:
                _turbo_state["done"] += 1
                try:
                    row = fut.result(timeout=1)
                except Exception:
                    row = None
                if row and row.get("turbo_score", 0) >= min_store_score:
                    row["scan_id"]    = scan_id
                    row["scanned_at"] = now_iso
                    # Guard: fill any DB column missing from the row (e.g. newly added cols)
                    _TEXT_FILL = {"tz_sig", "vol_bucket", "sig_ages", "data_source", "sector"}
                    for _c in cols:
                        if _c not in row:
                            row[_c] = "" if _c in _TEXT_FILL else 0
                    batch.append(row)
                    found += 1
                    _turbo_state["found"] += 1
                else:
                    _turbo_state["failed"] += 1
                # flush every 50 results so progress survives restart
                if len(batch) >= 50:
                    con = _db()
                    con.executemany(f"INSERT INTO turbo_scan_results ({', '.join(cols)}) VALUES ({ph})", batch)
                    con.commit()
                    con.close()
                    batch.clear()
                    gc.collect()  # release DataFrame memory between batches

        # flush remaining
        if batch:
            con = _db()
            con.executemany(f"INSERT INTO turbo_scan_results ({', '.join(cols)}) VALUES ({ph})", batch)
            con.commit()
            con.close()

        if not aborted:
            # mark completed — only for scans that ran to full completion
            con = _db()
            con.execute(
                "UPDATE turbo_scan_runs SET completed_at=?, result_count=?, is_complete=1 WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), found, scan_id),
            )
            # Keep last 3 completed runs per tf+universe combo (only touch this tf+universe)
            con.execute("""
                DELETE FROM turbo_scan_results WHERE scan_id IN (
                    SELECT id FROM turbo_scan_runs WHERE tf=? AND universe=?
                ) AND scan_id NOT IN (
                    SELECT id FROM turbo_scan_runs
                    WHERE tf=? AND universe=? AND is_complete=1
                    ORDER BY id DESC LIMIT 3
                )
            """, (interval, universe, interval, universe))
            con.commit()
            con.close()
        else:
            # aborted — remove partial results so they don't accumulate
            con = _db()
            con.execute("DELETE FROM turbo_scan_results WHERE scan_id=?", (scan_id,))
            con.execute("DELETE FROM turbo_scan_runs WHERE id=?", (scan_id,))
            con.commit()
            con.close()
    finally:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)
        if not _keep_running:
            _turbo_state["running"] = False
            _turbo_state["completed_at"] = time.time()

    log.info("Turbo scan done: %d/%d tickers, tf=%s universe=%s", found, len(tickers), interval, universe)
    return found


# ── Multi-TF scan ─────────────────────────────────────────────────────────────

def run_turbo_scan_all_tfs(
    universe: str = "sp500",
    workers: int = 4,
    lookback_n: int = 5,
    partial_day: bool = False,
    min_volume: float = 0,
    min_store_score: float = 5,
) -> dict:
    """
    Run turbo scan for all main timeframes (1wk, 1d, 4h, 1h) in sequence.
    One call covers all TFs so switching the TF display button needs no rescan.
    """
    global _turbo_state
    _turbo_state.update({"tfs_total": len(ALL_SCAN_TFS), "tfs_done": 0})
    results: dict = {}
    try:
        for i, tf in enumerate(ALL_SCAN_TFS):
            _turbo_state["tfs_done"] = i
            is_last = (i == len(ALL_SCAN_TFS) - 1)
            # _keep_running=True suppresses the running=False that run_turbo_scan
            # normally sets in its finally block — avoids the race where the 2-second
            # frontend poll catches running=False between consecutive TF scans.
            found = run_turbo_scan(
                tf, universe, workers, lookback_n, partial_day, min_volume,
                _keep_running=not is_last,
                min_store_score=min_store_score,
            )
            results[tf] = found
    finally:
        _turbo_state["running"] = False
        _turbo_state["completed_at"] = time.time()
        _turbo_state["tfs_done"] = len(ALL_SCAN_TFS)
    log.info("Multi-TF turbo scan done: %s, universe=%s", results, universe)
    return results


# ── Query ─────────────────────────────────────────────────────────────────────
_QUERY_COLS = (
    "ticker, turbo_score, vol_bucket, tz_sig, tz_bull, "
    "best_sig, strong_sig, vbo_up, vbo_dn, abs_sig, climb_sig, load_sig, "
    "ns, nd, sc, bc, sq, "
    "buy_2809, rocket, sig3g, rtv, hilo_buy, hilo_sell, atr_brk, bb_brk, "
    "bias_up, bias_down, cons_atr, "
    "fri34, fri43, fri64, l34, l43, l64, l22, l555, only_l2l4, "
    "blue, cci_ready, cci_0_retest, cci_blue_turn, "
    "bo_up, bo_dn, bx_up, bx_dn, be_up, be_dn, "
    "fuchsia_rh, fuchsia_rl, pre_pump, "
    "wick_bull, wick_bear, "
    "rsi, cci, "
    "d_strong_bull, d_absorb_bull, d_div_bull, d_cd_bull, d_surge_bull, d_blast_bull, "
    "d_strong_bear, d_absorb_bear, d_div_bear, d_cd_bear, d_surge_bear, d_blast_bear, "
    "sig_260308, sig_l88, "
    "eb_bull, eb_bear, fbo_bull, fbo_bear, bf_buy, bf_sell, "
    "ultra_3up, ultra_3dn, best_long, best_short, "
    "last_price, change_pct, scanned_at"
)
_QUERY_KEYS = [c.strip() for c in _QUERY_COLS.split(",")]


def get_turbo_results(
    limit: int = 10000,
    min_score: float = 0,
    direction: str = "bull",
    tf: str = "1d",
    universe: str = "sp500",
    price_min: float = 0,
    price_max: float = 1e9,
    rsi_min: float = 0,
    rsi_max: float = 100,
    cci_min: float = -9999,
    cci_max: float = 9999,
    vol_min: float = 0,
    vol_max: float = 0,
) -> list[dict]:
    _init_db()
    con = _db()
    try:
        row = con.execute(
            "SELECT id FROM turbo_scan_runs WHERE tf=? AND universe=? ORDER BY id DESC LIMIT 1",
            (tf, universe),
        ).fetchone()
        if not row:
            return []
        scan_id = row["id"]

        where  = "scan_id = ? AND turbo_score >= ?"
        params: list = [scan_id, min_score]

        if direction == "bull":
            where += " AND tz_bull = 1"
        elif direction == "bear":
            where += " AND tz_bull = 0"

        if price_min > 0:
            where += " AND last_price >= ?"; params.append(price_min)
        if price_max < 1e8:
            where += " AND last_price <= ?"; params.append(price_max)
        if rsi_min > 0:
            where += " AND rsi >= ?"; params.append(rsi_min)
        if rsi_max < 100:
            where += " AND rsi <= ?"; params.append(rsi_max)
        if cci_min > -9999:
            where += " AND cci >= ?"; params.append(cci_min)
        if cci_max < 9999:
            where += " AND cci <= ?"; params.append(cci_max)
        if vol_min > 0:
            where += " AND avg_vol >= ?"; params.append(vol_min)
        if vol_max > 0:
            where += " AND avg_vol < ?";  params.append(vol_max)

        rows = con.execute(
            f"SELECT * FROM turbo_scan_results WHERE {where} "
            f"ORDER BY turbo_score DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        # Sanitize NaN/Inf floats so JSON serialization never fails
        clean = []
        for row in rows:
            clean.append({
                k: (0.0 if isinstance(v, float) and (v != v or v == float('inf') or v == float('-inf')) else v)
                for k, v in row.items()
            })
        return clean
    finally:
        con.close()


def get_last_turbo_scan_time(tf: str = "1d", universe: str = "sp500") -> str | None:
    _init_db()
    con = _db()
    try:
        row = con.execute(
            "SELECT completed_at FROM turbo_scan_runs WHERE tf=? AND universe=? ORDER BY id DESC LIMIT 1",
            (tf, universe),
        ).fetchone()
        return row["completed_at"] if row else None
    finally:
        con.close()
