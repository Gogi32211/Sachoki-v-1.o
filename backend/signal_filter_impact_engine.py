"""
signal_filter_impact_engine.py — computes how context filters lift/drag each signal's
performance, producing replay_filter_impact_statistics rows.

For each T/Z base_signal × filter_name × filter_value × horizon:
  - Compute the same outcome metrics as signal_statistics_engine
  - Compute lift_median_return and lift_hit_10pct vs the unfiltered baseline

Filters checked:
  Categorical: ema50_state, volume_bucket, abr_category, candle_color,
               price_pos_20bar_bucket
  Boolean (True only): had_t_last_3d, had_z_last_3d, had_wlnbb_l_last_5d,
                        had_ema50_reclaim_last_5d, had_volume_burst_last_5d
"""
from __future__ import annotations
import logging

from signal_statistics_engine import (
    _HORIZONS, _aggregate_for_key, _verdict,
)

log = logging.getLogger(__name__)

_CATEGORICAL_FILTERS = [
    "ema50_state",
    "volume_bucket",
    "abr_category",
    "candle_color",
    "price_pos_20bar_bucket",
    "score_bucket",
]

_BOOLEAN_FILTERS = [
    "had_t_last_3d",
    "had_z_last_3d",
    "had_wlnbb_l_last_5d",
    "had_ema50_reclaim_last_5d",
    "had_volume_burst_last_5d",
]

_MIN_SAMPLE = 5


def build_filter_impact_statistics(
    events_rows: list[dict],
    outcomes_rows: list[dict],
    *,
    replay_run_id: int,
) -> list[dict]:
    """Build replay_filter_impact_statistics rows.

    Only processes T/Z family signals (event_signal starting with T or Z).
    Computes baseline (unfiltered) per (signal, horizon) then lifted stats per
    (signal, filter_name, filter_value, horizon).
    """
    by_event: dict[int, dict] = {ev["id"]: ev for ev in events_rows}

    # ── Pass 1: build baseline per (signal, horizon) ─────────────────────────
    base_groups: dict[tuple[str, str], list[dict]] = {}
    for oc in outcomes_rows:
        ev = by_event.get(oc.get("signal_event_id"))
        if not ev:
            continue
        sig = ev.get("event_signal") or ""
        if not sig.startswith(("T", "Z")):
            continue
        horizon = oc.get("horizon")
        if horizon not in _HORIZONS:
            continue
        base_groups.setdefault((sig, horizon), []).append({**oc, "_ev": ev})

    baseline: dict[tuple[str, str], dict] = {}
    for (sig, horizon), rows in base_groups.items():
        if len(rows) >= _MIN_SAMPLE:
            baseline[(sig, horizon)] = _aggregate_for_key(rows, horizon)

    # ── Pass 2: filter groups ─────────────────────────────────────────────────
    filter_groups: dict[tuple[str, str, str, str], list[dict]] = {}

    for oc in outcomes_rows:
        ev = by_event.get(oc.get("signal_event_id"))
        if not ev:
            continue
        sig = ev.get("event_signal") or ""
        if not sig.startswith(("T", "Z")):
            continue
        horizon = oc.get("horizon")
        if horizon not in _HORIZONS:
            continue
        row_data = {**oc, "_ev": ev}

        for fname in _CATEGORICAL_FILTERS:
            fval = ev.get(fname)
            if fval is None or str(fval).strip() == "":
                continue
            filter_groups.setdefault((sig, fname, str(fval), horizon), []).append(row_data)

        for fname in _BOOLEAN_FILTERS:
            fval = ev.get(fname)
            # SQLite stores bools as 0/1; PG as Python bool
            if fval and fval not in (0, False, "0", "false", "False"):
                filter_groups.setdefault((sig, fname, "true", horizon), []).append(row_data)

    # ── Assemble output rows ──────────────────────────────────────────────────
    out_rows: list[dict] = []
    for (sig, filter_name, filter_value, horizon), rows in filter_groups.items():
        if len(rows) < _MIN_SAMPLE:
            continue

        agg = _aggregate_for_key(rows, horizon)
        verdict, _ = _verdict(agg)

        base = baseline.get((sig, horizon), {})
        lift_med = None
        lift_hit = None
        if base.get("median_return") is not None and agg.get("median_return") is not None:
            lift_med = round(agg["median_return"] - base["median_return"], 4)
        if base.get("hit_10pct_rate") is not None and agg.get("hit_10pct_rate") is not None:
            lift_hit = round(agg["hit_10pct_rate"] - base["hit_10pct_rate"], 4)

        out_rows.append({
            "replay_run_id":      replay_run_id,
            "stat_key":           f"{sig}|{filter_name}={filter_value}|{horizon}",
            "base_signal":        sig,
            "filter_name":        filter_name,
            "filter_value":       filter_value,
            "horizon":            horizon,
            "sample_size":        agg["sample_size"],
            "median_return":      agg.get("median_return"),
            "avg_return":         agg.get("avg_return"),
            "win_rate":           agg.get("win_rate"),
            "hit_10pct_rate":     agg.get("hit_10pct_rate"),
            "hit_20pct_rate":     agg.get("hit_20pct_rate"),
            "fail_10pct_rate":    agg.get("fail_10pct_rate"),
            "avg_max_gain":       agg.get("avg_max_gain"),
            "avg_max_drawdown":   agg.get("avg_max_drawdown"),
            "confidence_score":   agg.get("confidence_score"),
            "confidence_label":   agg.get("confidence_label"),
            "lift_median_return": lift_med,
            "lift_hit_10pct":     lift_hit,
            "verdict":            verdict,
        })

    return out_rows
