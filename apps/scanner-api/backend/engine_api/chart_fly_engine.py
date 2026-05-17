"""
chart_fly_engine.py — Phase 8G commit 6: port of root backend/fly_engine.py.

260424 FLY ABCD EMA DP pattern detector.
Verbatim port — only signal_engine import path is changed.

Output columns: fly_abcd, fly_cd, fly_bd, fly_ad (boolean).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .chart_signal_engine import compute_signals as _compute_tz


def compute_fly_series(df: pd.DataFrame) -> pd.DataFrame:
    """Compute FLY signals for every bar. Returns boolean DataFrame."""
    n = len(df)
    _empty = pd.DataFrame({
        "fly_abcd": np.zeros(n, dtype=bool),
        "fly_cd":   np.zeros(n, dtype=bool),
        "fly_bd":   np.zeros(n, dtype=bool),
        "fly_ad":   np.zeros(n, dtype=bool),
    }, index=df.index)
    if n < 60:
        return _empty
    try:
        sig_df = _compute_tz(df)
        bc  = sig_df["bc"].fillna(0).astype(int).values
        zc  = sig_df["zc"].fillna(0).astype(int).values

        A_SET = frozenset({3, 4})
        B_SET = frozenset({9, 1, 2, 5, 10, 8, 12, 7})
        C_SET = frozenset({9, 10, 12, 7, 5})
        D_SET = frozenset({1, 2, 4, 6})

        c_arr = df["close"].values.astype(float)
        o_arr = df["open"].values.astype(float)

        def _ema(span):
            return pd.Series(c_arr).ewm(span=span, adjust=False).mean().values

        emas = [_ema(s) for s in (9, 20, 50, 89, 200)]
        p_arr = np.zeros(n, dtype=bool)
        d_arr = np.zeros(n, dtype=bool)
        for e in emas:
            p_arr |= (o_arr <= e) & (c_arr > e)
            d_arr |= (o_arr >= e) & (c_arr < e)
        e1_arr = p_arr | d_arr
        e2_arr = p_arr

        LOOKBACK = 30
        WIN      = 20
        WIN_AB   = 30

        def ema_seq_at(pos: int) -> bool:
            lo = max(0, pos - LOOKBACK)
            bse2 = None
            for j in range(pos, lo - 1, -1):
                if e2_arr[j]:
                    bse2 = pos - j
                    break
            if bse2 is None:
                return False
            e2_pos = pos - bse2
            for j in range(e2_pos - 1, lo - 1, -1):
                if e1_arr[j]:
                    return True
            return False

        fly_cd   = np.zeros(n, dtype=bool)
        fly_bd   = np.zeros(n, dtype=bool)
        fly_ad   = np.zeros(n, dtype=bool)
        fly_abcd = np.zeros(n, dtype=bool)

        for i in range(60, n):
            if bc[i] not in D_SET:
                continue
            lo_w  = max(-1, i - WIN - 1)
            lo_ab = max(-1, i - WIN_AB - 1)

            for ic in range(i - 1, lo_w, -1):
                if bc[ic] in C_SET and ema_seq_at(ic):
                    fly_cd[i] = True
                    break
            for ib in range(i - 1, lo_w, -1):
                if zc[ib] in B_SET and ema_seq_at(ib):
                    fly_bd[i] = True
                    break
            for ia in range(i - 1, lo_w, -1):
                if zc[ia] in A_SET and ema_seq_at(ia):
                    fly_ad[i] = True
                    break

            for ic in range(i - 1, lo_ab, -1):
                if bc[ic] not in C_SET:
                    continue
                for ib in range(ic - 1, lo_ab, -1):
                    if zc[ib] not in B_SET:
                        continue
                    for ia in range(ib - 1, lo_ab, -1):
                        if zc[ia] in A_SET and ema_seq_at(ia):
                            fly_abcd[i] = True
                            break
                    if fly_abcd[i]:
                        break
                if fly_abcd[i]:
                    break

        return pd.DataFrame({
            "fly_abcd": fly_abcd, "fly_cd": fly_cd,
            "fly_bd":   fly_bd,   "fly_ad": fly_ad,
        }, index=df.index)

    except Exception:
        return _empty


# Display routing — FLY row
FLY_SIG_COLS: list[tuple[str, str]] = [
    ("fly_abcd", "ABCD"),
    ("fly_cd",   "CD"),
    ("fly_bd",   "BD"),
    ("fly_ad",   "AD"),
]
