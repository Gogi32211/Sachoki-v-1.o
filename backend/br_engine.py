"""
br_engine.py — Break Readiness (BR%) scan engine.
"""
from __future__ import annotations

import os
import sqlite3
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from indicators    import rsi as _rsi_ind, atr as _atr_hlc
from signal_engine import compute_signals
from wlnbb_engine  import compute_wlnbb
from combo_engine  import compute_combo, last_n_active, active_signal_labels
from wick_engine   import compute_wick
from cisd_engine   import compute_cisd

log = logging.getLogger(__name__)
DB_PATH = os.environ.get("DB_PATH", "/tmp/scanner.db")

_br_state: dict = {"running": False, "done": 0, "total": 0, "found": 0}


def get_br_scan_progress() -> dict:
    return dict(_br_state)


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _rsi(s: pd.Series, n: int) -> pd.Series:
    return _rsi_ind(s, n)


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    return _atr_hlc(df["high"], df["low"], df["close"], n)


def compute_br(df: pd.DataFrame) -> pd.DataFrame:
    c, o, h, l = df["close"], df["open"], df["high"], df["low"]
    v = df["volume"] if "volume" in df.columns else pd.Series(1.0, index=df.index)
    n = len(df)
    idx = df.index

    atr14   = _atr(df, 14)
    squeeze = atr14 < atr14.rolling(20).mean() * 0.6
    cb_arr  = np.zeros(n)
    cb = 0
    for i in range(n):
        cb = cb + 1 if squeeze.iloc[i] else 0
        cb_arr[i] = cb
    cons_bars     = pd.Series(cb_arr, index=idx)
    cons_strength = np.minimum(100.0, cb_arr * 5.0)

    rsi14     = _rsi(c, 14)
    cap_sig   = ((rsi14.shift(1) >= 20) & (rsi14.shift(1) <= 32) &
                 (rsi14.shift(2) > rsi14.shift(1)) & (rsi14 > rsi14.shift(1)) &
                 ((rsi14 - rsi14.shift(1)) >= 3.0) &
                 (c > o) & (c > c.shift(1)))
    cap_cum   = cap_sig.astype(float).cumsum()
    cap_count = (cap_cum - cap_cum.shift(20).fillna(0)).clip(lower=0)
    cap_str   = np.minimum(100.0, cap_count.values * 20.0)

    vma20    = v.rolling(20).mean()
    vr       = (v / vma20.replace(0, np.nan)).fillna(0)
    rr       = ((h - l) / (h - l).rolling(20).mean().replace(0, np.nan)).fillna(1)
    ac_bar   = (vr >= 1.1) & (vr < 1.5) & (rr < 0.6)
    ac_arr   = np.zeros(n)
    ac = 0
    for i in range(n):
        ac = ac + 1 if ac_bar.iloc[i] else 0
        ac_arr[i] = ac
    acc_sig     = pd.Series(ac_arr >= 3, index=idx)
    acc_cum     = acc_sig.astype(float).cumsum()
    acc_cluster = (acc_cum - acc_cum.shift(20).fillna(0)).clip(lower=0)
    acc_str     = np.minimum(100.0, acc_cluster.values * 25.0)

    vma       = v.rolling(20).mean()
    vc1       = v > vma * 1.5
    vc2       = v > vma * 2.5
    vol_str   = np.where(vc2, 100.0, np.where(vc1, 60.0, 0.0))
    bear_bar  = c < o
    vol_pen   = np.where(bear_bar & vc2, -40.0, np.where(bear_bar & vc1, -20.0, 0.0))

    rsi_ph = np.where(rsi14 < 30, 0.0,
             np.where(rsi14 <= 40, 50.0,
             np.where(rsi14 >= 50, 100.0, 75.0)))

    base = (cons_strength + cap_str + acc_str + vol_str + rsi_ph) / 5.0

    avg_vol_atr  = v.rolling(20).mean()
    vc_atr_up    = (v > avg_vol_atr * 2.0) & (c > o)
    lv_arr       = np.full(n, np.nan)
    lv = np.nan
    for i in range(n):
        if vc_atr_up.iloc[i]:
            lv = i
        lv_arr[i] = lv
    bars_lv   = np.arange(n) - lv_arr
    strong2   = vc_atr_up.values & (~np.isnan(lv_arr)) & (bars_lv <= 5) & (bars_lv > 0)
    atr_bon   = np.where(vc_atr_up, 6.0, 0.0)
    atr_bon2  = np.where(strong2, 10.0, 0.0)

    em = c.ewm(span=20, adjust=False).mean()
    es1, el1 = c.ewm(span=3, adjust=False).mean(), c.ewm(span=5, adjust=False).mean()
    es2, el2 = c.ewm(span=8, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    dz1  = (es1 > el1) & (es1.shift(1) <= el1.shift(1)) & (es1 < em) & (el1 < em)
    tz1  = dz1 & (o < el1) & (c > es1)
    dz2  = (es2 > el2) & (es2.shift(1) <= el2.shift(1)) & (es2 > em) & (el2 > em)
    tz2  = dz2 & (o < el2) & (c > es2)
    gmma = np.where(tz2, 15.0, np.where(tz1, 10.0, 0.0))

    rh20     = rsi14.rolling(20).max().shift(1)
    tl_brk   = (rsi14 > rh20) & (rsi14.shift(1) <= rh20.shift(1))
    lb_arr   = np.full(n, np.nan)
    lb = np.nan
    for i in range(n):
        if tl_brk.iloc[i]:
            lb = i
        lb_arr[i] = lb
    bfb      = np.arange(n) - lb_arr
    tl_ret   = (~np.isnan(lb_arr)) & (bfb > 0) & (bfb <= 20) & ((rsi14.values - rh20.values) > 0) & ((rsi14.values - rh20.values) <= 7.0) & (~tl_brk.values)
    tl_bon   = np.where(tl_brk, 15.0, 0.0) + np.where(tl_ret, 15.0, 0.0)

    rsi_mom = np.where((c > o) & (rsi14 > rsi14.shift(1)), 2.0,
              np.where((c < o) & (rsi14 < rsi14.shift(1)), -2.0, 0.0))

    ema89    = c.ewm(span=89, adjust=False).mean()
    e89_bon  = np.where(c > ema89, 10.0, -15.0)

    spr    = h - l
    clv_ns = ((c - l) / spr.replace(0, np.nan)).fillna(0.5)
    ns_bar = (spr < atr14 * 0.8) & (v < vma20 * 0.9) & (c < c.shift(1)) & (clv_ns >= 0.45)
    ns_rec = ns_bar.rolling(5, min_periods=1).max().astype(bool)
    ns_bon = np.where(ns_rec, 8.0, 0.0)

    vm = v.rolling(20).mean(); vstd = v.rolling(20).std()
    vlo = vm - vstd; vup = vm + vstd
    isw = v < vlo; isl = (~isw) & (v < vm)
    isn = (~isw) & (~isl) & (v < vup); isb = (~isw) & (~isl) & (~isn) & (v < vup + vm)
    isvb = ~(isw | isl | isn | isb)
    same = ((isw & isw.shift(1)) | (isl & isl.shift(1)) | (isn & isn.shift(1)) |
            (isb & isb.shift(1)) | (isvb & isvb.shift(1)))
    bup  = ((isw.shift(1) & (isl | isn | isb | isvb)) |
            (isl.shift(1) & (isn | isb | isvb)) |
            (isn.shift(1) & (isb | isvb)) |
            (isb.shift(1) & isvb))
    vua  = bup | (same & (v > v.shift(1)))
    l34r = vua & (c > c.shift(1)) & (c <= h.shift(1)) & (c > o)
    l43r = vua & (c < c.shift(1)) & (c <= h.shift(1)) & (c > o)
    l34_rec = (l34r | l43r).rolling(5, min_periods=1).max().astype(bool)
    l34_bon = np.where(l34_rec, 8.0, 0.0)

    lk = 50
    h_np, l_np = h.values, l.values
    dp_loh = np.full(n, np.nan); dp_hol = np.full(n, np.nan)
    for i in range(lk - 1, n):
        sh = h_np[i - lk + 1:i + 1]; sl = l_np[i - lk + 1:i + 1]
        dp_loh[i] = sl[np.argmax(sh)]
        dp_hol[i] = sh[np.argmin(sl)]
    dp_loh_s = pd.Series(dp_loh, index=idx)
    dp_hol_s = pd.Series(dp_hol, index=idx)
    dp_near  = ((dp_hol_s - c).abs() / dp_hol_s.replace(0, np.nan) * 100 <= 2.0)
    dp_brk   = (c > dp_loh_s) & (c.shift(1) <= dp_loh_s.shift(1))
    dp_bon   = np.where(dp_brk, 12.0, np.where(dp_near, 8.0, 0.0))

    tz_df    = compute_signals(df)
    is_bull  = tz_df["is_bull"].astype(bool)
    is_bear  = tz_df["is_bear"].astype(bool)
    tz_bon   = np.where(is_bull, 10.0, np.where(is_bear, -12.0, 0.0))

    wdf      = compute_wlnbb(df)
    blue_f   = wdf.get("BLUE",  pd.Series(False, index=idx)).astype(bool)
    fri34_f  = wdf.get("FRI34", pd.Series(False, index=idx)).astype(bool)
    l34_f    = wdf.get("L34",   pd.Series(False, index=idx)).astype(bool)
    prev_bl  = blue_f.astype(int).rolling(11).sum() - blue_f.astype(int)
    st_sig   = blue_f & (prev_bl >= 1)
    ui_sig   = blue_f & (prev_bl >= 2)
    blue_bon = np.where(blue_f, 8.0, 0.0)
    fri_bon  = np.where(fri34_f, 12.0, 0.0)
    stui_bon = np.where(ui_sig, 8.0, np.where(st_sig, 5.0, 0.0))

    wkdf     = compute_wick(df)
    wk_bull  = wkdf.get("WICK_BULL_CONFIRM", pd.Series(False, index=idx)).astype(bool)
    wk_bear  = wkdf.get("WICK_BEAR_CONFIRM", pd.Series(False, index=idx)).astype(bool)
    wk_pat   = wkdf.get("WICK_PATTERN",      pd.Series(False, index=idx)).astype(bool)
    w2_bon   = np.where(wk_bull, 10.0, np.where(wk_bear, -8.0, np.where(wk_pat, 6.0, 0.0)))

    cdf      = compute_cisd(df)
    c_ppm    = cdf.get("CISD_PPM", pd.Series(False, index=idx)).astype(bool)
    c_seq    = cdf.get("CISD_SEQ", pd.Series(False, index=idx)).astype(bool)
    c_pmm    = cdf.get("CISD_PMM", pd.Series(False, index=idx)).astype(bool)
    c_mpm    = cdf.get("CISD_MPM", pd.Series(False, index=idx)).astype(bool)
    cisd_bon = np.where(c_ppm, 12.0, np.where(c_seq, 10.0,
               np.where(c_pmm, -10.0, np.where(c_mpm, -8.0, 0.0))))

    me_lb  = 10; me_min = 3
    h_np2  = h.values; l_np2 = l.values
    me_cnt = np.zeros(n)
    for j in range(1, me_lb + 1):
        hp = np.concatenate([np.full(j, np.nan), h_np2[:-j]])
        lp = np.concatenate([np.full(j, np.nan), l_np2[:-j]])
        me_cnt += np.where(np.isnan(hp), 0, ((h_np2 >= hp) & (l_np2 <= lp)).astype(float))
    me_count = pd.Series(me_cnt, index=idx)
    me_ok    = me_count >= me_min
    me_bull  = me_ok & (c > o)
    me_bear  = me_ok & (c < o)
    me_bon   = np.where(me_bull & (me_count >= 6), 12.0,
               np.where(me_bull, 8.0, np.where(me_bear, -10.0, 0.0)))

    cob     = compute_combo(df)
    p3_f    = cob.get("preup3",  pd.Series(False, index=idx)).astype(bool)
    p89_f   = cob.get("preup89", pd.Series(False, index=idx)).astype(bool)
    e9      = c.ewm(span=9,  adjust=False).mean()
    e20     = c.ewm(span=20, adjust=False).mean()
    e50     = c.ewm(span=50, adjust=False).mean()
    prev_bl2 = (c.shift(1) < e9.shift(1)) & (c.shift(1) < e20.shift(1)) & (c.shift(1) < e50.shift(1))
    cur_abv  = (o > e9) & (o > e20) & (o > e50) & (c > e9) & (c > e20) & (c > e50)
    sig3g    = prev_bl2 & cur_abv
    pre_bon  = np.where(sig3g, 12.0, np.where(p3_f, 10.0, np.where(p89_f, 8.0, 0.0)))

    rsi2_s   = _rsi(c, 2)
    rtv_base = ((rsi2_s.shift(1) < 20) & (rsi2_s > 20) &
                ((c.shift(1) < o.shift(1)) | (c.shift(2) < o.shift(2))) &
                (c > o))
    wvf      = (c.rolling(22).max() - l) / c.rolling(22).max() * 100
    wvf_upp  = wvf.rolling(20).mean() + wvf.rolling(20).std() * 2
    wvf_rng  = wvf.rolling(50).max() * 0.85
    vix_f    = (wvf >= wvf_upp) | (wvf >= wvf_rng)
    rtv_sig  = rtv_base & (vix_f | vix_f.shift(1).fillna(False))
    rtv_bon  = np.where(rtv_sig, 8.0, 0.0)

    cap_vcomb = cap_sig & (vc_atr_up.values.astype(bool))
    cap_bvcomb = cap_sig.shift(1).fillna(False) & tl_brk & vc_atr_up
    cap_rtv    = cap_sig.shift(1).fillna(False) & pd.Series(tl_ret, index=idx) & pd.Series(strong2, index=idx)
    cmb_bon    = np.where(cap_rtv, 20.0, np.where(cap_bvcomb, 12.0, np.where(cap_vcomb, 8.0, 0.0)))

    bonus = (atr_bon + atr_bon2 + gmma + tl_bon + rsi_mom + e89_bon +
             ns_bon + l34_bon + dp_bon + vol_pen + tz_bon +
             blue_bon + fri_bon + stui_bon + w2_bon + cisd_bon +
             me_bon + pre_bon + rtv_bon + cmb_bon)
    br_score = pd.Series(np.clip(base + bonus, 0, 100), index=idx).round(1)

    ema34  = c.ewm(span=34, adjust=False).mean()
    is_ll  = l == l.rolling(5).min()
    hlc3   = (h + l + c) / 3
    cpv = 0.0; cvv = 0.0
    cpv_arr = np.zeros(n); cvv_arr = np.zeros(n)
    for i in range(n):
        if is_ll.iloc[i] or i == 0:
            cpv = float(hlc3.iloc[i] * v.iloc[i])
            cvv = float(v.iloc[i])
        else:
            cpv += float(hlc3.iloc[i] * v.iloc[i])
            cvv += float(v.iloc[i])
        cpv_arr[i] = cpv; cvv_arr[i] = cvv
    vwap_ll  = pd.Series(np.where(cvv_arr == 0, c.values, cpv_arr / cvv_arr), index=idx)
    buy_sig  = (vwap_ll > ema34) & (vwap_ll.shift(1) <= ema34.shift(1))

    lb2_arr  = np.full(n, np.nan); lb2 = np.nan
    for i in range(n):
        if buy_sig.iloc[i]:
            lb2 = i
        lb2_arr[i] = lb2
    bsb  = np.arange(n) - lb2_arr
    in_bc = (~np.isnan(lb2_arr)) & (bsb >= 1) & (bsb <= 4)
    bc_sig  = pd.Series(in_bc, index=idx) & (c > o) & (c > ema34)
    big_sig = buy_sig & (o > ema34) & (c > ema34) & (c > o)

    gb_arr = np.full(n, np.nan); gb = np.nan
    for i in range(n):
        if buy_sig.iloc[i] and br_score.iloc[i] >= 71:
            gb = i
        gb_arr[i] = gb
    gbs  = np.arange(n) - gb_arr
    go_win = (~np.isnan(gb_arr)) & (gbs >= 1) & (gbs <= 4)
    go_sig  = pd.Series(go_win, index=idx) & bc_sig

    up_sig  = (br_score - br_score.shift(1)) > 35.0

    return pd.DataFrame({
        "br_score":      br_score,
        "cons_bars":     cons_bars,
        "cap_count":     cap_count.round(0),
        "accum_cluster": acc_cluster.round(0),
        "me_bull":       me_bull,
        "me_bear":       me_bear,
        "me_count":      me_count,
        "buy":           buy_sig,
        "bc":            bc_sig,
        "big":           big_sig,
        "go":            go_sig,
        "up":            up_sig,
        "tz_bull":       is_bull,
        "tz_sig":        tz_df["sig_name"],
        "blue":          blue_f,
        "fri34":         fri34_f,
        "l34":           l34_f,
        "rtv":           rtv_sig,
        "sig3g":         sig3g,
        "raw_p3":        p3_f,
        "raw_p89":       p89_f,
        "wick_bull":     wk_bull,
        "cisd_ppm":      c_ppm,
        "cisd_seq":      c_seq,
    }, index=idx)


_TZ_WEIGHT = {
    "T4": 4, "T6": 4, "T1G": 3, "T2G": 3,
    "T1": 2, "T2": 2, "T9": 1, "T10": 1, "T3": 1, "T11": 1, "T5": 1,
}

_BR_COLS = [
    "master_score",
    "br_score", "cons_bars", "cap_count", "accum_cluster",
    "me_bull", "me_bear", "me_count",
    "buy", "bc", "big", "go", "up",
    "tz_bull", "tz_sig", "blue", "fri34", "l34",
    "rtv", "sig3g", "raw_p3", "raw_p89",
    "wick_bull", "cisd_ppm", "cisd_seq",
    "combo_score", "combo_labels",
]


def _init_db() -> None:
    con = _db()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS br_scan_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            tf           TEXT    DEFAULT '1d',
            started_at   TEXT,
            completed_at TEXT,
            result_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS br_scan_results (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id       INTEGER,
            ticker        TEXT NOT NULL,
            master_score  REAL    DEFAULT 0,
            br_score      REAL    DEFAULT 0,
            cons_bars     INTEGER DEFAULT 0,
            cap_count     INTEGER DEFAULT 0,
            accum_cluster INTEGER DEFAULT 0,
            me_bull       INTEGER DEFAULT 0,
            me_bear       INTEGER DEFAULT 0,
            me_count      INTEGER DEFAULT 0,
            buy           INTEGER DEFAULT 0,
            bc            INTEGER DEFAULT 0,
            big           INTEGER DEFAULT 0,
            go            INTEGER DEFAULT 0,
            up            INTEGER DEFAULT 0,
            tz_bull       INTEGER DEFAULT 0,
            tz_sig        TEXT    DEFAULT '',
            blue          INTEGER DEFAULT 0,
            fri34         INTEGER DEFAULT 0,
            l34           INTEGER DEFAULT 0,
            rtv           INTEGER DEFAULT 0,
            sig3g         INTEGER DEFAULT 0,
            raw_p3        INTEGER DEFAULT 0,
            raw_p89       INTEGER DEFAULT 0,
            wick_bull     INTEGER DEFAULT 0,
            cisd_ppm      INTEGER DEFAULT 0,
            cisd_seq      INTEGER DEFAULT 0,
            combo_score   INTEGER DEFAULT 0,
            combo_labels  TEXT    DEFAULT '',
            last_price    REAL,
            change_pct    REAL,
            scanned_at    TEXT
        );
    """)
    existing = {r[1] for r in con.execute("PRAGMA table_info(br_scan_results)").fetchall()}
    for col, defn in [("master_score", "REAL DEFAULT 0"),
                      ("combo_score",  "INTEGER DEFAULT 0"),
                      ("combo_labels", "TEXT DEFAULT ''")]:
        if col not in existing:
            con.execute(f"ALTER TABLE br_scan_results ADD COLUMN {col} {defn}")
    run_cols = {r[1] for r in con.execute("PRAGMA table_info(br_scan_runs)").fetchall()}
    if "tf" not in run_cols:
        con.execute("ALTER TABLE br_scan_runs ADD COLUMN tf TEXT DEFAULT '1d'")
    con.commit()
    con.close()


def _scan_br_ticker(ticker: str, interval: str) -> dict | None:
    try:
        import yfinance as yf
        from datetime import date as _date
        raw = yf.Ticker(ticker).history(period="90d", interval=interval, auto_adjust=True)
        if raw is None or raw.empty or len(raw) < 40:
            return None

        raw.columns = [str(c).lower() for c in raw.columns]
        df = raw[["open", "high", "low", "close", "volume"]].dropna()
        if len(df) < 40:
            return None

        last_date = df.index[-1]
        if hasattr(last_date, "date"):
            last_date = last_date.date()
        from datetime import timezone as _tz
        today = datetime.now(_tz.utc).date()
        if last_date == today:
            df = df.iloc[:-1]
        if len(df) < 40:
            return None

        br = compute_br(df)
        last = br.iloc[-1]
        row  = df.iloc[-1]
        prev = df.iloc[-2]

        price      = float(row["close"])
        change_pct = round((price - float(prev["close"])) / float(prev["close"]) * 100, 2)

        try:
            combo        = compute_combo(df)
            active       = last_n_active(combo, 3)
            combo_score  = sum(1 for v in active.values() if v)
            combo_labels = ",".join(active_signal_labels(active))
        except Exception:
            combo_score, combo_labels = 0, ""

        tz_w   = _TZ_WEIGHT.get(str(last["tz_sig"]), 0) if bool(last["tz_bull"]) else 0
        l_pts  = int(bool(last["l34"])) + int(bool(last["blue"])) + int(bool(last["fri34"]))
        master_score = round(min(100.0,
            float(last["br_score"]) * 0.5 +
            min(combo_score * 6, 24) +
            tz_w * 3 +
            l_pts * 3
        ), 1)

        return {
            "ticker":        ticker,
            "master_score":  master_score,
            "br_score":      float(last["br_score"]),
            "cons_bars":     int(last["cons_bars"]),
            "cap_count":     int(last["cap_count"]),
            "accum_cluster": int(last["accum_cluster"]),
            "me_bull":       int(bool(last["me_bull"])),
            "me_bear":       int(bool(last["me_bear"])),
            "me_count":      int(last["me_count"]),
            "buy":           int(bool(last["buy"])),
            "bc":            int(bool(last["bc"])),
            "big":           int(bool(last["big"])),
            "go":            int(bool(last["go"])),
            "up":            int(bool(last["up"])),
            "tz_bull":       int(bool(last["tz_bull"])),
            "tz_sig":        str(last["tz_sig"]),
            "blue":          int(bool(last["blue"])),
            "fri34":         int(bool(last["fri34"])),
            "l34":           int(bool(last["l34"])),
            "rtv":           int(bool(last["rtv"])),
            "sig3g":         int(bool(last["sig3g"])),
            "raw_p3":        int(bool(last["raw_p3"])),
            "raw_p89":       int(bool(last["raw_p89"])),
            "wick_bull":     int(bool(last["wick_bull"])),
            "cisd_ppm":      int(bool(last["cisd_ppm"])),
            "cisd_seq":      int(bool(last["cisd_seq"])),
            "combo_score":   combo_score,
            "combo_labels":  combo_labels,
            "last_price":    price,
            "change_pct":    change_pct,
        }
    except Exception as exc:
        log.debug("br_scan %s error: %s", ticker, exc)
        return None


def run_br_scan(interval: str = "1d", workers: int = 8) -> int:
    from scanner import get_tickers
    global _br_state

    _init_db()
    tickers = get_tickers()
    _br_state.update({"running": True, "done": 0, "total": len(tickers), "found": 0})
    now_iso = datetime.now(timezone.utc).isoformat()

    con = _db()
    scan_id = con.execute(
        "INSERT INTO br_scan_runs (tf, started_at) VALUES (?, ?)", (interval, now_iso)
    ).lastrowid
    con.commit()
    con.close()

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_br_ticker, t, interval): t for t in tickers}
        for fut in as_completed(futures):
            _br_state["done"] += 1
            row = fut.result()
            if row:
                row["scan_id"]   = scan_id
                row["scanned_at"] = now_iso
                results.append(row)
                _br_state["found"] += 1

    if results:
        con = _db()
        cols = ["scan_id", "ticker", "last_price", "change_pct", "scanned_at"] + _BR_COLS
        placeholders = ", ".join(f":{c}" for c in cols)
        con.executemany(
            f"INSERT INTO br_scan_results ({', '.join(cols)}) VALUES ({placeholders})",
            results,
        )
        con.execute(
            "UPDATE br_scan_runs SET completed_at=?, result_count=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), len(results), scan_id),
        )
        con.execute("""
            DELETE FROM br_scan_results WHERE scan_id NOT IN (
                SELECT id FROM br_scan_runs ORDER BY id DESC LIMIT 2
            )
        """)
        con.commit()
        con.close()

    _br_state["running"] = False
    log.info("BR scan done: %d/%d tickers, interval=%s", len(results), len(tickers), interval)
    return len(results)


def get_br_results(
    limit: int = 300,
    min_br: float = 0,
    entry_filter: str = "all",
    tf: str = "1d",
) -> list[dict]:
    _init_db()
    con = _db()
    try:
        row = con.execute(
            "SELECT id FROM br_scan_runs WHERE tf = ? ORDER BY id DESC LIMIT 1",
            (tf,),
        ).fetchone()
        if not row:
            row = con.execute("SELECT id FROM br_scan_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return []
        scan_id = row[0]

        where = "scan_id = ? AND br_score >= ?"
        params: list = [scan_id, min_br]

        if entry_filter != "all":
            where += f" AND {entry_filter} = 1"

        rows = con.execute(
            f"""SELECT ticker, master_score, br_score, cons_bars, cap_count, accum_cluster,
                       me_bull, me_bear, me_count,
                       buy, bc, big, go, up,
                       tz_bull, tz_sig, blue, fri34, l34,
                       rtv, sig3g, raw_p3, raw_p89, wick_bull, cisd_ppm, cisd_seq,
                       combo_score, combo_labels,
                       last_price, change_pct, scanned_at
                FROM br_scan_results
                WHERE {where}
                ORDER BY master_score DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()

        keys = ["ticker", "master_score", "br_score", "cons_bars", "cap_count", "accum_cluster",
                "me_bull", "me_bear", "me_count",
                "buy", "bc", "big", "go", "up",
                "tz_bull", "tz_sig", "blue", "fri34", "l34",
                "rtv", "sig3g", "raw_p3", "raw_p89", "wick_bull", "cisd_ppm", "cisd_seq",
                "combo_score", "combo_labels",
                "last_price", "change_pct", "scanned_at"]
        return [dict(zip(keys, r)) for r in rows]
    finally:
        con.close()


def get_last_br_scan_time(tf: str = "1d") -> str | None:
    _init_db()
    con = _db()
    try:
        row = con.execute(
            "SELECT completed_at FROM br_scan_runs WHERE tf = ? ORDER BY id DESC LIMIT 1",
            (tf,),
        ).fetchone()
        if row is None:
            row = con.execute(
                "SELECT completed_at FROM br_scan_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None
    finally:
        con.close()