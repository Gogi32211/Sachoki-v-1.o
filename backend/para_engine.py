"""
para_engine.py — 260420 Parabola Start Detector v3.6 (Turbo screener port).

Translates Pine Script stateful logic: base compression → seed → PARA/PARA+/RETEST,
with campaign lock / rearm / reset cycle.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _barssince(bool_arr: np.ndarray) -> np.ndarray:
    """Bars since last True (0=current bar is True, NaN if never seen)."""
    result = np.full(len(bool_arr), np.nan)
    last = -1
    for i, v in enumerate(bool_arr):
        if v:
            last = i
        if last >= 0:
            result[i] = i - last
    return result


def _rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothed MA (ta.atr / ta.rsi in Pine)."""
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = _rma(delta.clip(lower=0), length)
    loss  = _rma((-delta).clip(lower=0), length)
    rs    = gain / loss.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def compute_para(df: pd.DataFrame, is_daily: bool = False) -> dict:
    """
    Compute PREP, PARA, PARA+, RETEST signals on the last bar.

    Parameters use 260420 v3.6 defaults; daily chart uses relaxed thresholds.
    Returns dict: prep, para_start, para_plus, para_retest  (0 or 1 each).
    """
    zero = dict(prep=0, para_start=0, para_plus=0, para_retest=0)
    if df is None or len(df) < 60:
        return zero
    try:
        # ── Default parameters ────────────────────────────────────────────
        BASE_LEN      = 20
        BASE_MEM      = 8  if is_daily else 4
        ATR_LEN       = 14
        MAX_BASE_RNG  = 0.28
        MAX_ATR_PCT   = 0.09
        MAX_EMA_SPR   = 0.14
        EMA_F, EMA_M, EMA_S = 9, 20, 50
        DRY_REC, DRY_OLD    = 5, 10
        DRY_FACTOR           = 0.85
        BRK_LEN       = 20
        VOL_LEN       = 20
        VOL_MUL_AVG   = 1.10 if is_daily else 1.25
        VOL_MUL_PREV  = 1.05 if is_daily else 1.10
        MIN_CPOS      = 0.55
        MIN_BODY      = 0.22
        SEED_WIN      = 8  if is_daily else 4
        MAX_RUN       = 1.40 if is_daily else 0.90
        MAX_DIST      = 0.70 if is_daily else 0.40
        MAX_RSI       = 98.0 if is_daily else 92.0
        COOLDOWN      = 6
        REARM_MIN     = 7
        MINI_LEN      = 4
        MAX_MINI      = 0.10
        REARM_EMA     = 0.08
        RESET_PB      = 0.18
        MAX_RTESTS    = 2
        RTEST_CD      = 3
        RTEST_BARS    = 8
        RTEST_DEPTH   = 0.10
        VE_WIN        = 2

        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        c = df["close"].values.astype(float)
        o = df["open"].values.astype(float)
        v = df["volume"].values.astype(float)
        n = len(c)

        # ── EMAs ──────────────────────────────────────────────────────────
        def _ema(arr, span):
            s = pd.Series(arr).ewm(span=span, adjust=False).mean().values
            return s

        ef = _ema(c, EMA_F)
        em = _ema(c, EMA_M)
        es_ = _ema(c, EMA_S)

        # ── ATR (Wilder's) ────────────────────────────────────────────────
        prev_c = np.concatenate([[np.nan], c[:-1]])
        tr_arr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
        atr    = _rma(pd.Series(tr_arr), ATR_LEN).values

        vol_ma = pd.Series(v).rolling(VOL_LEN, min_periods=max(1, VOL_LEN // 2)).mean().values
        rsi_v  = _rsi(pd.Series(c), 14).values

        # ── Base detection using [1]-shifted series ────────────────────────
        h1 = np.concatenate([[np.nan], h[:-1]])
        l1 = np.concatenate([[np.nan], l[:-1]])
        c1 = np.concatenate([[np.nan], c[:-1]])
        ef1 = np.concatenate([[np.nan], ef[:-1]])
        em1 = np.concatenate([[np.nan], em[:-1]])
        es1 = np.concatenate([[np.nan], es_[:-1]])

        def _rolling_max(arr, w):
            return pd.Series(arr).rolling(w, min_periods=w).max().values

        def _rolling_min(arr, w):
            return pd.Series(arr).rolling(w, min_periods=w).min().values

        def _rolling_mean(arr, w, min_p=1):
            return pd.Series(arr).rolling(w, min_periods=min_p).mean().values

        base_high = _rolling_max(h1, BASE_LEN)
        base_low  = _rolling_min(l1, BASE_LEN)
        base_mid  = (base_high + base_low) / 2.0

        with np.errstate(invalid='ignore', divide='ignore'):
            base_rng = np.where(base_mid != 0, (base_high - base_low) / base_mid, np.nan)
            atr_pct1 = np.where(c1 != 0, np.concatenate([[np.nan], atr[:-1]]) / c1, np.nan)

        ema_top = np.maximum(ef1, np.maximum(em1, es1))
        ema_bot = np.minimum(ef1, np.minimum(em1, es1))
        with np.errstate(invalid='ignore', divide='ignore'):
            ema_spr = np.where(c1 != 0, (ema_top - ema_bot) / c1, np.nan)

        base_ready = (
            np.isfinite(base_rng) & (base_rng <= MAX_BASE_RNG) &
            np.isfinite(atr_pct1) & (atr_pct1 <= MAX_ATR_PCT) &
            np.isfinite(ema_spr)  & (ema_spr  <= MAX_EMA_SPR)
        )
        recent_base = np.isfinite(_barssince(base_ready)) & (_barssince(base_ready) <= BASE_MEM)

        # ── EMA structure ─────────────────────────────────────────────────
        ema_ok = (c > em) & (em >= em1) & (em > es_)

        # ── Dry volume ────────────────────────────────────────────────────
        v1         = np.concatenate([[np.nan], v[:-1]])
        dry_rec    = _rolling_mean(v1, DRY_REC)
        dry_old    = _rolling_mean(np.concatenate([np.full(DRY_REC + 1, np.nan), v[:-(DRY_REC + 1)]]), DRY_OLD)
        dry_ref    = _rolling_mean(v1, VOL_LEN)
        dry_ok     = (
            np.isfinite(dry_rec) & np.isfinite(dry_old) &
            (dry_rec <= dry_old * DRY_FACTOR) &
            np.isfinite(dry_ref) & (dry_rec < dry_ref)
        )

        # ── Breakout ──────────────────────────────────────────────────────
        brk_from_base = base_high
        brk_lookback  = _rolling_max(h1, BRK_LEN)
        brk_ref       = np.maximum(brk_from_base, brk_lookback)

        bar_rng = h - l
        with np.errstate(invalid='ignore', divide='ignore'):
            cpos = np.where(bar_rng > 0, (c - l) / bar_rng, 0.0)
            bpct = np.where(bar_rng > 0, np.abs(c - o) / bar_rng, 0.0)

        price_brk = (h > brk_ref) & ((c > brk_ref) | (cpos >= MIN_CPOS))
        if is_daily:
            price_brk = price_brk | ((h > brk_ref) & (c > ef))

        v_prev = np.concatenate([[np.nan], v[:-1]])
        v_roll2 = pd.Series(v).rolling(2, min_periods=1).max().values
        if is_daily:
            vol_ign = np.isfinite(vol_ma) & (
                (v >= vol_ma * VOL_MUL_AVG) |
                (v >= v_prev * VOL_MUL_PREV) |
                (v_roll2 >= vol_ma * VOL_MUL_AVG)
            )
            cand_ok = (c > o) | (cpos >= MIN_CPOS)
        else:
            vol_ign = np.isfinite(vol_ma) & (
                (v >= vol_ma * VOL_MUL_AVG) |
                (v >= v_prev * VOL_MUL_PREV)
            )
            cand_ok = (c > o) & (cpos >= MIN_CPOS) & (bpct >= MIN_BODY)

        with np.errstate(invalid='ignore', divide='ignore'):
            run_pct  = np.where(base_low != 0, (c - base_low) / base_low, np.nan)
            dist_em  = np.where(em != 0, (c - em) / em, np.nan)

        early = (
            np.isfinite(run_pct) & np.isfinite(dist_em) &
            (run_pct <= MAX_RUN) & (dist_em <= MAX_DIST) &
            np.isfinite(rsi_v)   & (rsi_v <= MAX_RSI)
        )

        # ── V+E & seed ────────────────────────────────────────────────────
        ve_coin   = vol_ign & early
        ve_bs     = _barssince(ve_coin)
        recent_ve = np.isfinite(ve_bs) & (ve_bs <= VE_WIN)

        ve_ign    = recent_base & price_brk & ema_ok & (c >= em) & recent_ve
        seed_core = recent_base & price_brk & cand_ok & vol_ign & ema_ok
        seed      = seed_core | ve_ign

        seed_bs   = _barssince(seed)
        sw_active = np.isfinite(seed_bs) & (seed_bs >= 0) & (seed_bs <= SEED_WIN)

        c_prev    = np.concatenate([[np.nan], c[:-1]])
        para_raw  = (
            sw_active & (c >= em) &
            ((c > c_prev) | seed) &
            (early | recent_ve)
        )

        # ── PREP zone (stateless) ─────────────────────────────────────────
        prep_zone = recent_base & ema_ok & (c >= em) & (c <= brk_ref * (1.04 if is_daily else 1.02))
        pz_prev   = np.concatenate([[False], prep_zone[:-1]])
        prep_sig  = prep_zone & ~pz_prev

        # ── Mini-base & rearm-near-ema ─────────────────────────────────────
        mb_high  = _rolling_max(h1, MINI_LEN)
        mb_low   = _rolling_min(l1, MINI_LEN)
        mb_mid   = (mb_high + mb_low) / 2.0
        with np.errstate(invalid='ignore', divide='ignore'):
            mb_rng   = np.where(mb_mid != 0, (mb_high - mb_low) / mb_mid, np.nan)
        mb_tight = np.isfinite(mb_rng) & (mb_rng <= MAX_MINI)

        with np.errstate(invalid='ignore', divide='ignore'):
            rearm_ema_cond = (em != 0) & (
                (np.abs(c - em) / em <= REARM_EMA) |
                (l <= ef) |
                (l <= em * (1 + REARM_EMA))
            )

        # ── Sequential: campaign state ────────────────────────────────────
        para_arr  = np.zeros(n, dtype=np.int8)
        plus_arr  = np.zeros(n, dtype=np.int8)
        rtest_arr = np.zeros(n, dtype=np.int8)

        in_camp   = False
        last_para = -99999
        camp_high = np.nan
        r_count   = 0
        last_rt   = -99999

        for i in range(n):
            rearm = (
                in_camp and
                (i - last_para) >= REARM_MIN and
                (bool(mb_tight[i]) or bool(base_ready[i])) and
                bool(rearm_ema_cond[i])
            )
            gate     = (not in_camp) or rearm
            cd_ok    = (i - last_para) > COOLDOWN
            pv       = bool(para_raw[i]) and gate and cd_ok
            ve_up    = bool(recent_ve[i]) and float(c[i]) >= float(ef[i])
            plv      = pv and (bool(dry_ok[i]) or ve_up)

            if pv:
                if not in_camp:
                    r_count, last_rt, camp_high = 0, -99999, float(h[i])
                else:
                    camp_high = max(camp_high, float(h[i])) if not np.isnan(camp_high) else float(h[i])
                in_camp, last_para = True, i
            elif in_camp:
                camp_high = max(camp_high, float(h[i])) if not np.isnan(camp_high) else float(h[i])

            para_arr[i] = int(pv)
            plus_arr[i] = int(plv)

            bfp      = i - last_para if last_para > -9000 else 99999
            in_rwin  = in_camp and 1 <= bfp <= RTEST_BARS
            em_v     = float(em[i])
            rt_near  = em_v != 0 and abs(float(l[i]) - em_v) / em_v <= RTEST_DEPTH
            rt_held  = float(c[i]) > em_v and em_v > float(es_[i])
            h_p      = float(h[i-1]) if i > 0 else 0.0
            rt_bull  = float(c[i]) > float(o[i]) and float(c[i]) > h_p
            vm_v     = float(vol_ma[i])
            rt_vol   = not np.isnan(vm_v) and float(v[i]) >= vm_v * 0.9
            rt_raw   = in_camp and in_rwin and rt_near and rt_held and rt_bull and rt_vol
            rt_cd    = (i - last_rt) > RTEST_CD if last_rt > -9000 else True
            rv       = rt_raw and r_count < MAX_RTESTS and rt_cd
            if rv:
                r_count += 1
                last_rt  = i
            rtest_arr[i] = int(rv)

            # Campaign reset
            if not pv and in_camp:
                cv   = float(c[i])
                emv  = float(em[i])
                esv  = float(es_[i])
                efv  = float(ef[i])
                em_p = float(em[i-1]) if i > 0 else emv
                c_p  = float(c[i-1])  if i > 0 else cv
                rst  = (
                    cv < esv or
                    (cv < emv and c_p < em_p and emv <= em_p) or
                    (not np.isnan(camp_high) and cv <= camp_high * (1.0 - RESET_PB) and cv < efv)
                )
                if rst:
                    in_camp, camp_high, r_count, last_rt = False, np.nan, 0, -99999

        return dict(
            prep        = int(bool(prep_sig[-1])),
            para_start  = int(para_arr[-1]),
            para_plus   = int(plus_arr[-1]),
            para_retest = int(rtest_arr[-1]),
            # internal arrays exposed for series mode
            _prep_arr   = prep_sig,
            _para_arr   = para_arr,
            _plus_arr   = plus_arr,
            _rtest_arr  = rtest_arr,
        )

    except Exception:
        return zero


def compute_para_series(df: pd.DataFrame, is_daily: bool = False) -> "pd.DataFrame | None":
    """Return per-bar PARA signals as a DataFrame (all bars, not just last)."""
    res = compute_para(df, is_daily=is_daily)
    if not res or "_para_arr" not in res:
        return None
    return pd.DataFrame({
        "para_start":  res["_para_arr"].astype(bool),
        "para_plus":   res["_plus_arr"].astype(bool),
        "para_retest": res["_rtest_arr"].astype(bool),
        "para_prep":   res["_prep_arr"].astype(bool),
    })
