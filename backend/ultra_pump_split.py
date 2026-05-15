"""
ultra_pump_split.py — Phase 4 split-impact classification + split-aware lift.

For each pump episode, classify split_status:
  CLEAN_NON_SPLIT              — no split event within window
  SPLIT_RELATED                — any split event within window (any direction)
  REVERSE_SPLIT_RELATED        — reverse split (ratio < 1) within window
  POST_REVERSE_SPLIT_PUMP      — pump anchor is AFTER a reverse split, within window
  FORWARD_SPLIT_RELATED        — forward split (ratio > 1) within window
  SPLIT_CONTAMINATED           — multiple splits in window
  UNKNOWN                      — split data unavailable for symbol

Split data source: backend/split_universe.py — `get_split_universe()` returns
list of dicts with at least {ticker, split_date, ratio}. We use this where
available; otherwise we tag UNKNOWN and proceed.
"""
from __future__ import annotations
import logging
from collections import defaultdict
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


def _parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _load_split_index() -> dict[str, list[dict]]:
    """ticker → list of {date, ratio} sorted ascending."""
    try:
        from split_universe import split_service  # module-level singleton
    except Exception as exc:
        log.debug("ultra_pump_split: split_service unavailable: %s", exc)
        return {}
    try:
        rows = split_service.get_split_universe(force_refresh=False)
    except Exception as exc:
        log.debug("ultra_pump_split: could not load split universe: %s", exc)
        return {}
    idx: dict[str, list[dict]] = defaultdict(list)
    for r in rows or []:
        sym = (r.get("ticker") or "").upper()
        d = _parse_date(r.get("split_date"))
        try:
            ratio = float(r.get("ratio") or 0)
        except Exception:
            ratio = 0.0
        if not sym or not d:
            continue
        idx[sym].append({"date": d, "ratio": ratio})
    for sym in idx:
        idx[sym].sort(key=lambda x: x["date"])
    return idx


def classify_episodes_split_status(
    episodes: list[dict],
    *,
    split_impact_window_days: int = 30,
) -> list[dict]:
    """Returns a *new* list of episode dicts with split_status + helper fields
    attached. Original list is not mutated."""
    idx = _load_split_index()
    win = int(split_impact_window_days)
    out: list[dict] = []
    for ep in episodes:
        ne = dict(ep)
        sym = (ep.get("symbol") or "").upper()
        anchor = _parse_date(ep.get("anchor_date"))
        if not sym or not anchor:
            ne["split_status"] = "UNKNOWN"
            ne["split_events_in_window"] = 0
            ne["nearest_split_date"] = None
            ne["nearest_split_ratio"] = None
            ne["split_direction"] = None
            ne["split_days_offset"] = None
            out.append(ne)
            continue
        splits = idx.get(sym)
        if not splits:
            ne["split_status"] = "CLEAN_NON_SPLIT"
            ne["split_events_in_window"] = 0
            ne["nearest_split_date"] = None
            ne["nearest_split_ratio"] = None
            ne["split_direction"] = None
            ne["split_days_offset"] = None
            out.append(ne)
            continue
        # Splits in window [-win, +win] around anchor
        in_win = [
            s for s in splits
            if abs((s["date"] - anchor).days) <= win
        ]
        if not in_win:
            ne["split_status"] = "CLEAN_NON_SPLIT"
            ne["split_events_in_window"] = 0
            ne["nearest_split_date"] = None
            ne["nearest_split_ratio"] = None
            ne["split_direction"] = None
            ne["split_days_offset"] = None
            out.append(ne)
            continue
        nearest = min(in_win, key=lambda s: abs((s["date"] - anchor).days))
        days_offset = (anchor - nearest["date"]).days  # +ve = pump after split
        ratio = nearest["ratio"]
        direction = "REVERSE" if ratio and ratio < 1.0 else ("FORWARD" if ratio and ratio > 1.0 else "UNKNOWN")

        if len(in_win) > 1:
            status = "SPLIT_CONTAMINATED"
        elif direction == "REVERSE":
            if days_offset > 0:
                status = "POST_REVERSE_SPLIT_PUMP"
            else:
                status = "REVERSE_SPLIT_RELATED"
        elif direction == "FORWARD":
            status = "FORWARD_SPLIT_RELATED"
        else:
            status = "SPLIT_RELATED"
        ne["split_status"] = status
        ne["split_events_in_window"] = len(in_win)
        ne["nearest_split_date"] = nearest["date"].isoformat()
        ne["nearest_split_ratio"] = ratio
        ne["split_direction"] = direction
        ne["split_days_offset"] = days_offset
        out.append(ne)
    return out


def build_split_partitions(episodes_with_status: list[dict]) -> dict[str, list[dict]]:
    """Group episodes by status into the three required artifact buckets."""
    buckets = {
        "split_related_pumps": [],
        "clean_non_split_pumps": [],
        "post_reverse_split_pumps": [],
    }
    for ep in episodes_with_status:
        st = ep.get("split_status")
        if st == "CLEAN_NON_SPLIT":
            buckets["clean_non_split_pumps"].append(ep)
        elif st == "POST_REVERSE_SPLIT_PUMP":
            buckets["post_reverse_split_pumps"].append(ep)
            buckets["split_related_pumps"].append(ep)
        elif st in ("SPLIT_RELATED", "REVERSE_SPLIT_RELATED",
                    "FORWARD_SPLIT_RELATED", "SPLIT_CONTAMINATED"):
            buckets["split_related_pumps"].append(ep)
    return buckets


def split_aware_pattern_stats(
    pattern_rows: list[dict],
    pattern_lift_rows: list[dict],
    episodes_with_status: list[dict],
    pre_pump_signals: list[dict],
    pre_pump_combos: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Recompute lift per pattern per split partition. Returns:
      split_impact_stats_rows
      updated_pattern_lift_rows (with lift_clean_non_split, lift_split_related,
                                 lift_post_reverse_split populated)
    """
    # episode_id -> split_status
    eid_status = {ep["episode_id"]: ep.get("split_status") for ep in episodes_with_status
                  if ep.get("episode_id")}
    # episode_id -> signals/combos sets
    sig_per_eid = defaultdict(set)
    for r in pre_pump_signals:
        eid = r.get("episode_id")
        s = r.get("signal_name")
        if eid and s:
            sig_per_eid[eid].add(s)
    combo_per_eid = defaultdict(set)
    for r in pre_pump_combos:
        eid = r.get("episode_id")
        c = r.get("combo_key")
        if eid and c:
            combo_per_eid[eid].add(c)

    partitions = ("CLEAN_NON_SPLIT", "SPLIT_RELATED_ALL", "POST_REVERSE_SPLIT_PUMP")

    def _partition_eids(part: str) -> set[str]:
        if part == "CLEAN_NON_SPLIT":
            return {eid for eid, st in eid_status.items() if st == "CLEAN_NON_SPLIT"}
        if part == "POST_REVERSE_SPLIT_PUMP":
            return {eid for eid, st in eid_status.items() if st == "POST_REVERSE_SPLIT_PUMP"}
        if part == "SPLIT_RELATED_ALL":
            return {eid for eid, st in eid_status.items()
                    if st in ("SPLIT_RELATED", "REVERSE_SPLIT_RELATED",
                              "POST_REVERSE_SPLIT_PUMP", "FORWARD_SPLIT_RELATED",
                              "SPLIT_CONTAMINATED")}
        return set()

    split_impact_rows: list[dict] = []
    lift_by_key = {row["pattern_key"]: dict(row) for row in pattern_lift_rows}

    # Always emit a partition-summary row per non-empty partition so the slot
    # is informative even when no per-pattern stats can be computed (e.g.,
    # pattern_rows is empty because pre-pump signal extraction yielded nothing).
    for part in partitions:
        part_eids = _partition_eids(part)
        if not part_eids:
            continue
        split_impact_rows.append({
            "pattern_key": "__ALL__",
            "split_status": part,
            "count": len(part_eids),
            "lift": None,
            "precision": None,
        })

    for prow in pattern_rows:
        key = prow["pattern_key"]
        ptype = prow["pattern_type"]
        full_lift = prow.get("lift_vs_baseline")
        for part in partitions:
            part_eids = _partition_eids(part)
            if not part_eids:
                continue
            # Count how many episodes in this partition have the pattern
            if ptype == "signal":
                with_p = sum(1 for eid in part_eids if key in sig_per_eid.get(eid, set()))
            elif ptype == "combo":
                with_p = sum(1 for eid in part_eids if key in combo_per_eid.get(eid, set()))
            else:
                # band/profile — assume same coverage as full (we don't recompute)
                with_p = 0
            count = len(part_eids)
            precision = (with_p / count) if count else 0.0
            # partition-specific lift = (precision / global_precision) * full_lift
            global_precision = prow.get("precision") or 0.0
            if global_precision > 0 and full_lift is not None:
                part_lift = (precision / global_precision) * full_lift
            else:
                part_lift = None
            split_impact_rows.append({
                "pattern_key": key,
                "split_status": part,
                "count": with_p,
                "lift": round(part_lift, 4) if part_lift is not None else None,
                "precision": round(precision, 4),
            })
            row = lift_by_key.setdefault(key, {
                "pattern_key": key,
                "lift_all": full_lift,
                "lift_clean_non_split": None,
                "lift_split_related": None,
                "lift_post_reverse_split": None,
            })
            if part == "CLEAN_NON_SPLIT":
                row["lift_clean_non_split"] = round(part_lift, 4) if part_lift is not None else None
            elif part == "SPLIT_RELATED_ALL":
                row["lift_split_related"] = round(part_lift, 4) if part_lift is not None else None
            elif part == "POST_REVERSE_SPLIT_PUMP":
                row["lift_post_reverse_split"] = round(part_lift, 4) if part_lift is not None else None

    return split_impact_rows, list(lift_by_key.values())
