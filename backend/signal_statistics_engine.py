"""
signal_statistics_engine.py — aggregates replay_signal_events × replay_signal_outcomes
into replay_signal_statistics rows.

Phase 1 scope (per spec):
  - stat_type = SIGNAL          (key: event_signal)
  - stat_type = SIGNAL_FAMILY   (key: event_signal_family)
  - stat_type = SIGNAL_TYPE     (key: event_signal_type)
  - stat_type = SIGNAL_DIRECTION (key: event_direction)
  - stat_type = ROLE            (key: role)
  - stat_type = SCORE_BUCKET    (key: score_bucket)

Each (key, horizon) combination produces one stats row.

Phase 2 will extend to pattern statistics (sequences + signal+context combos)
and filter impact.
"""
from __future__ import annotations
import json
import logging
import math
from typing import Iterable

log = logging.getLogger(__name__)

_HORIZONS = ("3d", "5d", "10d", "20d")


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f = math.floor(k); c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def _median(values: list[float]) -> float | None:
    return _percentile(values, 0.5)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator * 100 if denominator else None


def _confidence_label(n: int) -> str:
    if n < 30:  return "TOO_FEW_SAMPLES"
    if n < 75:  return "LOW"
    if n < 200: return "MEDIUM"
    return "HIGH"


def _confidence_score(n: int) -> float:
    """0..1 monotonic; 30→0.2, 75→0.4, 200→0.7, 500→0.9, 1000+→0.98."""
    if n <= 0: return 0.0
    return max(0.0, min(0.99, 1 - math.exp(-n / 400)))


def _robustness_warnings(stats: dict) -> list[str]:
    """Returns a list of data quality / robustness warning codes."""
    warnings: list[str] = []
    n    = stats.get("sample_size") or 0
    avg  = stats.get("avg_return")
    med  = stats.get("median_return")
    fail = stats.get("fail_10pct_rate")
    avg_dd = stats.get("avg_max_drawdown")

    if n < 30:
        warnings.append("TOO_FEW_SAMPLES")

    if avg is not None and med is not None:
        diff = abs(avg - med)
        base = max(abs(med), 0.5)
        if diff > 4 and diff / base > 1.5:
            # avg diverges >1.5× from median — heavy-tail / outlier influence
            if avg > med * 2.5 and avg > 5:
                warnings.append("OUTLIER_DRIVEN")
            else:
                warnings.append("AVG_MEDIAN_DIVERGENCE")

    if avg_dd is not None and avg_dd < -15 and med is not None and med > 2:
        warnings.append("HIGH_DRAWDOWN_DESPITE_GOOD_RETURN")

    if fail is not None and fail > 30:
        warnings.append("HIGH_FAIL_RATE")

    return warnings


def _verdict(stats: dict) -> tuple[str, str]:
    """Returns (verdict, recommendation). Recommendation may be prefixed with WARN tags."""
    n   = stats.get("sample_size") or 0
    med = stats.get("median_return")
    hit = stats.get("hit_10pct_rate")
    fail = stats.get("fail_10pct_rate")

    warns = _robustness_warnings(stats)
    warn_prefix = f"[WARN: {', '.join(warns)}] " if warns else ""

    if n < 30:
        return ("TOO_FEW_SAMPLES",
                f"{warn_prefix}Sample size {n} is too small to draw conclusions.")
    if med is None:
        return ("NO_EDGE", f"{warn_prefix}Insufficient outcome data.")
    if med < 0 and fail is not None and hit is not None and fail > hit:
        return ("NEGATIVE_EDGE",
                f"{warn_prefix}Median 10D return {med:+.2f}%, fail -10% rate ({fail:.1f}%) exceeds "
                f"hit +10% rate ({hit:.1f}%). Consider downgrading or filtering.")
    if med > 2 and hit is not None and fail is not None and hit >= 20 and fail <= 20 and n >= 50:
        return ("STRONG_EDGE",
                f"{warn_prefix}Median 10D return {med:+.2f}%, hit +10% rate {hit:.1f}%, "
                f"fail -10% rate {fail:.1f}% on {n} samples. Promote in scoring.")
    if med > 1 and hit is not None and fail is not None and hit > fail and n >= 50:
        return ("GOOD_WITH_CONTEXT",
                f"{warn_prefix}Median 10D return {med:+.2f}% with hit/fail edge ({hit:.1f}% vs "
                f"{fail:.1f}%). Useful with context filters.")
    if med > 0:
        return ("WATCH_ONLY",
                f"{warn_prefix}Median 10D return {med:+.2f}% is positive but hit/fail profile "
                f"is not strong. Monitor before promoting.")
    return ("NO_EDGE",
            f"{warn_prefix}Median 10D return {med:+.2f}%. No actionable edge detected.")


def _aggregate_for_key(rows: list[dict], horizon: str) -> dict:
    """rows = list of {return_pct, max_gain_pct, max_drawdown_pct, hit_*pct, fail_*pct,
                       alpha_vs_spy, alpha_vs_qqq, outcome_label} dicts for one (key, horizon).
    """
    returns = [r["return_pct"] for r in rows if r.get("return_pct") is not None]
    gains   = [r["max_gain_pct"] for r in rows if r.get("max_gain_pct") is not None]
    dds     = [r["max_drawdown_pct"] for r in rows if r.get("max_drawdown_pct") is not None]
    a_spy   = [r["alpha_vs_spy"] for r in rows if r.get("alpha_vs_spy") is not None]
    a_qqq   = [r["alpha_vs_qqq"] for r in rows if r.get("alpha_vs_qqq") is not None]

    n = len(rows)
    wins = sum(1 for v in returns if v > 0)
    losses = sum(1 for v in returns if v < 0)
    hit5  = sum(1 for r in rows if r.get("hit_5pct"))
    hit10 = sum(1 for r in rows if r.get("hit_10pct"))
    hit20 = sum(1 for r in rows if r.get("hit_20pct"))
    hit50 = sum(1 for r in rows if r.get("hit_50pct"))
    fail5  = sum(1 for r in rows if r.get("fail_5pct"))
    fail10 = sum(1 for r in rows if r.get("fail_10pct"))

    avg_g = _mean(gains); med_g = _median(gains)
    avg_d = _mean(dds);   med_d = _median(dds)

    expectancy = _mean(returns)
    rr = None
    if avg_g is not None and avg_d is not None and avg_d != 0:
        rr = abs(avg_g / avg_d)

    return {
        "sample_size":      n,
        "avg_return":       _mean(returns),
        "median_return":    _median(returns),
        "p25_return":       _percentile(returns, 0.25),
        "p75_return":       _percentile(returns, 0.75),
        "win_rate":         _rate(wins, len(returns)),
        "loss_rate":        _rate(losses, len(returns)),
        "hit_5pct_rate":    _rate(hit5, n),
        "hit_10pct_rate":   _rate(hit10, n),
        "hit_20pct_rate":   _rate(hit20, n),
        "hit_50pct_rate":   _rate(hit50, n),
        "fail_5pct_rate":   _rate(fail5, n),
        "fail_10pct_rate":  _rate(fail10, n),
        "avg_max_gain":     avg_g,
        "median_max_gain":  med_g,
        "avg_max_drawdown": avg_d,
        "median_max_drawdown": med_d,
        "expectancy":       expectancy,
        "risk_reward_ratio": rr,
        "alpha_vs_spy_avg": _mean(a_spy),
        "alpha_vs_qqq_avg": _mean(a_qqq),
        "confidence_score": _confidence_score(n),
        "confidence_label": _confidence_label(n),
    }


def build_signal_statistics(
    events_rows: list[dict],
    outcomes_rows: list[dict],
    *,
    replay_run_id: int,
) -> list[dict]:
    """Build replay_signal_statistics rows from raw events + outcomes.

    events_rows:   list of {id, event_signal, event_signal_family,
                            event_signal_type, event_direction,
                            role, matched_status, score_bucket}.
    outcomes_rows: list of {signal_event_id, horizon, return_pct, max_gain_pct,
                            max_drawdown_pct, hit_5pct, hit_10pct, hit_20pct,
                            hit_50pct, fail_5pct, fail_10pct,
                            alpha_vs_spy, alpha_vs_qqq, outcome_label}.

    Returns a list of replay_signal_statistics dicts.
    """
    by_event: dict[int, dict] = {ev["id"]: ev for ev in events_rows}

    # group outcomes by (key, horizon)
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    # key = (stat_type, stat_key, horizon)
    def _push(stat_type: str, stat_key: str, horizon: str, outcome: dict, ev: dict) -> None:
        grouped.setdefault((stat_type, stat_key, horizon), []).append({**outcome, "_ev": ev})

    for oc in outcomes_rows:
        ev_id = oc.get("signal_event_id")
        ev = by_event.get(ev_id)
        if not ev:
            continue
        horizon = oc.get("horizon")
        if horizon not in _HORIZONS:
            continue

        sig    = ev.get("event_signal")
        family = ev.get("event_signal_family")
        sigt   = ev.get("event_signal_type")
        direc  = ev.get("event_direction")
        role   = ev.get("role") or ev.get("matched_status")
        bucket = ev.get("score_bucket")

        if sig:    _push("SIGNAL",           str(sig),    horizon, oc, ev)
        if family: _push("SIGNAL_FAMILY",    str(family), horizon, oc, ev)
        if sigt:   _push("SIGNAL_TYPE",      str(sigt),   horizon, oc, ev)
        if direc:  _push("SIGNAL_DIRECTION", str(direc),  horizon, oc, ev)
        if role:   _push("ROLE",             str(role),   horizon, oc, ev)
        if bucket: _push("SCORE_BUCKET",     str(bucket), horizon, oc, ev)

    out_rows: list[dict] = []
    for (stat_type, stat_key, horizon), rows in grouped.items():
        agg = _aggregate_for_key(rows, horizon)
        # Pull representative metadata from first row's _ev
        ev0 = rows[0]["_ev"]
        verdict, recommendation = _verdict(agg)

        examples = []
        for r in rows[:5]:
            examples.append({
                "symbol":     r["_ev"].get("symbol"),
                "scan_date":  r["_ev"].get("scan_date"),
                "return_pct": r.get("return_pct"),
                "max_gain_pct": r.get("max_gain_pct"),
                "outcome_label": r.get("outcome_label"),
            })

        row = {
            "replay_run_id":       replay_run_id,
            "stat_key":            stat_key,
            "stat_type":           stat_type,
            "event_signal":        ev0.get("event_signal")        if stat_type == "SIGNAL"           else None,
            "event_signal_family": ev0.get("event_signal_family") if stat_type == "SIGNAL_FAMILY"    else None,
            "event_signal_type":   ev0.get("event_signal_type")   if stat_type == "SIGNAL_TYPE"      else None,
            "event_direction":     ev0.get("event_direction")     if stat_type == "SIGNAL_DIRECTION" else None,
            "horizon":             horizon,
            **agg,
            "verdict":        verdict,
            "recommendation": recommendation,
            "examples_json":  json.dumps(examples, default=str),
        }
        out_rows.append(row)
    return out_rows
