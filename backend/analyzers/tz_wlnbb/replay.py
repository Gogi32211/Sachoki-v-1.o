"""Replay analytics for TZ/WLNBB signals."""
import csv
import io
import json
import random
import re
import statistics
import zipfile
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from .config import TZ_WLNBB_VERSION, DEFAULT_LOOKBACK_TRADING_DAYS

log = logging.getLogger(__name__)

# ── Composite label parsing constants ────────────────────────────────────────
# Longer signal names must come first to avoid partial matches (T2G before T2).
_T_SIGNALS_LONGEST_FIRST = [
    "T11", "T12", "T10", "T2G", "T1G", "T9", "T6", "T5", "T4", "T3", "T2", "T1"
]
_Z_SIGNALS_LONGEST_FIRST = [
    "Z12", "Z11", "Z10", "Z2G", "Z1G", "Z9", "Z8", "Z7", "Z6",
    "Z5", "Z4", "Z3", "Z2", "Z1"
]
_L_COMPONENT_RE = re.compile(r"^(L[1-6]+)")
_VALID_SUFFIX_RE = re.compile(r"^[NE][UDB]?[PRH]?$")


def parse_composite_label(label: str) -> dict:
    """Parse a composite label into t_signal, z_signal, l_signal, composite_core, full_suffix.

    Examples:
      T2GL46ED  -> t_signal=T2G, l_signal=L46, composite_core=T2GL46, full_suffix=ED
      Z2GL12NU  -> z_signal=Z2G, l_signal=L12, composite_core=Z2GL12, full_suffix=NU
      T11L5EDP  -> t_signal=T11, l_signal=L5,  composite_core=T11L5,  full_suffix=EDP
      T4EBP     -> t_signal=T4,  l_signal="",  composite_core=T4,     full_suffix=EBP
      L34NDP    -> l_signal=L34, composite_core=L34, full_suffix=NDP
    """
    t_sig = z_sig = l_sig = ""
    rest = label or ""

    for sig in _T_SIGNALS_LONGEST_FIRST:
        if rest.startswith(sig):
            t_sig = sig
            rest = rest[len(sig):]
            break

    if not t_sig:
        for sig in _Z_SIGNALS_LONGEST_FIRST:
            if rest.startswith(sig):
                z_sig = sig
                rest = rest[len(sig):]
                break

    m = _L_COMPONENT_RE.match(rest)
    if m:
        l_sig = m.group(1)
        rest = rest[len(l_sig):]

    if t_sig:
        core = t_sig + l_sig
    elif z_sig:
        core = z_sig + l_sig
    else:
        core = l_sig

    return {
        "t_signal": t_sig,
        "z_signal": z_sig,
        "l_signal": l_sig,
        "composite_core": core,
        "full_suffix": rest,
    }


def is_valid_full_suffix(s: str) -> bool:
    """Return True for empty string or a valid suffix matching ^[NE][UDB]?[PRH]?$."""
    if not s:
        return True
    return bool(_VALID_SUFFIX_RE.match(s))


def _extract_suffix_from_label(label: str) -> str:
    """Parse label and return only the suffix portion (empty string if none)."""
    if not label:
        return ""
    return parse_composite_label(label)["full_suffix"]


def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return None


# ── Price bucket / robust metrics constants ──────────────────────────────────
OUTLIER_RET_10D_THRESHOLD = 50.0  # ret_10d > 50% → outlier
ROBUST_MIN_COUNT = 30
ROBUST_TOP_FAIL_RATE_MAX    = 30.0
ROBUST_TOP_OUTLIER_RATE_MAX = 0.10
ROBUST_BAD_FAIL_RATE_MIN    = 30.0


def _classify_price_bucket(close) -> str:
    """Map a close price to a price-bucket label."""
    try:
        c = float(close)
    except (TypeError, ValueError):
        return ""
    if c != c:
        return ""
    if c < 1:    return "LT1"
    if c < 5:    return "1_5"
    if c < 20:   return "5_20"
    if c < 50:   return "20_50"
    if c < 150:  return "50_150"
    if c < 300:  return "150_300"
    return "300_PLUS"


def _row_price_bucket(r: dict) -> str:
    """Get price_bucket from row, falling back to computing from close."""
    pb = r.get("price_bucket") or ""
    if pb:
        return pb
    return _classify_price_bucket(r.get("close"))


def _percentile(sorted_vals: list, q: float) -> Optional[float]:
    """Linear-interpolated percentile for 0<=q<=1 on a sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _trimmed_mean(vals: list, trim_frac: float = 0.05) -> Optional[float]:
    """Mean after trimming bottom and top trim_frac of values. Falls back to mean for tiny groups."""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    k = int(n * trim_frac)
    if k == 0 and n >= 3:
        k = 1
    if n - 2 * k <= 0:
        return sum(s) / n
    return sum(s[k:n - k]) / (n - 2 * k)


def _winsorized_mean(vals: list, q_lo: float = 0.05, q_hi: float = 0.95) -> Optional[float]:
    """Mean after clipping values to the q_lo / q_hi percentiles."""
    if not vals:
        return None
    s = sorted(vals)
    lo = _percentile(s, q_lo)
    hi = _percentile(s, q_hi)
    if lo is None or hi is None:
        return sum(s) / len(s)
    clipped = [min(max(v, lo), hi) for v in vals]
    return sum(clipped) / len(clipped)


def _robust_metrics(grp: list) -> dict:
    """Compute the full robust metric dict for a group of rows.
    Returns numeric fields rounded for CSV use.
    """
    def _vals(k):
        return [v for r in grp for v in [_safe_float(r.get(k))] if v is not None]

    out = {}
    for tf_key in ("1d", "3d", "5d", "10d"):
        col = f"ret_{tf_key}"
        v = _vals(col)
        if v:
            sv = sorted(v)
            n = len(sv)
            avg = sum(v) / n
            med = _percentile(sv, 0.5)
            out[f"avg_ret_{tf_key}"]    = round(avg, 4)
            out[f"median_ret_{tf_key}"] = round(med, 4)
        else:
            out[f"avg_ret_{tf_key}"]    = None
            out[f"median_ret_{tf_key}"] = None

    v10 = _vals("ret_10d")
    if v10:
        sv10 = sorted(v10)
        n = len(v10)
        avg10  = sum(v10) / n
        med10  = _percentile(sv10, 0.5)
        trim10 = _trimmed_mean(v10, 0.05)
        wins10 = _winsorized_mean(v10, 0.05, 0.95)
        p25    = _percentile(sv10, 0.25)
        p75    = _percentile(sv10, 0.75)
        p90    = _percentile(sv10, 0.90)
        max10  = max(v10)
        min10  = min(v10)
        outlier_count = sum(1 for x in v10 if x > OUTLIER_RET_10D_THRESHOLD)
        outlier_rate  = outlier_count / n if n else 0.0
        gap = avg10 - med10 if (avg10 is not None and med10 is not None) else None

        out["trimmed_avg_ret_10d"]    = round(trim10, 4) if trim10 is not None else None
        out["winsorized_avg_ret_10d"] = round(wins10, 4) if wins10 is not None else None
        out["p25_ret_10d"]            = round(p25, 4) if p25 is not None else None
        out["p75_ret_10d"]            = round(p75, 4) if p75 is not None else None
        out["p90_ret_10d"]            = round(p90, 4) if p90 is not None else None
        out["max_ret_10d"]            = round(max10, 4)
        out["min_ret_10d"]            = round(min10, 4)
        out["outlier_count_10d"]      = outlier_count
        out["outlier_rate_10d"]       = round(outlier_rate, 4)
        out["avg_vs_median_gap_10d"]  = round(gap, 4) if gap is not None else None
    else:
        for k in ("trimmed_avg_ret_10d", "winsorized_avg_ret_10d",
                  "p25_ret_10d", "p75_ret_10d", "p90_ret_10d",
                  "max_ret_10d", "min_ret_10d", "avg_vs_median_gap_10d"):
            out[k] = None
        out["outlier_count_10d"] = 0
        out["outlier_rate_10d"]  = 0.0

    def _rate_pct(k):
        vs = _vals(k)
        return round(sum(vs) / len(vs) * 100, 2) if vs else None

    big_win = _rate_pct("big_win_10d")
    fail    = _rate_pct("fail_10d")
    out["big_win_10d_rate"] = big_win
    out["fail_10d_rate"]    = fail

    out["avg_mfe_10d"] = round(sum(_vals("mfe_10d")) / len(_vals("mfe_10d")), 4) if _vals("mfe_10d") else None
    out["avg_mae_10d"] = round(sum(_vals("mae_10d")) / len(_vals("mae_10d")), 4) if _vals("mae_10d") else None
    out["reward_risk_ratio"] = _reward_risk(out["avg_mfe_10d"], out["avg_mae_10d"])

    # Robust composite score
    med10 = out["median_ret_10d"] or 0.0
    trim  = out["trimmed_avg_ret_10d"] or 0.0
    bw    = big_win or 0.0
    fl    = fail or 0.0
    orate = out["outlier_rate_10d"] or 0.0
    out["robust_score"] = round(
        med10 + 0.5 * trim + 0.25 * bw - 0.35 * fl - 0.5 * (orate * 100),
        4,
    )

    # median ret 5d already computed above
    return out


# Standard set of perf columns present in every perf CSV (in order).
PERF_METRIC_COLS = [
    "count",
    "avg_ret_1d", "avg_ret_3d", "avg_ret_5d", "avg_ret_10d",
    "median_ret_1d", "median_ret_3d", "median_ret_5d", "median_ret_10d",
    "trimmed_avg_ret_10d", "winsorized_avg_ret_10d",
    "p25_ret_10d", "p75_ret_10d", "p90_ret_10d",
    "max_ret_10d", "min_ret_10d",
    "outlier_count_10d", "outlier_rate_10d", "avg_vs_median_gap_10d",
    "big_win_10d_rate", "fail_10d_rate",
    "avg_mfe_10d", "avg_mae_10d", "reward_risk_ratio",
    "robust_score",
]


def _reward_risk(avg_mfe, avg_mae):
    """MFE / abs(MAE). None if either is None or MAE=0."""
    if avg_mfe is None or avg_mae is None:
        return None
    if avg_mae == 0:
        return None
    return round(avg_mfe / abs(avg_mae), 3)


def _primary_signal(r: dict) -> str:
    """Get the primary signal name for a row (T > Z > L > PREUP > PREDN)."""
    return (
        r.get("t_signal") or r.get("z_signal") or
        r.get("l_signal") or r.get("preup_signal") or r.get("predn_signal") or ""
    )


def _bar_events(r: dict) -> list:
    """Return list of (family, signal_name) tuples for every signal active on a bar.
    Each bar may emit multiple events (e.g. T4 + L34 + PREUP coexist), enabling
    base sequence detection across all families (Z_to_L, L_to_T, PREUP_after_Z, etc).
    """
    events = []
    if r.get("t_signal"):     events.append(("T",     r["t_signal"]))
    if r.get("z_signal"):     events.append(("Z",     r["z_signal"]))
    if r.get("l_signal"):     events.append(("L",     r["l_signal"]))
    if r.get("preup_signal"): events.append(("PREUP", r["preup_signal"]))
    if r.get("predn_signal"): events.append(("PREDN", r["predn_signal"]))
    return events


def _full_label(r: dict) -> str:
    """Get suffix-aware full label for the primary signal in a row."""
    if r.get("t_signal"):
        return r.get("lane1_label") or r.get("t_signal", "")
    if r.get("z_signal"):
        return r.get("lane3_label") or r.get("z_signal", "")
    if r.get("l_signal"):
        return r.get("lane1_label") or r.get("l_signal", "")
    suf = r.get("ne_suffix", "") + r.get("wick_suffix", "") + r.get("penetration_suffix", "")
    if r.get("preup_signal"):
        return r.get("preup_signal", "") + suf
    if r.get("predn_signal"):
        return r.get("predn_signal", "") + suf
    return ""


def _signal_perf(rows: List[dict]) -> List[dict]:
    """Aggregate signal performance by signal_type + signal_name. Includes median."""
    groups: Dict[tuple, list] = {}
    signal_types = [
        ("T", "t_signal"), ("Z", "z_signal"), ("L", "l_signal"),
        ("PREUP", "preup_signal"), ("PREDN", "predn_signal"),
    ]
    for r in rows:
        uni = r.get("universe", "")
        tf  = r.get("timeframe", "")
        for sig_type, col in signal_types:
            name = r.get(col, "")
            if not name:
                continue
            key = (sig_type, name, uni, tf)
            groups.setdefault(key, []).append(r)

    result = []
    for (sig_type, name, uni, tf), grp in groups.items():
        row = {
            "signal_type": sig_type, "signal_name": name, "universe": uni, "timeframe": tf,
            "count": len(grp),
            **_robust_metrics(grp),
            "tz_wlnbb_version": TZ_WLNBB_VERSION,
        }
        result.append(row)
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _combo_perf(rows: List[dict]) -> List[dict]:
    """Aggregate by combination of t/z/l/preup/predn/ne/wick. Includes median."""
    groups: Dict[tuple, list] = {}
    for r in rows:
        pen = r.get("penetration_suffix", "")
        key = (
            r.get("t_signal", ""), r.get("z_signal", ""), r.get("l_signal", ""),
            r.get("preup_signal", ""), r.get("predn_signal", ""),
            r.get("ne_suffix", ""), r.get("wick_suffix", ""), pen,
        )
        groups.setdefault(key, []).append(r)

    result = []
    for (ts, zs, ls, ps, ds, ne, wk, pen), grp in groups.items():
        if not any([ts, zs, ls, ps, ds]):
            continue
        result.append({
            "t_signal": ts, "z_signal": zs, "l_signal": ls,
            "preup_signal": ps, "predn_signal": ds, "ne_suffix": ne, "wick_suffix": wk,
            "penetration_suffix": pen,
            "full_suffix": ne + wk + pen,
            "count": len(grp),
            **_robust_metrics(grp),
            "tz_wlnbb_version": TZ_WLNBB_VERSION,
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _suffix_perf(rows: List[dict]) -> List[dict]:
    """
    Group by signal_family + signal_name + ne_suffix + wick_suffix + penetration_suffix.
    Answers: does penetration suffix improve/weaken signal quality?
    """
    groups: Dict[tuple, list] = {}
    signal_cols = [
        ("T", "t_signal"), ("Z", "z_signal"), ("L", "l_signal"),
        ("PREUP", "preup_signal"), ("PREDN", "predn_signal"),
    ]
    for r in rows:
        ne  = r.get("ne_suffix", "")
        wk  = r.get("wick_suffix", "")
        pen = r.get("penetration_suffix", "")
        uni = r.get("universe", "")
        tf  = r.get("timeframe", "")
        for sig_type, col in signal_cols:
            name = r.get(col, "")
            if not name:
                continue
            key = (sig_type, name, ne, wk, pen, ne + wk + pen, uni, tf)
            groups.setdefault(key, []).append(r)

    result = []
    for (sig_type, name, ne, wk, pen, full_suf, uni, tf), grp in groups.items():
        result.append({
            "signal_type": sig_type, "signal_name": name,
            "ne_suffix": ne, "wick_suffix": wk, "penetration_suffix": pen,
            "full_suffix": full_suf,
            "universe": uni, "timeframe": tf, "count": len(grp),
            **_robust_metrics(grp),
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _composite_perf(rows: List[dict]) -> List[dict]:
    """
    Group by composite_full_label + composite_core + all suffix components.
    Critical file: answers whether T3L25NE works better than T3 alone, etc.
    """
    groups: Dict[tuple, list] = {}
    for r in rows:
        cfl = r.get("composite_full_label", "") or r.get("composite_primary_label", "")
        if not cfl:
            continue
        key = (
            cfl,
            r.get("composite_core", ""),
            r.get("composite_full_suffix", "") or r.get("composite_suffix", ""),
            r.get("t_signal", ""), r.get("z_signal", ""), r.get("l_signal", ""),
            r.get("ne_suffix", ""), r.get("wick_suffix", ""), r.get("penetration_suffix", ""),
            r.get("universe", ""), r.get("timeframe", ""),
        )
        groups.setdefault(key, []).append(r)

    result = []
    for (cfl, core, fsuf, ts, zs, ls, ne, wk, pen, uni, tf), grp in groups.items():
        result.append({
            "composite_full_label": cfl, "composite_core": core, "composite_full_suffix": fsuf,
            "t_signal": ts, "z_signal": zs, "l_signal": ls,
            "ne_suffix": ne, "wick_suffix": wk, "penetration_suffix": pen,
            "full_suffix": ne + wk + pen,
            "universe": uni, "timeframe": tf, "count": len(grp),
            **_robust_metrics(grp),
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _wick_behavior_perf(rows: List[dict]) -> List[dict]:
    """
    Group by wick_suffix + penetration_suffix + full_suffix + ne_suffix + signal context.
    Answers: Is U after T4 continuation or exhaustion? Is R after Z4 absorption?
    """
    groups: Dict[tuple, list] = {}
    for r in rows:
        wk  = r.get("wick_suffix", "")
        pen = r.get("penetration_suffix", "")
        ne  = r.get("ne_suffix", "")
        full = ne + wk + pen
        ts  = r.get("t_signal", "")
        zs  = r.get("z_signal", "")
        ls  = r.get("l_signal", "")
        ps  = r.get("preup_signal", "")
        ds  = r.get("predn_signal", "")
        uni = r.get("universe", "")
        tf  = r.get("timeframe", "")
        if not any([wk, pen]):
            continue
        key = (wk, pen, full, ne, ts, zs, ls, ps, ds, uni, tf)
        groups.setdefault(key, []).append(r)

    result = []
    for (wk, pen, full, ne, ts, zs, ls, ps, ds, uni, tf), grp in groups.items():
        result.append({
            "wick_suffix": wk, "penetration_suffix": pen, "full_suffix": full, "ne_suffix": ne,
            "t_signal": ts, "z_signal": zs, "l_signal": ls,
            "preup_signal": ps, "predn_signal": ds,
            "universe": uni, "timeframe": tf, "count": len(grp),
            **_robust_metrics(grp),
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _composite_sequence_perf(rows: List[dict]) -> List[dict]:
    """
    2-bar and 3-bar sequences using composite full labels.
    E.g. Z4L64ER -> T4L34NDP (base: Z4->T4)
    """
    by_ticker: Dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(str(r.get("ticker") or ""), []).append(r)

    def _parse_date(d):
        try: return datetime.strptime(d, "%Y-%m-%d")
        except: return datetime.min

    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: _parse_date(x.get("date", "")))

    seq2: Dict[tuple, list] = {}
    seq3: Dict[tuple, list] = {}
    # Store first-seen full labels per key
    seq2_labels: Dict[tuple, tuple] = {}
    seq3_labels: Dict[tuple, tuple] = {}

    def _clabel(r):
        return (r.get("composite_primary_label") or
                r.get("composite_full_label") or
                _full_label(r) or "")

    def _csuf(r):
        return r.get("ne_suffix", "") + r.get("wick_suffix", "") + r.get("penetration_suffix", "")

    for ticker, t_rows in by_ticker.items():
        n = len(t_rows)
        for i in range(n):
            curr = t_rows[i]
            curr_lbl = _clabel(curr)
            curr_base = _primary_signal(curr)
            if not curr_lbl:
                continue
            uni = curr.get("universe", "")
            tf  = curr.get("timeframe", "")

            # 2-bar sequences
            for lag in range(1, 6):
                if i - lag < 0:
                    break
                prev = t_rows[i - lag]
                prev_lbl = _clabel(prev)
                prev_base = _primary_signal(prev)
                if not prev_lbl:
                    continue
                composite_pat = f"{prev_lbl}->{curr_lbl}"
                base_pat      = f"{prev_base}->{curr_base}"
                key = (composite_pat, base_pat, lag, uni, tf)
                seq2.setdefault(key, []).append(curr)
                if key not in seq2_labels:
                    seq2_labels[key] = (
                        prev_lbl, curr_lbl,
                        _csuf(prev), _csuf(curr),
                        prev.get("wick_suffix", ""), prev.get("penetration_suffix", ""),
                        curr.get("wick_suffix", ""), curr.get("penetration_suffix", ""),
                    )
                break

            # 3-bar sequences
            for lag1 in range(1, 4):
                if i - lag1 < 0: break
                mid = t_rows[i - lag1]
                mid_lbl = _clabel(mid)
                if not mid_lbl: continue
                for lag2 in range(lag1 + 1, lag1 + 4):
                    if i - lag2 < 0: break
                    prev2 = t_rows[i - lag2]
                    prev2_lbl = _clabel(prev2)
                    if not prev2_lbl: break
                    composite_pat = f"{prev2_lbl}->{mid_lbl}->{curr_lbl}"
                    base_pat = (f"{_primary_signal(prev2)}->"
                                f"{_primary_signal(mid)}->{curr_base}")
                    key = (composite_pat, base_pat, lag1, lag2, uni, tf)
                    seq3.setdefault(key, []).append(curr)
                    if key not in seq3_labels:
                        seq3_labels[key] = (
                            prev2_lbl, curr_lbl,
                            _csuf(prev2), _csuf(curr),
                            prev2.get("wick_suffix", ""), prev2.get("penetration_suffix", ""),
                            curr.get("wick_suffix", ""), curr.get("penetration_suffix", ""),
                        )
                    break
                break

    result = []

    for (cpat, bpat, lag, uni, tf), grp in seq2.items():
        lbl_data = seq2_labels.get((cpat, bpat, lag, uni, tf), ("","","","","","","",""))
        result.append({
            "sequence_type": "2bar",
            "composite_sequence_pattern": cpat, "base_sequence_pattern": bpat,
            "bars_between": lag, "bars_between_1": lag, "bars_between_2": "",
            "universe": uni, "timeframe": tf, "count": len(grp),
            "source_full_label": lbl_data[0], "confirmation_full_label": lbl_data[1],
            "source_full_suffix": lbl_data[2], "confirmation_full_suffix": lbl_data[3],
            "source_wick_suffix": lbl_data[4], "source_penetration_suffix": lbl_data[5],
            "confirmation_wick_suffix": lbl_data[6], "confirmation_penetration_suffix": lbl_data[7],
            **_robust_metrics(grp),
        })

    for (cpat, bpat, lag1, lag2, uni, tf), grp in seq3.items():
        lbl_data = seq3_labels.get((cpat, bpat, lag1, lag2, uni, tf), ("","","","","","","",""))
        result.append({
            "sequence_type": "3bar",
            "composite_sequence_pattern": cpat, "base_sequence_pattern": bpat,
            "bars_between": lag1, "bars_between_1": lag1, "bars_between_2": lag2,
            "universe": uni, "timeframe": tf, "count": len(grp),
            "source_full_label": lbl_data[0], "confirmation_full_label": lbl_data[1],
            "source_full_suffix": lbl_data[2], "confirmation_full_suffix": lbl_data[3],
            "source_wick_suffix": lbl_data[4], "source_penetration_suffix": lbl_data[5],
            "confirmation_wick_suffix": lbl_data[6], "confirmation_penetration_suffix": lbl_data[7],
            **_robust_metrics(grp),
        })

    return sorted(result, key=lambda x: -(x["count"] or 0))


def _wick_sequence_perf(rows: List[dict]) -> List[dict]:
    """
    Sequences of wick extension/penetration patterns between bars.
    E.g. D->P within 1-3 bars, B->T4 within 1-3 bars.
    """
    by_ticker: Dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(str(r.get("ticker") or ""), []).append(r)

    def _parse_date(d):
        try: return datetime.strptime(d, "%Y-%m-%d")
        except: return datetime.min

    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: _parse_date(x.get("date", "")))

    def _wick_key(r):
        wk = r.get("wick_suffix", "")
        pen = r.get("penetration_suffix", "")
        return wk + pen  # e.g. "DP", "UP", "BH", "R", "D"

    groups: Dict[tuple, list] = {}
    for ticker, t_rows in by_ticker.items():
        n = len(t_rows)
        for i in range(n):
            curr = t_rows[i]
            curr_wk = _wick_key(curr)
            if not curr_wk:
                continue
            uni = curr.get("universe", "")
            tf  = curr.get("timeframe", "")
            base_sig = _primary_signal(curr)
            for lag in range(1, 4):
                if i - lag < 0:
                    break
                prev = t_rows[i - lag]
                prev_wk = _wick_key(prev)
                if not prev_wk:
                    continue
                pat = f"{prev_wk}->{curr_wk}"
                key = (pat, base_sig, lag, uni, tf)
                groups.setdefault(key, []).append(curr)
                break

    result = []
    for (pat, base_sig, lag, uni, tf), grp in groups.items():
        result.append({
            "wick_sequence_pattern": pat, "base_signal_context": base_sig,
            "bars_between": lag, "universe": uni, "timeframe": tf, "count": len(grp),
            **_robust_metrics(grp),
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _sequence_perf_expanded(rows: List[dict]) -> List[dict]:
    """
    Detect 2-bar and 3-bar sequences across all signal families.
    Rows must be sorted by ticker+date.
    """
    from .config import signal_family, sequence_family

    # Group by ticker
    by_ticker: Dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(str(r.get("ticker") or ""), []).append(r)

    # Sort each ticker chronologically (parse dates to avoid lexicographic mis-sort)
    def _parse_date(d: str):
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except Exception:
            return datetime.min

    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: _parse_date(x.get("date", "")))

    seq2_groups: Dict[tuple, list] = {}  # (family, pattern, lag, uni, tf) -> [outcome_rows]
    seq2_labels: Dict[tuple, tuple] = {}  # key -> (source_full_label, confirmation_full_label)
    seq3_groups: Dict[tuple, list] = {}  # (family, pattern, lag1, lag2, uni, tf) -> [outcome_rows]
    seq3_labels: Dict[tuple, tuple] = {}  # key -> (source_full_label, confirmation_full_label)

    for ticker, t_rows in by_ticker.items():
        n = len(t_rows)
        for i in range(n):
            curr = t_rows[i]
            curr_events = _bar_events(curr)
            if not curr_events:
                continue
            uni = curr.get("universe", "")
            timeframe = curr.get("timeframe", "")

            # 2-bar sequences: for each curr family event, find nearest prev bar
            # with at least one event forming a known sequence_family combo.
            for _, curr_sig in curr_events:
                for lag in range(1, 6):
                    if i - lag < 0:
                        break
                    prev = t_rows[i - lag]
                    prev_events = _bar_events(prev)
                    if not prev_events:
                        continue
                    matched = False
                    for _, prev_sig in prev_events:
                        fam = sequence_family(prev_sig, curr_sig)
                        if not fam:
                            continue
                        pattern = f"{prev_sig}->{curr_sig}"
                        key = (fam, pattern, lag, uni, timeframe)
                        seq2_groups.setdefault(key, []).append(curr)
                        if key not in seq2_labels:
                            seq2_labels[key] = (_full_label(prev), _full_label(curr))
                        matched = True
                    if matched:
                        break  # nearest matching prev bar for this curr event

            # 3-bar sequences: for each curr family event, find nearest mid event
            # with valid family combo, then nearest prev2 event with valid combo.
            for _, curr_sig in curr_events:
                mid_found = False
                for lag1 in range(1, 4):
                    if i - lag1 < 0 or mid_found:
                        break
                    mid = t_rows[i - lag1]
                    mid_events = _bar_events(mid)
                    if not mid_events:
                        continue
                    for _, mid_sig in mid_events:
                        if not sequence_family(mid_sig, curr_sig):
                            continue
                        # find prev2 for this (mid_sig, curr_sig) chain
                        for lag2 in range(lag1 + 1, lag1 + 4):
                            if i - lag2 < 0:
                                break
                            prev2 = t_rows[i - lag2]
                            prev2_events = _bar_events(prev2)
                            if not prev2_events:
                                continue
                            p2_matched = False
                            for _, prev2_sig in prev2_events:
                                if not sequence_family(prev2_sig, mid_sig):
                                    continue
                                fam = (f"{signal_family(prev2_sig)}_to_"
                                       f"{signal_family(mid_sig)}_to_"
                                       f"{signal_family(curr_sig)}")
                                pattern = f"{prev2_sig}->{mid_sig}->{curr_sig}"
                                key = (fam, pattern, lag1, lag2, uni, timeframe)
                                seq3_groups.setdefault(key, []).append(curr)
                                if key not in seq3_labels:
                                    seq3_labels[key] = (_full_label(prev2), _full_label(curr))
                                p2_matched = True
                            if p2_matched:
                                break  # nearest prev2 for this mid->curr chain
                        mid_found = True
                    # outer continues to next lag1 if no mid event matched

    result = []

    for (fam, pattern, lag, uni, tf2), grp in seq2_groups.items():
        src_lbl, conf_lbl = seq2_labels.get((fam, pattern, lag, uni, tf2), ("", ""))
        result.append({
            "sequence_type": "2bar",
            "sequence_family": fam, "sequence_pattern": pattern,
            "bars_between": lag, "bars_between_1": lag, "bars_between_2": "",
            "universe": uni, "timeframe": tf2, "count": len(grp),
            "source_full_label": src_lbl, "confirmation_full_label": conf_lbl,
            "source_full_suffix": _extract_suffix_from_label(src_lbl),
            "confirmation_full_suffix": _extract_suffix_from_label(conf_lbl),
            **_robust_metrics(grp),
        })

    for (fam, pattern, lag1, lag2, uni, tf2), grp in seq3_groups.items():
        src_lbl, conf_lbl = seq3_labels.get((fam, pattern, lag1, lag2, uni, tf2), ("", ""))
        result.append({
            "sequence_type": "3bar",
            "sequence_family": fam, "sequence_pattern": pattern,
            "bars_between": lag1, "bars_between_1": lag1, "bars_between_2": lag2,
            "universe": uni, "timeframe": tf2, "count": len(grp),
            "source_full_label": src_lbl, "confirmation_full_label": conf_lbl,
            "source_full_suffix": _extract_suffix_from_label(src_lbl),
            "confirmation_full_suffix": _extract_suffix_from_label(conf_lbl),
            **_robust_metrics(grp),
        })

    return sorted(result, key=lambda x: -(x["count"] or 0))


def _suspicious_patterns(rows: List[dict], *_legacy_args, **_legacy_kw) -> List[dict]:
    """Group raw rows by composite_full_label (or primary signal) and flag groups
    with inflated metrics (huge avg-vs-median gap, outliers, etc.).

    Triggers when ANY of:
      avg_ret_10d > 15%  AND count >= 30
      avg_vs_median_gap_10d > 10
      outlier_rate_10d > 0.10
      max_ret_10d > 100
    """
    groups: Dict[tuple, list] = {}
    for r in rows:
        pat = (r.get("composite_full_label") or
               r.get("composite_primary_label") or
               _primary_signal(r) or "")
        if not pat:
            continue
        uni = r.get("universe", "")
        nbatch = r.get("nasdaq_batch", "") or ""
        pb = _row_price_bucket(r)
        key = (pat, uni, nbatch, pb)
        groups.setdefault(key, []).append(r)

    result = []
    for (pat, uni, nbatch, pb), grp in groups.items():
        m = _robust_metrics(grp)
        cnt = len(grp)
        avg10 = m.get("avg_ret_10d") or 0.0
        med10 = m.get("median_ret_10d") or 0.0
        gap   = m.get("avg_vs_median_gap_10d") or 0.0
        orate = m.get("outlier_rate_10d") or 0.0
        max10 = m.get("max_ret_10d") or 0.0
        flagged = (
            (avg10 > 15.0 and cnt >= 30) or
            (gap > 10.0) or
            (orate > 0.10) or
            (max10 > 100.0)
        )
        if not flagged:
            continue
        ex_t, ex_d, ex_c, ex_r = [], [], [], []
        for rr in grp[:5]:
            ex_t.append(str(rr.get("ticker") or ""))
            ex_d.append(str(rr.get("date") or ""))
            ex_c.append(str(rr.get("close") or ""))
            ex_r.append(str(rr.get("ret_10d") or ""))
        # Determine pattern_type (composite if matches form, else signal)
        rr0 = grp[0]
        ptype = "composite" if (
            rr0.get("composite_full_label") or rr0.get("composite_primary_label")
        ) else "signal"
        result.append({
            "pattern_type": ptype,
            "pattern_name": pat,
            "universe": uni,
            "nasdaq_batch": nbatch,
            "price_bucket": pb,
            "count": cnt,
            "avg_ret_10d": m.get("avg_ret_10d"),
            "median_ret_10d": med10,
            "trimmed_avg_ret_10d": m.get("trimmed_avg_ret_10d"),
            "winsorized_avg_ret_10d": m.get("winsorized_avg_ret_10d"),
            "max_ret_10d": m.get("max_ret_10d"),
            "avg_vs_median_gap_10d": m.get("avg_vs_median_gap_10d"),
            "outlier_count_10d": m.get("outlier_count_10d"),
            "outlier_rate_10d": m.get("outlier_rate_10d"),
            "example_tickers": "|".join(ex_t),
            "example_dates": "|".join(ex_d),
            "example_close_prices": "|".join(ex_c),
            "example_ret_10d_values": "|".join(ex_r),
        })
    return sorted(result, key=lambda x: -(_safe_float(x.get("avg_ret_10d")) or 0))


# ── Price-bucketed perf functions ────────────────────────────────────────────

def _signal_perf_by_price_bucket(rows: List[dict]) -> List[dict]:
    groups: Dict[tuple, list] = {}
    signal_types = [
        ("T", "t_signal"), ("Z", "z_signal"), ("L", "l_signal"),
        ("PREUP", "preup_signal"), ("PREDN", "predn_signal"),
    ]
    for r in rows:
        pb = _row_price_bucket(r)
        if not pb:
            continue
        uni = r.get("universe", "")
        tf  = r.get("timeframe", "")
        for sig_type, col in signal_types:
            name = r.get(col, "")
            if not name:
                continue
            key = (sig_type, name, uni, tf, pb)
            groups.setdefault(key, []).append(r)
    result = []
    for (sig_type, name, uni, tf, pb), grp in groups.items():
        result.append({
            "signal_type": sig_type, "signal_name": name,
            "universe": uni, "timeframe": tf, "price_bucket": pb,
            "count": len(grp),
            **_robust_metrics(grp),
            "tz_wlnbb_version": TZ_WLNBB_VERSION,
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _composite_perf_by_price_bucket(rows: List[dict]) -> List[dict]:
    groups: Dict[tuple, list] = {}
    for r in rows:
        pb = _row_price_bucket(r)
        if not pb:
            continue
        cfl = r.get("composite_full_label", "") or r.get("composite_primary_label", "")
        if not cfl:
            continue
        key = (
            cfl,
            r.get("composite_core", ""),
            r.get("composite_full_suffix", "") or r.get("composite_suffix", ""),
            r.get("t_signal", ""), r.get("z_signal", ""), r.get("l_signal", ""),
            r.get("ne_suffix", ""), r.get("wick_suffix", ""), r.get("penetration_suffix", ""),
            r.get("universe", ""), r.get("timeframe", ""), pb,
        )
        groups.setdefault(key, []).append(r)
    result = []
    for (cfl, core, fsuf, ts, zs, ls, ne, wk, pen, uni, tf, pb), grp in groups.items():
        result.append({
            "composite_full_label": cfl, "composite_core": core, "composite_full_suffix": fsuf,
            "t_signal": ts, "z_signal": zs, "l_signal": ls,
            "ne_suffix": ne, "wick_suffix": wk, "penetration_suffix": pen,
            "full_suffix": ne + wk + pen,
            "universe": uni, "timeframe": tf, "price_bucket": pb,
            "count": len(grp),
            **_robust_metrics(grp),
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _wick_behavior_perf_by_price_bucket(rows: List[dict]) -> List[dict]:
    groups: Dict[tuple, list] = {}
    for r in rows:
        pb = _row_price_bucket(r)
        if not pb:
            continue
        wk  = r.get("wick_suffix", "")
        pen = r.get("penetration_suffix", "")
        ne  = r.get("ne_suffix", "")
        full = ne + wk + pen
        ts  = r.get("t_signal", ""); zs = r.get("z_signal", ""); ls = r.get("l_signal", "")
        ps  = r.get("preup_signal", ""); ds = r.get("predn_signal", "")
        uni = r.get("universe", ""); tf = r.get("timeframe", "")
        if not any([wk, pen]):
            continue
        key = (wk, pen, full, ne, ts, zs, ls, ps, ds, uni, tf, pb)
        groups.setdefault(key, []).append(r)
    result = []
    for (wk, pen, full, ne, ts, zs, ls, ps, ds, uni, tf, pb), grp in groups.items():
        result.append({
            "wick_suffix": wk, "penetration_suffix": pen, "full_suffix": full, "ne_suffix": ne,
            "t_signal": ts, "z_signal": zs, "l_signal": ls,
            "preup_signal": ps, "predn_signal": ds,
            "universe": uni, "timeframe": tf, "price_bucket": pb,
            "count": len(grp),
            **_robust_metrics(grp),
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _sequence_perf_by_price_bucket(rows: List[dict]) -> List[dict]:
    """Price-bucketed expanded sequence perf. Bucket from curr row."""
    from .config import signal_family, sequence_family

    by_ticker: Dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(str(r.get("ticker") or ""), []).append(r)

    def _parse_date(d):
        try: return datetime.strptime(d, "%Y-%m-%d")
        except: return datetime.min

    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: _parse_date(x.get("date", "")))

    seq2_groups: Dict[tuple, list] = {}
    seq2_labels: Dict[tuple, tuple] = {}
    seq3_groups: Dict[tuple, list] = {}
    seq3_labels: Dict[tuple, tuple] = {}

    for ticker, t_rows in by_ticker.items():
        n = len(t_rows)
        for i in range(n):
            curr = t_rows[i]
            curr_events = _bar_events(curr)
            if not curr_events:
                continue
            pb = _row_price_bucket(curr)
            if not pb:
                continue
            uni = curr.get("universe", ""); tf = curr.get("timeframe", "")

            for _, curr_sig in curr_events:
                for lag in range(1, 6):
                    if i - lag < 0:
                        break
                    prev = t_rows[i - lag]
                    prev_events = _bar_events(prev)
                    if not prev_events:
                        continue
                    matched = False
                    for _, prev_sig in prev_events:
                        fam = sequence_family(prev_sig, curr_sig)
                        if not fam:
                            continue
                        pattern = f"{prev_sig}->{curr_sig}"
                        key = (fam, pattern, lag, uni, tf, pb)
                        seq2_groups.setdefault(key, []).append(curr)
                        if key not in seq2_labels:
                            seq2_labels[key] = (_full_label(prev), _full_label(curr))
                        matched = True
                    if matched:
                        break

            for _, curr_sig in curr_events:
                mid_found = False
                for lag1 in range(1, 4):
                    if i - lag1 < 0 or mid_found:
                        break
                    mid = t_rows[i - lag1]
                    mid_events = _bar_events(mid)
                    if not mid_events:
                        continue
                    for _, mid_sig in mid_events:
                        if not sequence_family(mid_sig, curr_sig):
                            continue
                        for lag2 in range(lag1 + 1, lag1 + 4):
                            if i - lag2 < 0:
                                break
                            prev2 = t_rows[i - lag2]
                            prev2_events = _bar_events(prev2)
                            if not prev2_events:
                                continue
                            p2_matched = False
                            for _, prev2_sig in prev2_events:
                                if not sequence_family(prev2_sig, mid_sig):
                                    continue
                                fam = (f"{signal_family(prev2_sig)}_to_"
                                       f"{signal_family(mid_sig)}_to_"
                                       f"{signal_family(curr_sig)}")
                                pattern = f"{prev2_sig}->{mid_sig}->{curr_sig}"
                                key = (fam, pattern, lag1, lag2, uni, tf, pb)
                                seq3_groups.setdefault(key, []).append(curr)
                                if key not in seq3_labels:
                                    seq3_labels[key] = (_full_label(prev2), _full_label(curr))
                                p2_matched = True
                            if p2_matched:
                                break
                        mid_found = True

    result = []
    for (fam, pattern, lag, uni, tf2, pb), grp in seq2_groups.items():
        src_lbl, conf_lbl = seq2_labels.get((fam, pattern, lag, uni, tf2, pb), ("", ""))
        result.append({
            "sequence_type": "2bar",
            "sequence_family": fam, "sequence_pattern": pattern,
            "bars_between": lag, "bars_between_1": lag, "bars_between_2": "",
            "universe": uni, "timeframe": tf2, "price_bucket": pb, "count": len(grp),
            "source_full_label": src_lbl, "confirmation_full_label": conf_lbl,
            "source_full_suffix": _extract_suffix_from_label(src_lbl),
            "confirmation_full_suffix": _extract_suffix_from_label(conf_lbl),
            **_robust_metrics(grp),
        })
    for (fam, pattern, lag1, lag2, uni, tf2, pb), grp in seq3_groups.items():
        src_lbl, conf_lbl = seq3_labels.get((fam, pattern, lag1, lag2, uni, tf2, pb), ("", ""))
        result.append({
            "sequence_type": "3bar",
            "sequence_family": fam, "sequence_pattern": pattern,
            "bars_between": lag1, "bars_between_1": lag1, "bars_between_2": lag2,
            "universe": uni, "timeframe": tf2, "price_bucket": pb, "count": len(grp),
            "source_full_label": src_lbl, "confirmation_full_label": conf_lbl,
            "source_full_suffix": _extract_suffix_from_label(src_lbl),
            "confirmation_full_suffix": _extract_suffix_from_label(conf_lbl),
            **_robust_metrics(grp),
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _composite_sequence_perf_by_price_bucket(rows: List[dict]) -> List[dict]:
    by_ticker: Dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(str(r.get("ticker") or ""), []).append(r)

    def _parse_date(d):
        try: return datetime.strptime(d, "%Y-%m-%d")
        except: return datetime.min
    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: _parse_date(x.get("date", "")))

    seq2: Dict[tuple, list] = {}
    seq3: Dict[tuple, list] = {}
    seq2_labels: Dict[tuple, tuple] = {}
    seq3_labels: Dict[tuple, tuple] = {}

    def _clabel(r):
        return (r.get("composite_primary_label") or
                r.get("composite_full_label") or
                _full_label(r) or "")

    def _csuf(r):
        return r.get("ne_suffix", "") + r.get("wick_suffix", "") + r.get("penetration_suffix", "")

    for ticker, t_rows in by_ticker.items():
        n = len(t_rows)
        for i in range(n):
            curr = t_rows[i]
            curr_lbl = _clabel(curr)
            curr_base = _primary_signal(curr)
            if not curr_lbl:
                continue
            pb = _row_price_bucket(curr)
            if not pb:
                continue
            uni = curr.get("universe", ""); tf = curr.get("timeframe", "")
            for lag in range(1, 6):
                if i - lag < 0: break
                prev = t_rows[i - lag]
                prev_lbl = _clabel(prev)
                prev_base = _primary_signal(prev)
                if not prev_lbl:
                    continue
                composite_pat = f"{prev_lbl}->{curr_lbl}"
                base_pat = f"{prev_base}->{curr_base}"
                key = (composite_pat, base_pat, lag, uni, tf, pb)
                seq2.setdefault(key, []).append(curr)
                if key not in seq2_labels:
                    seq2_labels[key] = (
                        prev_lbl, curr_lbl, _csuf(prev), _csuf(curr),
                        prev.get("wick_suffix", ""), prev.get("penetration_suffix", ""),
                        curr.get("wick_suffix", ""), curr.get("penetration_suffix", ""),
                    )
                break
            for lag1 in range(1, 4):
                if i - lag1 < 0: break
                mid = t_rows[i - lag1]
                mid_lbl = _clabel(mid)
                if not mid_lbl: continue
                for lag2 in range(lag1 + 1, lag1 + 4):
                    if i - lag2 < 0: break
                    prev2 = t_rows[i - lag2]
                    prev2_lbl = _clabel(prev2)
                    if not prev2_lbl: break
                    composite_pat = f"{prev2_lbl}->{mid_lbl}->{curr_lbl}"
                    base_pat = (f"{_primary_signal(prev2)}->"
                                f"{_primary_signal(mid)}->{curr_base}")
                    key = (composite_pat, base_pat, lag1, lag2, uni, tf, pb)
                    seq3.setdefault(key, []).append(curr)
                    if key not in seq3_labels:
                        seq3_labels[key] = (
                            prev2_lbl, curr_lbl, _csuf(prev2), _csuf(curr),
                            prev2.get("wick_suffix", ""), prev2.get("penetration_suffix", ""),
                            curr.get("wick_suffix", ""), curr.get("penetration_suffix", ""),
                        )
                    break
                break

    result = []
    for (cpat, bpat, lag, uni, tf, pb), grp in seq2.items():
        ld = seq2_labels.get((cpat, bpat, lag, uni, tf, pb), ("","","","","","","",""))
        result.append({
            "sequence_type": "2bar",
            "composite_sequence_pattern": cpat, "base_sequence_pattern": bpat,
            "bars_between": lag, "bars_between_1": lag, "bars_between_2": "",
            "universe": uni, "timeframe": tf, "price_bucket": pb, "count": len(grp),
            "source_full_label": ld[0], "confirmation_full_label": ld[1],
            "source_full_suffix": ld[2], "confirmation_full_suffix": ld[3],
            "source_wick_suffix": ld[4], "source_penetration_suffix": ld[5],
            "confirmation_wick_suffix": ld[6], "confirmation_penetration_suffix": ld[7],
            **_robust_metrics(grp),
        })
    for (cpat, bpat, lag1, lag2, uni, tf, pb), grp in seq3.items():
        ld = seq3_labels.get((cpat, bpat, lag1, lag2, uni, tf, pb), ("","","","","","","",""))
        result.append({
            "sequence_type": "3bar",
            "composite_sequence_pattern": cpat, "base_sequence_pattern": bpat,
            "bars_between": lag1, "bars_between_1": lag1, "bars_between_2": lag2,
            "universe": uni, "timeframe": tf, "price_bucket": pb, "count": len(grp),
            "source_full_label": ld[0], "confirmation_full_label": ld[1],
            "source_full_suffix": ld[2], "confirmation_full_suffix": ld[3],
            "source_wick_suffix": ld[4], "source_penetration_suffix": ld[5],
            "confirmation_wick_suffix": ld[6], "confirmation_penetration_suffix": ld[7],
            **_robust_metrics(grp),
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _wick_sequence_perf_by_price_bucket(rows: List[dict]) -> List[dict]:
    by_ticker: Dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(str(r.get("ticker") or ""), []).append(r)

    def _parse_date(d):
        try: return datetime.strptime(d, "%Y-%m-%d")
        except: return datetime.min
    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: _parse_date(x.get("date", "")))

    def _wick_key(r):
        return r.get("wick_suffix", "") + r.get("penetration_suffix", "")

    groups: Dict[tuple, list] = {}
    for ticker, t_rows in by_ticker.items():
        n = len(t_rows)
        for i in range(n):
            curr = t_rows[i]
            curr_wk = _wick_key(curr)
            if not curr_wk:
                continue
            pb = _row_price_bucket(curr)
            if not pb:
                continue
            uni = curr.get("universe", ""); tf = curr.get("timeframe", "")
            base_sig = _primary_signal(curr)
            for lag in range(1, 4):
                if i - lag < 0:
                    break
                prev = t_rows[i - lag]
                prev_wk = _wick_key(prev)
                if not prev_wk:
                    continue
                pat = f"{prev_wk}->{curr_wk}"
                key = (pat, base_sig, lag, uni, tf, pb)
                groups.setdefault(key, []).append(curr)
                break
    result = []
    for (pat, base_sig, lag, uni, tf, pb), grp in groups.items():
        result.append({
            "wick_sequence_pattern": pat, "base_signal_context": base_sig,
            "bars_between": lag, "universe": uni, "timeframe": tf,
            "price_bucket": pb, "count": len(grp),
            **_robust_metrics(grp),
        })
    return sorted(result, key=lambda x: -(x["count"] or 0))


def _date_forward(date_str: str, n: int) -> str:
    """Return date_str + n calendar days as YYYY-MM-DD, or '' on error."""
    try:
        return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _return_validation_examples(rows: List[dict], n: int = 200) -> List[dict]:
    """Sample rows with forward returns for manual validation.
    date_plus_N uses actual future trading-row dates (not calendar offsets).
    """
    # Build per-ticker sorted date lists for actual trading-day lookups
    by_ticker_dates: Dict[str, list] = {}
    for r in rows:
        ticker = str(r.get("ticker") or "")
        date = r.get("date", "")
        if ticker and date:
            by_ticker_dates.setdefault(ticker, set()).add(date)  # type: ignore[arg-type]
    by_ticker_dates = {t: sorted(dates) for t, dates in by_ticker_dates.items()}  # type: ignore[assignment]

    with_returns = [r for r in rows if r.get("ret_10d") not in (None, "", "None")]
    # take spread: first 50, last 50, random middle 100
    sample = with_returns[:50]
    if len(with_returns) > 100:
        mid = with_returns[50:-50]
        sample += random.sample(mid, min(100, len(mid)))
        sample += with_returns[-50:]
    fields = ["ticker", "date", "date_plus_1", "date_plus_3", "date_plus_5", "date_plus_10",
              "close", "ret_1d", "ret_3d", "ret_5d", "ret_10d",
              "max_high_5d", "max_high_10d", "max_drawdown_5d", "max_drawdown_10d",
              "mfe_5d", "mfe_10d", "mae_5d", "mae_10d",
              "big_win_10d", "fail_10d", "t_signal", "z_signal", "l_signal"]
    result = []
    for r in sample[:n]:
        row_out = {f: r.get(f, "") for f in fields}
        ticker = str(r.get("ticker") or "")
        date = r.get("date", "")
        dates = by_ticker_dates.get(ticker, [])
        try:
            idx = dates.index(date)
            row_out["date_plus_1"]  = dates[idx + 1]  if idx + 1  < len(dates) else ""
            row_out["date_plus_3"]  = dates[idx + 3]  if idx + 3  < len(dates) else ""
            row_out["date_plus_5"]  = dates[idx + 5]  if idx + 5  < len(dates) else ""
            row_out["date_plus_10"] = dates[idx + 10] if idx + 10 < len(dates) else ""
        except ValueError:
            row_out["date_plus_1"] = row_out["date_plus_3"] = ""
            row_out["date_plus_5"] = row_out["date_plus_10"] = ""
        result.append(row_out)
    return result


def _sequence_event_audit(rows: List[dict]) -> List[dict]:
    """
    Per-signal audit: how many times each signal appears and how often it is
    used as the source bar in a 2-bar sequence (followed by another signal within 5 bars).
    Columns: family, signal_name, count, rows_with_signal, used_in_sequence_count, used_in_sequence_rate
    """
    from .config import signal_family as _sig_family

    signal_cols = [
        ("T", "t_signal"), ("Z", "z_signal"), ("L", "l_signal"),
        ("PREUP", "preup_signal"), ("PREDN", "predn_signal"),
    ]

    signal_counts: Dict[str, int] = {}
    for r in rows:
        for _, col in signal_cols:
            sig = r.get(col, "")
            if sig:
                signal_counts[sig] = signal_counts.get(sig, 0) + 1

    by_ticker: Dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(str(r.get("ticker") or ""), []).append(r)

    def _parse_date(d: str):
        try: return datetime.strptime(d, "%Y-%m-%d")
        except: return datetime.min

    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: _parse_date(x.get("date", "")))

    used_in_seq: Dict[str, int] = {}
    for ticker, t_rows in by_ticker.items():
        n = len(t_rows)
        for i in range(n):
            curr_events = _bar_events(t_rows[i])
            if not curr_events:
                continue
            # has any signal within next 5 bars?
            has_next = False
            for lag in range(1, 6):
                if i + lag >= n:
                    break
                if _bar_events(t_rows[i + lag]):
                    has_next = True
                    break
            if not has_next:
                continue
            for _, curr_sig in curr_events:
                used_in_seq[curr_sig] = used_in_seq.get(curr_sig, 0) + 1

    result = []
    for sig, cnt in signal_counts.items():
        fam = _sig_family(sig)
        in_seq = used_in_seq.get(sig, 0)
        result.append({
            "family": fam,
            "signal_name": sig,
            "count": cnt,
            "rows_with_signal": cnt,
            "used_in_sequence_count": in_seq,
            "used_in_sequence_rate": round(in_seq / cnt, 4) if cnt else 0.0,
        })
    return sorted(result, key=lambda x: -x["count"])


def _date_order_audit(rows: List[dict]) -> List[dict]:
    """
    Verify chronological ordering of dates per ticker.
    Returns one row per ticker summarising any ordering issues.
    """
    by_ticker: Dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(str(r.get("ticker") or ""), []).append(r)

    issues = []
    for ticker, t_rows in by_ticker.items():
        dates = [r.get("date", "") for r in t_rows]
        parsed = []
        invalid = 0
        for d in dates:
            try:
                parsed.append(datetime.strptime(d, "%Y-%m-%d"))
            except Exception:
                parsed.append(None)
                invalid += 1
        out_of_order = sum(
            1 for i in range(1, len(parsed))
            if parsed[i] is not None and parsed[i - 1] is not None and parsed[i] < parsed[i - 1]
        )
        issues.append({
            "ticker": ticker,
            "total_rows": len(t_rows),
            "invalid_date_format": invalid,
            "out_of_order_count": out_of_order,
            "first_date": dates[0] if dates else "",
            "last_date": dates[-1] if dates else "",
            "status": "OK" if out_of_order == 0 and invalid == 0 else "ISSUE",
        })

    issues.sort(key=lambda x: -(x["out_of_order_count"] + x["invalid_date_format"]))
    return issues


def _unscored_audit(rows: List[dict]) -> List[dict]:
    """
    Find any signal column values that don't appear in the known signal registry.
    L dynamic combos (e.g. L12, L346) matching ^L[1-6]+$ are considered valid.
    """
    from .config import ALL_KNOWN_SIGNALS, is_known_l_signal
    unknown: Dict[str, dict] = {}
    for r in rows:
        for col in ("t_signal", "z_signal", "l_signal", "preup_signal", "predn_signal"):
            sig = r.get(col, "")
            if not sig:
                continue
            if sig in ALL_KNOWN_SIGNALS:
                continue
            # Dynamic L combos like L12, L346 are valid
            if col == "l_signal" and is_known_l_signal(sig):
                continue
            d = unknown.setdefault(sig, {"raw_signal": sig, "normalized_signal": sig,
                                         "source_column": col, "count": 0,
                                         "tickers": [], "dates": []})
            d["count"] += 1
            if len(d["tickers"]) < 3:
                d["tickers"].append(str(r.get("ticker") or ""))
                d["dates"].append(r.get("date", ""))
    result = []
    for sig, d in unknown.items():
        result.append({
            "raw_signal": d["raw_signal"],
            "normalized_signal": d["normalized_signal"],
            "source_column": d["source_column"],
            "count": d["count"],
            "example_tickers": "|".join(d["tickers"]),
            "example_dates": "|".join(d["dates"]),
        })
    return sorted(result, key=lambda x: -x["count"])


def _invalid_suffix_audit(rows: List[dict]) -> List[dict]:
    """
    Find rows where composite_full_suffix or the suffix parsed from
    composite_full_label is not a valid full_suffix string.
    Valid pattern: ^[NE][UDB]?[PRH]?$ (or empty).
    """
    issues: Dict[tuple, dict] = {}
    for r in rows:
        checks = [
            ("composite_full_suffix", r.get("composite_full_suffix") or ""),
            ("parsed_from_composite_full_label",
             _extract_suffix_from_label(r.get("composite_full_label") or "")),
        ]
        for field, val in checks:
            if not val:
                continue
            if is_valid_full_suffix(val):
                continue
            key = (field, val)
            d = issues.setdefault(key, {
                "field_name": field, "invalid_suffix": val,
                "count": 0, "source_labels": [], "tickers": [], "dates": [],
            })
            d["count"] += 1
            if len(d["tickers"]) < 5:
                d["tickers"].append(str(r.get("ticker") or ""))
                d["dates"].append(r.get("date", ""))
                d["source_labels"].append(r.get("composite_full_label", ""))

    result = []
    for d in sorted(issues.values(), key=lambda x: -x["count"]):
        result.append({
            "field_name": d["field_name"],
            "invalid_suffix": d["invalid_suffix"],
            "source_label": "|".join(d["source_labels"]),
            "ticker": "|".join(d["tickers"]),
            "date": "|".join(d["dates"]),
            "count": d["count"],
        })
    return result


def _build_metadata(rows: List[dict], universe: str, tf: str,
                    ticker_count: int = 0, audit: dict = None,
                    nasdaq_batch: str = "") -> dict:
    """Build enhanced metadata dict."""
    dates = sorted(r.get("date", "") for r in rows if r.get("date"))
    start_date = dates[0] if dates else ""
    end_date   = dates[-1] if dates else ""

    # Trading days per ticker
    per_ticker: Dict[str, set] = {}
    for r in rows:
        per_ticker.setdefault(str(r.get("ticker") or ""), set()).add(r.get("date", ""))
    counts = [len(v) for v in per_ticker.values() if v]

    rows_with_fwd = sum(1 for r in rows if r.get("ret_10d") not in (None, "", "None"))
    rows_dropped  = len(rows) - rows_with_fwd

    meta = {
        "version": TZ_WLNBB_VERSION,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "universe": universe,
        "nasdaq_batch": nasdaq_batch or None,
        "timeframe": tf,
        "ticker_count": ticker_count or len(per_ticker),
        "rows_total": len(rows),
        "start_date": start_date,
        "end_date": end_date,
        "lookback_trading_days_requested": DEFAULT_LOOKBACK_TRADING_DAYS,
        "trading_days_per_ticker_min":    min(counts) if counts else 0,
        "trading_days_per_ticker_median": statistics.median(counts) if counts else 0,
        "trading_days_per_ticker_max":    max(counts) if counts else 0,
        "rows_with_t_signal":     sum(1 for r in rows if r.get("t_signal")),
        "rows_with_z_signal":     sum(1 for r in rows if r.get("z_signal")),
        "rows_with_l_signal":     sum(1 for r in rows if r.get("l_signal")),
        "rows_with_preup":        sum(1 for r in rows if r.get("preup_signal")),
        "rows_with_predn":        sum(1 for r in rows if r.get("predn_signal")),
        "rows_with_combo":        sum(1 for r in rows if r.get("has_tz_l_combo") in (1, "1", True)),
        "rows_with_sequence":     sum(1 for r in rows if r.get("t_after_z_confirmed") in (1, "1", True)),
        "rows_with_forward_returns_available": rows_with_fwd,
        "rows_dropped_due_to_missing_forward_returns": rows_dropped,
    }
    rows_with_pen_p = sum(1 for r in rows if r.get("penetration_suffix") == "P")
    rows_with_pen_r = sum(1 for r in rows if r.get("penetration_suffix") == "R")
    rows_with_pen_h = sum(1 for r in rows if r.get("penetration_suffix") == "H")
    rows_with_any_pen = rows_with_pen_p + rows_with_pen_r + rows_with_pen_h

    meta["rows_with_penetration_p"] = rows_with_pen_p
    meta["rows_with_penetration_r"] = rows_with_pen_r
    meta["rows_with_penetration_h"] = rows_with_pen_h
    meta["rows_with_any_penetration_suffix"] = rows_with_any_pen

    # Composite counts
    meta["rows_with_composite_t_label"]    = sum(1 for r in rows if r.get("composite_t_label") or r.get("t_signal"))
    meta["rows_with_composite_z_label"]    = sum(1 for r in rows if r.get("composite_z_label") or r.get("z_signal"))
    meta["rows_with_composite_primary_label"] = sum(1 for r in rows if r.get("composite_primary_label") or r.get("composite_full_label"))
    meta["unique_composite_full_labels"]   = len(set(r.get("composite_full_label") or r.get("composite_primary_label", "") for r in rows if r.get("composite_full_label") or r.get("composite_primary_label")))
    meta["unique_composite_cores"]         = len(set(r.get("composite_core", "") for r in rows if r.get("composite_core")))

    # Wick extension counts
    meta["rows_with_wick_u"]   = sum(1 for r in rows if r.get("wick_suffix") == "U")
    meta["rows_with_wick_d"]   = sum(1 for r in rows if r.get("wick_suffix") == "D")
    meta["rows_with_wick_b"]   = sum(1 for r in rows if r.get("wick_suffix") == "B")
    meta["rows_with_any_wick_extension"] = sum(1 for r in rows if r.get("wick_suffix") in ("U", "D", "B"))

    # Penetration counts (may already exist from earlier version, update/overwrite)
    meta["rows_with_penetration_p"] = sum(1 for r in rows if r.get("penetration_suffix") == "P")
    meta["rows_with_penetration_r"] = sum(1 for r in rows if r.get("penetration_suffix") == "R")
    meta["rows_with_penetration_h"] = sum(1 for r in rows if r.get("penetration_suffix") == "H")
    meta["rows_with_any_penetration_suffix"] = sum(1 for r in rows if r.get("penetration_suffix") in ("P", "R", "H"))

    # Distributions
    from collections import Counter
    wk_dist = Counter(r.get("wick_suffix", "") for r in rows if r.get("wick_suffix"))
    pen_dist = Counter(r.get("penetration_suffix", "") for r in rows if r.get("penetration_suffix"))
    full_dist_items = Counter(
        (r.get("ne_suffix", "") + r.get("wick_suffix", "") + r.get("penetration_suffix", ""))
        for r in rows
        if r.get("ne_suffix") or r.get("wick_suffix") or r.get("penetration_suffix")
    )
    meta["wick_suffix_distribution"]        = dict(wk_dist.most_common(10))
    meta["penetration_suffix_distribution"] = dict(pen_dist.most_common(10))
    meta["full_suffix_distribution"]        = dict(full_dist_items.most_common(20))

    if nasdaq_batch:
        first_letters = sorted(
            set(str(r.get("ticker") or "")[:1].upper()
                for r in rows if str(r.get("ticker") or "")[:1].isalpha())
        )
        meta["ticker_first_letter_min"] = first_letters[0] if first_letters else ""
        meta["ticker_first_letter_max"] = first_letters[-1] if first_letters else ""

    # Robust metrics & price bucketing flags
    meta["robust_metrics_enabled"] = True
    meta["price_buckets_enabled"]  = True
    pb_counter = Counter(_row_price_bucket(r) for r in rows)
    meta["price_bucket_distribution"] = dict(pb_counter)

    def _close(r):
        try:
            return float(r.get("close"))
        except (TypeError, ValueError):
            return None
    closes = [(_close(r)) for r in rows]
    meta["is_penny_stock_count"] = sum(1 for c in closes if c is not None and c < 5)
    meta["is_sub_dollar_count"]  = sum(1 for c in closes if c is not None and c < 1)
    meta["is_low_price_count"]   = sum(1 for c in closes if c is not None and c < 20)
    meta["is_high_price_count"]  = sum(1 for c in closes if c is not None and c >= 150)
    meta["suspicious_pattern_count"] = 0
    meta["robust_ranking_files"]   = []
    meta["price_bucketed_files"]   = []
    meta["raw_avg_ranking_files"]  = []
    meta["ticker_na_preserved"]    = any(str(r.get("ticker")) == "NA" for r in rows)

    if audit:
        meta.update(audit)
    return meta


def get_config_snapshot() -> dict:
    """Return a pure-dict snapshot of current config (no pandas dependency)."""
    from .config import (
        TZ_WLNBB_VERSION, OUTPUT_SCHEMA_VERSION, DEFAULT_LOOKBACK_TRADING_DAYS,
        USE_WICK, MIN_BODY_RATIO, DOJI_THRESH, WLNBB_MA_PERIOD,
        T_PRIORITY, Z_PRIORITY, PREUP_PRIORITY, PREDN_PRIORITY,
        ALL_KNOWN_SIGNALS, SEQUENCE_FAMILIES,
    )
    return {
        "TZ_WLNBB_ANALYZER_VERSION": TZ_WLNBB_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "default_lookback_trading_days": DEFAULT_LOOKBACK_TRADING_DAYS,
        "parameters": {
            "useWick": USE_WICK,
            "minBodyRatio": MIN_BODY_RATIO,
            "dojiThresh": DOJI_THRESH,
            "ma_period": WLNBB_MA_PERIOD,
        },
        "t_priority_order": T_PRIORITY,
        "z_priority_order": Z_PRIORITY,
        "preup_priority_order": PREUP_PRIORITY,
        "predn_priority_order": PREDN_PRIORITY,
        "wlnbb_bucket_logic": "Bollinger Bands period=20 std=1 on volume: W<low, L<mid, N<up, B<up+mid, VB=rest",
        "suffix_logic": {
            "NE": "E if close>high[1] or close<low[1] else N",
            "wick": "U if high>high[1], D if low<low[1], B if both, empty if neither"
        },
        "known_signal_registry": sorted(ALL_KNOWN_SIGNALS),
        "sequence_families_enabled": SEQUENCE_FAMILIES,
        "penetration_suffix_enabled": True,
        "penetration_suffix_logic": {
            "P": "current high inside previous upper wick zone (high >= prevBodyTop and high <= prev_high)",
            "R": "current low inside previous lower wick zone (low <= prevBodyBot and low >= prev_low)",
            "H": "both P and R true",
            "empty": "no wick-zone penetration",
        },
        "full_suffix_format": "ne_suffix + wick_suffix + penetration_suffix",
        "composite_state_enabled": True,
        "wick_behavior_analytics_enabled": True,
        "wick_extension_logic": {
            "U": "current high > previous high (liquidity sweep up)",
            "D": "current low < previous low (liquidity sweep down)",
            "B": "both high > previous high and low < previous low",
            "empty": "no wick extension beyond previous bar range",
        },
        "composite_label_format": "composite_core + full_suffix",
        "composite_core_format": "T_signal + L_signal (e.g. T3L25, Z4L64, L34, T4)",
        "base_sequence_scope": "multi-family (T, Z, L, PREUP, PREDN) — every signal on a bar emits an event",
        "composite_sequence_scope": "T/Z + L composite labels (full_label including suffixes)",
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "source_pine_script": "260506_TZ_F_WLNBB_CMB — Combined Oscillator",
    }


def generate_replay_zip(
    rows: List[dict],
    output_path: str = "replay_tz_wlnbb_analytics.zip",
    universe: str = "",
    tf: str = "",
    ticker_count: int = 0,
    audit: dict = None,
    nasdaq_batch: str = "",
) -> str:
    """Generate replay ZIP with all analytics CSVs. Returns output_path."""
    # rows already have forward returns baked in from stock_stat CSV
    sp = _signal_perf(rows)
    cp = _combo_perf(rows)
    sq = _sequence_perf_expanded(rows)
    suffix_perf = _suffix_perf(rows)
    cp_composite = _composite_perf(rows)
    wp = _wick_behavior_perf(rows)
    csp = _composite_sequence_perf(rows)
    wsp = _wick_sequence_perf(rows)

    # Price-bucketed analytics
    sp_pb  = _signal_perf_by_price_bucket(rows)
    cp_pb  = _composite_perf_by_price_bucket(rows)
    wp_pb  = _wick_behavior_perf_by_price_bucket(rows)
    sq_pb  = _sequence_perf_by_price_bucket(rows)
    csp_pb = _composite_sequence_perf_by_price_bucket(rows)
    wsp_pb = _wick_sequence_perf_by_price_bucket(rows)

    # Robust top/bad rankings (filter & sort by robust_score)
    def _robust_top_filter(coll):
        out = [r for r in coll
               if (r.get("count") or 0) >= ROBUST_MIN_COUNT
               and (r.get("median_ret_10d") or 0) > 0
               and (r.get("trimmed_avg_ret_10d") or 0) > 0
               and (r.get("fail_10d_rate") or 0) <= ROBUST_TOP_FAIL_RATE_MAX
               and (r.get("outlier_rate_10d") or 0) <= ROBUST_TOP_OUTLIER_RATE_MAX]
        out.sort(key=lambda x: -(x.get("robust_score") or 0))
        return out

    def _robust_bad_filter(coll):
        out = [r for r in coll
               if (r.get("count") or 0) >= ROBUST_MIN_COUNT
               and ((r.get("median_ret_10d") or 0) < 0 or (r.get("trimmed_avg_ret_10d") or 0) < 0)
               and (r.get("fail_10d_rate") or 0) >= ROBUST_BAD_FAIL_RATE_MIN]
        out.sort(key=lambda x: (x.get("robust_score") or 0))
        return out

    top_signals_robust       = _robust_top_filter(sp)
    top_composites_robust    = _robust_top_filter(cp_composite)
    top_wick_robust          = _robust_top_filter(wp)
    top_sequences_robust     = _robust_top_filter(sq)
    top_comp_seq_robust      = _robust_top_filter(csp)
    top_wick_seq_robust      = _robust_top_filter(wsp)

    bad_signals_robust       = _robust_bad_filter(sp)
    bad_composites_robust    = _robust_bad_filter(cp_composite)
    bad_wick_robust          = _robust_bad_filter(wp)
    bad_sequences_robust     = _robust_bad_filter(sq)
    bad_comp_seq_robust      = _robust_bad_filter(csp)
    bad_wick_seq_robust      = _robust_bad_filter(wsp)

    # Raw avg ranking (research only, count >= 30, sorted by -avg_ret_10d)
    def _raw_avg_top(coll):
        out = [r for r in coll
               if (r.get("count") or 0) >= ROBUST_MIN_COUNT]
        out.sort(key=lambda x: -(_safe_float(x.get("avg_ret_10d")) or 0))
        return out

    raw_avg_top_composites = _raw_avg_top(cp_composite)
    raw_avg_top_sequences  = _raw_avg_top(sq)
    raw_avg_top_wick       = _raw_avg_top(wp)

    top = [r for r in sp if (r.get("count") or 0) >= 30 and (_safe_float(r.get("avg_ret_10d")) or 0) > 0]
    top.sort(key=lambda x: -(_safe_float(x.get("avg_ret_10d")) or 0))

    bad = [r for r in sp if (_safe_float(r.get("avg_ret_10d")) or 0) < 0
           or (_safe_float(r.get("fail_10d_rate")) or 0) > 20]
    bad.sort(key=lambda x: (_safe_float(x.get("avg_ret_10d")) or 0))

    top_composites = [r for r in cp_composite if (r.get("count") or 0) >= 30 and (_safe_float(r.get("avg_ret_10d")) or 0) > 0]
    top_composites.sort(key=lambda x: -(_safe_float(x.get("avg_ret_10d")) or 0))
    MIN_RANK_COUNT = 30
    bad_composites = [r for r in cp_composite if (r.get("count") or 0) >= MIN_RANK_COUNT and ((_safe_float(r.get("avg_ret_10d")) or 0) < 0 or (_safe_float(r.get("fail_10d_rate")) or 0) > 20)]
    bad_composites.sort(key=lambda x: (_safe_float(x.get("avg_ret_10d")) or 0))

    top_composite_seqs = [r for r in csp if (r.get("count") or 0) >= MIN_RANK_COUNT and (_safe_float(r.get("avg_ret_10d")) or 0) > 0]
    top_composite_seqs.sort(key=lambda x: -(_safe_float(x.get("avg_ret_10d")) or 0))
    bad_composite_seqs = [r for r in csp if (r.get("count") or 0) >= MIN_RANK_COUNT and ((_safe_float(r.get("avg_ret_10d")) or 0) < 0 or (_safe_float(r.get("fail_10d_rate")) or 0) > 20)]
    bad_composite_seqs.sort(key=lambda x: (_safe_float(x.get("avg_ret_10d")) or 0))

    top_wick = [r for r in wp if (r.get("count") or 0) >= MIN_RANK_COUNT and (_safe_float(r.get("avg_ret_10d")) or 0) > 0]
    top_wick.sort(key=lambda x: -(_safe_float(x.get("avg_ret_10d")) or 0))
    bad_wick = [r for r in wp if (r.get("count") or 0) >= MIN_RANK_COUNT and ((_safe_float(r.get("avg_ret_10d")) or 0) < 0 or (_safe_float(r.get("fail_10d_rate")) or 0) > 20)]
    bad_wick.sort(key=lambda x: (_safe_float(x.get("avg_ret_10d")) or 0))

    suspicious = _suspicious_patterns(rows)
    validation_examples = _return_validation_examples(rows)
    unscored = _unscored_audit(rows)
    date_audit = _date_order_audit(rows)
    seq_event_audit = _sequence_event_audit(rows)
    invalid_suffix = _invalid_suffix_audit(rows)
    if invalid_suffix:
        log.warning(
            "TZ_WLNBB_SUFFIX_PARSE_WARNING: %d invalid full_suffix values found "
            "in composite labels — see replay_tz_wlnbb_invalid_suffix_audit.csv",
            sum(r["count"] for r in invalid_suffix),
        )
    meta = _build_metadata(rows, universe, tf, ticker_count, audit, nasdaq_batch=nasdaq_batch)
    config_snap = get_config_snapshot()

    # Attach date audit summary to metadata
    issue_tickers = [r["ticker"] for r in date_audit if r["status"] == "ISSUE"]
    meta["date_order_issues"] = len(issue_tickers)
    meta["date_order_issue_tickers_sample"] = issue_tickers[:10]

    # Ranking file counts and min-count threshold
    meta["min_count_for_rankings"] = MIN_RANK_COUNT
    meta["top_composites_count"] = len(top_composites)
    meta["bad_composites_count"] = len(bad_composites)
    meta["top_wick_patterns_count"] = len(top_wick)
    meta["bad_wick_patterns_count"] = len(bad_wick)
    meta["top_composite_sequences_count"] = len(top_composite_seqs)
    meta["bad_composite_sequences_count"] = len(bad_composite_seqs)

    meta["suspicious_pattern_count"] = len(suspicious)
    meta["robust_ranking_files"] = [
        "replay_tz_wlnbb_top_signals_robust.csv",
        "replay_tz_wlnbb_top_composites_robust.csv",
        "replay_tz_wlnbb_top_wick_patterns_robust.csv",
        "replay_tz_wlnbb_top_sequences_robust.csv",
        "replay_tz_wlnbb_top_composite_sequences_robust.csv",
        "replay_tz_wlnbb_top_wick_sequences_robust.csv",
        "replay_tz_wlnbb_bad_signals_robust.csv",
        "replay_tz_wlnbb_bad_composites_robust.csv",
        "replay_tz_wlnbb_bad_wick_patterns_robust.csv",
        "replay_tz_wlnbb_bad_sequences_robust.csv",
        "replay_tz_wlnbb_bad_composite_sequences_robust.csv",
        "replay_tz_wlnbb_bad_wick_sequences_robust.csv",
    ]
    meta["price_bucketed_files"] = [
        "replay_tz_wlnbb_signal_perf_by_price_bucket.csv",
        "replay_tz_wlnbb_composite_perf_by_price_bucket.csv",
        "replay_tz_wlnbb_wick_behavior_perf_by_price_bucket.csv",
        "replay_tz_wlnbb_sequence_perf_by_price_bucket.csv",
        "replay_tz_wlnbb_composite_sequence_perf_by_price_bucket.csv",
        "replay_tz_wlnbb_wick_sequence_perf_by_price_bucket.csv",
    ]
    meta["raw_avg_ranking_files"] = [
        "replay_tz_wlnbb_top_composites_raw_avg.csv",
        "replay_tz_wlnbb_top_sequences_raw_avg.csv",
        "replay_tz_wlnbb_top_wick_patterns_raw_avg.csv",
    ]

    def _to_csv_bytes(data: list, fields: list) -> bytes:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(data)
        return buf.getvalue().encode("utf-8")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        sp_fields = [
            "signal_type", "signal_name", "universe", "timeframe", "count",
            "avg_ret_1d", "avg_ret_3d", "avg_ret_5d", "avg_ret_10d",
            "median_ret_5d", "median_ret_10d", "big_win_10d_rate", "fail_10d_rate",
            "avg_mfe_10d", "avg_mae_10d", "tz_wlnbb_version",
        ]
        zf.writestr("replay_tz_wlnbb_signal_perf.csv", _to_csv_bytes(sp, sp_fields))

        cp_fields = [
            "t_signal", "z_signal", "l_signal", "preup_signal", "predn_signal",
            "ne_suffix", "wick_suffix", "penetration_suffix", "full_suffix", "count",
            "avg_ret_5d", "avg_ret_10d", "median_ret_5d", "median_ret_10d",
            "big_win_10d_rate", "fail_10d_rate", "avg_mfe_10d", "avg_mae_10d",
            "tz_wlnbb_version",
        ]
        zf.writestr("replay_tz_wlnbb_combo_perf.csv", _to_csv_bytes(cp, cp_fields))

        sq_fields = [
            "sequence_type", "sequence_family", "sequence_pattern",
            "bars_between", "bars_between_1", "bars_between_2",
            "universe", "timeframe", "count",
            "avg_ret_1d", "avg_ret_3d", "avg_ret_5d", "avg_ret_10d",
            "median_ret_5d", "median_ret_10d",
            "big_win_10d_rate", "fail_10d_rate", "avg_mfe_10d", "avg_mae_10d",
            "source_full_label", "confirmation_full_label",
            "source_full_suffix", "confirmation_full_suffix",
        ]
        zf.writestr("replay_tz_wlnbb_sequence_perf.csv", _to_csv_bytes(sq, sq_fields))

        suffix_perf_fields = [
            "signal_type", "signal_name", "ne_suffix", "wick_suffix", "penetration_suffix",
            "full_suffix", "universe", "timeframe", "count",
            "avg_ret_1d", "avg_ret_3d", "avg_ret_5d", "avg_ret_10d",
            "median_ret_5d", "median_ret_10d", "big_win_10d_rate", "fail_10d_rate",
            "avg_mfe_10d", "avg_mae_10d",
        ]
        zf.writestr("replay_tz_wlnbb_suffix_perf.csv", _to_csv_bytes(suffix_perf, suffix_perf_fields))

        zf.writestr("replay_tz_wlnbb_top_patterns.csv",  _to_csv_bytes(top, sp_fields))
        zf.writestr("replay_tz_wlnbb_bad_patterns.csv",  _to_csv_bytes(bad, sp_fields))

        unscored_fields = [
            "raw_signal", "normalized_signal", "source_column",
            "count", "example_tickers", "example_dates",
        ]
        zf.writestr("replay_tz_wlnbb_unscored_or_unknown.csv",
                    _to_csv_bytes(unscored, unscored_fields))

        val_fields = [
            "ticker", "date", "date_plus_1", "date_plus_3", "date_plus_5", "date_plus_10",
            "close", "ret_1d", "ret_3d", "ret_5d", "ret_10d",
            "max_high_5d", "max_high_10d", "max_drawdown_5d", "max_drawdown_10d",
            "mfe_5d", "mfe_10d", "mae_5d", "mae_10d",
            "big_win_10d", "fail_10d", "t_signal", "z_signal", "l_signal",
        ]
        zf.writestr("replay_tz_wlnbb_return_validation_examples.csv",
                    _to_csv_bytes(validation_examples, val_fields))

        date_audit_fields = [
            "ticker", "total_rows", "invalid_date_format", "out_of_order_count",
            "first_date", "last_date", "status",
        ]
        zf.writestr("replay_tz_wlnbb_date_order_audit.csv",
                    _to_csv_bytes(date_audit, date_audit_fields))

        zf.writestr("replay_tz_wlnbb_metadata.json",
                    json.dumps(meta, indent=2))

        zf.writestr("tz_wlnbb_config_snapshot.json",
                    json.dumps(config_snap, indent=2))

        composite_fields = [
            "composite_full_label", "composite_core", "composite_full_suffix",
            "t_signal", "z_signal", "l_signal", "ne_suffix", "wick_suffix",
            "penetration_suffix", "full_suffix", "universe", "timeframe", "count",
            "avg_ret_1d", "avg_ret_3d", "avg_ret_5d", "avg_ret_10d",
            "median_ret_5d", "median_ret_10d", "big_win_10d_rate", "fail_10d_rate",
            "avg_mfe_10d", "avg_mae_10d", "reward_risk_ratio",
        ]
        zf.writestr("replay_tz_wlnbb_composite_perf.csv",
                    _to_csv_bytes(cp_composite, composite_fields))
        zf.writestr("replay_tz_wlnbb_top_composites.csv",
                    _to_csv_bytes(top_composites, composite_fields))
        zf.writestr("replay_tz_wlnbb_bad_composites.csv",
                    _to_csv_bytes(bad_composites, composite_fields))

        wick_fields = [
            "wick_suffix", "penetration_suffix", "full_suffix", "ne_suffix",
            "t_signal", "z_signal", "l_signal", "preup_signal", "predn_signal",
            "universe", "timeframe", "count",
            "avg_ret_1d", "avg_ret_3d", "avg_ret_5d", "avg_ret_10d",
            "median_ret_5d", "median_ret_10d", "big_win_10d_rate", "fail_10d_rate",
            "avg_mfe_10d", "avg_mae_10d", "reward_risk_ratio",
        ]
        zf.writestr("replay_tz_wlnbb_wick_behavior_perf.csv",
                    _to_csv_bytes(wp, wick_fields))
        zf.writestr("replay_tz_wlnbb_top_wick_patterns.csv",
                    _to_csv_bytes(top_wick, wick_fields))
        zf.writestr("replay_tz_wlnbb_bad_wick_patterns.csv",
                    _to_csv_bytes(bad_wick, wick_fields))

        cseq_fields = [
            "sequence_type", "composite_sequence_pattern", "base_sequence_pattern",
            "bars_between", "bars_between_1", "bars_between_2",
            "universe", "timeframe", "count",
            "source_full_label", "confirmation_full_label",
            "source_full_suffix", "confirmation_full_suffix",
            "source_wick_suffix", "source_penetration_suffix",
            "confirmation_wick_suffix", "confirmation_penetration_suffix",
            "avg_ret_1d", "avg_ret_3d", "avg_ret_5d", "avg_ret_10d",
            "median_ret_5d", "median_ret_10d", "big_win_10d_rate", "fail_10d_rate",
            "avg_mfe_10d", "avg_mae_10d", "reward_risk_ratio",
        ]
        zf.writestr("replay_tz_wlnbb_composite_sequence_perf.csv",
                    _to_csv_bytes(csp, cseq_fields))
        zf.writestr("replay_tz_wlnbb_top_composite_sequences.csv",
                    _to_csv_bytes(top_composite_seqs, cseq_fields))
        zf.writestr("replay_tz_wlnbb_bad_composite_sequences.csv",
                    _to_csv_bytes(bad_composite_seqs, cseq_fields))

        wseq_fields = [
            "wick_sequence_pattern", "base_signal_context", "bars_between",
            "universe", "timeframe", "count",
            "avg_ret_1d", "avg_ret_3d", "avg_ret_5d", "avg_ret_10d",
            "median_ret_5d", "median_ret_10d", "big_win_10d_rate", "fail_10d_rate",
            "avg_mfe_10d", "avg_mae_10d", "reward_risk_ratio",
        ]
        zf.writestr("replay_tz_wlnbb_wick_sequence_perf.csv",
                    _to_csv_bytes(wsp, wseq_fields))

        suspicious_fields = [
            "pattern_type", "pattern_name", "universe", "nasdaq_batch", "price_bucket",
            "count", "avg_ret_10d", "median_ret_10d",
            "trimmed_avg_ret_10d", "winsorized_avg_ret_10d",
            "max_ret_10d", "avg_vs_median_gap_10d",
            "outlier_count_10d", "outlier_rate_10d",
            "example_tickers", "example_dates",
            "example_close_prices", "example_ret_10d_values",
        ]
        if suspicious:
            zf.writestr("replay_tz_wlnbb_suspicious_return_patterns.csv",
                        _to_csv_bytes(suspicious, suspicious_fields))

        # ── Price-bucketed perf CSVs ────────────────────────────────────────
        sp_pb_fields = [
            "signal_type", "signal_name", "universe", "timeframe", "price_bucket",
        ] + PERF_METRIC_COLS + ["tz_wlnbb_version"]
        zf.writestr("replay_tz_wlnbb_signal_perf_by_price_bucket.csv",
                    _to_csv_bytes(sp_pb, sp_pb_fields))

        cp_pb_fields = [
            "composite_full_label", "composite_core", "composite_full_suffix",
            "t_signal", "z_signal", "l_signal",
            "ne_suffix", "wick_suffix", "penetration_suffix", "full_suffix",
            "universe", "timeframe", "price_bucket",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_composite_perf_by_price_bucket.csv",
                    _to_csv_bytes(cp_pb, cp_pb_fields))

        wp_pb_fields = [
            "wick_suffix", "penetration_suffix", "full_suffix", "ne_suffix",
            "t_signal", "z_signal", "l_signal", "preup_signal", "predn_signal",
            "universe", "timeframe", "price_bucket",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_wick_behavior_perf_by_price_bucket.csv",
                    _to_csv_bytes(wp_pb, wp_pb_fields))

        sq_pb_fields = [
            "sequence_type", "sequence_family", "sequence_pattern",
            "bars_between", "bars_between_1", "bars_between_2",
            "universe", "timeframe", "price_bucket",
            "source_full_label", "confirmation_full_label",
            "source_full_suffix", "confirmation_full_suffix",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_sequence_perf_by_price_bucket.csv",
                    _to_csv_bytes(sq_pb, sq_pb_fields))

        csp_pb_fields = [
            "sequence_type", "composite_sequence_pattern", "base_sequence_pattern",
            "bars_between", "bars_between_1", "bars_between_2",
            "universe", "timeframe", "price_bucket",
            "source_full_label", "confirmation_full_label",
            "source_full_suffix", "confirmation_full_suffix",
            "source_wick_suffix", "source_penetration_suffix",
            "confirmation_wick_suffix", "confirmation_penetration_suffix",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_composite_sequence_perf_by_price_bucket.csv",
                    _to_csv_bytes(csp_pb, csp_pb_fields))

        wsp_pb_fields = [
            "wick_sequence_pattern", "base_signal_context", "bars_between",
            "universe", "timeframe", "price_bucket",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_wick_sequence_perf_by_price_bucket.csv",
                    _to_csv_bytes(wsp_pb, wsp_pb_fields))

        # ── Robust top/bad rankings ─────────────────────────────────────────
        sp_robust_fields = [
            "signal_type", "signal_name", "universe", "timeframe",
        ] + PERF_METRIC_COLS + ["tz_wlnbb_version"]
        zf.writestr("replay_tz_wlnbb_top_signals_robust.csv",
                    _to_csv_bytes(top_signals_robust, sp_robust_fields))
        zf.writestr("replay_tz_wlnbb_bad_signals_robust.csv",
                    _to_csv_bytes(bad_signals_robust, sp_robust_fields))

        comp_robust_fields = [
            "composite_full_label", "composite_core", "composite_full_suffix",
            "t_signal", "z_signal", "l_signal",
            "ne_suffix", "wick_suffix", "penetration_suffix", "full_suffix",
            "universe", "timeframe",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_top_composites_robust.csv",
                    _to_csv_bytes(top_composites_robust, comp_robust_fields))
        zf.writestr("replay_tz_wlnbb_bad_composites_robust.csv",
                    _to_csv_bytes(bad_composites_robust, comp_robust_fields))

        wick_robust_fields = [
            "wick_suffix", "penetration_suffix", "full_suffix", "ne_suffix",
            "t_signal", "z_signal", "l_signal", "preup_signal", "predn_signal",
            "universe", "timeframe",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_top_wick_patterns_robust.csv",
                    _to_csv_bytes(top_wick_robust, wick_robust_fields))
        zf.writestr("replay_tz_wlnbb_bad_wick_patterns_robust.csv",
                    _to_csv_bytes(bad_wick_robust, wick_robust_fields))

        seq_robust_fields = [
            "sequence_type", "sequence_family", "sequence_pattern",
            "bars_between", "bars_between_1", "bars_between_2",
            "universe", "timeframe",
            "source_full_label", "confirmation_full_label",
            "source_full_suffix", "confirmation_full_suffix",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_top_sequences_robust.csv",
                    _to_csv_bytes(top_sequences_robust, seq_robust_fields))
        zf.writestr("replay_tz_wlnbb_bad_sequences_robust.csv",
                    _to_csv_bytes(bad_sequences_robust, seq_robust_fields))

        cseq_robust_fields = [
            "sequence_type", "composite_sequence_pattern", "base_sequence_pattern",
            "bars_between", "bars_between_1", "bars_between_2",
            "universe", "timeframe",
            "source_full_label", "confirmation_full_label",
            "source_full_suffix", "confirmation_full_suffix",
            "source_wick_suffix", "source_penetration_suffix",
            "confirmation_wick_suffix", "confirmation_penetration_suffix",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_top_composite_sequences_robust.csv",
                    _to_csv_bytes(top_comp_seq_robust, cseq_robust_fields))
        zf.writestr("replay_tz_wlnbb_bad_composite_sequences_robust.csv",
                    _to_csv_bytes(bad_comp_seq_robust, cseq_robust_fields))

        wseq_robust_fields = [
            "wick_sequence_pattern", "base_signal_context", "bars_between",
            "universe", "timeframe",
        ] + PERF_METRIC_COLS
        zf.writestr("replay_tz_wlnbb_top_wick_sequences_robust.csv",
                    _to_csv_bytes(top_wick_seq_robust, wseq_robust_fields))
        zf.writestr("replay_tz_wlnbb_bad_wick_sequences_robust.csv",
                    _to_csv_bytes(bad_wick_seq_robust, wseq_robust_fields))

        # ── Raw avg research files ──────────────────────────────────────────
        zf.writestr("replay_tz_wlnbb_top_composites_raw_avg.csv",
                    _to_csv_bytes(raw_avg_top_composites, comp_robust_fields))
        zf.writestr("replay_tz_wlnbb_top_sequences_raw_avg.csv",
                    _to_csv_bytes(raw_avg_top_sequences, seq_robust_fields))
        zf.writestr("replay_tz_wlnbb_top_wick_patterns_raw_avg.csv",
                    _to_csv_bytes(raw_avg_top_wick, wick_robust_fields))

        seq_audit_fields = [
            "family", "signal_name", "count", "rows_with_signal",
            "used_in_sequence_count", "used_in_sequence_rate",
        ]
        zf.writestr("replay_tz_wlnbb_sequence_event_audit.csv",
                    _to_csv_bytes(seq_event_audit, seq_audit_fields))

        invalid_suffix_fields = [
            "field_name", "invalid_suffix", "source_label",
            "ticker", "date", "count",
        ]
        zf.writestr("replay_tz_wlnbb_invalid_suffix_audit.csv",
                    _to_csv_bytes(invalid_suffix, invalid_suffix_fields))

    return output_path
