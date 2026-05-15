"""
signal_combo_engine.py — multi-context combination statistics for the Signal Replay engine.

Produces rows with the same schema as replay_signal_statistics, using new stat_type values:
  COMBO_SIG_ABR_EMA50      — final_signal + abr_category + ema50_state
  COMBO_SIG_ABR_WLNBB      — final_signal + abr_category + had_wlnbb_l_last_5d
  COMBO_SEQ4_ABR_EMA50     — sequence_4bar + abr_category + ema50_state
  COMBO_SEQ4_ABR_WLNBB_POS — sequence_4bar + abr_category + wlnbb + price_pos_20bar_bucket
  COMBO_SIG_SCORE_ABR      — final_signal + score_bucket + abr_category
  COMBO_SIG_VOL_CANDLE     — final_signal + volume_bucket + candle_color

Only T/Z base signals are included (event_signal starts with T or Z, or final_signal does).
Min sample = 5.
"""
from __future__ import annotations
import json
import logging

from signal_statistics_engine import _HORIZONS, _aggregate_for_key, _verdict

log = logging.getLogger(__name__)

_MIN_SAMPLE = 5

# Each combo: (stat_type, key_builder_fn)
# key_builder receives the event dict and returns the stat_key string or None (skip)
def _combo_key_sig_abr_ema50(ev: dict) -> str | None:
    sig = ev.get("event_signal") or ""
    if not sig.startswith(("T", "Z")):
        return None
    abr = ev.get("abr_category") or ""
    ema = ev.get("ema50_state") or ""
    if not abr or not ema:
        return None
    return f"{sig}|ABR={abr}|EMA50={ema}"


def _combo_key_sig_abr_wlnbb(ev: dict) -> str | None:
    sig = ev.get("event_signal") or ""
    if not sig.startswith(("T", "Z")):
        return None
    abr = ev.get("abr_category") or ""
    wlnbb = ev.get("had_wlnbb_l_last_5d")
    if not abr or wlnbb is None:
        return None
    wval = "yes" if wlnbb and wlnbb not in (0, False, "0", "false") else "no"
    return f"{sig}|ABR={abr}|WLNBB={wval}"


def _combo_key_seq4_abr_ema50(ev: dict) -> str | None:
    seq = ev.get("sequence_4bar") or ""
    if not seq:
        return None
    abr = ev.get("abr_category") or ""
    ema = ev.get("ema50_state") or ""
    if not abr or not ema:
        return None
    return f"SEQ4:{seq}|ABR={abr}|EMA50={ema}"


def _combo_key_seq4_abr_wlnbb_pos(ev: dict) -> str | None:
    seq = ev.get("sequence_4bar") or ""
    if not seq:
        return None
    abr = ev.get("abr_category") or ""
    pos = ev.get("price_pos_20bar_bucket") or ""
    wlnbb = ev.get("had_wlnbb_l_last_5d")
    if not abr or not pos or wlnbb is None:
        return None
    wval = "yes" if wlnbb and wlnbb not in (0, False, "0", "false") else "no"
    return f"SEQ4:{seq}|ABR={abr}|WLNBB={wval}|POS={pos}"


def _combo_key_sig_score_abr(ev: dict) -> str | None:
    sig = ev.get("event_signal") or ""
    if not sig.startswith(("T", "Z")):
        return None
    score = ev.get("score_bucket") or ""
    abr = ev.get("abr_category") or ""
    if not score or not abr:
        return None
    return f"{sig}|SCORE={score}|ABR={abr}"


def _combo_key_sig_vol_candle(ev: dict) -> str | None:
    sig = ev.get("event_signal") or ""
    if not sig.startswith(("T", "Z")):
        return None
    vol = ev.get("volume_bucket") or ""
    candle = ev.get("candle_color") or ""
    if not vol or not candle or vol == "unknown" or candle == "unknown":
        return None
    return f"{sig}|VOL={vol}|CANDLE={candle}"


_COMBOS: list[tuple[str, object]] = [
    ("COMBO_SIG_ABR_EMA50",      _combo_key_sig_abr_ema50),
    ("COMBO_SIG_ABR_WLNBB",      _combo_key_sig_abr_wlnbb),
    ("COMBO_SEQ4_ABR_EMA50",     _combo_key_seq4_abr_ema50),
    ("COMBO_SEQ4_ABR_WLNBB_POS", _combo_key_seq4_abr_wlnbb_pos),
    ("COMBO_SIG_SCORE_ABR",      _combo_key_sig_score_abr),
    ("COMBO_SIG_VOL_CANDLE",     _combo_key_sig_vol_candle),
]


def build_combo_statistics(
    events_rows: list[dict],
    outcomes_rows: list[dict],
    *,
    replay_run_id: int,
) -> list[dict]:
    """Build multi-context combo stats rows (same schema as replay_signal_statistics).

    events_rows must contain: id, event_signal, sequence_4bar, abr_category,
    ema50_state, had_wlnbb_l_last_5d, price_pos_20bar_bucket, score_bucket,
    volume_bucket, candle_color, symbol, scan_date.
    """
    by_event: dict[int, dict] = {ev["id"]: ev for ev in events_rows}

    # grouped[(stat_type, stat_key, horizon)] = [outcome_dict, ...]
    grouped: dict[tuple[str, str, str], list[dict]] = {}

    for oc in outcomes_rows:
        ev = by_event.get(oc.get("signal_event_id"))
        if not ev:
            continue
        horizon = oc.get("horizon")
        if horizon not in _HORIZONS:
            continue

        for stat_type, key_fn in _COMBOS:
            key = key_fn(ev)
            if not key:
                continue
            grouped.setdefault((stat_type, key, horizon), []).append({**oc, "_ev": ev})

    out_rows: list[dict] = []
    for (stat_type, stat_key, horizon), rows in grouped.items():
        if len(rows) < _MIN_SAMPLE:
            continue

        agg = _aggregate_for_key(rows, horizon)
        verdict, recommendation = _verdict(agg)

        examples = []
        for r in rows[:5]:
            examples.append({
                "symbol":        r["_ev"].get("symbol"),
                "scan_date":     r["_ev"].get("scan_date"),
                "return_pct":    r.get("return_pct"),
                "max_gain_pct":  r.get("max_gain_pct"),
                "outcome_label": r.get("outcome_label"),
            })

        out_rows.append({
            "replay_run_id":       replay_run_id,
            "stat_key":            stat_key,
            "stat_type":           stat_type,
            "event_signal":        None,
            "event_signal_family": None,
            "event_signal_type":   None,
            "event_direction":     None,
            "horizon":             horizon,
            **agg,
            "verdict":        verdict,
            "recommendation": recommendation,
            "examples_json":  json.dumps(examples, default=str),
        })

    return out_rows
