"""
sequence_engine.py — universe-wide N-bar T/Z sequence analyzer.

Reads the canonical TZ/WLNBB stock_stat CSV that
``backend/analyzers/tz_wlnbb/stock_stat.py`` already writes — does NOT
re-fetch OHLCV. Each row in that CSV contains:
  • ticker / date / bar_datetime
  • t_signal / z_signal      — current bar's T or Z label (may be empty)
  • ret_1d / ret_3d / ret_5d / ret_10d / mfe_* / mae_*  — pre-computed
                                                          forward returns

For every ticker we collect the rows where (t_signal or z_signal) belongs
to the standard pool (T7/T8/Z8 are excluded by spec), order them
chronologically, and slide an N-bar window. The forward-return measured
for the sequence is the ``ret_1d`` of the LAST bar in the window — i.e.
"if you saw this sequence and entered at the close of the last bar,
what was your 1-day return".

Ranking: ``score = win_rate × log1p(count)`` — same balance as the
existing ``robust_score`` in tz_wlnbb/replay.py.
"""
from __future__ import annotations

import csv
import logging
import math
import os
from collections import defaultdict
from typing import Callable, Iterable

log = logging.getLogger(__name__)

# Canonical CSV path candidates.
#   • TZ/WLNBB stock_stat   — has t_signal / z_signal / ret_1d already
#   • Bulk Stock Stat (Admin/api_stock_stat_trigger) — has compact T / Z
#     columns with full labels (e.g. T="T4", Z="Z3") and `close` for
#     forward-return derivation.
def _stat_path(universe: str, tf: str, nasdaq_batch: str = "") -> str:
    if nasdaq_batch and nasdaq_batch != "all":
        if universe == "nasdaq":
            return f"stock_stat_tz_wlnbb_nasdaq_{nasdaq_batch}_{tf}.csv"
        if universe == "nasdaq_gt5":
            return f"stock_stat_tz_wlnbb_nasdaq_gt5_{nasdaq_batch}_{tf}.csv"
    return f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"


def _bulk_stat_path(universe: str, tf: str) -> str:
    """Bulk Stock Stat CSV path (Admin tab → /api/stock-stat/trigger)."""
    return os.path.join("stock_stat_output", f"stock_stat_{universe}_{tf}.csv")


def _candidate_paths(universe: str, tf: str, nasdaq_batch: str = "") -> list[str]:
    """Ordered list of CSV paths the engine may use. Preferred first.

    The engine will try each in order; if the first existing path yields
    zero ticker rows (stale / empty file from a prior session), it falls
    through to the next. This keeps things working even when a leftover
    empty TZ/WLNBB CSV would otherwise shadow the freshly-generated bulk
    Stock Stat CSV.
    """
    return [
        _stat_path(universe, tf, nasdaq_batch),
        f"stock_stat_tz_wlnbb_{universe}_{tf}.csv",
        f"stock_stat_tz_wlnbb_{tf}.csv",
        _bulk_stat_path(universe, tf),
    ]


def _resolve_stat_path(universe: str, tf: str, nasdaq_batch: str = "") -> str | None:
    """First existing candidate path, or None."""
    for p in _candidate_paths(universe, tf, nasdaq_batch):
        if os.path.exists(p):
            return p
    return None


def _read_rows_grouped(path: str) -> dict[str, list]:
    """Read a stock_stat CSV grouped by ticker. Returns {} on read errors
    or when the file has no usable rows."""
    grouped: dict[str, list] = defaultdict(list)
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = r.get("ticker", "")
                if t:
                    grouped[t].append(r)
    except (OSError, csv.Error) as exc:
        log.warning("sequence_engine: cannot read %s: %s", path, exc)
        return {}
    return dict(grouped)


# ─────────────────────────────────────────────────────────────────────────────
# Standard sequence pool — excludes T7, T8, Z8 per spec.
# ─────────────────────────────────────────────────────────────────────────────

BULL_SIGNALS = (
    "T1G", "T1", "T2G", "T2", "T3", "T4", "T5", "T6", "T9", "T10", "T11", "T12",
)
BEAR_SIGNALS = (
    "Z1G", "Z1", "Z2G", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z9", "Z10", "Z11", "Z12",
)
_BULL_SET = frozenset(BULL_SIGNALS)
_BEAR_SET = frozenset(BEAR_SIGNALS)
ALLOWED_SIGNALS = _BULL_SET | _BEAR_SET   # T7, T8, Z8 excluded


def _classify(t_sig: str, z_sig: str) -> tuple[str, str] | None:
    """Return (type_letter, full_label) for the bar, or None if the bar
    has no allowed signal (or only T7/T8/Z8 — explicitly excluded)."""
    t = (t_sig or "").strip()
    z = (z_sig or "").strip()
    if t in _BULL_SET:
        return ("T", t)
    if z in _BEAR_SET:
        return ("Z", z)
    return None


def _safe_float(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f  # NaN
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Row → (t_signal, z_signal) extraction — supports BOTH CSV layouts.
#
#   • TZ/WLNBB stock_stat: row['t_signal'] / row['z_signal'] (lowercase)
#   • Bulk Stock Stat:     row['T'] / row['Z'] (uppercase compact strings,
#                           one full label per cell or empty)
# Bulk Stock Stat may also store the label as the only token in the cell, or
# multiple space-separated tokens — we take the first allowed token.
# ─────────────────────────────────────────────────────────────────────────────

def _first_allowed(text: str, pool: frozenset) -> str:
    if not text:
        return ""
    for tok in str(text).replace(",", " ").split():
        u = tok.strip().upper()
        if u in pool:
            return u
    return ""


def _extract_tz_for_row(row: dict) -> tuple[str, str]:
    """Return (t_signal, z_signal) for a row from either CSV layout.

    Returns ("", "") if the bar has no allowed T/Z signal.
    """
    # TZ/WLNBB layout
    t = (row.get("t_signal") or "").strip()
    z = (row.get("z_signal") or "").strip()
    # Bulk Stock Stat layout (uppercase columns)
    if not t:
        t = _first_allowed(row.get("T", ""), _BULL_SET)
    if not z:
        z = _first_allowed(row.get("Z", ""), _BEAR_SET)
    return t, z


# ─────────────────────────────────────────────────────────────────────────────
# Main scan
# ─────────────────────────────────────────────────────────────────────────────

# Forward-return horizons aggregated per sequence. 1d remains the
# canonical measure used for the score; 3d/5d/9d are additive context.
_HORIZONS = (1, 3, 5, 9)


def run_sequence_scan(
    universe:    str = "sp500",
    tf:          str = "1d",
    seq_len:     int = 4,
    min_count:   int = 10,
    mode:        str = "type",        # "type"  → TZTZ  |  "full" → T4|Z2|T1G|Z3
    nasdaq_batch: str = "",
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
    """Read the existing TZ/WLNBB stock_stat CSV, slide an N-bar window over
    each ticker's chronologically-ordered T/Z signal events, aggregate
    per-sequence stats across the whole universe, and return a sorted result
    list.

    Returns a dict so the caller can distinguish 'no CSV yet' (status='no_data')
    from 'CSV present but no sequences match min_count' (status='ok',
    results=[]).
    """
    if seq_len < 2 or seq_len > 6:
        return {"status": "error", "error": f"seq_len must be 2..6 (got {seq_len})"}
    if mode not in ("type", "full"):
        return {"status": "error", "error": f"mode must be 'type' or 'full' (got {mode!r})"}

    # Walk the candidate paths, keeping the first one that contains at least
    # one ticker row. A leftover empty TZ/WLNBB CSV from a prior session must
    # NOT shadow a freshly-generated bulk Stock Stat file.
    candidates = _candidate_paths(universe, tf, nasdaq_batch)
    rows_by_ticker: dict[str, list] = {}
    stat_path: str | None = None
    tried: list = []
    for p in candidates:
        if not os.path.exists(p):
            continue
        tried.append(p)
        rows_by_ticker = _read_rows_grouped(p)
        if rows_by_ticker:
            stat_path = p
            break
        log.info("sequence_engine: %s exists but contains 0 ticker rows; "
                 "trying next candidate", p)
    if not stat_path:
        if tried:
            return {
                "status": "no_data",
                "error": (
                    "Stock Stat CSV(s) found but contain 0 ticker rows: "
                    + ", ".join(tried) + ". Re-run Stock Stat."
                ),
                "tried_paths": tried,
                "results": [],
            }
        return {
            "status": "no_data",
            "error": (
                f"No Stock Stat CSV for universe={universe} tf={tf}. "
                "Run Admin → Stock Stat or TZ/WLNBB → Generate Stock Stat first."
            ),
            "tried_paths": [],
            "results": [],
        }

    tickers = list(rows_by_ticker.keys())
    total = len(tickers)
    if progress_cb:
        progress_cb(0, total)

    seq_map: dict[str, dict] = defaultdict(
        lambda: {
            "wins": 0,
            "count": 0,
            # Per-horizon return lists. 1d is canonical; 3/5/9d are extras.
            "rets_by_h": {n: [] for n in _HORIZONS},
            "tickers": set(),
        }
    )

    for idx, ticker in enumerate(tickers):
        rows = rows_by_ticker[ticker]
        rows.sort(key=lambda r: r.get("bar_datetime") or r.get("date", ""))

        # Pre-compute close-derived returns for every horizon a row lacks.
        # TZ/WLNBB CSV already populates ret_1d/ret_3d/ret_5d/ret_10d; we
        # still need to derive ret_9d for it and all four horizons for the
        # bulk Stock Stat CSV.
        for i, r in enumerate(rows):
            c0 = _safe_float(r.get("close"))
            if c0 is None or c0 <= 0:
                continue
            for n in _HORIZONS:
                key = f"ret_{n}d"
                if _safe_float(r.get(key)) is not None:
                    continue
                if i + n >= len(rows):
                    continue
                cn = _safe_float(rows[i + n].get("close"))
                if cn is None or cn <= 0:
                    continue
                r[key] = (cn / c0 - 1) * 100   # match TZ/WLNBB units (%)

        # Reduce to bars whose signal is in the allowed pool.
        events = []
        for r in rows:
            t_sig, z_sig = _extract_tz_for_row(r)
            cls = _classify(t_sig, z_sig)
            if cls is None:
                continue
            ret1 = _safe_float(r.get("ret_1d"))
            if ret1 is None:
                continue        # last few bars with no forward bar — skip
            # Other horizons may legitimately be None for events near the
            # end of the dataset (e.g. ret_9d on the last 9 bars).
            rets_by_h = {n: _safe_float(r.get(f"ret_{n}d")) for n in _HORIZONS}
            events.append((cls[0], cls[1], rets_by_h, r.get("date", "")))

        # Slide window. Forward returns are taken from the LAST bar of the
        # window (entry-at-close, exit-N-bars-later).
        for i in range(len(events) - seq_len + 1):
            window = events[i : i + seq_len]
            last_rets = window[-1][2]
            if mode == "type":
                key = "".join(w[0] for w in window)
            else:
                key = "|".join(w[1] for w in window)
            entry = seq_map[key]
            entry["count"] += 1
            for n in _HORIZONS:
                v = last_rets.get(n)
                if v is None:
                    continue
                entry["rets_by_h"][n].append(v)
            entry["tickers"].add(ticker)
            # 1d-derived counters retained for backward compat with the
            # canonical 'wins' column and ranking.
            r1 = last_rets.get(1)
            if r1 is not None and r1 > 0:
                entry["wins"] += 1

        if progress_cb:
            progress_cb(idx + 1, total)

    def _stats(xs: list) -> tuple[float | None, float | None, float | None, float | None]:
        """Return (avg, median, std, win_rate) for a list of returns; (None,…)
        if empty."""
        n = len(xs)
        if n == 0:
            return (None, None, None, None)
        avg = sum(xs) / n
        srt = sorted(xs)
        med = (srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2)
        if n > 1:
            std = math.sqrt(sum((x - avg) ** 2 for x in xs) / (n - 1))
        else:
            std = 0.0
        wins = sum(1 for x in xs if x > 0)
        return (avg, med, std, wins / n)

    # Build result list.
    results = []
    for key, d in seq_map.items():
        if d["count"] < min_count:
            continue

        # 1d remains the canonical horizon for the headline win_rate / wins
        # / score (so existing sort_by=score stays comparable across runs).
        rets1 = d["rets_by_h"][1]
        avg1, med1, std1, wr1 = _stats(rets1)
        wr1 = wr1 if wr1 is not None else (d["wins"] / d["count"])

        if mode == "full":
            type_seq = "".join("T" if p[:1] == "T" else "Z" for p in key.split("|"))
        else:
            type_seq = key
        score = round(wr1 * math.log1p(d["count"]), 4)

        out = {
            "sequence":     key,
            "type_seq":     type_seq,
            "count":        d["count"],
            "wins":         d["wins"],
            "win_rate":     round(wr1, 4),
            "avg_ret_1d":   round(avg1, 6) if avg1 is not None else None,
            "med_ret_1d":   round(med1, 6) if med1 is not None else None,
            "std_ret":      round(std1, 6) if std1 is not None else None,
            "ticker_count": len(d["tickers"]),
            "score":        score,
        }
        # 3d / 5d / 9d additive context. None when no events had that
        # horizon populated (typical near the end of the dataset).
        for n in (3, 5, 9):
            xs = d["rets_by_h"][n]
            avg, med, _std, wr = _stats(xs)
            out[f"win_rate_{n}d"] = round(wr,  4) if wr  is not None else None
            out[f"avg_ret_{n}d"]  = round(avg, 6) if avg is not None else None
            out[f"med_ret_{n}d"]  = round(med, 6) if med is not None else None
            out[f"count_{n}d"]    = len(xs)
        results.append(out)

    results.sort(key=lambda x: (-x["score"], -x["count"]))
    return {
        "status":       "ok",
        "stat_path":    stat_path,
        "universe":     universe,
        "tf":           tf,
        "seq_len":      seq_len,
        "mode":         mode,
        "min_count":    min_count,
        "tickers_seen": total,
        "results":      results,
    }
