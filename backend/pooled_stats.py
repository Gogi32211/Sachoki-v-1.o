"""
pooled_stats.py — Cross-universe pooled T/Z and L-combo signal statistics.

Scans all tickers in a universe historically, aggregates
  sequence → next-signal distributions
for use as a high-sample-size baseline in the predictor.

Usage:
  build_pooled_stats(universe='sp500', interval='1d')   # background job
  get_pooled_predict(sig3, sig2, l3, l2, universe, interval)  # query
  get_pooled_tz_freq(universe, interval)                 # per-signal frequency stats
  get_pooled_status(universe, interval)
"""
from __future__ import annotations

import os
import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np

from signal_engine import SIG_NAMES, BULLISH_SIGS, BEARISH_SIGS
from wlnbb_engine import compute_wlnbb
from db import get_db, USE_PG, pk_col

log = logging.getLogger(__name__)

# L-combo inter-bar separator (§ never appears inside an l_combo value)
_SEP = "§"

# L combos that carry a directional bias
_BULL_L = frozenset({"L34", "L43", "L1L2", "FRI34", "FRI43", "L1"})
_BEAR_L = frozenset({"L22", "L64", "L2L5", "L5", "L6"})

_pooled_state: dict = {
    "running": False, "done": 0, "total": 0,
    "started_at": 0, "error": None,
}


# ── DB ────────────────────────────────────────────────────────────────────────

def _db():
    return get_db()


# SQL for upsert (different syntax SQLite vs PostgreSQL)
if USE_PG:
    _UPSERT_TZ = (
        "INSERT INTO pooled_tz_stats "
        "(universe,interval,pattern_len,sig_seq,next_sig_id,count) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (universe,interval,pattern_len,sig_seq,next_sig_id) "
        "DO UPDATE SET count=excluded.count"
    )
    _UPSERT_L = (
        "INSERT INTO pooled_l_stats "
        "(universe,interval,pattern_len,l_seq,next_l,count) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (universe,interval,pattern_len,l_seq,next_l) "
        "DO UPDATE SET count=excluded.count"
    )
else:
    _UPSERT_TZ = (
        "INSERT OR REPLACE INTO pooled_tz_stats "
        "(universe,interval,pattern_len,sig_seq,next_sig_id,count) VALUES (?,?,?,?,?,?)"
    )
    _UPSERT_L = (
        "INSERT OR REPLACE INTO pooled_l_stats "
        "(universe,interval,pattern_len,l_seq,next_l,count) VALUES (?,?,?,?,?,?)"
    )

if USE_PG:
    _UPSERT_FREQ = (
        "INSERT INTO pooled_tz_freq_stats (universe,interval,sig_id,count) "
        "VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (universe,interval,sig_id) DO UPDATE SET count=excluded.count"
    )
    _UPSERT_TZL = (
        "INSERT INTO pooled_tzl_stats (universe,interval,sig_id,l_col,count) "
        "VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (universe,interval,sig_id,l_col) DO UPDATE SET count=excluded.count"
    )
else:
    _UPSERT_FREQ = (
        "INSERT OR REPLACE INTO pooled_tz_freq_stats "
        "(universe,interval,sig_id,count) VALUES (?,?,?,?)"
    )
    _UPSERT_TZL = (
        "INSERT OR REPLACE INTO pooled_tzl_stats "
        "(universe,interval,sig_id,l_col,count) VALUES (?,?,?,?,?)"
    )

# L columns used in TZ×L co-occurrence (must match stats_engine.L_COLS)
_TZLCOLS = ["L1", "L2", "L3", "L4", "L5", "L6",
            "L34", "L22", "L64", "L43", "L1L2", "L2L5"]


def _init_db() -> None:
    con = get_db()
    _pk = pk_col()
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS pooled_tz_stats (
            universe    TEXT NOT NULL,
            interval    TEXT NOT NULL,
            pattern_len INTEGER NOT NULL,
            sig_seq     TEXT NOT NULL,
            next_sig_id INTEGER NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (universe, interval, pattern_len, sig_seq, next_sig_id)
        );
        CREATE TABLE IF NOT EXISTS pooled_l_stats (
            universe    TEXT NOT NULL,
            interval    TEXT NOT NULL,
            pattern_len INTEGER NOT NULL,
            l_seq       TEXT NOT NULL,
            next_l      TEXT NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (universe, interval, pattern_len, l_seq, next_l)
        );
        CREATE TABLE IF NOT EXISTS pooled_stats_meta (
            id           {_pk},
            universe     TEXT NOT NULL,
            interval     TEXT NOT NULL,
            built_at     TEXT NOT NULL,
            ticker_count INTEGER NOT NULL,
            tz_patterns  INTEGER NOT NULL,
            l_patterns   INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pooled_tz_freq_stats (
            universe TEXT NOT NULL,
            interval TEXT NOT NULL,
            sig_id   INTEGER NOT NULL,
            count    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (universe, interval, sig_id)
        );
        CREATE TABLE IF NOT EXISTS pooled_tz_freq_meta (
            id         {_pk},
            universe   TEXT NOT NULL,
            interval   TEXT NOT NULL,
            total_bars INTEGER NOT NULL DEFAULT 0,
            bull_bars  INTEGER NOT NULL DEFAULT 0,
            bear_bars  INTEGER NOT NULL DEFAULT 0,
            doji_bars  INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pooled_tzl_stats (
            universe TEXT NOT NULL,
            interval TEXT NOT NULL,
            sig_id   INTEGER NOT NULL,
            l_col    TEXT NOT NULL,
            count    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (universe, interval, sig_id, l_col)
        );
    """)
    con.commit()
    con.close()


# ── Per-ticker worker ─────────────────────────────────────────────────────────

def _worker(ticker: str, interval: str, days: int) -> dict | None:
    """Return accumulated (seq→next) counts for one ticker."""
    try:
        from data_polygon import fetch_bars, polygon_available
        df = None
        if polygon_available():
            try:
                df = fetch_bars(ticker, interval=interval, days=days)
            except Exception:
                pass
        if df is None or df.empty:
            import yfinance as yf
            period = "5y" if interval in ("1d", "1wk") else "1y"
            raw = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
            if raw is None or raw.empty:
                return None
            raw.columns = [str(c).lower() for c in raw.columns]
            df = raw[["open", "high", "low", "close", "volume"]].dropna()

        if len(df) < 50:
            return None

        from signal_engine import compute_signals
        sigs    = compute_signals(df)
        wlnbb   = compute_wlnbb(df)

        sig_ids  = sigs["sig_id"].values
        l_combos = wlnbb["l_combo"].values
        n = len(sig_ids)

        tz3: Counter = Counter()
        tz2: Counter = Counter()
        l3:  Counter = Counter()
        l2:  Counter = Counter()

        for i in range(n - 3):
            seq = f"{sig_ids[i]},{sig_ids[i+1]},{sig_ids[i+2]}"
            tz3[(seq, int(sig_ids[i + 3]))] += 1

        for i in range(n - 2):
            seq = f"{sig_ids[i]},{sig_ids[i+1]}"
            tz2[(seq, int(sig_ids[i + 2]))] += 1

        for i in range(n - 3):
            seq = f"{l_combos[i]}{_SEP}{l_combos[i+1]}{_SEP}{l_combos[i+2]}"
            l3[(seq, str(l_combos[i + 3]))] += 1

        for i in range(n - 2):
            seq = f"{l_combos[i]}{_SEP}{l_combos[i+1]}"
            l2[(seq, str(l_combos[i + 2]))] += 1

        # Per-bar and per-signal frequency (for aggregate T/Z stats)
        c_arr = df["close"].to_numpy(dtype=float)
        o_arr = df["open"].to_numpy(dtype=float)
        h_arr = df["high"].to_numpy(dtype=float)
        l_arr = df["low"].to_numpy(dtype=float)
        rng  = h_arr - l_arr
        body = np.abs(c_arr - o_arr)
        is_doji = body / np.where(rng > 1e-9, rng, 1e-9) <= 0.05
        freq = {
            "total_bars": n,
            "bull_bars":  int((c_arr > o_arr).sum()),
            "bear_bars":  int((c_arr < o_arr).sum()),
            "doji_bars":  int(is_doji.sum()),
            "sig_counts": Counter(int(s) for s in sig_ids),
        }

        # T/Z × L co-occurrence (for pooled TZ×L matrix)
        combined = sigs.join(wlnbb)
        tzl: Counter = Counter()
        for sid in range(1, 26):
            mask  = combined["sig_id"] == sid
            total = int(mask.sum())
            if total == 0:
                continue
            tzl[(sid, "_total")] = total
            for col in _TZLCOLS:
                if col in combined.columns:
                    cnt = int(combined.loc[mask, col].astype(bool).sum())
                    if cnt > 0:
                        tzl[(sid, col)] = cnt

        return {"tz3": tz3, "tz2": tz2, "l3": l3, "l2": l2, "freq": freq, "tzl": tzl}

    except Exception as exc:
        log.debug("pooled_stats skip %s: %s", ticker, exc)
        return None


# ── Build job ─────────────────────────────────────────────────────────────────

def build_pooled_stats(
    universe: str = "sp500",
    interval: str = "1d",
    workers: int = 6,
    max_tickers: int = 2000,
) -> None:
    """Background task: scan up to max_tickers (random sample) and persist pooled stats."""
    import random
    global _pooled_state
    from scanner import get_universe_tickers

    _init_db()
    _pooled_state.update({
        "running": True, "done": 0, "total": 0,
        "started_at": time.time(), "error": None,
    })

    try:
        tickers = get_universe_tickers(universe)
    except Exception as exc:
        _pooled_state.update({"running": False, "error": str(exc)})
        return

    if len(tickers) > max_tickers:
        tickers = random.sample(tickers, max_tickers)
    _pooled_state["total"] = len(tickers)
    days = 1500 if interval in ("1d", "1wk", "1w") else 300

    # Accumulate in memory across all tickers
    all_tz3: Counter = Counter()
    all_tz2: Counter = Counter()
    all_l3:  Counter = Counter()
    all_l2:  Counter = Counter()
    all_sig_counts: Counter = Counter()
    all_tzl:        Counter = Counter()
    all_total_bars = all_bull_bars = all_bear_bars = all_doji_bars = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, t, interval, days): t for t in tickers}
        for fut in as_completed(futures):
            _pooled_state["done"] += 1
            result = fut.result()
            if result:
                all_tz3 += result["tz3"]
                all_tz2 += result["tz2"]
                all_l3  += result["l3"]
                all_l2  += result["l2"]
                f = result.get("freq")
                if f:
                    all_total_bars += f["total_bars"]
                    all_bull_bars  += f["bull_bars"]
                    all_bear_bars  += f["bear_bars"]
                    all_doji_bars  += f["doji_bars"]
                    all_sig_counts += f["sig_counts"]
                if result.get("tzl"):
                    all_tzl += result["tzl"]

    # Write to DB (replace old data for this universe+interval)
    con = _db()
    con.execute("DELETE FROM pooled_tz_stats WHERE universe=? AND interval=?", (universe, interval))
    con.execute("DELETE FROM pooled_l_stats   WHERE universe=? AND interval=?", (universe, interval))

    tz_rows = [
        (universe, interval, 3, seq, nxt, cnt)
        for (seq, nxt), cnt in all_tz3.items()
    ] + [
        (universe, interval, 2, seq, nxt, cnt)
        for (seq, nxt), cnt in all_tz2.items()
    ]

    l_rows = [
        (universe, interval, 3, seq, nxt, cnt)
        for (seq, nxt), cnt in all_l3.items()
    ] + [
        (universe, interval, 2, seq, nxt, cnt)
        for (seq, nxt), cnt in all_l2.items()
    ]

    con.executemany(_UPSERT_TZ, tz_rows)
    con.executemany(_UPSERT_L,  l_rows)
    con.execute(
        "INSERT INTO pooled_stats_meta "
        "(universe,interval,built_at,ticker_count,tz_patterns,l_patterns) VALUES (?,?,?,?,?,?)",
        (universe, interval, datetime.now(timezone.utc).isoformat(),
         len(tickers), len(tz_rows), len(l_rows)),
    )

    # Persist T/Z signal frequency aggregate
    con.execute("DELETE FROM pooled_tz_freq_stats WHERE universe=? AND interval=?", (universe, interval))
    con.execute("DELETE FROM pooled_tz_freq_meta   WHERE universe=? AND interval=?", (universe, interval))
    freq_rows = [(universe, interval, sid, cnt) for sid, cnt in all_sig_counts.items()]
    con.executemany(_UPSERT_FREQ, freq_rows)
    con.execute(
        "INSERT INTO pooled_tz_freq_meta "
        "(universe,interval,total_bars,bull_bars,bear_bars,doji_bars) VALUES (?,?,?,?,?,?)",
        (universe, interval, all_total_bars, all_bull_bars, all_bear_bars, all_doji_bars),
    )

    # Persist T/Z × L co-occurrence aggregate
    con.execute("DELETE FROM pooled_tzl_stats WHERE universe=? AND interval=?", (universe, interval))
    tzl_rows = [(universe, interval, sid, col, cnt)
                for (sid, col), cnt in all_tzl.items()]
    con.executemany(_UPSERT_TZL, tzl_rows)

    con.commit()
    con.close()

    _pooled_state.update({"running": False})
    log.info("pooled_stats built: universe=%s interval=%s tz=%d l=%d freq=%d tzl=%d",
             universe, interval, len(tz_rows), len(l_rows), len(freq_rows), len(tzl_rows))


# ── Query ─────────────────────────────────────────────────────────────────────

def _l_bias(combo: str) -> bool | None:
    parts = set(combo.split("|"))
    if parts & _BULL_L:
        return True
    if parts & _BEAR_L:
        return False
    return None


def get_pooled_predict(
    sig_seq_3: tuple,
    sig_seq_2: tuple,
    l_seq_3: tuple,
    l_seq_2: tuple,
    universe: str = "sp500",
    interval: str = "1d",
) -> dict:
    """
    Query pooled stats for the given current sequences.

    sig_seq_3 / sig_seq_2 : tuples of int sig_ids  (last 3 / 2 bars)
    l_seq_3   / l_seq_2   : tuples of str l_combos (last 3 / 2 bars)
    """
    _init_db()
    con = _db()

    tz3_str = ",".join(str(s) for s in sig_seq_3)
    tz2_str = ",".join(str(s) for s in sig_seq_2)
    l3_str  = _SEP.join(str(s) for s in l_seq_3)
    l2_str  = _SEP.join(str(s) for s in l_seq_2)

    def _qtz(plen: int, seq: str) -> list:
        return con.execute(
            "SELECT next_sig_id, SUM(count) c FROM pooled_tz_stats "
            "WHERE universe=? AND interval=? AND pattern_len=? AND sig_seq=? "
            "GROUP BY next_sig_id ORDER BY c DESC LIMIT 10",
            (universe, interval, plen, seq),
        ).fetchall()

    def _ql(plen: int, seq: str) -> list:
        return con.execute(
            "SELECT next_l, SUM(count) c FROM pooled_l_stats "
            "WHERE universe=? AND interval=? AND pattern_len=? AND l_seq=? "
            "GROUP BY next_l ORDER BY c DESC LIMIT 10",
            (universe, interval, plen, seq),
        ).fetchall()

    tz3_rows = _qtz(3, tz3_str)
    tz2_rows = _qtz(2, tz2_str)
    l3_rows  = _ql(3, l3_str)
    l2_rows  = _ql(2, l2_str)
    con.close()

    # Include the current pattern labels in the response (same format as predictor.py)
    sig3_label = " → ".join(SIG_NAMES.get(int(s), "NONE") for s in sig_seq_3)
    sig2_label = " → ".join(SIG_NAMES.get(int(s), "NONE") for s in sig_seq_2)
    l3_label   = " → ".join(str(s) for s in l_seq_3)
    l2_label   = " → ".join(str(s) for s in l_seq_2)

    def _fmt_tz(rows: list, label: str) -> dict:
        total = sum(r["c"] for r in rows)
        return {
            "signals": label,
            "total_matches": total,
            "top_outcomes": [
                {
                    "sig_id":   int(r["next_sig_id"]),
                    "sig_name": SIG_NAMES.get(int(r["next_sig_id"]), "NONE"),
                    "count":    r["c"],
                    "pct":      round(r["c"] / total * 100) if total else 0,
                    "is_bull":  int(r["next_sig_id"]) in BULLISH_SIGS,
                    "is_bear":  int(r["next_sig_id"]) in BEARISH_SIGS,
                }
                for r in rows
            ],
        }

    def _fmt_l(rows: list, label: str) -> dict:
        total = sum(r["c"] for r in rows)
        return {
            "pattern": label,
            "total_matches": total,
            "top_outcomes": [
                {
                    "l_combo":    r["next_l"],
                    "count":      r["c"],
                    "pct":        round(r["c"] / total * 100) if total else 0,
                    "is_bullish": _l_bias(r["next_l"]),
                }
                for r in rows
            ],
        }

    return {
        "tz_3bar": _fmt_tz(tz3_rows, sig3_label),
        "tz_2bar": _fmt_tz(tz2_rows, sig2_label),
        "l_3bar":  _fmt_l(l3_rows, l3_label),
        "l_2bar":  _fmt_l(l2_rows, l2_label),
    }


# ── Transition matrix (bar+1, bar+2) ─────────────────────────────────────────

def get_pooled_tz_matrix(universe: str = "sp500", interval: str = "1d") -> dict:
    """
    Derive T/Z transition matrix from existing pooled_tz_stats (pattern_len=2).

    From each (A,B)→C row:
      bar+1: source=A, next=B   (what signal followed A directly)
      bar+2: source=A, next=C   (what signal appeared 2 bars after A)

    Returns {"bar1": {src: {nxt: count}}, "bar2": {src: {nxt: count}}}
    """
    try:
        _init_db()
        con = _db()
        rows = con.execute(
            "SELECT sig_seq, next_sig_id, count FROM pooled_tz_stats "
            "WHERE universe=? AND interval=? AND pattern_len=2",
            (universe, interval),
        ).fetchall()
        con.close()

        if not rows:
            return {"bar1": {}, "bar2": {}}

        bar1: dict = {}
        bar2: dict = {}

        for row in rows:
            parts = row["sig_seq"].split(",")
            src = str(int(parts[0]))
            n1  = str(int(parts[1]))
            n2  = str(int(row["next_sig_id"]))
            cnt = int(row["count"])

            if src not in bar1:
                bar1[src] = {}
            bar1[src][n1] = bar1[src].get(n1, 0) + cnt

            if src not in bar2:
                bar2[src] = {}
            bar2[src][n2] = bar2[src].get(n2, 0) + cnt

        return {"bar1": bar1, "bar2": bar2}
    except Exception as exc:
        log.debug("get_pooled_tz_matrix: %s", exc)
        return {"bar1": {}, "bar2": {}}


# ── Pooled T/Z × L co-occurrence matrix ──────────────────────────────────────

def get_pooled_tzl_matrix(universe: str = "sp500", interval: str = "1d") -> list | None:
    """
    Return pooled T/Z × L co-occurrence matrix (same format as compute_tz_l_matrix)
    aggregated across all tickers in the universe.  Returns None if not built yet.
    """
    try:
        _init_db()
        con = _db()
        rows = con.execute(
            "SELECT sig_id, l_col, count FROM pooled_tzl_stats "
            "WHERE universe=? AND interval=?",
            (universe, interval),
        ).fetchall()
        con.close()

        if not rows:
            return None

        # Build lookup: {sig_id: {l_col: count}}
        data: dict = {}
        for row in rows:
            sid = int(row["sig_id"])
            col = row["l_col"]
            cnt = int(row["count"])
            if sid not in data:
                data[sid] = {}
            data[sid][col] = cnt

        from stats_engine import SIG_ORDER, L_COLS, GOOD_FOR_T, GOOD_FOR_Z

        result = []
        for sig_id, sig_name in SIG_ORDER:
            is_t     = sig_id <= 12
            sig_data = data.get(sig_id, {})
            total    = sig_data.get("_total", 0)
            counts   = {col: sig_data.get(col, 0) for col in L_COLS}
            max_cnt  = max(counts.values()) if any(counts.values()) else 0

            cols_meta: dict = {}
            for col in L_COLS:
                cnt  = counts[col]
                pct  = round(cnt / total * 100) if total > 0 else 0
                is_gold = cnt == max_cnt and cnt > 0
                is_good = (col in GOOD_FOR_T) if is_t else (col in GOOD_FOR_Z)
                is_bad  = (col in GOOD_FOR_Z) if is_t else (col in GOOD_FOR_T)
                color   = "gold" if is_gold else ("green" if is_good and cnt > 0
                          else ("red" if is_bad and cnt > 0 else ""))
                cols_meta[col] = {"count": cnt, "pct": pct, "color": color}

            result.append({
                "sig_id":   sig_id,
                "sig_name": sig_name,
                "is_bull":  is_t,
                "total":    total,
                "cols":     cols_meta,
            })

        return result
    except Exception as exc:
        log.debug("get_pooled_tzl_matrix: %s", exc)
        return None


# ── Aggregate T/Z frequency ───────────────────────────────────────────────────

def get_pooled_tz_freq(universe: str = "sp500", interval: str = "1d") -> dict | None:
    """
    Return aggregate T/Z signal frequency stats built from all universe tickers.
    Returns None if frequency stats haven't been built yet.
    """
    try:
        _init_db()
        con = _db()
        freq_rows = con.execute(
            "SELECT sig_id, count FROM pooled_tz_freq_stats "
            "WHERE universe=? AND interval=?",
            (universe, interval),
        ).fetchall()
        if not freq_rows:
            con.close()
            return None
        meta = con.execute(
            "SELECT total_bars, bull_bars, bear_bars, doji_bars "
            "FROM pooled_tz_freq_meta WHERE universe=? AND interval=? "
            "ORDER BY id DESC LIMIT 1",
            (universe, interval),
        ).fetchone()
        con.close()

        sig_counts = {int(r["sig_id"]): int(r["count"]) for r in freq_rows}
        total_bars = int(meta["total_bars"]) if meta else 0
        bull_bars  = int(meta["bull_bars"])  if meta else 0
        bear_bars  = int(meta["bear_bars"])  if meta else 0
        doji_bars  = int(meta["doji_bars"])  if meta else 0

        from predictor import _T_SIGS, _Z_SIGS

        def _pct(n: int, d: int) -> float:
            return round(n / d * 100, 1) if d > 0 else 0.0

        t_counts = {s: sig_counts.get(s, 0) for s in _T_SIGS}
        z_counts = {s: sig_counts.get(s, 0) for s in _Z_SIGS}
        t_total  = sum(t_counts.values())
        z_total  = sum(z_counts.values())

        t_signals = [
            {"sig_id": s, "name": SIG_NAMES[s], "count": t_counts[s],
             "group_pct": _pct(t_counts[s], t_total),
             "bar_pct":   _pct(t_counts[s], bull_bars)}
            for s in _T_SIGS
        ]
        z_signals = [
            {"sig_id": s, "name": SIG_NAMES[s], "count": z_counts[s],
             "group_pct": _pct(z_counts[s], z_total),
             "bar_pct":   _pct(z_counts[s], doji_bars if s == 21 else bear_bars)}
            for s in _Z_SIGS
        ]

        uni_label = {"sp500": "SP500", "nasdaq": "NASDAQ", "russell2k": "R2K"}.get(
            universe, universe.upper()
        )
        return {
            "total_bars": total_bars,
            "bull_bars":  bull_bars,
            "bear_bars":  bear_bars,
            "doji_bars":  doji_bars,
            "t_total":    t_total,
            "z_total":    z_total,
            "t_signals":  t_signals,
            "z_signals":  z_signals,
            "bench_ticker": f"{uni_label} pooled",
        }
    except Exception as exc:
        log.debug("get_pooled_tz_freq: %s", exc)
        return None


# ── Status ────────────────────────────────────────────────────────────────────

def get_pooled_status(universe: str = "sp500", interval: str = "1d") -> dict:
    try:
        _init_db()
        con = _db()
        row = con.execute(
            "SELECT built_at, ticker_count, tz_patterns, l_patterns "
            "FROM pooled_stats_meta WHERE universe=? AND interval=? "
            "ORDER BY id DESC LIMIT 1",
            (universe, interval),
        ).fetchone()
        con.close()
        if row:
            return {
                "available": True,
                "built_at":     row["built_at"],
                "ticker_count": row["ticker_count"],
                "tz_patterns":  row["tz_patterns"],
                "l_patterns":   row["l_patterns"],
            }
    except Exception:
        pass
    return {"available": False}


def get_pooled_state() -> dict:
    state = dict(_pooled_state)
    if state.get("started_at"):
        state["elapsed"] = round(time.time() - state["started_at"], 1)
    return state
