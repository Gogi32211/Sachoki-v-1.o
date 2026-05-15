"""
l_sequence_predictor.py — 3-bar and 2-bar L-combo sequence predictor.

Same structure as predictor.py but uses l_combo strings instead of sig_id.
NONE bars ARE valid in sequences — do not skip them.
"""
from __future__ import annotations
from collections import Counter

import pandas as pd

# L combos that are primarily bullish (raw L signals only: L1-L6)
_BULLISH_BASE = {"L1", "L2", "L3"}
_BEARISH_BASE = {"L5", "L6"}


def predict_l_next(df: pd.DataFrame, lookback: int = 5000) -> dict:
    """
    Given df with l_combo column, find last 3-bar and 2-bar L-combo patterns
    then count what l_combo appeared on the next bar historically.

    Returns
    -------
    {
      "l_3bar": {
        "pattern": "L3|L4 → L6 → L3|L4",
        "total_matches": 18,
        "top_outcomes": [
          {"l_combo": "L3|L4", "count": 7, "pct": 39, "is_bullish": True},
          ...
        ]
      },
      "l_2bar": { same structure }
    }
    """
    if "l_combo" not in df.columns:
        return _empty()

    combos = df["l_combo"].tail(lookback).tolist()
    n = len(combos)

    if n < 4:
        return _empty()

    last3 = combos[-3:]
    last2 = combos[-2:]

    pat3_str = " → ".join(last3)
    pat2_str = " → ".join(last2)

    outcomes3: list[str] = []
    outcomes2: list[str] = []

    for i in range(n - 3):
        if (combos[i]   == last3[0] and
                combos[i+1] == last3[1] and
                combos[i+2] == last3[2]):
            outcomes3.append(combos[i + 3])

    for i in range(n - 2):
        if combos[i] == last2[0] and combos[i+1] == last2[1]:
            outcomes2.append(combos[i + 2])

    return {
        "l_3bar": _summarize(pat3_str, outcomes3),
        "l_2bar": _summarize(pat2_str, outcomes2),
    }


def _is_bullish_l(combo: str) -> bool | None:
    """Classify an l_combo as bullish, bearish, or neutral."""
    if combo == "NONE":
        return None
    parts = set(combo.split("|"))
    has_bull = bool(parts & _BULLISH_BASE)
    has_bear = bool(parts & _BEARISH_BASE)
    if has_bull and not has_bear:
        return True
    if has_bear and not has_bull:
        return False
    return None  # mixed (e.g. L4 only, or L3|L6)


def _summarize(pattern_str: str, outcomes: list[str]) -> dict:
    total = len(outcomes)
    if total == 0:
        return {"pattern": pattern_str, "total_matches": 0, "top_outcomes": []}

    counts = Counter(outcomes)
    top = [
        {
            "l_combo":    combo,
            "count":      cnt,
            "pct":        round(cnt / total * 100),
            "is_bullish": _is_bullish_l(combo),
        }
        for combo, cnt in counts.most_common(10)
    ]
    return {"pattern": pattern_str, "total_matches": total, "top_outcomes": top}


def _empty() -> dict:
    e = {"pattern": "", "total_matches": 0, "top_outcomes": []}
    return {"l_3bar": e, "l_2bar": e}
