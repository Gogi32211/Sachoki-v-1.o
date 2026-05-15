"""
signal_pattern_engine.py — aggregates T/Z sequence patterns from replay events × outcomes
into replay_pattern_statistics rows.

Phase 2 scope:
  - SEQUENCE_2 through SEQUENCE_7 pattern types
  - Same metrics as signal_statistics_engine
  - terminal_signal = last signal in the sequence
"""
from __future__ import annotations
import json
import logging
from typing import Any

from signal_statistics_engine import (
    _HORIZONS, _aggregate_for_key, _verdict, _confidence_score, _confidence_label,
)

log = logging.getLogger(__name__)

_SEQUENCE_COLS = [
    ("SEQUENCE_2",  "sequence_2bar"),
    ("SEQUENCE_3",  "sequence_3bar"),
    ("SEQUENCE_4",  "sequence_4bar"),
    ("SEQUENCE_5",  "sequence_5bar"),
    ("SEQUENCE_7",  "sequence_7bar"),
    ("SEQUENCE_10", "sequence_10bar"),
]

_MIN_SAMPLE = 3  # skip patterns with fewer than this many occurrences


def build_pattern_statistics(
    events_rows: list[dict],
    outcomes_rows: list[dict],
    *,
    replay_run_id: int,
) -> list[dict]:
    """Build replay_pattern_statistics rows from raw events + outcomes.

    events_rows: same format as build_signal_statistics input.
    outcomes_rows: same format as build_signal_statistics input.
    Returns list of replay_pattern_statistics dicts.
    """
    by_event: dict[int, dict] = {ev["id"]: ev for ev in events_rows}

    # grouped[(pattern_type, pattern_value, horizon)] = [outcome_dict, ...]
    grouped: dict[tuple[str, str, str], list[dict]] = {}

    for oc in outcomes_rows:
        ev_id = oc.get("signal_event_id")
        ev = by_event.get(ev_id)
        if not ev:
            continue
        horizon = oc.get("horizon")
        if horizon not in _HORIZONS:
            continue

        for pt, col in _SEQUENCE_COLS:
            val = ev.get(col)
            if not val:
                continue
            grouped.setdefault((pt, val, horizon), []).append({**oc, "_ev": ev})

    out_rows: list[dict] = []
    for (pattern_type, pattern_value, horizon), rows in grouped.items():
        if len(rows) < _MIN_SAMPLE:
            continue

        agg = _aggregate_for_key(rows, horizon)
        verdict, recommendation = _verdict(agg)

        # terminal = last signal in the arrow-separated sequence
        parts = [p.strip() for p in pattern_value.split("->") if p.strip()]
        terminal = parts[-1] if parts else None
        lead_in  = " -> ".join(parts[:-1]) if len(parts) > 1 else None

        examples = []
        for r in rows[:5]:
            examples.append({
                "symbol":      r["_ev"].get("symbol"),
                "scan_date":   r["_ev"].get("scan_date"),
                "return_pct":  r.get("return_pct"),
                "max_gain_pct": r.get("max_gain_pct"),
                "outcome_label": r.get("outcome_label"),
            })

        out_rows.append({
            "replay_run_id":   replay_run_id,
            "stat_key":        f"{pattern_type}:{pattern_value}:{horizon}",
            "pattern_type":    pattern_type,
            "pattern_value":   pattern_value,
            "terminal_signal": terminal,
            "lead_in":         lead_in,
            "horizon":         horizon,
            **agg,
            "verdict":         verdict,
            "recommendation":  recommendation,
            "examples_json":   json.dumps(examples, default=str),
        })

    return out_rows
