"""
chart_b_engine.py — Phase 8G commit 6: port of compute_b_signals + compute_g_signals
                    from root backend/signal_engine.py.

B1–B11 multi-bar T/Z sequence buy patterns AND G1/G2/G4/G6/G11 state-machine
patterns. Both depend on the T/Z code stream from chart_signal_engine.

Verbatim formula port — only the signal_engine import path is changed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .chart_signal_engine import compute_signals as _compute_tz


def compute_g_signals(df: pd.DataFrame) -> pd.DataFrame:
    """G-signal state machine matching 260410_G_BUILDER. Returns g1,g2,g4,g6,g11."""
    sig = _compute_tz(df)
    sid = sig["sig_id"].values.astype(int)
    n   = len(sid)

    g1  = np.zeros(n, dtype=bool)
    g2  = np.zeros(n, dtype=bool)
    g4  = np.zeros(n, dtype=bool)
    g6  = np.zeros(n, dtype=bool)
    g11 = np.zeros(n, dtype=bool)

    g_armed   = False
    g11_armed = False

    for i in range(n):
        s = sid[i]
        trigger_z = s in (23, 24, 25)   # Z10, Z11, Z12
        g1_raw  = g_armed and s == 2    # T1
        g2_raw  = g_armed and s == 1    # T1G
        g4_raw  = g_armed and s == 6    # T4
        g6_raw  = g_armed and s == 8    # T6
        any_g   = g1_raw or g2_raw or g4_raw or g6_raw
        g1[i]  = g1_raw
        g2[i]  = g2_raw
        g4[i]  = g4_raw
        g6[i]  = g6_raw
        g_armed = (g_armed or trigger_z) and not any_g

        g11_trigger = s in (10, 11)     # T10, T11
        g11_raw = g11_armed and s == 2  # T1
        g11[i]  = g11_raw
        g11_armed = (g11_armed or g11_trigger) and not g11_raw

    return pd.DataFrame(
        {"g1": g1, "g2": g2, "g4": g4, "g6": g6, "g11": g11},
        index=df.index,
    )


def compute_b_signals(df: pd.DataFrame) -> pd.DataFrame:
    """B1–B11 buy patterns via T/Z combinations."""
    sig  = _compute_tz(df)
    bc   = sig["bc"].fillna(0).astype(int)
    zc   = sig["zc"].fillna(0).astype(int)
    cls  = df["close"] if "close" in df.columns else df[df.columns[3]]
    opn  = df["open"]  if "open"  in df.columns else df[df.columns[0]]

    fv = 0
    bc1 = bc.shift(1, fill_value=fv).astype(int)
    bc2 = bc.shift(2, fill_value=fv).astype(int)
    bc3 = bc.shift(3, fill_value=fv).astype(int)
    bc6 = bc.shift(6, fill_value=fv).astype(int)
    zc1 = zc.shift(1, fill_value=fv).astype(int)
    zc2 = zc.shift(2, fill_value=fv).astype(int)
    zc3 = zc.shift(3, fill_value=fv).astype(int)
    c1  = cls.shift(1)

    B1 = (
        ((bc1==11) & (bc==6)) |
        ((bc1==10) & (bc==4)) |
        ((zc2==10) & bc.isin([9, 5, 3])) |
        ((zc2==2)  & (zc1==6) & (bc==5)) |
        ((bc1==5)  & (bc==4)) |
        ((bc1==7)  & (bc==6)) |
        ((zc3==3)  & (bc==4))
    )

    B2 = (
        (bc2.isin([5,3,6,4,7,9,11,2,1,10,8]) & (bc==1)) |
        (bc1.isin([1,4,3,9,7,5,8,10,2]) & (bc==2)) |
        ((bc2==10) & (bc==2)) |
        ((bc3==9)  & (bc==1)) |
        ((bc6==9)  & (bc==1)) |
        ((zc1==4)  & (bc==1)) |
        (((zc1==2) | (zc2==2)) & (bc==1))
    )

    _B3_strong2 = bc2.isin([3, 4, 1, 7, 11])
    B3 = (
        ((bc2==2)  & (bc==5)) |
        ((zc2==9) & (zc1==6)  & (bc==9)) |
        ((zc2==9) & (bc1==9)  & (bc==6)) |
        (_B3_strong2 & (zc1==1) & bc.isin([5, 9])) |
        ((bc1==8)  & bc.isin([3, 4, 6]))
    )

    B4 = ((zc1==1) & (bc==3))

    B5 = (
        ((zc2==1)  & (bc1==11) & ((bc==6) | (bc==4))) |
        ((zc2==11) & (bc1==11) & (bc==4)) |
        ((zc2==4)  & (bc1==11) & (bc==4)) |
        ((zc2==6)  & (bc1==11) & (bc==2))
    )

    B6 = (
        ((bc2==1)  & (bc1==8) & (bc==4)) |
        ((zc2==6)  & (zc1==4) & (bc==7))
    )

    B7 = (
        ((zc2==11) & (zc1==6) & (bc==5)) |
        ((bc2==7)  & (bc1==6) & (bc==6)) |
        ((bc1==5)  & (bc==6)) |
        ((bc1==6)  & (bc==4)) |
        ((bc1==1)  & (bc==4)) |
        ((bc1==3)  & (bc==4))
    )

    B8 = (
        ((bc2==3)  & (bc==3)) |
        (zc2.isin([4,3]) & bc.isin([3,7])) |
        (((zc1==4) | (zc1==3)) & (bc==3)) |
        ((zc2==3)  & (bc==3)) |
        ((zc2==2)  & (bc==3)) |
        ((bc2==7)  & (zc1==7) & (bc==9)) |
        ((zc2==9) & (zc1==4) & (bc==3))
    )

    B9 = ((zc3==8) & (zc2==4) & (bc1==7) & (cls > opn))

    B10_s1 = (
        ((zc2==8)  & (zc1==4)  & (bc==5)) |
        ((zc1==8)  & ((bc==5)  | (bc==1))) |
        ((zc2==8)  & (zc1==10) & (zc==13)) |
        ((zc2==8)  & (zc1==4)  & (bc==3))
    )
    B10_s2 = ((zc2==8) & ((zc1==2) | (bc1==7)) & ((bc==5) | (bc==2)))
    B10_s3 = (((zc2==2) | (zc2==4)) & (zc1==8) & ((bc==3) | (bc==2)))
    B10 = B10_s1 | B10_s2 | B10_s3

    B11 = (
        ((zc1==10) & (bc==5)) |
        ((bc2==9)  & (bc1==10) & (bc==6)) |
        ((zc2==9) & (zc1==8)  & (zc==10)) |
        ((zc2==7)  & (zc1==8)  & (cls > c1)) |
        ((zc2==2)  & (zc1==8)  & (cls > c1)) |
        (((bc3==9) | (bc3==1)) & (zc2==1) & (zc1==6) & bc.isin([7, 3])) |
        ((zc2==6)  & (bc1==3)  & (bc==6)) |
        ((bc2==9)  & (bc1==4)  & (bc==6)) |
        ((zc2==6)  & (bc1==9)  & (bc==6))
    )

    return pd.DataFrame(
        {"b1": B1, "b2": B2, "b3": B3, "b4": B4, "b5": B5,
         "b6": B6, "b7": B7, "b8": B8, "b9": B9, "b10": B10, "b11": B11},
        index=df.index,
    ).fillna(False)


# Display routing — B row + G row
B_SIG_COLS: list[tuple[str, str]] = [
    ("b1",  "B1"),  ("b2",  "B2"),  ("b3",  "B3"),  ("b4",  "B4"),
    ("b5",  "B5"),  ("b6",  "B6"),  ("b7",  "B7"),  ("b8",  "B8"),
    ("b9",  "B9"),  ("b10", "B10"), ("b11", "B11"),
]

G_SIG_COLS: list[tuple[str, str]] = [
    ("g1",  "G1"),
    ("g2",  "G2"),
    ("g4",  "G4"),
    ("g6",  "G6"),
    ("g11", "G11"),
]
