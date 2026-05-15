"""
ultra_pump_recommendations.py — Phase 5 recommendation engine.

Reads pattern_stats + pattern_lift + split_impact rows (already produced by
Phases 3/4) and emits a recommendations bundle:

  ultra_recommendations.json    — actionable per-pattern recommendations
  research_bundle.json          — full analytics bundle (overwrites the
                                  Phase-1 stub with real content)

Recommendation rules (per spec):
  - Promote: lift_all >= 3.0 AND precision >= 0.30 AND pump_count >= 5
  - Watch:   1.5 <= lift_all < 3.0 AND pump_count >= 3
  - Skip:    lift_all < 1.0 OR precision < 0.05
  - Investigate: very high lift but lift_clean_non_split is None or much
                 lower than lift_split_related (split-only edge)
"""
from __future__ import annotations
from typing import Any


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _classify(prow: dict, lift_row: dict | None) -> tuple[str, list[str]]:
    """Return (verdict, badges)."""
    lift = _f(prow.get("lift_vs_baseline"))
    precision = _f(prow.get("precision"))
    pump_count = int(prow.get("pump_count") or 0)
    badges: list[str] = []

    lift_clean = _f((lift_row or {}).get("lift_clean_non_split"))
    lift_split = _f((lift_row or {}).get("lift_split_related"))

    # Split edge analysis
    if lift_split is not None and (lift_clean is None or lift_clean < 1.0) and lift_split >= 2.0:
        badges.append("SPLIT_ONLY_EDGE")
    elif lift_clean is not None and lift_clean >= 2.0 and (lift_split is None or lift_split < lift_clean):
        badges.append("CLEAN_EDGE")
    elif lift_clean is not None and lift_split is not None and abs(lift_clean - lift_split) < 0.5:
        badges.append("UNIVERSAL_EDGE")

    if lift is None:
        return "INSUFFICIENT_DATA", badges
    if lift < 1.0 or (precision is not None and precision < 0.05):
        return "SKIP", badges
    if lift >= 3.0 and (precision or 0) >= 0.30 and pump_count >= 5:
        return "PROMOTE", badges
    if lift >= 1.5 and pump_count >= 3:
        return "WATCH", badges
    return "OBSERVE", badges


def build_recommendations(
    pattern_rows: list[dict],
    lift_rows: list[dict],
    *,
    summary: dict[str, Any],
) -> dict:
    lift_by_key = {r["pattern_key"]: r for r in lift_rows}
    recs: list[dict] = []
    counts = {"PROMOTE": 0, "WATCH": 0, "OBSERVE": 0, "SKIP": 0, "INSUFFICIENT_DATA": 0}
    for p in pattern_rows:
        verdict, badges = _classify(p, lift_by_key.get(p["pattern_key"]))
        counts[verdict] = counts.get(verdict, 0) + 1
        recs.append({
            "pattern_key":   p["pattern_key"],
            "pattern_type":  p["pattern_type"],
            "verdict":       verdict,
            "badges":        badges,
            "lift_vs_baseline": p.get("lift_vs_baseline"),
            "lift_clean_non_split":   (lift_by_key.get(p["pattern_key"]) or {}).get("lift_clean_non_split"),
            "lift_split_related":     (lift_by_key.get(p["pattern_key"]) or {}).get("lift_split_related"),
            "lift_post_reverse_split":(lift_by_key.get(p["pattern_key"]) or {}).get("lift_post_reverse_split"),
            "precision":     p.get("precision"),
            "recall":        p.get("recall"),
            "pump_count":    p.get("pump_count"),
            "baseline_count": p.get("baseline_count"),
            "median_future_gain": p.get("median_future_gain"),
            "median_days_to_peak": p.get("median_days_to_peak"),
        })

    # Sort: PROMOTE first, then WATCH, then by lift desc
    order = {"PROMOTE": 0, "WATCH": 1, "OBSERVE": 2, "SKIP": 3, "INSUFFICIENT_DATA": 4}
    recs.sort(key=lambda r: (order.get(r["verdict"], 99),
                             -(r.get("lift_vs_baseline") or 0)))

    return {
        "summary": summary,
        "verdict_counts": counts,
        "recommendations": recs,
    }
