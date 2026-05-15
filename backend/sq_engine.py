"""
sq_engine.py — VSA-style signals from Pine Script 260312.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from indicators import atr as _atr_hlc


def compute_sq(
    df: pd.DataFrame,
    vol_len: int = 20,
    hi_vol_mult: float = 1.6,
    lo_vol_mult: float = 0.80,
    atr_len: int = 14,
    narrow_mult: float = 1.0,
    clv_high: float = 0.70,
    clv_low: float = 0.30,
    test_n: int = 3,
) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]
    v = df.get("volume", pd.Series(0.0, index=df.index)).fillna(0)

    vol_ma = v.ewm(alpha=1 / vol_len, adjust=False).mean()
    hi_vol = v >= vol_ma * hi_vol_mult
    lo_vol = v <= vol_ma * lo_vol_mult

    spread = h - l
    atr  = _atr_hlc(h, l, c, atr_len)

    narrow     = (spread > 0) & (spread < atr * narrow_mult)
    inside_bar = (h <= h.shift(1)) & (l >= l.shift(1))

    clv = ((c - l) / spread).where(spread > 0, other=0.5)

    up_bar = c > c.shift(1)
    dn_bar = c < c.shift(1)

    SQ = hi_vol & narrow
    NS = lo_vol & (narrow | inside_bar) & dn_bar & (clv >= clv_high)
    ND = lo_vol & (narrow | inside_bar) & up_bar  & (clv <= clv_low)

    effort_recent = SQ.shift(1).fillna(False) | SQ.shift(2).fillna(False)

    test_signal = NS | ND | (lo_vol & (narrow | inside_bar))
    test_recent = test_signal.rolling(test_n, min_periods=1).max().astype(bool)

    highest2  = h.shift(1).rolling(2, min_periods=1).max()
    lowest2   = l.shift(1).rolling(2, min_periods=1).min()
    confirm_up = (c > highest2)  & (clv >= 0.55)
    confirm_dn = (c < lowest2)   & (clv <= 0.45)

    SIG3_UP = effort_recent & test_recent & confirm_up
    SIG3_DN = effort_recent & test_recent & confirm_dn

    return pd.DataFrame(
        {
            "SQ":      SQ,
            "NS":      NS,
            "ND":      ND,
            "SIG3_UP": SIG3_UP,
            "SIG3_DN": SIG3_DN,
        },
        index=df.index,
    )