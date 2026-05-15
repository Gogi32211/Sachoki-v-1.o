"""
signal_stats_engine.py — Signal Performance Analyzer.

For a ticker + timeframe, fetches 1-2 years of OHLCV, runs all signal
engines, and computes forward outcomes for each signal (or AND-combo).

Metrics per signal:
  n           — occurrences
  bull_rate   — % of fires where next bar closed higher
  avg_1bar    — avg % return after 1 bar (close[+1] / entry - 1)
  avg_3bar    — avg max gain over next 3 bars (max_high[+1:+4] / entry - 1)
  avg_5bar    — avg max gain over next 5 bars
  mae_3       — avg max drawdown over next 3 bars (min_low[+1:+4] / entry - 1)
  false_rate  — % of fires where next-3-bar max gain was negative
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── Available signals with display labels ─────────────────────────────────────
SIGNAL_LABELS: dict[str, str] = {
    # T/Z
    "tz_bull":       "T/Z any bull",
    "tz_T4T6":       "T4/T6/T1G/T2G (strong)",
    "tz_bull_flip":  "TZ→3 (fresh Bull Dom)",
    "tz_attempt":    "TZ→2 (Bull Attempt)",
    # VABS
    "best_sig":      "BEST★",
    "vbo_up":        "VBO↑",
    "abs_sig":       "ABS",
    "ns":            "NS (no supply)",
    "sq":            "SQ",
    "load_sig":      "LD (load)",
    "climb_sig":     "CLB",
    # WLNBB
    "fri34":         "FRI34",
    "fri43":         "FRI43",
    "l34":           "L34",
    "l43":           "L43",
    "bo_up":         "BO↑",
    "be_up":         "BE↑",
    "blue":          "BLUE",
    "cci_ready":     "CCI ready",
    # Combo / 2809
    "rocket":        "🚀 ROCKET",
    "buy_2809":      "BUY 2809",
    "bf_buy":        "4BF",
    "sig3g":         "3G gap",
    "hilo_buy":      "HILO↑",
    "va":            "VA (vol surge)",
    "cd":            "CD (BullDom+B)",
    "ca":            "CA (BullAtt+B)",
    "cw":            "CW (BearWeak+B)",
    "sig_l88":       "L88",
    # PREUP
    "preup66":       "P66 (EMA200+)",
    "preup55":       "P55 (EMA89+)",
    "preup89":       "P89",
    "preup3":        "P3",
    "preup2":        "P2",
    # Ultra
    "fbo_bull":      "FBO↑",
    "eb_bull":       "EB↑",
    "ultra_3up":     "3↑",
    # Delta
    "d_blast_bull":  "ΔΔ↑ (delta blast)",
    "d_spring":      "dSPR (delta spring)",
    "d_strong_bull": "B/S↑",
    "d_absorb_bull": "Ab↑",
    "d_surge_bull":  "Δ↑",
    "d_div_bull":    "T↓ (div bull)",
    # Wick
    "x2g_wick":      "X2G",
    "x2_wick":       "X2",
    "x1g_wick":      "X1G",
    "wick_bull":     "WK↑",
    # PARA
    "para_start":    "PARA",
    "para_plus":     "PARA+",
    "para_retest":   "RETEST",
    # FLY 260424
    "fly_abcd":      "FLY ABCD",
    "fly_cd":        "FLY CD",
    "fly_bd":        "FLY BD",
    "fly_ad":        "FLY AD",
    # F signals (b1-b11)
    "b1":  "F1", "b2":  "F2", "b3":  "F3", "b4":  "F4",
    "b5":  "F5", "b6":  "F6", "b7":  "F7", "b8":  "F8",
    "b9":  "F9", "b10": "F10","b11": "F11",
    # G signals (260410)
    "g1": "G1", "g2": "G2", "g4": "G4", "g6": "G6", "g11": "G11",
    # W signal — Bear Weakening + bullish bar
    "tz_weak_bull":  "W (BearWeak↑)",
    # Combo / 2809 extras
    "conso_2809":    "CON (consolidation)",
    "um_2809":       "UM (upmove)",
    "svs_2809":      "SVS (vol spike in conso)",
    "bias_up":       "↑BIAS",
    # PREDN (EMA drop — bearish context)
    "predn66": "D66 (drop EMA200)", "predn55": "D55", "predn89": "D89",
    "predn3":  "D3", "predn2": "D2",
    # WLNBB extras
    "l64":          "L64",
    "l22":          "L22",
    "fuchsia_rh":   "RH (fuchsia high)",
    "fuchsia_rl":   "RL (fuchsia low)",
    "bo_dn":        "BO↓",
    "bx_up":        "BX↑",
    "bx_dn":        "BX↓",
    "be_dn":        "BE↓",
    # Volume spikes (vs previous bar)
    "vol_spike_5x":  "Vol×5",
    "vol_spike_10x": "Vol×10",
    "vol_spike_20x": "Vol×20",
}

# ── OHLCV fetch ───────────────────────────────────────────────────────────────
def _fetch_df(ticker: str, interval: str) -> "pd.DataFrame | None":
    from data_polygon import fetch_bars, polygon_available
    df = None
    days = 730 if interval in ("1d", "1wk", "1w") else 365 if interval == "4h" else 180
    if polygon_available():
        try:
            df = fetch_bars(ticker, interval=interval, days=days)
        except Exception:
            pass
    if df is None or df.empty:
        import yfinance as yf
        period = "5y" if interval in ("1d", "1wk", "1w") else "2y" if interval == "4h" else "1y"
        try:
            raw = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
            if raw is not None and not raw.empty:
                raw.columns = [str(c).lower() for c in raw.columns]
                df = raw[["open", "high", "low", "close", "volume"]].dropna()
        except Exception:
            pass
    return df if df is not None and len(df) >= 60 else None


# ── Signal computation (all engines, full series) ────────────────────────────
def compute_signal_cols(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Run all engines and return a DataFrame of boolean signal columns (full series)."""
    from signal_engine import compute_signals, compute_b_signals
    from wlnbb_engine  import compute_wlnbb
    from combo_engine  import compute_combo, compute_tz_state
    from vabs_engine   import compute_vabs
    from ultra_engine  import compute_ultra_v2, compute_260308_l88
    from wick_engine   import compute_wick_x

    n = len(df)
    cols: dict[str, np.ndarray] = {}

    def _get(frame, col):
        if frame is None or col not in frame.columns:
            return np.zeros(n, dtype=bool)
        return frame[col].astype(bool).values[:n]

    # ── T/Z ──────────────────────────────────────────────────────────────────
    sig = compute_signals(df)
    if "is_bull" in sig.columns:
        cols["tz_bull"]    = sig["is_bull"].astype(bool).values[:n]
    else:
        cols["tz_bull"]    = np.zeros(n, dtype=bool)
    if "sig_name" in sig.columns:
        cols["tz_T4T6"]    = sig["sig_name"].isin({"T4","T6","T1G","T2G"}).values[:n]
    else:
        cols["tz_T4T6"]    = np.zeros(n, dtype=bool)

    # TZ state machine
    tz_st = compute_tz_state(df)
    tz_arr = tz_st.astype(int).values[:n]
    tz_prev = np.concatenate([[0], tz_arr[:-1]])
    cols["tz_bull_flip"]  = (tz_arr == 3) & (tz_prev != 3)
    cols["tz_attempt"]    = (tz_arr == 2) & (tz_prev != 2)
    bar_bull = (df["close"].values[:n] > df["open"].values[:n])
    cols["tz_weak_bull"]  = (tz_arr == 1) & bar_bull   # W: Bear Weakening + green bar

    # ── VABS ─────────────────────────────────────────────────────────────────
    vabs = compute_vabs(df)
    for c in ("best_sig","strong_sig","vbo_up","abs_sig","climb_sig","load_sig","ns","sq","sc"):
        cols[c] = _get(vabs, c)

    # ── WLNBB ────────────────────────────────────────────────────────────────
    wlnbb = compute_wlnbb(df)
    wmap = {
        "FRI34":"fri34","FRI43":"fri43","FRI64":"fri64","L34":"l34","L43":"l43",
        "L64":"l64","L22":"l22",
        "BO_UP":"bo_up","BO_DN":"bo_dn",
        "BX_UP":"bx_up","BX_DN":"bx_dn",
        "BE_UP":"be_up","BE_DN":"be_dn",
        "FUCHSIA_RH":"fuchsia_rh","FUCHSIA_RL":"fuchsia_rl",
        "BLUE":"blue","CCI_READY":"cci_ready","PRE_PUMP":"pre_pump",
    }
    for src, dst in wmap.items():
        cols[dst] = _get(wlnbb, src)

    # ── Combo ─────────────────────────────────────────────────────────────────
    combo = compute_combo(df)
    for c in ("buy_2809","rocket","sig3g","rtv","hilo_buy","atr_brk","bb_brk","va",
              "conso_2809","um_2809","svs_2809","bias_up",
              "preup66","preup55","preup89","preup3","preup2","preup50",
              "predn66","predn55","predn89","predn3","predn2"):
        cols[c] = _get(combo, c)

    # ── G signals (260410) ────────────────────────────────────────────────────
    try:
        from signal_engine import compute_g_signals
        g_sigs = compute_g_signals(df)
        for c in ("g1","g2","g4","g6","g11"):
            cols[c] = _get(g_sigs, c)
    except Exception:
        for c in ("g1","g2","g4","g6","g11"):
            cols[c] = np.zeros(n, dtype=bool)

    # ── Volume spikes (vs previous bar) ──────────────────────────────────────
    _vol = df["volume"].values.astype(float)
    _vs = np.zeros(n, dtype=float)
    _vs[1:] = np.where(_vol[:-1] > 0, _vol[1:] / _vol[:-1], 0.0)
    cols["vol_spike_5x"]  = _vs >= 5.0
    cols["vol_spike_10x"] = _vs >= 10.0
    cols["vol_spike_20x"] = _vs >= 20.0

    # ── B signals → any_b → CD/CA/CW ─────────────────────────────────────────
    b_sigs = compute_b_signals(df)
    any_b = np.zeros(n, dtype=bool)
    for i in range(1, 12):
        c = f"b{i}"
        cols[c] = _get(b_sigs, c)
        any_b |= cols[c]
    cols["cd"] = any_b & (tz_arr == 3)
    cols["ca"] = any_b & (tz_arr == 2)
    cols["cw"] = any_b & (tz_arr == 1)

    # ── Ultra v2 ─────────────────────────────────────────────────────────────
    uv2 = compute_ultra_v2(df)
    for c in ("bf_buy","fbo_bull","eb_bull","ultra_3up","best_long"):
        cols[c] = _get(uv2, c)

    # ── 260308 / L88 ─────────────────────────────────────────────────────────
    try:
        u308 = compute_260308_l88(df)
        cols["sig_l88"]    = _get(u308, "sig_l88")
        cols["sig_260308"] = _get(u308, "sig_260308")
    except Exception:
        cols["sig_l88"] = cols["sig_260308"] = np.zeros(n, dtype=bool)

    # ── Delta ────────────────────────────────────────────────────────────────
    try:
        from delta_engine import compute_delta
        ddf = compute_delta(df)
        for src, dst in [("blast_bull","d_blast_bull"),("spring","d_spring"),
                          ("strong_bull","d_strong_bull"),("absorb_bull","d_absorb_bull"),
                          ("surge_bull","d_surge_bull"),("div_bull","d_div_bull"),
                          ("vd_div_bull","d_vd_div_bull")]:
            cols[dst] = _get(ddf, src)
    except Exception:
        for dst in ("d_blast_bull","d_spring","d_strong_bull","d_absorb_bull",
                    "d_surge_bull","d_div_bull","d_vd_div_bull"):
            cols[dst] = np.zeros(n, dtype=bool)

    # ── Wick X ───────────────────────────────────────────────────────────────
    try:
        wx = compute_wick_x(df)
        for c in ("x2g_wick","x2_wick","x1g_wick","x1_wick","x3_wick"):
            cols[c] = _get(wx, c)
        from wick_engine import compute_wick
        wk = compute_wick(df)
        cols["wick_bull"] = _get(wk, "WICK_BULL_CONFIRM")
    except Exception:
        for c in ("x2g_wick","x2_wick","x1g_wick","x1_wick","x3_wick","wick_bull"):
            cols[c] = np.zeros(n, dtype=bool)

    # ── PARA (full series via exposed internal arrays) ────────────────────────
    try:
        from para_engine import compute_para_series
        _is_daily = interval in ("1d", "1wk", "1w")
        para_df = compute_para_series(df, is_daily=_is_daily)
        if para_df is not None:
            cols["para_start"]  = para_df["para_start"].values[:n]
            cols["para_plus"]   = para_df["para_plus"].values[:n]
            cols["para_retest"] = para_df["para_retest"].values[:n]
        else:
            raise ValueError("para_df is None")
    except Exception:
        cols["para_start"] = cols["para_plus"] = cols["para_retest"] = np.zeros(n, dtype=bool)

    # ── FLY 260424 (full series) ──────────────────────────────────────────────
    try:
        from fly_engine import compute_fly_series
        fly_df = compute_fly_series(df)
        for c in ("fly_abcd", "fly_cd", "fly_bd", "fly_ad"):
            cols[c] = fly_df[c].values[:n]
    except Exception:
        for c in ("fly_abcd", "fly_cd", "fly_bd", "fly_ad"):
            cols[c] = np.zeros(n, dtype=bool)

    return pd.DataFrame(cols, index=df.index[:n])


# ── Outcome computation ───────────────────────────────────────────────────────
def compute_outcomes(df: pd.DataFrame, sig_col: pd.Series, horizon: int = 5) -> pd.DataFrame:
    """Forward returns for each bar where signal fired."""
    hi = df["high"].values.astype(float)
    lo = df["low"].values.astype(float)
    cl = df["close"].values.astype(float)
    sig = sig_col.values.astype(bool)
    n = len(sig)
    results = []

    for i in range(n - horizon):
        if not sig[i]:
            continue
        entry = cl[i]
        if entry <= 0:
            continue
        next1 = cl[i + 1] / entry - 1
        next3 = hi[i + 1:i + 4].max() / entry - 1
        next5 = hi[i + 1:i + 6].max() / entry - 1
        dd3   = lo[i + 1:i + 4].min() / entry - 1
        results.append({"next1": next1, "next3": next3, "next5": next5, "dd3": dd3})

    if not results:
        return pd.DataFrame(columns=["next1", "next3", "next5", "dd3"])
    return pd.DataFrame(results)


def summarize(oc: pd.DataFrame) -> "dict | None":
    if len(oc) == 0:
        return None
    return {
        "n":         len(oc),
        "bull_rate": round(float((oc["next1"] > 0).mean()), 3),
        "avg_1bar":  round(float(oc["next1"].mean()) * 100, 2),
        "avg_3bar":  round(float(oc["next3"].mean()) * 100, 2),
        "avg_5bar":  round(float(oc["next5"].mean()) * 100, 2),
        "mae_3":     round(float(oc["dd3"].mean()) * 100, 2),
        "false_rate":round(float((oc["next3"] < 0).mean()), 3),
    }


# ── Main entry ────────────────────────────────────────────────────────────────
def run_signal_stats(
    ticker: str,
    interval: str,
    signals: list[str],
    combo: bool = False,
    min_n: int = 5,
) -> dict:
    df = _fetch_df(ticker, interval)
    if df is None:
        return {"error": "no_data", "ticker": ticker, "interval": interval}

    try:
        sig_df = compute_signal_cols(df, interval)
    except Exception as exc:
        log.warning("signal_stats compute error %s/%s: %s", ticker, interval, exc)
        return {"error": str(exc), "ticker": ticker, "interval": interval}

    # Filter to signals that exist in our computed df
    valid = [s for s in signals if s in sig_df.columns]
    results: dict[str, dict] = {}

    for sig in valid:
        oc = compute_outcomes(df, sig_df[sig])
        st = summarize(oc)
        if st and st["n"] >= min_n:
            results[sig] = st
        elif st:
            results[sig] = {**st, "warning": f"only {st['n']} occurrences"}
        else:
            results[sig] = {"n": 0}

    # Combo: AND of all selected signals
    if combo and len(valid) >= 2:
        combo_mask = pd.Series(True, index=sig_df.index)
        for s in valid:
            combo_mask = combo_mask & sig_df[s]
        oc = compute_outcomes(df, combo_mask)
        st = summarize(oc)
        combo_key = "+".join(valid)
        results[f"COMBO:{combo_key}"] = st or {"n": 0}

    return {
        "ticker":   ticker,
        "interval": interval,
        "bars":     len(df),
        "results":  results,
        "labels":   {k: SIGNAL_LABELS.get(k, k) for k in valid},
    }
