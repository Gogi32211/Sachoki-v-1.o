"""
signal_event_extractor.py — extracts replay_signal_events rows from a series
of fully-enriched bar dicts (output of `api_bar_signals(ticker, "1d", N)`).

Public API
──────────
extract_events(bars, scan_idx, *, ticker, universe, replay_run_id) -> list[dict]
    Builds one or more event rows for the bar at index `scan_idx`.
    Uses ONLY bars[: scan_idx + 1] (causal); never reads forward.

Each returned dict has the column set of replay_signal_events.
The caller is responsible for inserting into the DB.

Design notes
────────────
A single bar may produce multiple events (one per active signal). All events
from a single bar share the same context (sequence, price position, EMAs,
ABR, role, etc.) — only event_signal / family / type / direction differ.
"""
from __future__ import annotations
import json
import math
from typing import Any, Iterable

# ─── Signal direction table ───────────────────────────────────────────────────
# Best-effort labelling. Stats reveal the truth; this is just a UX label.
_DIRECTION: dict[str, str] = {
    # T family (bullish bias on Tx; T2/T4 mixed)
    "T1": "bullish", "T1G": "bullish", "T2G": "bullish", "T9": "bullish",
    "T3": "neutral", "T5": "neutral", "T10": "neutral",
    "T11": "bullish", "T12": "neutral", "T2": "bearish", "T4": "bearish",
    "T6": "bearish",
    # Z family — mostly setup, direction depends on context
    "Z1": "neutral", "Z1G": "bullish", "Z2": "neutral", "Z2G": "bullish",
    "Z3": "neutral", "Z4": "bearish", "Z5": "neutral", "Z6": "bearish",
    "Z7": "neutral", "Z9": "bullish", "Z10": "neutral", "Z11": "bullish",
    "Z12": "neutral",
    # F family
    "F1": "bullish", "F5": "bullish", "F7": "bullish", "F9": "bullish",
    "F10": "bullish",
    "F2": "bearish", "F4": "bearish", "F6": "bearish", "F8": "bearish",
    "F11": "bearish",
}

_T_PREFIX = ("T1", "T2", "T3", "T4", "T5", "T6", "T9", "T10", "T11", "T12")
_Z_PREFIX = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z9", "Z10", "Z11", "Z12")


def _f(v, default=None):
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _bar_active_tz(bar: dict) -> str:
    tz = str(bar.get("tz") or "")
    if tz.startswith(("T", "Z")):
        return tz
    return ""


def _direction(sig: str) -> str:
    if sig in _DIRECTION:
        return _DIRECTION[sig]
    if sig.startswith("T"):
        return "neutral"
    if sig.startswith("Z"):
        return "neutral"
    if sig.startswith("F"):
        return "neutral"
    return "unknown"


def _family_for(sig: str) -> str:
    if sig.startswith("T") and (sig in _DIRECTION or sig[:2] in _T_PREFIX):
        return "T"
    if sig.startswith("Z") and (sig in _DIRECTION or sig[:2] in _Z_PREFIX):
        return "Z"
    if sig.startswith("F"):
        return "F"
    if sig.startswith("G"):
        return "G"
    if sig.startswith("B"):
        return "B"
    if sig.startswith("L"):
        return "L"
    if sig.startswith(("P", "preup", "predn")):
        return "EMA"
    if sig in ("VOLUME_BURST", "VOLUME_EXPANSION", "VOLUME_DRYUP",
              "HIGH_VOLUME_BREAKOUT", "HIGH_VOLUME_FAILURE", "LOW_VOLUME_PULLBACK"):
        return "VOLUME"
    if sig.startswith("CLOSE_"):
        return "PRICE_POSITION"
    if sig in ("GO", "FORMING", "READY", "WATCH_T", "WATCH_Z", "REJECT"):
        return "ROLE"
    if sig.startswith("SCORE_"):
        return "SCORE"
    return "OTHER"


# ─── EMA / ATR helpers (compute from bars; no lookahead) ──────────────────────

def _ema_series(closes: list[float], period: int) -> list[float]:
    if not closes or period <= 0:
        return [float("nan")] * len(closes)
    k = 2.0 / (period + 1)
    out: list[float] = []
    ema_val = float("nan")
    for i, c in enumerate(closes):
        if i < period - 1:
            out.append(float("nan"))
            continue
        if math.isnan(ema_val):
            ema_val = sum(closes[i - period + 1 : i + 1]) / period
        else:
            ema_val = c * k + ema_val * (1 - k)
        out.append(ema_val)
    return out


def _ema_state(close: float, ema_now: float, ema_prev: float) -> tuple[str, bool]:
    """Returns (state_label, reclaim_bool). reclaim = price crossed above ema this bar."""
    if math.isnan(ema_now) or math.isnan(ema_prev) or close is None:
        return ("unknown", False)
    above = close > ema_now
    # reclaim if previously below ema and now above
    prev_close_above = False  # we don't have prev close here; reclaim approximated below
    state = "above" if above else "below"
    return (state, above and not prev_close_above)


def _atr_pct(bars: list[dict], i: int, period: int = 14) -> float | None:
    if i < period:
        return None
    trs: list[float] = []
    for j in range(i - period + 1, i + 1):
        h = _f(bars[j].get("high"))
        l = _f(bars[j].get("low"))
        pc = _f(bars[j - 1].get("close")) if j > 0 else None
        if h is None or l is None:
            return None
        tr = h - l
        if pc is not None:
            tr = max(tr, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if not trs:
        return None
    atr = sum(trs) / len(trs)
    c = _f(bars[i].get("close"))
    if not c or c == 0:
        return None
    return round(atr / c * 100, 4)


def _bucketize(value: float | None, edges: list[tuple[float, str]], default: str = "unknown") -> str:
    if value is None:
        return default
    for upper, label in edges:
        if value < upper:
            return label
    return edges[-1][1] if edges else default


def _price_position(bars: list[dict], i: int, lookback: int) -> tuple[float | None, str]:
    if i < lookback - 1:
        return (None, "INSUFFICIENT_HISTORY")
    hi = max((_f(b.get("high")) or -1e18) for b in bars[i - lookback + 1 : i + 1])
    lo = min((_f(b.get("low"))  or  1e18) for b in bars[i - lookback + 1 : i + 1])
    c = _f(bars[i].get("close"))
    if c is None or hi <= lo:
        return (None, "unknown")
    pos = (c - lo) / (hi - lo)
    pos = max(0.0, min(1.0, pos))
    if pos >= 0.90:   bucket = "top_10"
    elif pos >= 0.75: bucket = "top_25"
    elif pos >= 0.50: bucket = "upper_half"
    elif pos >= 0.25: bucket = "lower_half"
    else:             bucket = "bottom_25"
    return (round(pos, 4), bucket)


def _relative_volume(bars: list[dict], i: int, lookback: int = 20) -> float | None:
    if i < lookback:
        return None
    avg = sum((_f(b.get("volume")) or 0) for b in bars[i - lookback : i]) / lookback
    if avg <= 0:
        return None
    v = _f(bars[i].get("volume")) or 0
    return round(v / avg, 4) if avg else None


def _volume_bucket(rv: float | None) -> str:
    if rv is None: return "unknown"
    if rv >= 5:   return "explosive"
    if rv >= 3:   return "very_high"
    if rv >= 2:   return "high"
    if rv >= 1.2: return "above_avg"
    if rv >= 0.8: return "normal"
    if rv >= 0.5: return "low"
    return "very_low"


def _candle_geometry(bar: dict) -> dict:
    o = _f(bar.get("open")); h = _f(bar.get("high"))
    l = _f(bar.get("low"));  c = _f(bar.get("close"))
    if None in (o, h, l, c):
        return {"body_pct": None, "upper_wick_pct": None, "lower_wick_pct": None,
                "range_pct": None, "candle_color": "unknown"}
    rng = h - l if h > l else 0
    if rng == 0:
        return {"body_pct": 0.0, "upper_wick_pct": 0.0, "lower_wick_pct": 0.0,
                "range_pct": 0.0, "candle_color": "doji"}
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    color = "green" if c > o else "red" if c < o else "doji"
    return {
        "body_pct":       round(body / rng * 100, 4),
        "upper_wick_pct": round(upper / rng * 100, 4),
        "lower_wick_pct": round(lower / rng * 100, 4),
        "range_pct":      round(rng / c * 100, 4) if c else None,
        "candle_color":   color,
    }


def _gap_pct(bars: list[dict], i: int) -> float | None:
    if i == 0:
        return None
    prev_c = _f(bars[i - 1].get("close"))
    o = _f(bars[i].get("open"))
    if not prev_c or o is None:
        return None
    return round((o - prev_c) / prev_c * 100, 4)


def _score_bucket(score: float | None) -> str:
    if score is None:        return "unknown"
    if score >= 90:          return "90+"
    if score >= 75:          return "75-89"
    if score >= 60:          return "60-74"
    if score >= 45:          return "45-59"
    if score >= 35:          return "35-45"
    if score >= 20:          return "20-35"
    return "0-20"


# ─── Sequence + previous-signal context ───────────────────────────────────────

def _previous_tz_sequence(bars: list[dict], scan_idx: int, n: int) -> list[str]:
    """Return last n T/Z signals ending at scan_idx (oldest first).
    Pads with '' if fewer than n bars have a T/Z signal in the lookback window."""
    out: list[str] = []
    j = scan_idx
    while j >= 0 and len(out) < n:
        s = _bar_active_tz(bars[j])
        if s:
            out.append(s)
        j -= 1
    out.reverse()
    while len(out) < n:
        out.insert(0, "")
    return out


def _previous_signals(bars: list[dict], scan_idx: int, n: int) -> list[str]:
    """prev1..prevN — the T/Z signal from the bar 1..N bars before scan_idx
    (or '' if that bar had no T/Z). Index 0 = 1 bar ago."""
    out: list[str] = []
    for k in range(1, n + 1):
        j = scan_idx - k
        out.append(_bar_active_tz(bars[j]) if j >= 0 else "")
    return out


def _count_last_n(bars: list[dict], scan_idx: int, n: int, prefix: str) -> int:
    """Count bars in [scan_idx-n+1 .. scan_idx] whose tz starts with prefix."""
    start = max(0, scan_idx - n + 1)
    cnt = 0
    for b in bars[start : scan_idx + 1]:
        if str(b.get("tz") or "").startswith(prefix):
            cnt += 1
    return cnt


def _last_signal_and_days(bars: list[dict], scan_idx: int, prefix: str) -> tuple[str, int | None]:
    j = scan_idx
    while j >= 0:
        tz = str(bars[j].get("tz") or "")
        if tz.startswith(prefix):
            return (tz, scan_idx - j)
        j -= 1
    return ("", None)


def _signals_window_json(bars: list[dict], scan_idx: int, n: int) -> str:
    start = max(0, scan_idx - n + 1)
    snapshot = []
    for b in bars[start : scan_idx + 1]:
        snapshot.append({
            "date": b.get("date"),
            "tz":   b.get("tz") or "",
            "l":    list(b.get("l") or []),
            "f":    list(b.get("f") or []),
            "combo": list(b.get("combo") or []),
        })
    return json.dumps(snapshot, default=str)


def _had_in_last_n(bars: list[dict], scan_idx: int, n: int, predicate) -> bool:
    start = max(0, scan_idx - n + 1)
    return any(predicate(b) for b in bars[start : scan_idx + 1])


# ─── Active-signal enumeration for a bar ──────────────────────────────────────

def _active_signals_for_bar(bar: dict) -> list[tuple[str, str, str]]:
    """Yields (event_signal, event_signal_family, event_signal_type) tuples.
    Includes the bar's T/Z, every L/F/G/B/combo item, plus EMA/role events."""
    out: list[tuple[str, str, str]] = []

    tz = str(bar.get("tz") or "")
    if tz.startswith(("T", "Z")):
        fam = "T" if tz.startswith("T") else "Z"
        out.append((tz, fam, tz))

    for s in (bar.get("l") or []):
        ss = str(s)
        if ss:
            out.append((ss if ss.startswith("L") else f"L{ss}", "L", ss))

    for s in (bar.get("f") or []):
        ss = str(s).upper()
        if ss:
            out.append((ss if ss.startswith("F") else f"F{ss}", "F", ss))

    for s in (bar.get("g") or []):
        ss = str(s).upper()
        if ss:
            out.append((ss if ss.startswith("G") else f"G{ss}", "G", ss))

    for s in (bar.get("b") or []):
        ss = str(s).upper()
        if ss:
            out.append((ss if ss.startswith("B") else f"B{ss}", "B", ss))

    for s in (bar.get("combo") or []):
        ss = str(s)
        if not ss:
            continue
        u = ss.upper()
        if u.startswith("PREUP") or u in ("P50", "P89", "P3", "P2"):
            out.append((u, "EMA", u))
        elif u.startswith("PREDN"):
            out.append((u, "EMA", u))
        else:
            out.append((u, "COMBO", u))

    # Role / matched_status
    role = bar.get("role") or bar.get("matched_status") or ""
    if role:
        r = str(role).upper()
        if r in ("GO", "FORMING", "READY", "WATCH_T", "WATCH_Z", "REJECT"):
            out.append((r, "ROLE", r))

    return out


# ─── Public API ───────────────────────────────────────────────────────────────

def extract_events(
    bars: list[dict],
    scan_idx: int,
    *,
    ticker: str,
    universe: str,
    replay_run_id: int,
) -> list[dict]:
    """Build one or more event-row dicts for bars[scan_idx]. No lookahead.

    Returns [] if scan_idx is out of range, the bar has no active signals,
    or insufficient history exists for context (<10 bars before scan_idx).
    """
    if scan_idx < 0 or scan_idx >= len(bars):
        return []
    bar = bars[scan_idx]

    actives = _active_signals_for_bar(bar)
    if not actives:
        return []

    # ── Context (computed once per bar; shared across this bar's events) ──
    closes = [_f(b.get("close"), 0.0) for b in bars[: scan_idx + 1]]
    ema20  = _ema_series(closes, 20)
    ema50  = _ema_series(closes, 50)
    ema89  = _ema_series(closes, 89)
    ema200 = _ema_series(closes, 200)

    cur = closes[-1] if closes else None
    prev_cur = closes[-2] if len(closes) >= 2 else None

    def _state_and_reclaim(ema_arr: list[float]) -> tuple[str, bool, bool]:
        if not ema_arr or math.isnan(ema_arr[-1]):
            return ("INSUFFICIENT_HISTORY", False, False)
        if cur is None:
            return ("unknown", False, False)
        now = ema_arr[-1]
        prev = ema_arr[-2] if len(ema_arr) >= 2 else float("nan")
        above_now = cur > now
        above_prev = (prev_cur is not None and not math.isnan(prev) and prev_cur > prev)
        state = "above" if above_now else "below"
        reclaim = above_now and not above_prev
        return (state, reclaim, above_now)

    ema20_state,  reclaim_20,  above_20  = _state_and_reclaim(ema20)
    ema50_state,  reclaim_50,  above_50  = _state_and_reclaim(ema50)
    ema89_state,  reclaim_89,  above_89  = _state_and_reclaim(ema89)
    ema200_state, reclaim_200, above_200 = _state_and_reclaim(ema200)

    # Track which indicator fields lacked sufficient bars
    _ih = "INSUFFICIENT_HISTORY"
    insufficient_history_fields: list[str] = []
    if ema20_state  == _ih: insufficient_history_fields.append("ema20")
    if ema50_state  == _ih: insufficient_history_fields.append("ema50")
    if ema89_state  == _ih: insufficient_history_fields.append("ema89")
    if ema200_state == _ih: insufficient_history_fields.append("ema200")

    pos_4,  buck_4  = _price_position(bars, scan_idx, 4)
    pos_10, buck_10 = _price_position(bars, scan_idx, 10)
    pos_20, buck_20 = _price_position(bars, scan_idx, 20)
    pos_50, buck_50 = _price_position(bars, scan_idx, 50)
    if buck_50 == _ih: insufficient_history_fields.append("price_pos_50bar")

    rv = _relative_volume(bars, scan_idx, 20)
    vol_bucket = _volume_bucket(rv)
    geom = _candle_geometry(bar)
    atr_p = _atr_pct(bars, scan_idx, 14)
    vol_b = _bucketize(atr_p, [(2.0, "low"), (4.0, "normal"), (8.0, "high")], "very_high")

    dollar_v = (_f(bar.get("close")) or 0) * (_f(bar.get("volume")) or 0)
    dv_bucket = _bucketize(
        dollar_v,
        [(1e6, "thin"), (1e7, "low"), (1e8, "mid"), (1e9, "high")],
        "mega",
    )

    prev_tz = _previous_signals(bars, scan_idx, 10)
    sequence_2  = " -> ".join(s for s in _previous_tz_sequence(bars, scan_idx, 2)  if s)
    sequence_3  = " -> ".join(s for s in _previous_tz_sequence(bars, scan_idx, 3)  if s)
    sequence_4  = " -> ".join(s for s in _previous_tz_sequence(bars, scan_idx, 4)  if s)
    sequence_5  = " -> ".join(s for s in _previous_tz_sequence(bars, scan_idx, 5)  if s)
    sequence_7  = " -> ".join(s for s in _previous_tz_sequence(bars, scan_idx, 7)  if s)
    sequence_10 = " -> ".join(s for s in _previous_tz_sequence(bars, scan_idx, 10) if s)

    last_t, days_t = _last_signal_and_days(bars, scan_idx, "T")
    last_z, days_z = _last_signal_and_days(bars, scan_idx, "Z")

    t_cnt_3  = _count_last_n(bars, scan_idx, 3,  "T")
    t_cnt_5  = _count_last_n(bars, scan_idx, 5,  "T")
    t_cnt_10 = _count_last_n(bars, scan_idx, 10, "T")
    z_cnt_3  = _count_last_n(bars, scan_idx, 3,  "Z")
    z_cnt_5  = _count_last_n(bars, scan_idx, 5,  "Z")
    z_cnt_10 = _count_last_n(bars, scan_idx, 10, "Z")

    had_wlnbb_l_5 = _had_in_last_n(
        bars, scan_idx, 5,
        lambda b: any(str(x).upper().startswith("L") for x in (b.get("l") or [])),
    )
    had_vol_burst_5 = _had_in_last_n(
        bars, scan_idx, 5,
        lambda b: any(str(x).upper() in ("VOLUME_BURST", "VOL5X", "VOL10X", "VOL20X")
                      for x in (b.get("vol") or b.get("combo") or [])),
    )
    had_ema50_reclaim_5 = _had_in_last_n(
        bars, scan_idx, 5,
        lambda b: any(str(x).upper() in ("PREUP50", "P50") for x in (b.get("combo") or [])),
    )
    had_pullback_5 = _had_in_last_n(
        bars, scan_idx, 5,
        lambda b: (_f(b.get("close"), 0) or 0) < (_f(b.get("open"), 0) or 0)
                  and (_f(b.get("volume"), 0) or 0) > 0,
    )

    # ABR fields — pull from bar if available
    abr_category = str(bar.get("abr_category") or bar.get("ABR_CATEGORY") or "") or None
    abr = bar.get("abr") or {}
    score_val = (
        _f(bar.get("turbo_score"))
        if bar.get("turbo_score") is not None
        else _f(bar.get("score"))
    )

    final_signal = _bar_active_tz(bar)  # canonical T/Z for this bar
    snapshot = {
        "tz":    bar.get("tz"),
        "l":     list(bar.get("l")  or []),
        "f":     list(bar.get("f")  or []),
        "g":     list(bar.get("g")  or []),
        "b":     list(bar.get("b")  or []),
        "combo": list(bar.get("combo") or []),
        "vol":   list(bar.get("vol") or []),
        "wick":  list(bar.get("wick") or []),
        "turbo_score":   bar.get("turbo_score"),
        "ultra_score":   bar.get("ultra_score"),
        "beta_score":    bar.get("beta_score"),
        "beta_zone":     bar.get("beta_zone"),
        "abr_category":  bar.get("abr_category"),
        "profile_category": bar.get("profile_category"),
        "FINAL_REGIME":  bar.get("FINAL_REGIME"),
    }
    snapshot_json = json.dumps(snapshot, default=str)

    base = {
        "replay_run_id":    replay_run_id,
        "scan_date":        str(bar.get("date") or "")[:10],
        "symbol":           ticker,
        "universe":         universe,
        "timeframe":        "1d",
        "open":   _f(bar.get("open")), "high":   _f(bar.get("high")),
        "low":    _f(bar.get("low")),  "close":  _f(bar.get("close")),
        "volume": int(_f(bar.get("volume"), 0) or 0),
        "dollar_volume": round(dollar_v, 2) if dollar_v else 0,
        "final_signal": final_signal or None,
        "raw_signal":   final_signal or None,
        "tz_signal":    final_signal or None,
        "prev1_signal":  prev_tz[0] or None,
        "prev2_signal":  prev_tz[1] or None,
        "prev3_signal":  prev_tz[2] or None,
        "prev4_signal":  prev_tz[3] or None,
        "prev5_signal":  prev_tz[4] or None,
        "prev6_signal":  prev_tz[5] or None,
        "prev7_signal":  prev_tz[6] or None,
        "prev8_signal":  prev_tz[7] or None,
        "prev9_signal":  prev_tz[8] or None,
        "prev10_signal": prev_tz[9] or None,
        "sequence_2bar":  sequence_2  or None,
        "sequence_3bar":  sequence_3  or None,
        "sequence_4bar":  sequence_4  or None,
        "sequence_5bar":  sequence_5  or None,
        "sequence_7bar":  sequence_7  or None,
        "sequence_10bar": sequence_10 or None,
        "signals_last_3d_json":  _signals_window_json(bars, scan_idx, 3),
        "signals_last_5d_json":  _signals_window_json(bars, scan_idx, 5),
        "signals_last_10d_json": _signals_window_json(bars, scan_idx, 10),
        "t_signals_last_3d_count":  t_cnt_3,
        "t_signals_last_5d_count":  t_cnt_5,
        "t_signals_last_10d_count": t_cnt_10,
        "z_signals_last_3d_count":  z_cnt_3,
        "z_signals_last_5d_count":  z_cnt_5,
        "z_signals_last_10d_count": z_cnt_10,
        "last_t_signal":     last_t or None,
        "last_z_signal":     last_z or None,
        "days_since_last_t": days_t,
        "days_since_last_z": days_z,
        "had_t_last_3d":  t_cnt_3 > 0,
        "had_z_last_3d":  z_cnt_3 > 0,
        "had_t_last_5d":  t_cnt_5 > 0,
        "had_z_last_5d":  z_cnt_5 > 0,
        "had_wlnbb_l_last_5d":      had_wlnbb_l_5,
        "had_volume_burst_last_5d": had_vol_burst_5,
        "had_ema50_reclaim_last_5d": had_ema50_reclaim_5,
        "had_pullback_before_signal": had_pullback_5,
        "wlnbb_bucket":   "L" if had_wlnbb_l_5 else None,
        "l_signal":       " ".join(str(x) for x in (bar.get("l") or [])) or None,
        "volume_bucket":  vol_bucket,
        "wick_suffix":    (str(bar.get("wick") or [None])[0] if (bar.get("wick") or None) else None),
        "candle_color":   geom["candle_color"],
        "body_pct":       geom["body_pct"],
        "upper_wick_pct": geom["upper_wick_pct"],
        "lower_wick_pct": geom["lower_wick_pct"],
        "range_pct":      geom["range_pct"],
        "gap_pct":        _gap_pct(bars, scan_idx),
        "ema20":  round(ema20[-1], 4)  if not math.isnan(ema20[-1])  else None,
        "ema50":  round(ema50[-1], 4)  if not math.isnan(ema50[-1])  else None,
        "ema89":  round(ema89[-1], 4)  if not math.isnan(ema89[-1])  else None,
        "ema200": round(ema200[-1], 4) if not math.isnan(ema200[-1]) else None,
        "ema20_state":  ema20_state,  "ema50_state":  ema50_state,
        "ema89_state":  ema89_state,  "ema200_state": ema200_state,
        "ema_reclaim_20":  reclaim_20,  "ema_reclaim_50":  reclaim_50,
        "ema_reclaim_89":  reclaim_89,  "ema_reclaim_200": reclaim_200,
        "price_above_ema20":  above_20,  "price_above_ema50":  above_50,
        "price_above_ema89":  above_89,  "price_above_ema200": above_200,
        "price_pos_4bar":   pos_4,  "price_pos_4bar_bucket":  buck_4,
        "price_pos_10bar":  pos_10, "price_pos_10bar_bucket": buck_10,
        "price_pos_20bar":  pos_20, "price_pos_20bar_bucket": buck_20,
        "price_pos_50bar":  pos_50, "price_pos_50bar_bucket": buck_50,
        "atr_pct":          atr_p,
        "volatility_bucket": vol_b,
        "relative_volume":   rv,
        "relative_volume_bucket": vol_bucket,
        "dollar_volume_bucket":   dv_bucket,
        "liquidity_bucket":       dv_bucket,
        "abr_category":      abr_category,
        "abr_med10d":        _f(abr.get("median_10d") if isinstance(abr, dict) else None),
        "abr_fail10d":       _f(abr.get("fail_10d")   if isinstance(abr, dict) else None),
        "abr_prev1_quality": str(abr.get("prev1_quality")) if isinstance(abr, dict) and abr.get("prev1_quality") else None,
        "abr_prev2_quality": str(abr.get("prev2_quality")) if isinstance(abr, dict) and abr.get("prev2_quality") else None,
        "abr_sequence_key":  str(abr.get("sequence_key")) if isinstance(abr, dict) and abr.get("sequence_key") else None,
        "role":           str(bar.get("role") or "") or None,
        "matched_status": str(bar.get("matched_status") or bar.get("role") or "") or None,
        "score":          score_val,
        "score_bucket":   _score_bucket(score_val),
        "market_regime":  str(bar.get("FINAL_REGIME") or bar.get("market_regime") or "") or None,
        "sector":         str(bar.get("sector") or "") or None,
        "industry":       str(bar.get("industry") or "") or None,
        "insufficient_history_fields": (
            json.dumps(insufficient_history_fields) if insufficient_history_fields else None
        ),
        "event_snapshot_json": snapshot_json,
    }

    # Emit one row per active signal, sharing context
    events: list[dict] = []
    for sig, family, sig_type in actives:
        ev = dict(base)
        ev["event_signal"]        = sig
        ev["event_signal_family"] = family or _family_for(sig)
        ev["event_signal_type"]   = sig_type
        ev["event_direction"]     = _direction(sig)
        events.append(ev)
    return events
