"""
Canonical Scoring Engine — single source of truth for all score components.

All app modules (TURBO scanner, Superchart, stock_stat export, Replay Analytics)
must call compute_canonical_score() with the same sig_row dict and receive
identical FINAL_BULL_SCORE, FINAL_REGIME, and all sub-score columns.

No other module may recompute these values independently.

Data flow:
    Raw OHLCV + signal engines
        ↓
    sig_row dict (boolean signal flags)
        ↓
    compute_canonical_score(sig_row, profile)
        ↓
    canonical scored dict (FINAL_BULL_SCORE, FINAL_REGIME, sub-scores …)
        ↓
    All downstream: TURBO UI, Superchart, stock_stat CSV, Replay reports
"""

from turbo_engine import _calc_turbo_score

SCORING_ENGINE_NAME    = "canonical_v1"
SCORING_ENGINE_VERSION = "1.0"

# Score bucket edges  (lower bound inclusive, upper bound exclusive except last)
_BUCKET_EDGES = [
    (140, "ELITE_140+"),
    (120, "STRONG_120+"),
    (100, "BULL_100+"),
    (80,  "CONFIRMED_80+"),
    (60,  "ACTIONABLE_60+"),
    (40,  "EARLY_40+"),
    (20,  "WEAK_20+"),
    (0,   "NEUTRAL"),
]


# ── Sub-score helpers ──────────────────────────────────────────────────────────

def _rocket_score(r: dict) -> float:
    """Para-based launch signals — identifies explosive move setups."""
    s = 0.0
    if r.get("rocket"):       s += 25   # full para launch (PARA_PLUS + combo)
    elif r.get("buy_2809"):   s += 8    # 2809 buy setup (pre-launch)
    if r.get("seq_bcont"):    s += 8    # sequential bar continuation
    if r.get("vol_spike_10x"):s += 6    # volume 10× spike
    return min(s, 40.0)


def _clean_entry_score(r: dict, profile: str = "sp500") -> float:
    """Quality entry-signal cluster — F-class and B-class precise entries."""
    s = 0.0
    is_nq = profile == "nasdaq"
    # F-signal (clean entry patterns) — ranked by strength
    if r.get("f8"):    s += 12
    if r.get("f6"):    s += 10
    if r.get("f3"):    s += 5
    if r.get("f4"):    s += 5
    if r.get("f11"):   s += 2 if is_nq else 5   # NQ→2 (SIG_F11 avg10=−0.41% on NQ)
    # B-signal (breakout confirm entries)
    if r.get("b8"):    s += 5
    if r.get("b6"):    s += 5
    # Momentum / structure confirm
    if r.get("ultra_3up"): s += 8  # triple-up pattern
    if r.get("blue"):      s += 6  # WLNBB BLUE confirm
    # FLY patterns (clean harmonic entry)
    if r.get("fly_abcd"): s += 8
    if r.get("fly_cd"):   s += 5
    if r.get("fly_bd"):   s += 4
    # SP500-only boosts (Section 1C: signals positive on SP500, negative/neutral on NQ)
    if not is_nq:
        if r.get("f9"):        s += 6   # SIG_F9:  SP avg10=+0.89%, NQ=−0.44%
        if r.get("f5"):        s += 5   # SIG_F5:  SP avg10=+0.92%, NQ=−0.19%
        if r.get("svs_2809"):  s += 8   # SIG_SVS: SP avg10=+1.03%, NQ=−0.26%
        if r.get("buy_2809"):  s += 8   # SIG_BUY: SP avg10=+1.23%
        if r.get("g4"):        s += 5   # SIG_G4:  SP avg10=+0.94%
        if r.get("g1"):        s += 5   # SIG_G1:  SP avg10=+0.85%
        if r.get("strong_sig"):s += 5   # SIG_STRONG: SP avg10=+0.83%
        if r.get("eb_bull"):   s += 5   # SIG_EB_UP: SP avg10=+0.78%
        if r.get("be_up"):     s += 5   # SIG_BE_ANY: SP avg10=+0.80%
    return min(s, 40.0)


def _shakeout_absorb_score(r: dict) -> float:
    """Shakeout / absorption entry — bearish flush absorbed into bull move."""
    s = 0.0
    if r.get("be_up"):    s += 18   # full-body engulf up (strongest absorb)
    if r.get("eb_bull"):  s += 10   # expansion bull bar after shakeout
    if r.get("fbo_bull"): s += 8    # false-breakout-down → bull recovery
    return min(s, 30.0)


def _extra_bull_score(r: dict, profile: str = "sp500") -> float:
    """Extra bullish structure confirms — L-family and trend signals."""
    s = 0.0
    is_nq = profile == "nasdaq"
    if r.get("l34"):         s += 6
    if r.get("fri43"):       s += 0 if is_nq else 5  # NQ→0 (avg10=−0.79% on NQ)
    if r.get("fuchsia_rl"):  s += 1                   # BOTH→1 (avg10=−0.46% both)
    if r.get("cci_ready"):   s += 4
    if r.get("bx_up"):       s += 3
    if r.get("l43"):         s += 3
    # Exchange-specific signals
    if is_nq:
        if r.get("b9"):      s += 5   # SIG_B9: NQ only (NQ1 alone +2.53%, n=32)
        if r.get("fri64"):   s += 2   # SIG_FRI64: NQ→2 (reduce from 5)
    else:
        if r.get("l555"):      s += 6  # SIG_L555: SP avg10=+0.20%, NQ=−1.53%
        if r.get("fri64"):     s += 5  # SIG_FRI64: SP avg10=+0.31%
        if r.get("best_long"): s += 8  # SIG_BEST_UP: SP avg10=+0.22%, NQ=−2.96%
    return min(s, 20.0)


def _experimental_score(r: dict) -> float:
    """Experimental / newer signals under evaluation."""
    s = 0.0
    if r.get("fly_abcd"): s += 8
    if r.get("fly_cd"):   s += 5
    if r.get("fly_bd"):   s += 4
    if r.get("fly_ad"):   s += 3
    return min(s, 15.0)


def _rebound_squeeze_score(r: dict, profile: str = "sp500") -> float:
    """Rebound / squeeze-exit signals — TZ_FLIP, cycle approach."""
    s = 0.0
    is_nq = profile == "nasdaq"
    if r.get("tz_bull_flip"):  s += 8
    if r.get("ca"):            s += 5
    if r.get("tz_attempt"):    s += 4
    if r.get("tz_weak_bull"):  s += 2 if is_nq else 3  # NQ→2 (SIG_WK_UP reduce)
    return min(s, 20.0)


def _hard_bear_score(r: dict, profile: str = "sp500") -> float:
    """Hard bear / risk signals — penalizes missed wins from bear context."""
    s = 0.0
    is_nq = profile == "nasdaq"
    if r.get("fbo_bear"):   s += 0 if is_nq else 20  # NQ: remove (avg10=−0.03%), SP keep
    if r.get("fuchsia_rh"): s += 3 if is_nq else 10  # NQ→3 (reduce from 10)
    if r.get("bo_dn"):      s += 12
    if r.get("bx_dn"):      s += 8
    if r.get("eb_bear"):    s += 8
    # SP500-only bear context signals (negative predictive on SP500, irrelevant on NQ)
    if not is_nq:
        if r.get("bias_down"):    s += 6  # SIG_BIAS_DN: SP avg10=+1.20% (bear-in-bull ctx)
        if r.get("cci_0_retest"): s += 5  # SIG_CCI0R: SP avg10=+0.39% bear-watch signal
    return min(s, 40.0)


def _volatility_risk_score(r: dict) -> float:
    """Elevated volatility / risk context."""
    s = 0.0
    if r.get("vol_spike_10x"): s += 10
    return min(s, 20.0)


# ── Regime logic ───────────────────────────────────────────────────────────────

def _final_regime(fbs: float, rocket: float, clean: float,
                  shakeout: float, rebound: float, hard_bear: float,
                  r: dict) -> str:
    """
    Derive FINAL_REGIME from sub-scores.
    Priority order: BEARISH → ROCKET → ELITE → STRONG → CLEAN → SHAKEOUT →
                    REBOUND → ACTIONABLE → EARLY_WATCH → NEUTRAL
    """
    if hard_bear >= 30:
        return "BEARISH_PHASE"
    if fbs < 20:
        return "NEUTRAL_OR_LOW"

    is_rocket = r.get("rocket") or rocket >= 25

    if fbs >= 140:
        return "ELITE_CLEAN_BULL"
    if fbs >= 120:
        return "A_PLUS_CLEAN_BULL" if clean >= 15 else "CONFIRMED_BULL"
    if is_rocket and fbs >= 60:
        return "ROCKET_WATCH"
    if fbs >= 100:
        if clean >= 20:   return "A_PLUS_CLEAN_BULL"
        if shakeout >= 15: return "SHAKEOUT_ABSORB"
        return "CONFIRMED_BULL"
    if fbs >= 80:
        if clean >= 15:    return "CLEAN_ENTRY"
        if shakeout >= 15: return "SHAKEOUT_ABSORB"
        return "CONFIRMED_BULL"
    if fbs >= 60:
        if clean >= 10:    return "CLEAN_ENTRY"
        if shakeout >= 12: return "SHAKEOUT_ABSORB"
        if rebound >= 8:   return "REBOUND_SQUEEZE"
        return "ACTIONABLE_SETUP"
    if fbs >= 40:
        if rebound >= 8:   return "RISK_REBOUND"
        return "EARLY_WATCH"
    if fbs >= 20:
        return "EARLY_WATCH"
    return "NEUTRAL_OR_LOW"


def _score_bucket(fbs: float) -> str:
    for threshold, label in _BUCKET_EDGES:
        if fbs >= threshold:
            return label
    return "NEUTRAL"


# ── Named model booleans ───────────────────────────────────────────────────────

def _has_elite_model(fbs: float, rocket: float, r: dict) -> int:
    return int(fbs >= 120 or (r.get("rocket") and fbs >= 80))


def _has_rebound_model(rebound: float) -> int:
    return int(rebound >= 8)


def _has_strong_bull_model(clean: float, fbs: float) -> int:
    return int(clean >= 20 or fbs >= 100)


# ── Public API ─────────────────────────────────────────────────────────────────

def compute_canonical_score(sig_row: dict, profile: str = "sp500") -> dict:
    """
    Compute all canonical score columns from a signal-flag row dict.

    Args:
        sig_row: dict of boolean/numeric signal flags — same format used by
                 turbo_engine._calc_turbo_score (built inside api_bar_signals).
        profile: "sp500" | "nasdaq" | "all_us"

    Returns:
        dict with all canonical score columns.  Every key in this return value
        must also appear in the stock_stat CSV headers so Replay can read them.

    Canonical columns:
        turbo_score             — primary turbo score (0–150+)
        FINAL_BULL_SCORE        — same as turbo_score (canonical alias)
        ROCKET_SCORE            — para-launch signal strength (0–40)
        CLEAN_ENTRY_SCORE       — F/B entry cluster quality (0–40)
        SHAKEOUT_ABSORB_SCORE   — shakeout-absorb entry score (0–30)
        EXTRA_BULL_SCORE        — L-family / extra bull structure (0–20)
        EXPERIMENTAL_SCORE      — FLY / newer signals (0–15)
        REBOUND_SQUEEZE_SCORE   — TZ_FLIP / rebound entry (0–20)
        HARD_BEAR_SCORE         — hard bear / risk penalty (0–40)
        VOLATILITY_RISK_SCORE   — vol-spike risk context (0–20)
        HAS_ELITE_MODEL         — 0/1 boolean
        HAS_REBOUND_MODEL       — 0/1 boolean
        HAS_STRONG_BULL_MODEL   — 0/1 boolean
        FINAL_REGIME            — string label
        FINAL_SCORE_BUCKET      — string bucket label
    """
    # ── Primary score (turbo, unchanged weights) ───────────────────────────────
    turbo = round(float(_calc_turbo_score(sig_row, profile)), 1)

    # FINAL_BULL_SCORE is the canonical alias for turbo_score.
    # Both are computed from the same function with the same inputs.
    fbs = turbo

    # ── Sub-scores (breakdown of contributing signal families) ─────────────────
    rocket   = round(_rocket_score(sig_row),                  1)
    clean    = round(_clean_entry_score(sig_row, profile),    1)
    shakeout = round(_shakeout_absorb_score(sig_row),         1)
    extra    = round(_extra_bull_score(sig_row, profile),     1)
    exp      = round(_experimental_score(sig_row),            1)
    rebound  = round(_rebound_squeeze_score(sig_row, profile),1)
    hb       = round(_hard_bear_score(sig_row, profile),      1)
    vr       = round(_volatility_risk_score(sig_row),         1)

    # ── Named model booleans ───────────────────────────────────────────────────
    elite_m    = _has_elite_model(fbs, rocket, sig_row)
    rebound_m  = _has_rebound_model(rebound)
    strong_m   = _has_strong_bull_model(clean, fbs)

    # ── Regime + bucket ────────────────────────────────────────────────────────
    regime = _final_regime(fbs, rocket, clean, shakeout, rebound, hb, sig_row)
    bucket = _score_bucket(fbs)

    return {
        "turbo_score":           turbo,
        "FINAL_BULL_SCORE":      fbs,
        "ROCKET_SCORE":          rocket,
        "CLEAN_ENTRY_SCORE":     clean,
        "SHAKEOUT_ABSORB_SCORE": shakeout,
        "EXTRA_BULL_SCORE":      extra,
        "EXPERIMENTAL_SCORE":    exp,
        "REBOUND_SQUEEZE_SCORE": rebound,
        "HARD_BEAR_SCORE":       hb,
        "VOLATILITY_RISK_SCORE": vr,
        "HAS_ELITE_MODEL":       elite_m,
        "HAS_REBOUND_MODEL":     rebound_m,
        "HAS_STRONG_BULL_MODEL": strong_m,
        "FINAL_REGIME":          regime,
        "FINAL_SCORE_BUCKET":    bucket,
    }


def get_scoring_metadata() -> dict:
    """Return metadata dict to embed in every scored export/report."""
    import hashlib, os
    try:
        _self = os.path.abspath(__file__)
        with open(_self, "rb") as f:
            code_hash = hashlib.sha256(f.read()).hexdigest()[:12]
    except Exception:
        code_hash = "unknown"
    return {
        "scoring_engine_name":    SCORING_ENGINE_NAME,
        "scoring_engine_version": SCORING_ENGINE_VERSION,
        "scoring_code_hash":      code_hash,
        "canonical_score_source": True,
    }
