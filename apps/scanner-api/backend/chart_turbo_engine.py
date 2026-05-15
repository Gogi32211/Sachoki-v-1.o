"""
chart_turbo_engine.py — Phase 8I: VERBATIM port of _calc_turbo_score from
                        backend/turbo_engine.py.

What this file is:
    The pure scoring function `compute_turbo_score(row, profile)` that converts a
    fully-populated row dict (every signal flag + tz_sig + rtb_phase +
    rtb_transition + extended lookback flags) into a 0–100 turbo_score.

What this file is NOT:
    The old turbo_engine has ~2000 lines of orchestration plumbing (yfinance
    fetch, SQLite persistence, ThreadPool fan-out, N-bar snapshot reconstruction).
    NONE of that plumbing is ported — the new system already does fetching,
    scanning, and persistence through engine_registry + scan_engine. We only
    need the pure scoring math.

Hard rule for this port: do NOT change any constant, weight, branch, cap, or
condition. Magic numbers, profile constants, and "Avg3=…" / "Win%=…" comments
are preserved as-is so future audits can trace each number back to the old
pooled-stats analysis that produced it.

Verbatim parity is verified by scripts/phase_8i_turbo_parity_check.py
(synthetic rows compared to the old _calc_turbo_score).
"""
from __future__ import annotations


# ── T/Z signal weights (profile-specific) ────────────────────────────────────
_TZ_W_BASE = {
    "T4": 9, "T6": 9,
    "T1G": 6, "T2G": 8,
    "T1": 7, "T2": 5,
    "T9": 4, "T10": 4,
    "T3": 2, "T11": 2, "T5": 1,
}
_TZ_W_SP500  = {**_TZ_W_BASE, "T4": 6, "T6": 6}
_TZ_W_NASDAQ = {**_TZ_W_BASE, "T4": 7, "T6": 7, "T1": 5, "T9": 3}
_TZ_W_ALL_US = {**_TZ_W_BASE, "T4": 3, "T6": 3, "T1": 8, "T1G": 6, "T2G": 5, "T2": 4, "T9": 4}


def compute_turbo_score(r: dict, profile: str = "sp500") -> float:
    """
    Statistics-based scoring v3 (SP500 pooled stats, 500 tickers 2yr + co-occurrence analysis).
    Core backbone: conso_2809 (79% freq) → tz_bull (65%) → bf_buy (43%).
    Rarer signals score higher; redundant subsets don't double-count.
      Backbone      cap 18  — conso_2809, tz_bull, chain bonus
      Volume/accum  cap 22  — VABS atomic, Wyckoff, 260308/L88, svs_2809
      Breakout      cap 18  — ULTRA v2, BO/BX (rare→+5), RS
      Combo/trend   cap 14  — Combo signals
      L-structure   cap 13  — T/Z, WLNBB, RL Avg3=2.80, W Avg3=2.42
      Delta         cap 12  — Order-flow
      EMA cross     cap 8   — preup series
      G signals     cap 10  — G2 Avg3=2.64%/Win%=54.9% (best)
    Context (Wick+PARA+FLY+Vol×10) uncapped (max ~25).
    """
    has_conso   = bool(r.get("conso_2809"))
    has_tz_bull = bool(r.get("tz_bull"))
    has_bf_buy  = bool(r.get("bf_buy"))

    # ── Backbone / setup chain (cap 18) ───────────────────────────────────
    bkb = 0.0
    if has_conso:   bkb += 4
    if has_tz_bull: bkb += 6
    if has_conso and has_tz_bull and has_bf_buy:
        bkb += 8
    elif has_conso and has_tz_bull:
        bkb += 3
    s = min(bkb, 18)

    # ── Volume / accumulation family (cap 22) ─────────────────────────────
    vol = 0.0
    if r.get("abs_sig"):   vol += 5
    if r.get("climb_sig"): vol += 5
    if r.get("load_sig"):  vol += 5
    if r.get("vbo_up"):    vol += 4
    if r.get("ns"):        vol += 4
    if r.get("sq"):        vol += 5
    if r.get("sc"):        vol += 2
    if r.get("svs_2809"):  vol += 3
    if r.get("um_2809"):   vol += 3
    if r.get("sig_l88"):        vol += 5
    elif r.get("sig_260308"):   vol += 3
    if r.get("va"):             vol += 3
    s += min(vol, 22)

    # ── Breakout / expansion family (cap 18) ──────────────────────────────
    brk = 0.0
    if has_bf_buy:          brk += 6
    if r.get("fbo_bull"):   brk += 5
    if r.get("eb_bull"):    brk += 4
    if r.get("be_up"):      brk += 10
    if r.get("ultra_3up"):  brk += 3
    if r.get("bo_up") or r.get("bx_up"): brk += 5
    if r.get("rs_strong"):  brk += 5
    elif r.get("rs"):       brk += 3
    s += min(brk, 18)

    # ── Combo / momentum family (cap 14) ──────────────────────────────────
    combo = 0.0
    if r.get("rocket"):
        combo += 12
    elif r.get("buy_2809"):
        combo += 8
    if r.get("sig3g"):    combo += 4
    if r.get("rtv"):      combo += 3
    if r.get("hilo_buy"): combo += 4
    if r.get("atr_brk") or r.get("bb_brk"): combo += 2
    if r.get("cd"):   combo += 5
    elif r.get("ca"): combo += 3
    elif r.get("cw"): combo += 2
    if r.get("seq_bcont"): combo += 3
    s += min(combo, 14)

    # ── L-structure / trend family (cap 17) ───────────────────────────────
    trend = 0.0
    if profile == "nasdaq":
        _tz_w = _TZ_W_NASDAQ
    elif profile == "all_us":
        _tz_w = _TZ_W_ALL_US
    else:
        _tz_w = _TZ_W_SP500
    trend += _tz_w.get(r.get("tz_sig", ""), 0)
    if r.get("tz_bull_flip"):
        trend += 3 if has_bf_buy else 4
    elif r.get("tz_attempt"):
        trend += 2
    if r.get("fri34"):
        trend += 6
    elif r.get("fri43"):
        trend += 4
    if r.get("l34") and not r.get("fri34"): trend += 5
    if r.get("blue"):         trend += 5
    if r.get("cci_ready"):    trend += 2
    if r.get("l43") and not r.get("fri43") and not r.get("fri34"): trend += 5
    if r.get("fuchsia_rl"):   trend += 5
    if r.get("tz_weak_bull"): trend += 2
    s += min(trend, 17)

    # ── Delta / order-flow family (cap 12) ───────────────────────────────
    dlt = 0.0
    if r.get("d_blast_bull"):        dlt += 5
    elif r.get("d_surge_bull"):      dlt += 5
    if r.get("d_strong_bull"):       dlt += 4
    if r.get("d_absorb_bull"):       dlt += 6
    if r.get("d_spring"):            dlt += 6
    elif r.get("d_div_bull"):        dlt += 5
    if r.get("d_vd_div_bull"):       dlt += 3
    elif r.get("d_cd_bull"):         dlt += 2
    s += min(dlt, 12)

    # ── EMA cross family (cap 10) ─────────────────────────────────────────
    ema_x = 0.0
    if r.get("preup66"):   ema_x += 8
    elif r.get("preup55"): ema_x += 6
    elif r.get("preup89"): ema_x += 5
    elif r.get("preup3"):  ema_x += 5
    elif r.get("preup2"):  ema_x += 4
    s += min(ema_x, 10)

    # ── G signals family (cap 10) ─────────────────────────────────────────
    g_sig = 0.0
    if r.get("g2"):   g_sig += 5
    if r.get("g4"):   g_sig += 3
    if r.get("g1"):   g_sig += 3
    if r.get("g6"):   g_sig += 2
    if r.get("g11"):  g_sig += 2
    s += min(g_sig, 10)

    # ── Backtest-proven confluence bonuses (cap 18) ───────────────────────
    _d4  = bool(r.get("d_absorb_bull") or r.get("d_spring"))
    _d6  = bool(r.get("d_surge_bull")  or r.get("d_blast_bull"))
    _l34 = bool(r.get("l34") or r.get("fri34"))
    _be  = bool(r.get("be_up"))
    _l34_r3 = bool(r.get("_l34_recent_3b") or r.get("_fri34_recent_3b"))
    _dabs_r5 = bool(r.get("_dabsorb_recent_5b"))

    conf = 0.0
    if _d6 and _be:
        conf += 12
    if _d4 and _l34:
        conf += 5
    if _d4 and _be:
        conf += 5
    if _l34_r3 and _d4 and not _l34:
        conf += 15
    if _l34_r3 and _be and not _l34:
        conf += 3
    if _dabs_r5 and _be and not _d4:
        conf += 10
    if r.get("ns") and r.get("cons_atr") and _l34:
        conf += 4

    s += min(conf, 18)

    # ── SP500 profile combo bonuses (cap 20) ──────────────────────────────────
    if profile == "sp500":
        _ztrap_r = bool(r.get("_ztrap_recent_5b"))
        _l64_rp  = bool(r.get("_l64_recent_5b"))
        _l43_rp  = bool(r.get("_l43_recent_5b"))
        _l22_rp  = bool(r.get("_l22_recent_5b"))
        _t1_t1g  = r.get("tz_sig", "") in ("T1", "T1G")
        sp_cb = 0.0
        if r.get("sq") and r.get("climb_sig") and _ztrap_r:
            sp_cb += 12
        if r.get("sq") and r.get("load_sig") and _ztrap_r:
            sp_cb += 12
        if r.get("ns") and r.get("um_2809"):
            sp_cb += 10
        if _l43_rp and r.get("climb_sig") and _ztrap_r:
            sp_cb += 10
        if _l64_rp and _t1_t1g and r.get("svs_2809"):
            sp_cb += 12
        _ztrap_r = bool(r.get("_ztrap_recent_5b"))
        if (_l22_rp or _l64_rp) and r.get("sq") and _ztrap_r:
            sp_cb += 8
        s += min(sp_cb, 20)

    # ── NASDAQ profile combo bonuses (cap 25) ─────────────────────────────────
    elif profile == "nasdaq":
        _c_phase = r.get("rtb_phase") == "C"
        _btoc    = r.get("rtb_transition") == "B_TO_C"
        _ztrap_r = bool(r.get("_ztrap_recent_5b"))
        _l64_rp  = bool(r.get("_l64_recent_5b"))
        _l22_rp  = bool(r.get("_l22_recent_5b"))
        _blue_rp = bool(r.get("_blue_recent_5b"))
        _um      = bool(r.get("um_2809"))
        _tz_cur  = r.get("tz_sig", "")
        _t6      = (_tz_cur == "T6")
        _t4      = (_tz_cur == "T4")
        nq_cb = 0.0
        if _ztrap_r and _t6 and _c_phase:
            nq_cb += 14
        if _l64_rp and _um and _t6:
            nq_cb += 14
        if _um and r.get("be_up"):
            nq_cb += 12
        if _ztrap_r and _um and _t4:
            nq_cb += 12
        if _t6 and _btoc:
            nq_cb += 12
        if r.get("sq") and (r.get("blue") or _blue_rp) and _c_phase:
            nq_cb += 8
        if r.get("load_sig") and (r.get("blue") or _blue_rp) and _c_phase:
            nq_cb += 8
        if _um and r.get("vbo_up"):
            nq_cb += 8
        if _um and r.get("bx_up"):
            nq_cb += 8
        if (_l22_rp or _l64_rp) and r.get("sq") and r.get("load_sig"):
            nq_cb += 8
        s += min(nq_cb, 25)

    # ── Kill / penalty conditions ─────────────────────────────────────────
    if (r.get("g4") or r.get("g6")) and not _l34 and not _be and not _d4:
        s -= 4
    if r.get("d_strong_bull") and not _l34 and not _be and not _d4 and not _d6:
        s -= 3
    if _d6 and _l34 and not _be:
        s -= 5
    _rsi = float(r.get("rsi", 50.0))
    if _rsi > 80 and not _d4 and not _d6:
        s -= 6
    elif _rsi > 75 and not _d4 and not _d6 and not _be:
        s -= 3
    if r.get("bc") and not _be:
        s -= 3

    # ── NASDAQ-specific demotions ──────────────────────────────────────────────
    if profile == "nasdaq":
        if r.get("fuchsia_rl") and not r.get("um_2809") and not _be and not r.get("vbo_up") and not r.get("bx_up"):
            s -= 2
        if r.get("tz_sig", "") in ("T4", "T6"):
            _has_nq_ctx = (
                bool(r.get("_ztrap_recent_5b")) or
                bool(r.get("_l64_recent_5b"))   or
                bool(r.get("um_2809"))           or
                r.get("rtb_phase") == "C"        or
                r.get("rtb_transition") == "B_TO_C"
            )
            if not _has_nq_ctx:
                s -= 2

    # ── ALL-US profile — combo bonuses + bearish breakdown (cap 22 bull) ────────
    elif profile == "all_us":
        _ztrap_5  = bool(r.get("_ztrap_recent_5b"))
        _ztrap_15 = bool(r.get("_ztrap_recent_15b"))
        _l64_15   = bool(r.get("_l64_recent_15b"))
        _l22_15   = bool(r.get("_l22_recent_15b"))
        _l43_10   = bool(r.get("_l43_recent_10b"))
        _ns_5     = bool(r.get("_ns_recent_5b"))
        _ztrap_bg = _ztrap_5 or _ztrap_15
        _l_struct = _l64_15 or _l22_15
        _sq       = bool(r.get("sq"))
        _clm      = bool(r.get("climb_sig"))
        _load     = bool(r.get("load_sig"))
        _svs      = bool(r.get("svs_2809"))
        _t1_cur   = r.get("tz_sig", "") == "T1"
        _t1g_cur  = r.get("tz_sig", "") == "T1G"
        _t1_any   = _t1_cur or _t1g_cur

        au_bull = 0.0
        if _sq and _clm and _ztrap_bg:
            au_bull += 14
        if _sq and _load and _ztrap_bg:
            au_bull += 12
        if _l64_15 and _t1_any and _svs:
            au_bull += 12
        if (_l22_15 or _l64_15) and _sq and _ztrap_bg:
            au_bull += 12

        if _l43_10 and _clm and _ztrap_bg:
            au_bull += 10
        if _t1_any and _svs and _ns_5:
            au_bull += 10
        if _l64_15 and _clm and _load:
            au_bull += 9
        if _sq and bool(r.get("ns")) and _ztrap_bg:
            au_bull += 8
        if (_l22_15 or _l64_15) and _load and _clm and not _ztrap_bg:
            au_bull += 7

        _d4   = bool(r.get("d_absorb_bull") or r.get("d_spring"))
        _d6   = bool(r.get("d_surge_bull")  or r.get("d_blast_bull"))
        _l34_au  = bool(r.get("l34") or r.get("fri34"))
        _be_au   = bool(r.get("be_up"))
        _l34_r3_au  = bool(r.get("_l34_recent_3b") or r.get("_fri34_recent_3b"))
        _dabs_r5_au = bool(r.get("_dabsorb_recent_5b"))
        if _d6 and _be_au:
            au_bull += 10
        if _l34_r3_au and _d4 and not _l34_au:
            au_bull += 12
        if _dabs_r5_au and _be_au and not _d4:
            au_bull += 8

        s += min(au_bull, 22)

        _has_struct = _l_struct or _l43_10 or _ztrap_bg or _l34_au
        _has_activ  = _sq or _clm or _load or _svs or _t1_any

        if r.get("um_2809") and not _has_struct and not _has_activ:
            s -= 3
        if r.get("tz_sig", "") in ("T4", "T6"):
            if not _ztrap_bg and not _l_struct:
                s -= 3
        if r.get("fuchsia_rl") and not _has_activ and not _be_au:
            s -= 2
        if r.get("d_strong_bull") and not _l34_au and not _be_au and not _d4 and not _d6:
            s -= 2
        _rsi_au = float(r.get("rsi", 50.0))
        if _rsi_au > 80 and not _d4 and not _d6:
            s -= 5
        elif _rsi_au > 75 and not _d4 and not _d6 and not _be_au:
            s -= 2

        _t10t11_cur    = r.get("tz_sig", "") in ("T10", "T11", "T12")
        _t10t11_r5     = bool(r.get("_t10t11_recent_5b"))
        _rh            = bool(r.get("fuchsia_rh"))
        _bo_dn         = bool(r.get("bo_dn"))
        _bx_dn         = bool(r.get("bx_dn"))
        _breakdown     = _bo_dn or _bx_dn

        if (_t10t11_cur or _t10t11_r5) and _breakdown:
            s -= 8
        elif _rh and _breakdown:
            s -= 8
        if _rh and (_t10t11_cur or _t10t11_r5):
            s -= 5
        if _t10t11_cur and not _breakdown:
            s -= 2
        if _rh and not _breakdown and not _t10t11_cur:
            s -= 2

    s = max(0.0, s)

    # ── Composite setup signals (SMX / AKAN / NNN / MX / GOG) — cap 12 ──────
    _setup = 0.0
    if r.get("akan_sig"):  _setup += 8
    elif r.get("smx_sig"): _setup += 6
    if r.get("nnn_sig"):   _setup += 6
    if r.get("mx_sig"):    _setup += 5
    if r.get("gog_sig"):   _setup += 4
    s += min(_setup, 12)

    # ── Context / confirmation (uncapped, max ~18) ────────────────────────
    if r.get("x2g_wick"):      s += 5
    elif r.get("x2_wick"):     s += 4
    elif r.get("x1g_wick"):    s += 4
    elif r.get("x1_wick"):     s += 3
    elif r.get("x3_wick"):     s += 2
    if r.get("wick_bull"):     s += 5

    if r.get("para_retest"):                           s += 3
    elif r.get("para_plus") or r.get("para_start"):    s += 2

    if r.get("fly_abcd"):                              s += 4
    elif r.get("fly_cd") or r.get("fly_bd") or r.get("fly_ad"): s += 3

    if r.get("vol_spike_10x"): s += 10

    return round(min(100.0, s), 1)


__all__ = ["compute_turbo_score", "_TZ_W_SP500", "_TZ_W_NASDAQ", "_TZ_W_ALL_US"]
