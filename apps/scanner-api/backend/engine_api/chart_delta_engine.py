"""
chart_delta_engine.py — Phase 8J: VERBATIM port of backend/delta_engine.py.

Order-flow / footprint approximation (Pine 260403_Delta V2).
Open-Adjusted CLV volume decomposition → 24 signal boolean columns + delta value.

Hard rule: do NOT change any formula. Magic numbers, constants, comments
preserved as-is so future audits can trace.

Output columns (all bool unless noted):
    delta (float, signed)
    strong_bull / strong_bear
    absorb_bull / absorb_bear
    div_bull / div_bear
    cd_bull / cd_bear
    surge_bull / surge_bear / blast_bull / blast_bear
    vd_div_bull / vd_div_bear
    spring / upthrust (Wyckoff)
    flip_bull / flip_bear
    orange_bull
    blast_bull_red / blast_bear_grn / surge_bull_red / surge_bear_grn

The keys turbo_engine reads from this output (mapping in row builder):
    absorb_bull → d_absorb_bull
    spring      → d_spring
    surge_bull  → d_surge_bull
    blast_bull  → d_blast_bull
    strong_bull → d_strong_bull
    div_bull    → d_div_bull
    vd_div_bull → d_vd_div_bull
    cd_bull     → d_cd_bull
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
    """Return DataFrame with delta value + signal boolean columns."""
    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]
    v = df["volume"]

    body_top  = o.where(o > c, c)
    body_bot  = o.where(o < c, c)
    body_size = (c - o).abs()
    upper_wick = h - body_top
    lower_wick = body_bot - l
    bull_body  = body_size.where(c >= o, 0.0)
    bear_body  = body_size.where(c <  o, 0.0)

    range_nz = (h - l).clip(lower=1e-10)
    buy_vol  = v * (lower_wick + bull_body) / range_nz
    sell_vol = v * (upper_wick + bear_body) / range_nz
    delta    = buy_vol - sell_vol

    ask_imb = buy_vol  > sell_vol * imb_ratio
    bid_imb = sell_vol > buy_vol  * imb_ratio

    ask_grp   = (~ask_imb).cumsum()
    bid_grp   = (~bid_imb).cumsum()
    ask_stack = ask_imb.groupby(ask_grp).cumsum().where(ask_imb, 0)
    bid_stack = bid_imb.groupby(bid_grp).cumsum().where(bid_imb, 0)

    strong_bull = (ask_stack >= stack_len) & (delta > 0) & ~(
        (bid_stack >= stack_len) & (delta < 0))
    strong_bear = (bid_stack >= stack_len) & (delta < 0) & ~strong_bull

    avg_vol    = v.rolling(20, min_periods=5).mean()
    high_vol   = v > avg_vol * abs_vol_mult
    small_body = body_size / range_nz < abs_body_pct
    absorption = high_vol & small_body
    absorb_bull = absorption & (delta > 0)
    absorb_bear = absorption & (delta < 0)

    highest_h1 = h.rolling(div_len).max().shift(1)
    lowest_l1  = l.rolling(div_len).min().shift(1)
    price_hh = h > highest_h1
    price_ll = l < lowest_l1
    div_bear = price_hh & (delta < 0) & (c > o)
    div_bull = price_ll & (delta > 0) & (c < o)
    cd_bear = (c > o) & (delta < 0) & ~div_bear
    cd_bull = (c < o) & (delta > 0) & ~div_bull

    abs_d  = delta.abs()
    abs_d1 = abs_d.shift(1).fillna(0)
    blast_bull = (delta > 0) & (abs_d > abs_d1 * delta_mult2)
    blast_bear = (delta < 0) & (abs_d > abs_d1 * delta_mult2)
    surge_bull = (delta > 0) & (abs_d > abs_d1 * delta_mult1) & ~blast_bull
    surge_bear = (delta < 0) & (abs_d > abs_d1 * delta_mult1) & ~blast_bear

    delta1 = delta.shift(1).fillna(0)
    vd_div_bull = (v < v.shift(1)) & (abs_d > abs_d1) & (delta > delta1)
    vd_div_bear = (v > v.shift(1)) & (abs_d < abs_d1) & (delta < delta1)

    spring   = div_bull & absorb_bull
    upthrust = div_bear & absorb_bear

    any_bull = surge_bull | blast_bull
    any_bear = surge_bear | blast_bear
    flip_bull = any_bull & (any_bear.shift(1).fillna(False) | any_bear.shift(2).fillna(False))
    flip_bear = any_bear & (any_bull.shift(1).fillna(False) | any_bull.shift(2).fillna(False))

    orange_bull = (any_bear & (c > o)) | (any_bull & (c < o))

    blast_bull_red  = blast_bull  & (c < o)
    blast_bear_grn  = blast_bear  & (c > o)
    surge_bull_red  = surge_bull  & (c < o)
    surge_bear_grn  = surge_bear  & (c > o)

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


# ── Mapping for chart_turbo_row_builder ──────────────────────────────────────
# delta_engine column → turbo_row key. The old turbo_engine reads these keys
# (with `d_` prefix) when summing the Delta family points.
DELTA_TO_TURBO_ROW: dict[str, str] = {
    "absorb_bull": "d_absorb_bull",
    "spring":      "d_spring",
    "surge_bull":  "d_surge_bull",
    "blast_bull":  "d_blast_bull",
    "strong_bull": "d_strong_bull",
    "div_bull":    "d_div_bull",
    "vd_div_bull": "d_vd_div_bull",
    "cd_bull":     "d_cd_bull",
}

# Display labels — F row family (delta bull tokens) for Super Chart per spec.
DELTA_SIG_COLS: list[tuple[str, str]] = [
    ("blast_bull",  "ΔΔ↑"),
    ("surge_bull",  "Δ↑"),
    ("strong_bull", "B/S↑"),
    ("absorb_bull", "Ab↑"),
    ("vd_div_bull", "dSPR"),
    ("div_bull",    "T↓"),
    ("cd_bull",     "cd↑"),
    ("flip_bull",   "FLP↑"),
    ("orange_bull", "ORG↑"),
    ("spring",      "Δ↑R"),
    ("upthrust",    "Δ↓G"),
]


__all__ = ["compute_delta", "DELTA_TO_TURBO_ROW", "DELTA_SIG_COLS"]
