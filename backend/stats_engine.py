"""
stats_engine.py — T/Z × L co-occurrence matrix.

For each of the 25 T/Z signals, counts how often each L column
fires on the same bar. Adds gold/green/red color hints for the UI.
"""
from __future__ import annotations

import pandas as pd

# 12 L columns in display order
L_COLS = ["L1", "L2", "L3", "L4", "L5", "L6",
          "L34", "L22", "L64", "L43", "L1L2", "L2L5"]

# Good combos: T signals like accumulation L signals, Z signals like distribution L signals
GOOD_FOR_T = {"L1", "L2", "L3", "L34", "L1L2"}
GOOD_FOR_Z = {"L2", "L5", "L6", "L22", "L64"}

SIG_ORDER = [
    (1,  "T1G"), (2,  "T1"),  (3,  "T2G"), (4,  "T2"),  (5,  "T3"),
    (6,  "T4"),  (7,  "T5"),  (8,  "T6"),  (9,  "T9"),  (10, "T10"), (11, "T11"), (12, "T12"),
    (13, "Z1G"), (14, "Z1"),  (15, "Z2G"), (16, "Z2"),  (17, "Z3"),
    (18, "Z4"),  (19, "Z5"),  (20, "Z6"),  (21, "Z7"),
    (22, "Z9"),  (23, "Z10"), (24, "Z11"), (25, "Z12"),
]


def compute_tz_l_matrix(df: pd.DataFrame) -> list[dict]:
    """
    Parameters
    ----------
    df : DataFrame with sig_id (int) column and WLNBB L columns (bool).
         Typically the joined output of compute_signals() + compute_wlnbb().

    Returns
    -------
    List of row dicts, one per T/Z signal:
      {
        "sig_id": int,
        "sig_name": str,
        "is_bull": bool,
        "total": int,          # bars where this signal fired
        "cols": {              # one entry per L column
          "L1": {"count": int, "pct": int, "color": "gold"|"green"|"red"|""}
          ...
        }
      }
    """
    result = []

    for sig_id, sig_name in SIG_ORDER:
        is_t = sig_id <= 12
        subset = df[df["sig_id"] == sig_id] if "sig_id" in df.columns else df.iloc[0:0]
        total = len(subset)

        # Count occurrences for each L column
        counts: dict[str, int] = {}
        for col in L_COLS:
            if col in subset.columns and total > 0:
                counts[col] = int(subset[col].astype(bool).sum())
            else:
                counts[col] = 0

        max_count = max(counts.values()) if counts else 0

        cols_meta: dict[str, dict] = {}
        for col in L_COLS:
            cnt = counts[col]
            pct = round(cnt / total * 100) if total > 0 else 0
            is_gold = (cnt == max_count and cnt > 0)
            is_good = (col in GOOD_FOR_T) if is_t else (col in GOOD_FOR_Z)
            is_bad  = (col in GOOD_FOR_Z) if is_t else (col in GOOD_FOR_T)

            if is_gold:
                color = "gold"
            elif is_good and cnt > 0:
                color = "green"
            elif is_bad and cnt > 0:
                color = "red"
            else:
                color = ""

            cols_meta[col] = {"count": cnt, "pct": pct, "color": color}

        result.append({
            "sig_id":   sig_id,
            "sig_name": sig_name,
            "is_bull":  is_t,
            "total":    total,
            "cols":     cols_meta,
        })

    return result
