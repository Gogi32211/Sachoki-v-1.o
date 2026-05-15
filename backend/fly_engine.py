"""
fly_engine.py — 260424 FLY ABCD EMA DP pattern detector.

Detects ABCD sequences using T/Z role assignments and EMA crossing context.
A = ZC in {3,4}    (Z1G, Z2G)            — strong bearish
B = ZC in {9,1,2,5,10,8,12,7}            — various bearish codes
C = BC in {9,10,12,7,5}  (T3,T11,T12,T9,T1) — moderate bull
D = BC in {1,2,4,6}  (T4,T6,T2G,T2)     — strong bull (fires on current bar)

EMA sequence context: E1 (drop or cross) happened before E2 (cross only),
both within LOOKBACK bars of the role bar being tested.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def compute_fly_abcd(df: pd.DataFrame) -> dict:
    """
    Compute FLY ABCD, CD, BD, AD signals on the last bar.

    ABCD: D on current bar + ordered A→B→C in last 30 bars + EMA seq at A.
    CD / BD / AD: D on current bar + C / B / A within 20 bars + EMA seq at that bar.

    Returns dict: fly_abcd, fly_cd, fly_bd, fly_ad (0 or 1 each).
    """
    zero = dict(fly_abcd=0, fly_cd=0, fly_bd=0, fly_ad=0)
    if df is None or len(df) < 60:
        return zero
    try:
        from signal_engine import compute_signals
        sig_df = compute_signals(df)
        bc = sig_df["bc"].fillna(0).astype(int).values
        zc = sig_df["zc"].fillna(0).astype(int).values
        n = len(bc)

        # Role sets
        A_SET = frozenset({3, 4})                        # ZC: Z1G, Z2G
        B_SET = frozenset({9, 1, 2, 5, 10, 8, 12, 7})   # ZC: Z3,Z4,Z6,Z1,Z11,Z10,Z12,Z9
        C_SET = frozenset({9, 10, 12, 7, 5})             # BC: T3,T11,T12,T9,T1
        D_SET = frozenset({1, 2, 4, 6})                  # BC: T4,T6,T2G,T2

        # D must fire on the last bar
        if bc[n - 1] not in D_SET:
            return zero

        # EMA arrays
        c = df["close"].values.astype(float)
        o = df["open"].values.astype(float)

        def _ema(span):
            return pd.Series(c).ewm(span=span, adjust=False).mean().values

        emas = [_ema(9), _ema(20), _ema(50), _ema(89), _ema(200)]

        # P = any EMA cross up (open at/below, close above)
        # D = any EMA drop down (open at/above, close below)
        p_arr = np.zeros(n, dtype=bool)
        d_arr = np.zeros(n, dtype=bool)
        for e in emas:
            p_arr |= (o <= e) & (c > e)
            d_arr |= (o >= e) & (c < e)

        e1_arr = p_arr | d_arr  # E1: any EMA event (D or P)
        e2_arr = p_arr          # E2: EMA cross up only

        LOOKBACK = 30

        def ema_seq_at(pos: int) -> bool:
            """EMA sequence valid at bar `pos`: find recent E2, then older E1 before it."""
            # Find most recent E2 at or before pos
            bse2 = None
            lo = max(0, pos - LOOKBACK)
            for j in range(pos, lo - 1, -1):
                if e2_arr[j]:
                    bse2 = pos - j
                    break
            if bse2 is None:
                return False
            # Find most recent E1 that is strictly before the E2 event
            e2_pos = pos - bse2
            bse1 = None
            for j in range(e2_pos - 1, lo - 1, -1):
                if e1_arr[j]:
                    bse1 = pos - j
                    break
            if bse1 is None:
                return False
            return bse1 > bse2  # E1 is older than E2 ✓

        last = n - 1
        WIN    = 20   # window for CD / BD / AD
        WIN_AB = 30   # window for ABCD

        fly_cd = fly_bd = fly_ad = fly_abcd = 0

        # CD: C within WIN bars
        for ic in range(last - 1, max(-1, last - WIN - 1), -1):
            if bc[ic] in C_SET and ema_seq_at(ic):
                fly_cd = 1
                break

        # BD: B within WIN bars
        for ib in range(last - 1, max(-1, last - WIN - 1), -1):
            if zc[ib] in B_SET and ema_seq_at(ib):
                fly_bd = 1
                break

        # AD: A within WIN bars
        for ia in range(last - 1, max(-1, last - WIN - 1), -1):
            if zc[ia] in A_SET and ema_seq_at(ia):
                fly_ad = 1
                break

        # ABCD: ordered A→B→C within WIN_AB bars, EMA seq valid at A
        for ic in range(last - 1, max(-1, last - WIN_AB - 1), -1):
            if bc[ic] not in C_SET:
                continue
            for ib in range(ic - 1, max(-1, last - WIN_AB - 1), -1):
                if zc[ib] not in B_SET:
                    continue
                for ia in range(ib - 1, max(-1, last - WIN_AB - 1), -1):
                    if zc[ia] in A_SET and ema_seq_at(ia):
                        fly_abcd = 1
                        break
                if fly_abcd:
                    break
            if fly_abcd:
                break

        return dict(fly_abcd=fly_abcd, fly_cd=fly_cd, fly_bd=fly_bd, fly_ad=fly_ad)

    except Exception:
        return zero


def compute_fly_series(df: pd.DataFrame) -> pd.DataFrame:
    """Compute FLY signals for every bar (full series). Returns boolean DataFrame."""
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
        from signal_engine import compute_signals
        sig_df = compute_signals(df)
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
                    return True   # bse1 > bse2 guaranteed
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
