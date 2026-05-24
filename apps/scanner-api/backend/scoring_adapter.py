"""
scoring_adapter.py — Phase 7A: safe bridge from OHLCV signals → real Ultra score.

Maps compute_signals() output (EMA, RSI, volume, momentum) to the flat boolean
row shape expected by compute_ultra_score(), then calls it.

No imports from root backend/ — ultra_score.py and ultra_signal_parser.py
(stub) are co-located in this package.

Context signals (profile_score, tz_intel, pullback, rare_reversal, abr,
FINAL_REGIME) are not available from OHLCV data alone; they are set to
neutral/empty values so their scoring contribution is zero rather than
misleading.
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

SCORE_ENGINE = "real_ultra_score"


def _map_signals_to_row(
    symbol: str,
    signals: dict,
    df: pd.DataFrame | None,
    latest_bar: dict | None = None,
) -> dict:
    """Map compute_signals() + real engine output to a flat ultra_score row dict.

    Phase 8G commit 5: when latest_bar is provided, REAL signals from the
    engine_registry pipeline drive the boolean row. The legacy rule-of-thumb
    proxies remain only as a fallback when latest_bar is None.
    """
    pa20  = signals.get("price_above_ema20", False)
    pa50  = signals.get("price_above_ema50", False)
    e2050 = signals.get("ema20_above_ema50", False)
    cross = signals.get("ema_cross_up_5d", False)
    rsi   = float(signals.get("rsi") or 50.0)
    vr    = float(signals.get("vol_ratio") or 1.0)
    mom5  = float(signals.get("mom5d_pct") or 0.0)
    price = float(signals.get("price") or 0.0)

    # Bollinger Band breakout — requires DataFrame
    bb_brk = False
    if df is not None and len(df) >= 20:
        try:
            close     = df["close"]
            bb_upper  = close.rolling(20, min_periods=1).mean() + 2.0 * close.rolling(20, min_periods=1).std()
            bb_brk    = bool(price > float(bb_upper.iloc[-1]))
        except Exception:
            pass

    # ── Real signals (preferred) ─────────────────────────────────────────────
    real_signals = (latest_bar or {}).get("signals") or {}
    have_real    = bool(real_signals)

    if have_real:
        t_sigs    = set(real_signals.get("t",    []))
        l_sigs    = set(real_signals.get("l",    []))
        f_sigs    = set(real_signals.get("f",    []))
        i_sigs    = set(real_signals.get("i",    []))  # combo
        vabs_sigs = set(real_signals.get("vabs", []))
        wick_sigs = set(real_signals.get("wick", []))
        ult_sigs  = set(real_signals.get("ult",  []))

        # Combo (I row) → trigger booleans (labels match COMBO_SIG_COLS)
        buy_2809_b = "BUY"   in i_sigs
        rocket_b   = "ROCKET" in i_sigs
        bb_brk_b   = "BB↑"   in i_sigs
        svs_2809_b = "SVS"   in i_sigs
        # L row → breakout booleans
        bx_up_b    = "BX↑"   in l_sigs
        bo_up_b    = "BO↑"   in l_sigs
        be_up_b    = "BE↑"   in l_sigs
        # VABS row
        abs_sig_b   = "ABS"    in vabs_sigs
        climb_sig_b = "CLB"    in vabs_sigs
        load_sig_b  = "LD"     in vabs_sigs
        strong_sig_b = "STRONG" in vabs_sigs
        best_sig_b  = "BEST★"  in vabs_sigs
        # ULT row (still empty until commit 8, but check anyway)
        eb_bull_b = "EB↑" in ult_sigs
        # L row → setup
        l34_b   = "L34"   in l_sigs
        fri34_b = "FRI34" in f_sigs
        # T row → TZ flip
        tz_bull_flip_b = bool(t_sigs)
        # Combo VA badge
        va_b = "VA" in i_sigs
    else:
        # Fallback: legacy inferred-proxy logic.
        buy_2809_b   = bool(e2050 and pa20 and vr >= 1.5 and mom5 >= 3.0)
        rocket_b     = bool(vr >= 2.5 and mom5 >= 6.0 and pa20)
        bb_brk_b     = bb_brk
        bx_up_b      = bool(vr >= 2.0 and mom5 >= 4.0)
        eb_bull_b    = bool(e2050 and pa50 and rsi >= 55)
        be_up_b      = bool(mom5 >= 3.0 and pa20 and rsi >= 50)
        bo_up_b      = bool(vr >= 1.8 and pa50)
        abs_sig_b    = bool(pa50 and vr >= 1.2 and 40.0 <= rsi <= 65.0 and mom5 >= 0.0)
        va_b         = bool(pa20 and e2050 and 50.0 <= rsi <= 70.0)
        svs_2809_b   = False
        climb_sig_b  = bool(pa20 and mom5 >= 1.5)
        load_sig_b   = bool(pa50 and vr >= 1.3 and rsi < 55.0)
        strong_sig_b = bool(pa20 and rsi >= 55.0 and mom5 >= 2.0)
        best_sig_b   = False
        l34_b        = False
        fri34_b      = False
        tz_bull_flip_b = bool(cross)

    return {
        # Identity
        "symbol": symbol,
        "ticker": symbol,
        "price":  price,
        # ── Breakout / trigger signals ───────────────────────────────────────
        "buy_2809":   buy_2809_b,
        "rocket":     rocket_b,
        "bb_brk":     bb_brk_b,
        "bx_up":      bx_up_b,
        "eb_bull":    eb_bull_b,
        "be_up":      be_up_b,
        "bo_up":      bo_up_b,
        # ── Setup / accumulation signals ─────────────────────────────────────
        "abs_sig":    abs_sig_b,
        "va":         va_b,
        "svs_2809":   svs_2809_b,
        "climb_sig":  climb_sig_b,
        "load_sig":   load_sig_b,
        "strong_sig": strong_sig_b,
        "best_sig":   best_sig_b,
        "l34":        l34_b,
        "fri34":      fri34_b,
        "tz_bull_flip": tz_bull_flip_b,
        # ── Confirmation / quality signals ───────────────────────────────────
        "rs_strong": bool(rsi >= 62.0 and mom5 >= 1.5),
        # ── Extension / penalty signals ──────────────────────────────────────
        "already_extended": bool(mom5 >= 25.0 or rsi >= 78.0),
        "rsi_extended":     bool(rsi >= 78.0),
        "cci_extended":     False,
        # ── Context fields (not available from OHLCV — neutral) ──────────────
        "profile_score":    -1,
        "profile_category": "",
        "tz_intel":         {},
        "pullback":         {},
        "rare_reversal":    {},
        "abr":              {},
        "FINAL_REGIME":     "",
        # Debug flag — was the row built from real signals or proxies?
        "_signal_source": "engine_registry" if have_real else "inferred_proxy",
    }


def compute_scanner_ultra_candidate(
    symbol:         str,
    signals:        dict,
    timeframe:      str = "1d",
    df:             pd.DataFrame | None = None,
    temp_candidate: dict | None = None,
    latest_bar:     dict | None = None,
) -> dict:
    """
    Compute a candidate using the real Ultra scoring engine.

    Args:
        symbol:         Ticker symbol (uppercase).
        signals:        Output of scan_engine.compute_signals().
        timeframe:      Candle timeframe string.
        df:             Raw OHLCV DataFrame — enables BB computation.
        temp_candidate: Optional temporary-scorer output to inherit
                        metadata fields (sector, company, etc.).

    Returns:
        Candidate dict in the same shape as score_candidate(), scored by
        the real Ultra engine. score_engine field is "real_ultra_score".
    """
    from .ultra_score import compute_ultra_score

    # ── Phase 8I-fix: single-row scoring ─────────────────────────────────────
    # Old `ultra_orchestrator._attach_ultra_score(row)` passed the FULL Turbo
    # row (with rtb_phase, turbo_score, every signal flag) directly to
    # compute_ultra_score. The Phase 8H+8I scoring_adapter was rebuilding a
    # synthetic row from signal labels — that meant turbo and ultra were
    # scored on TWO parallel rows, breaking single-source-of-truth.
    #
    # New rule: if engine_registry already produced a turbo_row for this
    # bar, use it verbatim. Only fall back to _map_signals_to_row when the
    # registry didn't run (legacy / fast path / scoring_mode='temporary').
    turbo_row = (latest_bar or {}).get("_turbo_row") if latest_bar else None
    if turbo_row:
        row = dict(turbo_row)   # copy so we don't mutate the bar's row
        row.setdefault("symbol", symbol)
        row.setdefault("ticker", symbol)
        row["_signal_source"] = "engine_registry_turbo_row"
    else:
        row = _map_signals_to_row(symbol, signals, df, latest_bar=latest_bar)
    scored = compute_ultra_score(row)
    base   = temp_candidate or {}

    # ── Old-Ultra scoring fieldset ───────────────────────────────────────────
    # real_ultra_score: raw pre-band float (matches old Ultra naming)
    # signal_score: simple tally of active boolean triggers (lightweight stand-in
    #               for old turbo's signal-points sum — keeps a numeric handle on
    #               "how many real signals fired" until turbo_engine ports)
    # final_bull_score / final_bear_score: bull = ultra_score; bear is a
    #               placeholder mirror (commit 8 ports the dedicated bear path)
    real_ultra_score = float(scored.get("ultra_score_raw_before_penalty") or
                             scored.get("ultra_score") or 0.0)
    active_count = sum(1 for k in (
        "buy_2809", "rocket", "bb_brk", "bx_up", "eb_bull", "be_up", "bo_up",
        "abs_sig", "va", "svs_2809", "climb_sig", "load_sig", "strong_sig",
        "best_sig", "l34", "fri34", "tz_bull_flip", "rs_strong",
    ) if row.get(k))
    signal_score    = active_count * 5  # 5 pts per active signal (cap-aware)
    final_bull      = float(scored.get("ultra_score") or 0)
    # Penalize bear when bull triggers strongly; until real bear engine lands
    final_bear      = max(0.0, 100.0 - final_bull) if active_count >= 2 else 0.0
    sector_band     = base.get("sector_band") or ""
    profile_cat     = ""    # filled by profile_playbook port (commit 8)
    pf_value        = None  # filled by profile_playbook port (commit 8)

    return {
        # Identity (inherit from temp_candidate when available)
        "symbol":    symbol,
        "ticker":    symbol,
        "company":   base.get("company", ""),
        "sector":    base.get("sector", ""),      # filled by run_controlled_scan via sector_map
        "industry":  base.get("industry", ""),    # filled by run_controlled_scan via sector_map
        # Price / market data
        "price":      signals.get("price"),
        "prev_close": signals.get("prev_close"),
        "change_pct": signals.get("change_pct"),
        "volume":     base.get("volume"),
        # Real Ultra scores
        "ultra_score":          scored["ultra_score"],
        "ultra_score_band_v2":  scored["ultra_score_band_v2"],
        "ultra_score_band":     scored["ultra_score_band"],
        "band":                 scored["ultra_score_band_v2"],
        "ultra_score_priority": scored["ultra_score_priority"],
        "priority":             scored["ultra_score_priority"],
        # Computed technical signals
        "ema20":     signals.get("ema20"),
        "ema50":     signals.get("ema50"),
        "rsi":       signals.get("rsi"),
        "vol_ratio": signals.get("vol_ratio"),
        "mom5d_pct": signals.get("mom5d_pct"),
        # Reasons + flags from real scorer.
        # NOTE: "signals" key removed here — the normalized signal payload
        # from engine_registry is attached by scan_engine.run_controlled_scan
        # and must not be clobbered with the reasons list.
        "ultra_score_reasons": scored["ultra_score_reasons"],
        "why_selected":        scored["ultra_score_reasons"],
        "ultra_active_signals": scored["ultra_score_reasons"],  # active Ultra signal tokens (alias)
        "ultra_score_flags":   scored["ultra_score_flags"],
        "risk_flags":          scored["ultra_score_flags"],
        # Scoring diagnostics
        "ultra_score_raw_before_penalty": scored["ultra_score_raw_before_penalty"],
        "ultra_score_penalty_total":      scored["ultra_score_penalty_total"],
        "ultra_score_regime_bonus":       scored["ultra_score_regime_bonus"],
        "ultra_score_caps_applied":       scored["ultra_score_caps_applied"],
        "ultra_score_cap_reason":         scored.get("ultra_score_cap_reason", ""),
        # Old-Ultra scoring fields
        "real_ultra_score":  real_ultra_score,
        "signal_score":      signal_score,
        "final_bull_score":  final_bull,
        "final_bear_score":  final_bear,
        "pf":                pf_value,
        "cat":               profile_cat,
        "category":          profile_cat,
        "sector_band":       sector_band,
        "signal_source":     row.get("_signal_source", "inferred_proxy"),
        # Signal slots (not available from OHLCV pipeline)
        "final_signal":   "",
        "action_bucket":  "",
        "sequence_4bar":  "",
        "abr_category":   "",
        "wlnbb_bucket":   "",
        "ema_state":      "",
        # Metadata
        "score_engine":   SCORE_ENGINE,
        "data_provider":  "massive",
        "source":         "scanner-api-real-ultra",
        "timeframe":      timeframe,
    }
