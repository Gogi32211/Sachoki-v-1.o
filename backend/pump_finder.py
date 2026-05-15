"""
pump_finder.py — mine T/Z signal combos that historically precede 2x+ pumps.
"""
from __future__ import annotations

import logging
import sqlite3
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

from signal_engine import compute_signals, SIG_NAMES

log = logging.getLogger(__name__)
DB_PATH = os.environ.get("DB_PATH", "/tmp/scanner.db")


def find_pump_combos(
    ticker_list: list[str],
    pump_threshold: float = 2.0,   # e.g. 2.0 = 100% gain (2x)
    pump_window: int = 20,
    combo_len: int = 3,
    min_volume: int = 200_000,
    workers: int = 5,
) -> pd.DataFrame:
    """
    For each ticker:
      1. Fetch 2 years OHLCV
      2. compute_signals()
      3. Find bars where max price in next `pump_window` bars >= close * pump_threshold
      4. Record T/Z combo of `combo_len` bars BEFORE each pump start
      5. Skip combos containing NONE (sig_id==0)

    Returns DataFrame: combo | count | avg_gain_pct | max_gain_pct | win_rate
    """
    combo_data: dict[str, list[float]] = defaultdict(list)

    def _process(ticker: str) -> dict[str, list[float]]:
        try:
            raw = yf.Ticker(ticker).history(
                period="2y", interval="1d", auto_adjust=True
            )
            if raw is None or raw.empty:
                return {}
            if len(raw) < combo_len + pump_window + 5:
                return {}

            raw.columns = [str(c).lower() for c in raw.columns]

            if "volume" in raw.columns:
                avg_vol = raw["volume"].mean()
                if avg_vol < min_volume:
                    return {}

            df = raw[["open", "high", "low", "close"]].dropna()
            if len(df) < combo_len + pump_window + 5:
                return {}

            sigs   = compute_signals(df)
            ids    = sigs["sig_id"].values
            closes = df["close"].values
            n      = len(closes)

            local: dict[str, list[float]] = defaultdict(list)

            for i in range(combo_len, n - pump_window):
                combo_ids = ids[i - combo_len: i]
                # skip if any bar in combo is NONE
                if any(int(s) == 0 for s in combo_ids):
                    continue

                base  = closes[i]
                peak  = closes[i: i + pump_window].max()
                ratio = peak / base if base > 0 else 1.0

                if ratio >= pump_threshold:
                    gain_pct   = (ratio - 1.0) * 100.0
                    combo_str  = " → ".join(
                        SIG_NAMES.get(int(s), "NONE") for s in combo_ids
                    )
                    local[combo_str].append(gain_pct)

            return dict(local)

        except Exception as exc:
            log.debug("pump_finder skip %s: %s", ticker, exc)
            return {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process, t): t for t in ticker_list}
        for fut in as_completed(futures):
            for combo, gains in fut.result().items():
                combo_data[combo].extend(gains)

    if not combo_data:
        return pd.DataFrame(
            columns=["combo", "count", "avg_gain_pct", "max_gain_pct", "win_rate"]
        )

    rows = []
    for combo, gains in combo_data.items():
        rows.append({
            "combo":        combo,
            "count":        len(gains),
            "avg_gain_pct": round(sum(gains) / len(gains), 1),
            "max_gain_pct": round(max(gains), 1),
            "win_rate":     100,   # all recorded occurrences are pump events
        })

    return (
        pd.DataFrame(rows)
        .sort_values("count", ascending=False)
        .reset_index(drop=True)
    )


# ── SQLite persistence ────────────────────────────────────────────────────────

def _init_pump_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS pump_combos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            combo         TEXT NOT NULL,
            count         INTEGER,
            avg_gain_pct  REAL,
            max_gain_pct  REAL,
            win_rate      REAL,
            threshold     REAL DEFAULT 2.0,
            window        INTEGER DEFAULT 20,
            combo_len     INTEGER DEFAULT 3,
            created_at    TEXT
        )
    """)
    con.commit()


def save_pump_combos(
    df: pd.DataFrame,
    threshold: float = 2.0,
    window: int = 20,
    combo_len: int = 3,
) -> None:
    if df.empty:
        return
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    con = sqlite3.connect(DB_PATH)
    _init_pump_table(con)
    con.execute(
        "DELETE FROM pump_combos WHERE threshold=? AND window=? AND combo_len=?",
        (threshold, window, combo_len),
    )
    for _, row in df.iterrows():
        con.execute(
            "INSERT INTO pump_combos "
            "(combo,count,avg_gain_pct,max_gain_pct,win_rate,threshold,window,combo_len,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (row["combo"], int(row["count"]), row["avg_gain_pct"],
             row["max_gain_pct"], row["win_rate"],
             threshold, window, combo_len, now),
        )
    con.commit()
    con.close()


def get_pump_combos(
    threshold: float = 2.0,
    window: int = 20,
    combo_len: int = 3,
    limit: int = 50,
) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    _init_pump_table(con)
    rows = con.execute(
        "SELECT combo,count,avg_gain_pct,max_gain_pct,win_rate FROM pump_combos "
        "WHERE threshold=? AND window=? AND combo_len=? "
        "ORDER BY count DESC LIMIT ?",
        (threshold, window, combo_len, limit),
    ).fetchall()
    con.close()
    return [
        {
            "combo": r[0], "count": r[1],
            "avg_gain_pct": r[2], "max_gain_pct": r[3], "win_rate": r[4],
        }
        for r in rows
    ]
