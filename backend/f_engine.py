"""
f_engine.py — F1–F11 buy signal patterns (260418 F Builder).

Separate module so F signal logic can be updated independently
without touching the main signal_engine or turbo_engine code.

Signal → bc/zc code mapping (from signal_engine priority engine):
  Bull (bc): T4=1, T6=2, T1G=3, T2G=4, T1=5, T2=6, T9=7, T10=8,
             T3=9, T11=10, T5=11, T12=12
  Bear (zc): Z4=1, Z6=2, Z1G=3, Z2G=4, Z1=5, Z2=6, Z9=7, Z10=8,
             Z3=9, Z11=10, Z5=11, Z12=12, Z7=13
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def compute_f_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute F1–F11 buy patterns.
    Returns DataFrame with boolean columns f1..f11 and any_f.
    """
    from signal_engine import compute_signals

    sig = compute_signals(df)
    bc  = sig["bc"].fillna(0).astype(int)
    zc  = sig["zc"].fillna(0).astype(int)
    cls = df["close"] if "close" in df.columns else df[df.columns[3]]
    opn = df["open"]  if "open"  in df.columns else df[df.columns[0]]

    fv = 0
    bc1 = bc.shift(1, fill_value=fv).astype(int)
    bc2 = bc.shift(2, fill_value=fv).astype(int)
    bc3 = bc.shift(3, fill_value=fv).astype(int)
    bc4 = bc.shift(4, fill_value=fv).astype(int)
    zc1 = zc.shift(1, fill_value=fv).astype(int)
    zc2 = zc.shift(2, fill_value=fv).astype(int)
    zc3 = zc.shift(3, fill_value=fv).astype(int)
    zc4 = zc.shift(4, fill_value=fv).astype(int)

    cls2 = cls.shift(2)
    opn2 = opn.shift(2)
    opn3 = opn.shift(3)
    cls4 = cls.shift(4)
    opn4 = opn.shift(4)

    # ── F1 ───────────────────────────────────────────────────────────────────────
    F1 = (
        (zc2.isin([6, 7]))   & (zc1 == 10) & (bc == 5) & (cls > cls2) |
        (bc4 == 8)           & (bc2 == 9)  & (bc == 5) |
        (bc2.isin([6, 7]))   & (bc == 5)   & (cls > cls2) |
        (zc2.isin([3, 6]))   & (zc1 == 8)  & (bc == 5) |
        (zc2.isin([7, 10, 12]) | (bc2 == 11)) & (zc1.isin([4, 10, 3])) & (bc == 5) |
        (bc2.isin([5, 1, 3])) & (zc1.isin([7, 11])) & (bc == 5) |
        ((bc2 == 4) | (zc1 == 3)) & (bc == 5) & (cls > cls2) |
        (zc2 == 7) & (zc1 == 8) & (bc == 5) |
        (zc2.isin([3, 4]) | bc2.isin([11, 8, 3, 7])) & (zc1.isin([2, 1, 5])) & (bc == 5) |
        (zc2.isin([3, 4, 1, 9, 6]) | bc2.isin([9, 11])) &
        (bc1.isin([7, 11, 1, 9]) | zc1.isin([6, 7, 9, 11])) &
        (bc.isin([5, 3, 6, 4])) & (cls > cls2) & (cls > opn2) |
        (bc3.isin([11, 3, 12]) | (zc3 == 9)) & (zc2.isin([11, 1, 3, 6])) & (zc1 == 6) & (bc == 5) |
        (zc3.isin([1, 3]) | bc3.isin([7, 6])) & (zc2.isin([8, 10, 4, 9, 11])) & (zc1.isin([4, 6])) & (bc == 5) |
        ((bc3 == 6) | (zc3 == 6)) & (zc2.isin([3, 6])) & (zc1 == 4) & (bc == 5) |
        (zc3.isin([7, 4, 6]) | (bc3 == 4)) & (zc2.isin([4, 8, 9, 1])) & (zc1.isin([4, 6])) & (bc == 5) |
        (zc3 == 7) & (zc2 == 4) & (zc1 == 6) & (bc == 5) |
        (bc2.isin([6, 5])) & (bc.isin([6, 5])) & (cls > cls2) |
        (zc3 == 4) & (zc2 == 4) & (zc1 == 8) & (bc == 5) |
        (zc2 == 7) & (bc1 == 9) & (bc == 6) & (cls > cls2) & (cls > opn2) |
        (zc1 == 4) & (bc == 3) |
        ((bc2 == 5) | (zc2 == 7)) & (bc == 4) |
        (bc3.isin([2, 1, 5, 6]) | zc3.isin([10, 1, 5])) &
        (zc2.isin([1, 5, 7, 6, 9, 4])) & (zc1.isin([6, 4, 5, 10])) & (bc == 5)
    )

    # ── F2 ───────────────────────────────────────────────────────────────────────
    F2 = (
        (bc1.isin([5, 8])) & (bc == 6) |
        (zc2.isin([10, 4, 1])) & (bc1.isin([7, 11])) & (bc == 6) |
        (zc2 == 6) & (bc1 == 11) & (bc == 6) |
        (zc2.isin([7, 2, 9])) & (bc1.isin([9, 11])) & (bc == 6) |
        (zc3 == 9) & (bc1 == 10) & (bc == 6) |
        (bc2.isin([9, 1, 5, 7])) & (bc1 == 6) & (bc == 6) |
        (zc4 == 4) & (zc3 == 10) & (zc2 == 4) & (bc == 6) |
        (zc3 == 6) & (zc2 == 6) & (bc1 == 9) & (bc == 6) |
        (bc3.isin([6, 5, 4, 7]) | zc3.isin([2, 1])) & (bc1 == 9) & (bc == 6)
    )

    # ── F4 ───────────────────────────────────────────────────────────────────────
    F4 = (
        (bc2.isin([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]) | (bc3 == 6)) & (bc == 1)
    )

    # ── F5 ───────────────────────────────────────────────────────────────────────
    F5 = (
        (bc2.isin([9, 11, 7, 6]) | bc3.isin([7, 5])) & (zc1.isin([7, 13, 11, 2, 6, 1])) & (bc == 11) |
        (bc2.isin([4, 9]) | (zc2 == 10)) & (zc1.isin([5, 9, 11, 6])) & (bc == 11) |
        ((bc2 == 4) | (bc3 == 3)) & (zc1 == 1) & (bc == 9) |
        (zc3.isin([9, 4])) & (zc1.isin([4, 8, 5])) & (bc == 9) |
        (bc2.isin([5, 6])) & (zc1 == 9) & (bc == 9) |
        (zc3.isin([10]) | (bc3 == 9) | (zc2 == 7)) & (zc1.isin([2, 1, 6])) & (bc == 9)
    )

    # ── F6 ───────────────────────────────────────────────────────────────────────
    F6 = (
        (bc1.isin([1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])) & (bc == 2)
    )

    # ── F7 ───────────────────────────────────────────────────────────────────────
    F7 = (
        (zc2.isin([4, 1])) & (bc == 4) |
        (bc1.isin([12, 8, 10, 1, 5, 6, 9, 7, 3, 2])) & (bc == 4) |
        (zc3.isin([4, 2, 8, 7]) | bc3.isin([5, 1])) &
        (zc2.isin([4, 6, 9, 1, 2])) & (bc1.isin([11, 7, 9])) & (bc == 4)
    )

    # ── F8 ───────────────────────────────────────────────────────────────────────
    F8 = (
        (zc2 == 10) & (zc == 10) |
        (zc2 == 8)  & (zc == 8)  |
        (zc1 == 10) & (zc == 10) |
        (zc1 == 8)  & (zc == 10) |
        (zc1 == 8)  & (zc == 8)  |
        (zc1 == 10) & (zc == 8)  |
        (bc1 == 8)  & (bc == 8)  |
        (bc2 == 8)  & (bc == 8)  |
        (bc1 == 10) & (bc == 8)  |
        (bc1.isin([12]) | bc2.isin([12])) & (bc == 8) |
        (bc1 == 10) & (bc == 10) |
        (bc1.isin([12]) | bc2.isin([12])) & (bc == 10) |
        (bc1 == 8)  & (bc == 12) |
        (bc1 == 12) & (bc == 12)
    )

    # ── F9 ───────────────────────────────────────────────────────────────────────
    F9 = (
        (bc2 == 11) & (zc1 == 1)  & (bc == 7) |
        (zc2 == 4)  & (zc1 == 6)  & (bc == 7) |
        (zc2.isin([7, 5]) | (zc3 == 7)) & (bc == 7) & (cls > cls2) |
        (zc2 == 11) & (zc1 == 2)  & (bc == 7) & (cls > cls2) |
        (bc2 == 6)  & (zc1.isin([11, 3])) & (bc == 7) |
        (bc2.isin([4, 3])) & (zc1 == 5) & (bc == 7) |
        (zc3.isin([4, 9, 11]) | bc3.isin([4, 6]) | (zc1 == 5)) &
        (zc2.isin([6, 10, 3, 7])) & (zc1 == 4) & (bc == 7) |
        (zc3 == 9)  & (zc2 == 4)  & (zc1 == 4)  & (bc == 7) |
        (bc2 == 4)  & (zc1 == 1)  & (bc == 7) & (cls > cls2) |
        (bc3 == 6)  & (zc2 == 5)  & (zc1 == 4)  & (bc == 7) |
        (zc3.isin([4, 7, 6]) | bc3.isin([3, 4, 6])) &
        (bc2.isin([4, 3]) | zc2.isin([5, 10, 6, 1, 9])) &
        (zc1.isin([1, 4, 6])) & (bc == 7) |
        (zc3.isin([11, 2])) & (zc2.isin([8, 6, 2])) & (zc1 == 6) & (bc == 7) |
        (zc3 == 7)  & (bc2 == 5)  & (zc1 == 5)  & (bc == 7) |
        (bc4 == 5)  & (bc2 == 9)  & (bc == 7) |
        (zc4.isin([7, 1])) & (bc3.isin([7, 9])) & (zc2.isin([1, 11])) & (zc1 == 2) & (bc == 7)
    )

    # ── F10 ──────────────────────────────────────────────────────────────────────
    F10 = (
        (zc2.isin([1, 3]))   & (zc1 == 8)  & (bc == 3) |
        (bc4 == 9) & (bc2 == 5) & (bc == 3) |
        (bc2 == 1)  & (zc1 == 11) & (bc == 3) |
        (zc2.isin([1, 9, 8, 7, 10, 4, 6])) & (zc1.isin([4, 10])) & (bc == 3) |
        (bc2 == 9)  & (zc1 == 1)  & (bc == 3) |
        (zc2.isin([9, 10, 2, 5])) & (zc1 == 8) & (bc == 3) |
        (zc2.isin([2, 1, 5, 4])) & (zc1.isin([6, 13])) & (bc == 3) |
        (bc2.isin([5, 9, 6])) & (zc1 == 7) & (bc == 3) |
        (zc2.isin([4, 10]) | bc2.isin([11, 3, 4, 7, 6])) &
        (zc1.isin([2, 3, 11, 1, 5, 8])) & (bc == 3)
    )

    # ── F11 ──────────────────────────────────────────────────────────────────────
    F11 = (
        (zc2.isin([1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12])) & (bc == 1) |
        ((zc2 == 5) | (zc3 == 3)) & (zc1.isin([6, 2])) & (bc == 1)
    )

    # ── F3 (depends on F4, F6, F11) ──────────────────────────────────────────────
    F3 = (
        (F11 & (cls4 > opn4)) |
        ((F6 | F4) & ((cls4 > opn4) | (cls.shift(3) > opn.shift(3))))
    )

    any_f = F1 | F2 | F3 | F4 | F5 | F6 | F7 | F8 | F9 | F10 | F11

    return pd.DataFrame(
        {
            "f1": F1, "f2": F2, "f3": F3, "f4": F4, "f5": F5, "f6": F6,
            "f7": F7, "f8": F8, "f9": F9, "f10": F10, "f11": F11,
            "any_f": any_f,
        },
        index=df.index,
    ).fillna(False)
