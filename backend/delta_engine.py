"""
delta_engine.py — Order-flow / footprint approximation (Pine 260403_Delta V2).

Volume decomposition — Open-Adjusted CLV (V2):
    lowerWick = min(open,close) - low          ← buying pressure
    upperWick = high - max(open,close)          ← selling pressure
    body      → buy side if close >= open, sell side if close < open
    buyVol  = volume × (lowerWick + bull_body) / rangeNZ
    sellVol = volume × (upperWick + bear_body) / rangeNZ
    delta   = buyVol − sellVol

Improvement over V1 (pure CLV): +10-15% accuracy on wick-heavy bars
because open price disambiguates candle body direction.

Signals returned (bool Series):
    strong_bull  — N consecutive ask-imbalance bars + delta > 0
    strong_bear  — N consecutive bid-imbalance bars + delta < 0
    absorb_bull  — high vol + tiny body + delta > 0   (sell absorption)
    absorb_bear  — high vol + tiny body + delta < 0   (buy absorption)
    div_bull     — new price low  + delta > 0 + red bar   (bear trap)
    div_bear     — new price high + delta < 0 + green bar (bull trap)
    cd_bull      — red candle + delta > 0               (conflict ↑)
    cd_bear      — green candle + delta < 0             (conflict ↓)
    surge_bull   — |delta| > |delta[1]| × mult1         (Δ acceleration)
    surge_bear   — |delta| > |delta[1]| × mult1 (bear)
    blast_bull   — |delta| > |delta[1]| × mult2         (ΔΔ blast)
    blast_bear   — |delta| > |delta[1]| × mult2 (bear)
    vd_div_bull  — vol↓ + delta↑ (no supply / low effort high result)
    vd_div_bear  — vol↑ + delta↓ (distribution / effort without result)
    spring       — bear trap + sell absorption  (Wyckoff Spring)
    upthrust     — bull trap + buy absorption   (Wyckoff Upthrust)
"""
from __future__ import annotations

import pandas as pd


def compute_delta(
    df: pd.DataFrame,
    imb_ratio:    float = 3.0,
    stack_len:    int   = 3,
    abs_vol_mult: float = 1.5,
    abs_body_pct: float = 0.30,
    delta_mult1:  float = 1.5,
    delta_mult2:  float = 5.0,
    div_len:      int   = 3,
) -> pd.DataFrame:
    """Return DataFrame with delta value + 16 signal boolean columns."""
    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]
    v = df["volume"]

    # ── Open-Adjusted CLV (V2) ────────────────────────────────────────────
    body_top  = o.where(o > c, c)          # max(open, close)
    body_bot  = o.where(o < c, c)          # min(open, close)
    body_size = (c - o).abs()
    upper_wick = h - body_top              # selling pressure zone
    lower_wick = body_bot - l              # buying  pressure zone
    bull_body  = body_size.where(c >= o, 0.0)  # body on buy side
    bear_body  = body_size.where(c <  o, 0.0)  # body on sell side

    range_nz = (h - l).clip(lower=1e-10)
    buy_vol  = v * (lower_wick + bull_body) / range_nz
    sell_vol = v * (upper_wick + bear_body) / range_nz
    delta    = buy_vol - sell_vol

    # ── Imbalance stacks (vectorised) ─────────────────────────────────────
    ask_imb = buy_vol  > sell_vol * imb_ratio
    bid_imb = sell_vol > buy_vol  * imb_ratio

    ask_grp   = (~ask_imb).cumsum()
    bid_grp   = (~bid_imb).cumsum()
    ask_stack = ask_imb.groupby(ask_grp).cumsum().where(ask_imb, 0)
    bid_stack = bid_imb.groupby(bid_grp).cumsum().where(bid_imb, 0)

    strong_bull = (ask_stack >= stack_len) & (delta > 0) & ~(
        (bid_stack >= stack_len) & (delta < 0))
    strong_bear = (bid_stack >= stack_len) & (delta < 0) & ~strong_bull

    # ── Absorption ────────────────────────────────────────────────────────
    avg_vol    = v.rolling(20, min_periods=5).mean()
    high_vol   = v > avg_vol * abs_vol_mult
    small_body = body_size / range_nz < abs_body_pct
    absorption = high_vol & small_body

    absorb_bull = absorption & (delta > 0)
    absorb_bear = absorption & (delta < 0)

    # ── Divergence / trap signals ─────────────────────────────────────────
    highest_h1 = h.rolling(div_len).max().shift(1)
    lowest_l1  = l.rolling(div_len).min().shift(1)

    price_hh = h > highest_h1
    price_ll = l < lowest_l1

    div_bear = price_hh & (delta < 0) & (c > o)   # bull trap
    div_bull = price_ll & (delta > 0) & (c < o)   # bear trap

    cd_bear = (c > o) & (delta < 0) & ~div_bear
    cd_bull = (c < o) & (delta > 0) & ~div_bull

    # ── Delta surge / blast ───────────────────────────────────────────────
    abs_d  = delta.abs()
    abs_d1 = abs_d.shift(1).fillna(0)

    blast_bull = (delta > 0) & (abs_d > abs_d1 * delta_mult2)
    blast_bear = (delta < 0) & (abs_d > abs_d1 * delta_mult2)
    surge_bull = (delta > 0) & (abs_d > abs_d1 * delta_mult1) & ~blast_bull
    surge_bear = (delta < 0) & (abs_d > abs_d1 * delta_mult1) & ~blast_bear

    # ── Volume ↔ Delta divergence ─────────────────────────────────────────
    # vd_div_bull: vol↓ + |delta|↑ + delta > delta[1]  → no supply
    # vd_div_bear: vol↑ + |delta|↓ + delta < delta[1]  → distribution
    delta1 = delta.shift(1).fillna(0)
    vd_div_bull = (v < v.shift(1)) & (abs_d > abs_d1) & (delta > delta1)
    vd_div_bear = (v > v.shift(1)) & (abs_d < abs_d1) & (delta < delta1)

    # ── Wyckoff Spring / Upthrust ─────────────────────────────────────────
    spring   = div_bull & absorb_bull   # bear trap + absorbed selling
    upthrust = div_bear & absorb_bear   # bull trap + absorbed buying

    # ── Delta Flip sequence ───────────────────────────────────────────────
    # flip_bull: bull surge/blast now, after bear surge/blast 1-2 bars ago
    # flip_bear: bear surge/blast now, after bull surge/blast 1-2 bars ago
    any_bull = surge_bull | blast_bull
    any_bear = surge_bear | blast_bear
    flip_bull = any_bull & (any_bear.shift(1).fillna(False) | any_bear.shift(2).fillna(False))
    flip_bear = any_bear & (any_bull.shift(1).fillna(False) | any_bull.shift(2).fillna(False))

    # ── Orange Bull: same-bar contradiction ───────────────────────────────
    # bear delta on a bullish close (or bull delta on a bearish close)
    orange_bull = (any_bear & (c > o)) | (any_bull & (c < o))

    # ── Granular contradiction signals ────────────────────────────────────
    # blast/surge on a candle that contradicts the delta direction
    blast_bull_red  = blast_bull  & (c < o)   # ΔΔ bull + red candle  → hidden buying
    blast_bear_grn  = blast_bear  & (c > o)   # ΔΔ bear + green candle → hidden selling
    surge_bull_red  = surge_bull  & (c < o)   # Δ  bull + red candle
    surge_bear_grn  = surge_bear  & (c > o)   # Δ  bear + green candle

    return pd.DataFrame({
        "delta":           delta.round(0),
        "strong_bull":     strong_bull,
        "strong_bear":     strong_bear,
        "absorb_bull":     absorb_bull,
        "absorb_bear":     absorb_bear,
        "div_bull":        div_bull,
        "div_bear":        div_bear,
        "cd_bull":         cd_bull,
        "cd_bear":         cd_bear,
        "surge_bull":      surge_bull,
        "surge_bear":      surge_bear,
        "blast_bull":      blast_bull,
        "blast_bear":      blast_bear,
        "vd_div_bull":     vd_div_bull,
        "vd_div_bear":     vd_div_bear,
        "spring":          spring,
        "upthrust":        upthrust,
        "flip_bull":       flip_bull,
        "flip_bear":       flip_bear,
        "orange_bull":     orange_bull,
        "blast_bull_red":  blast_bull_red,
        "blast_bear_grn":  blast_bear_grn,
        "surge_bull_red":  surge_bull_red,
        "surge_bear_grn":  surge_bear_grn,
    }, index=df.index)

