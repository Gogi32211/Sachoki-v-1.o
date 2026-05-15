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


def _map_signals_to_row(symbol: str, signals: dict, df: pd.DataFrame | None) -> dict:
    """Map compute_signals() output to a flat ultra_score row dict."""
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

    return {
        # Identity
        "symbol": symbol,
        "ticker": symbol,
        "price":  price,
        # ── Breakout / trigger signals ───────────────────────────────────────
        # buy_2809: strong bullish setup — EMA aligned, elevated volume, momentum
        "buy_2809":  bool(e2050 and pa20 and vr >= 1.5 and mom5 >= 3.0),
        # rocket: explosive breakout — very high volume + strong momentum
        "rocket":    bool(vr >= 2.5 and mom5 >= 6.0 and pa20),
        # bb_brk: price breaks above Bollinger Band upper
        "bb_brk":    bb_brk,
        # bx_up: volume breakout upward
        "bx_up":     bool(vr >= 2.0 and mom5 >= 4.0),
        # eb_bull: EMA bull structure with RSI confirmation
        "eb_bull":   bool(e2050 and pa50 and rsi >= 55),
        # be_up: momentum entry with price above EMA20
        "be_up":     bool(mom5 >= 3.0 and pa20 and rsi >= 50),
        # bo_up: volume breakout above EMA50
        "bo_up":     bool(vr >= 1.8 and pa50),
        # ── Setup / accumulation signals ─────────────────────────────────────
        # abs_sig: accumulation — above EMA50, rising volume, RSI not extended
        "abs_sig":   bool(pa50 and vr >= 1.2 and 40.0 <= rsi <= 65.0 and mom5 >= 0.0),
        # va: volume absorption — above EMAs, RSI in healthy zone
        "va":        bool(pa20 and e2050 and 50.0 <= rsi <= 70.0),
        "svs_2809":  False,
        # climb_sig: steady climb above EMA20
        "climb_sig": bool(pa20 and mom5 >= 1.5),
        # load_sig: loading / accumulation below RSI 55 with elevated volume
        "load_sig":  bool(pa50 and vr >= 1.3 and rsi < 55.0),
        # strong_sig: strong setup — above EMA20, RSI healthy, positive momentum
        "strong_sig": bool(pa20 and rsi >= 55.0 and mom5 >= 2.0),
        "best_sig":  False,
        "l34":       False,
        "fri34":     False,
        # tz_bull_flip: EMA20 crossed above EMA50 within last 5 bars
        "tz_bull_flip": bool(cross),
        # ── Confirmation / quality signals ───────────────────────────────────
        # rs_strong: relative strength — RSI ≥62 with positive momentum
        "rs_strong": bool(rsi >= 62.0 and mom5 >= 1.5),
        # ── Extension / penalty signals ──────────────────────────────────────
        "already_extended": bool(mom5 >= 25.0 or rsi >= 78.0),
        "rsi_extended":     bool(rsi >= 78.0),
        "cci_extended":     False,
        # ── Context fields (not available from OHLCV — neutral) ──────────────
        "profile_score":    -1,   # -1 → contributes 0 to scoring
        "profile_category": "",
        "tz_intel":         {},
        "pullback":         {},
        "rare_reversal":    {},
        "abr":              {},
        "FINAL_REGIME":     "",
    }


def compute_scanner_ultra_candidate(
    symbol:         str,
    signals:        dict,
    timeframe:      str = "1d",
    df:             pd.DataFrame | None = None,
    temp_candidate: dict | None = None,
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

    row    = _map_signals_to_row(symbol, signals, df)
    scored = compute_ultra_score(row)
    base   = temp_candidate or {}

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
        # Reasons, signals, and flags from real scorer
        "ultra_score_reasons": scored["ultra_score_reasons"],
        "why_selected":        scored["ultra_score_reasons"],
        "signals":             scored["ultra_score_reasons"],   # active Ultra signal tokens
        "ultra_score_flags":   scored["ultra_score_flags"],
        "risk_flags":          scored["ultra_score_flags"],
        # Scoring diagnostics
        "ultra_score_raw_before_penalty": scored["ultra_score_raw_before_penalty"],
        "ultra_score_penalty_total":      scored["ultra_score_penalty_total"],
        "ultra_score_regime_bonus":       scored["ultra_score_regime_bonus"],
        "ultra_score_caps_applied":       scored["ultra_score_caps_applied"],
        "ultra_score_cap_reason":         scored.get("ultra_score_cap_reason", ""),
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
