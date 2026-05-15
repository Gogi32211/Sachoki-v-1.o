"""
power_engine.py — Cross-engine Power Scan.

Combines three independent signal engines for each ticker:
  1. 260323 Combo  (price structure: EMA, volume, breakout)
  2. T/Z Signal    (candle-level bull/bear pattern)
  3. WLNBB         (liquidity + momentum bull_score)

Only tickers with BOTH a 260323 signal AND T/Z or WLNBB confirmation are kept.

Power score = combo_count × 2 + tz_weight + wlnbb_bull_score
"""
from __future__ import annotations

import os
import sqlite3
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from combo_engine import compute_combo, last_n_active, active_signal_labels
from signal_engine import compute_signals
from wlnbb_engine import compute_wlnbb, score_last_bar, l_signal_label
from sq_engine   import compute_sq
from wick_engine import compute_wick
from cisd_engine import compute_cisd

log = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/tmp/scanner.db")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


# ── Progress state ─────────────────────────────────────────────────────────────
_power_state: dict = {"running": False, "done": 0, "total": 0, "found": 0}

# ── T/Z signal weights (bullish signals only) ──────────────────────────────────
_TZ_WEIGHT = {
    "T4": 4, "T6": 4,
    "T1G": 3, "T2G": 3,
    "T1": 2, "T2": 2,
    "T9": 1, "T10": 1, "T3": 1, "T11": 1, "T5": 1,
}

_EXTRA_COLS = [
    # WLNBB L signals
    "l34", "l43", "l64", "l22", "cci_ready", "blue", "fri34", "pre_pump", "bo_up", "bx_up",
    # WLNBB FUCHSIA
    "fuchsia_rh", "fuchsia_rl",
    # 260312 VSA
    "sq", "ns", "nd", "sig3_up", "sig3_dn",
    # 3112_2C wick
    "wick_bull", "wick_bear",
    # 250115 CISD
    "cisd_seq", "cisd_ppm", "cisd_mpm", "cisd_pmm",
]

_RESULT_COLS = [
    "ticker", "power_score", "combo_signals", "combo_score",
    "tz_sig", "tz_bull", "tz_weight", "wlnbb_bull", "l_signal",
    "last_price", "change_pct",
] + _EXTRA_COLS


# ── DB init ────────────────────────────────────────────────────────────────────

def _init_db() -> None:
    con = _db()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS power_scan_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at   TEXT,
            completed_at TEXT,
            result_count INTEGER DEFAULT 0,
            n_bars       INTEGER DEFAULT 3
        );
        CREATE TABLE IF NOT EXISTS power_scan_results (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id       INTEGER,
            ticker        TEXT NOT NULL,
            power_score   REAL    DEFAULT 0,
            combo_signals TEXT,
            combo_score   INTEGER DEFAULT 0,
            tz_sig        TEXT    DEFAULT '',
            tz_bull       INTEGER DEFAULT 0,
            tz_weight     INTEGER DEFAULT 0,
            wlnbb_bull    INTEGER DEFAULT 0,
            l_signal      TEXT    DEFAULT '',
            last_price    REAL,
            change_pct    REAL,
            scanned_at    TEXT,
            l34           INTEGER DEFAULT 0,
            l43           INTEGER DEFAULT 0,
            l64           INTEGER DEFAULT 0,
            l22           INTEGER DEFAULT 0,
            cci_ready     INTEGER DEFAULT 0,
            blue          INTEGER DEFAULT 0,
            fri34         INTEGER DEFAULT 0,
            pre_pump      INTEGER DEFAULT 0,
            bo_up         INTEGER DEFAULT 0,
            bx_up         INTEGER DEFAULT 0,
            fuchsia_rh    INTEGER DEFAULT 0,
            fuchsia_rl    INTEGER DEFAULT 0,
            sq            INTEGER DEFAULT 0,
            ns            INTEGER DEFAULT 0,
            nd            INTEGER DEFAULT 0,
            sig3_up       INTEGER DEFAULT 0,
            sig3_dn       INTEGER DEFAULT 0,
            wick_bull     INTEGER DEFAULT 0,
            wick_bear     INTEGER DEFAULT 0,
            cisd_seq      INTEGER DEFAULT 0,
            cisd_ppm      INTEGER DEFAULT 0,
            cisd_mpm      INTEGER DEFAULT 0,
            cisd_pmm      INTEGER DEFAULT 0
        );
    """)
    # Migration: add extra columns if missing (for older DBs)
    existing = {
        row[1] for row in con.execute(
            "PRAGMA table_info(power_scan_results)"
        ).fetchall()
    }
    for col in _EXTRA_COLS:
        if col not in existing:
            defn = "TEXT DEFAULT ''" if col == "tz_sig" else "INTEGER DEFAULT 0"
            con.execute(f"ALTER TABLE power_scan_results ADD COLUMN {col} {defn}")
    con.commit()
    con.close()


# ── Per-ticker worker ──────────────────────────────────────────────────────────

def _scan_power_ticker(ticker: str, interval: str, n_bars: int) -> dict | None:
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).history(
            period="90d", interval=interval, auto_adjust=True
        )
        if raw is None or raw.empty or len(raw) < 30:
            return None

        raw.columns = [str(c).lower() for c in raw.columns]
        needed = ["open", "high", "low", "close"]
        df = raw[needed + (["volume"] if "volume" in raw.columns else [])].dropna()
        if len(df) < 30:
            return None

        # ── 260323 Combo ───────────────────────────────────────────────────────
        combo  = compute_combo(df)
        active = last_n_active(combo, n_bars)
        if not any(active.values()):
            return None                         # no combo signal → skip

        combo_labels = active_signal_labels(active)
        combo_score  = len(combo_labels)

        # ── T/Z Signal (last bar) ──────────────────────────────────────────────
        sigs      = compute_signals(df)
        last_sig  = sigs.iloc[-1]
        tz_name   = str(last_sig["sig_name"])
        tz_bull   = bool(last_sig["is_bull"])
        tz_weight = _TZ_WEIGHT.get(tz_name, 0) if tz_bull else 0

        # ── WLNBB (last bar) ──────────────────────────────────────────────────
        wlnbb      = compute_wlnbb(df)
        bull_score, _ = score_last_bar(int(last_sig["sig_id"]), wlnbb)
        l_sig      = l_signal_label(wlnbb.iloc[-1])

        # ── Confluence gate: require T/Z bull OR strong WLNBB ─────────────────
        if tz_weight == 0 and bull_score < 3:
            return None

        power_score = combo_score * 2 + tz_weight + bull_score

        last   = df.iloc[-1]
        prev   = df.iloc[-2] if len(df) > 1 else last
        price  = float(last["close"])
        prev_p = float(prev["close"])
        chg    = round((price - prev_p) / prev_p * 100, 2) if prev_p else 0.0

        # ── Extra signals (all engines) ────────────────────────────────────────
        extra: dict = {col: 0 for col in _EXTRA_COLS}
        last_w = wlnbb.iloc[-1]
        extra.update({
            "l34":       int(bool(last_w.get("L34",       False))),
            "l43":       int(bool(last_w.get("L43",       False))),
            "l64":       int(bool(last_w.get("L64",       False))),
            "l22":       int(bool(last_w.get("L22",       False))),
            "cci_ready": int(bool(last_w.get("CCI_READY", False))),
            "blue":      int(bool(last_w.get("BLUE",      False))),
            "fri34":     int(bool(last_w.get("FRI34",     False))),
            "pre_pump":  int(bool(last_w.get("PRE_PUMP",  False))),
            "bo_up":     int(bool(last_w.get("BO_UP",     False))),
            "bx_up":     int(bool(last_w.get("BX_UP",     False))),
            "fuchsia_rh":int(bool(last_w.get("FUCHSIA_RH",False))),
            "fuchsia_rl":int(bool(last_w.get("FUCHSIA_RL",False))),
        })
        try:
            sq_df = compute_sq(df);  last_s = sq_df.iloc[-1]
            extra.update({
                "sq":      int(bool(last_s.get("SQ",      False))),
                "ns":      int(bool(last_s.get("NS",      False))),
                "nd":      int(bool(last_s.get("ND",      False))),
                "sig3_up": int(bool(last_s.get("SIG3_UP", False))),
                "sig3_dn": int(bool(last_s.get("SIG3_DN", False))),
            })
        except Exception:
            pass
        try:
            wk_df = compute_wick(df);  last_wk = wk_df.iloc[-1]
            extra.update({
                "wick_bull": int(bool(last_wk.get("WICK_BULL_CONFIRM", False))),
                "wick_bear": int(bool(last_wk.get("WICK_BEAR_CONFIRM", False))),
            })
        except Exception:
            pass
        try:
            last_c = compute_cisd(df).iloc[-1]
            extra.update({
                "cisd_seq": int(bool(last_c["CISD_SEQ"])),
                "cisd_ppm": int(bool(last_c["CISD_PPM"])),
                "cisd_mpm": int(bool(last_c["CISD_MPM"])),
                "cisd_pmm": int(bool(last_c["CISD_PMM"])),
            })
        except Exception:
            pass

        return {
            "ticker":        ticker,
            "power_score":   round(power_score, 1),
            "combo_signals": ",".join(combo_labels),
            "combo_score":   combo_score,
            "tz_sig":        tz_name if tz_bull else "",
            "tz_bull":       int(tz_bull),
            "tz_weight":     tz_weight,
            "wlnbb_bull":    bull_score,
            "l_signal":      l_sig,
            "last_price":    round(price, 2),
            "change_pct":    chg,
            **extra,
        }
    except Exception as exc:
        log.debug("Power skip %s: %s", ticker, exc)
        return None


# ── Main scan ──────────────────────────────────────────────────────────────────

def run_power_scan(interval: str = "1d", n_bars: int = 3, workers: int = 8) -> int:
    from scanner import get_tickers
    _init_db()

    tickers = get_tickers()
    now_iso = datetime.now(timezone.utc).isoformat()

    _power_state.update({"running": True, "done": 0,
                         "total": len(tickers), "found": 0})

    con = _db()
    cur = con.execute(
        "INSERT INTO power_scan_runs (started_at, n_bars) VALUES (?,?)",
        (now_iso, n_bars),
    )
    scan_id = cur.lastrowid
    con.commit()
    con.close()

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_scan_power_ticker, t, interval, n_bars): t
            for t in tickers
        }
        for fut in as_completed(futures):
            _power_state["done"] += 1
            row = fut.result()
            if row is None:
                continue
            row["scan_id"]    = scan_id
            row["scanned_at"] = now_iso
            results.append(row)
            _power_state["found"] = len(results)

    if results:
        insert_cols  = ["scan_id", "scanned_at"] + _RESULT_COLS
        placeholders = ", ".join(f":{c}" for c in insert_cols)
        col_names    = ", ".join(insert_cols)
        con = _db()
        con.executemany(
            f"INSERT INTO power_scan_results ({col_names}) VALUES ({placeholders})",
            results,
        )
        # Keep only last 2 scan runs
        con.execute("""
            DELETE FROM power_scan_results WHERE scan_id NOT IN (
                SELECT id FROM power_scan_runs ORDER BY id DESC LIMIT 2
            )
        """)
        con.execute(
            "UPDATE power_scan_runs SET completed_at=?, result_count=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), len(results), scan_id),
        )
        con.commit()
        con.close()

    _power_state["running"] = False
    return len(results)


# ── Query helpers ──────────────────────────────────────────────────────────────

def get_power_results(limit: int = 200) -> list[dict]:
    _init_db()
    con = _db()
    last_run = con.execute(
        "SELECT MAX(id) FROM power_scan_runs"
    ).fetchone()[0]
    if last_run is None:
        con.close()
        return []
    rows = con.execute(
        f"SELECT {', '.join(_RESULT_COLS)} FROM power_scan_results "
        f"WHERE scan_id=? ORDER BY power_score DESC LIMIT ?",
        (last_run, limit),
    ).fetchall()
    con.close()
    return [dict(zip(_RESULT_COLS, r)) for r in rows]


def get_last_power_scan_time() -> str | None:
    _init_db()
    con = _db()
    row = con.execute(
        "SELECT completed_at FROM power_scan_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row[0] if row else None


def get_power_scan_progress() -> dict:
    return dict(_power_state)
