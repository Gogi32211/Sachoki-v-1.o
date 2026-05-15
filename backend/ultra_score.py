"""
ultra_score.py — shared ULTRA Score helper.

Single source of truth for the ULTRA Score formula. Both:
  • live ULTRA orchestration (ultra_orchestrator.run_ultra_*_job)
  • historical Stock Stat / Bulk Signal CSV generation (main.api_stock_stat)
must call this module so the score is computed identically online and offline.

Hard rules
  • Pure read-only function. Never raises on missing fields — they
    contribute 0.
  • NO LOOKAHEAD: this module must NEVER read forward-return fields
    (ret_1d / ret_3d / ret_5d / ret_10d / mfe_* / mae_* / max_high_* /
    max_drawdown_*). They belong only to Replay Analytics.
  • Score is independent and additive — does not modify any input field.

Range: 0..100 integer, banded A (≥80) / B (≥65) / C (≥50) / D (<50).
"""
from __future__ import annotations

from typing import Iterable

# ─────────────────────────────────────────────────────────────────────────────
# Forward-return / future-bar fields. compute_ultra_score must NEVER read
# any of these (verified by tests). Any future analyst extending the formula
# should keep this list authoritative.
# ─────────────────────────────────────────────────────────────────────────────
_FORWARD_RETURN_FIELDS = frozenset({
    "ret_1d", "ret_3d", "ret_5d", "ret_10d",
    "mfe_5d", "mfe_10d", "mae_5d", "mae_10d",
    "max_high_5d", "max_high_10d", "max_drawdown_5d", "max_drawdown_10d",
    "clean_win_5d", "big_win_10d", "fail_5d", "fail_10d",
})


# ─────────────────────────────────────────────────────────────────────────────
# Signal-name normalisation
#
# The score must work on two row shapes:
#   • Live ULTRA rows — flat boolean keys: row['buy_2809']=1, row['bb_brk']=1
#   • Stock Stat bar rows — list-of-labels columns:
#         row['combo']=['BUY_2809', 'ROCKET', 'BB↑']
#         row['vabs']=['ABS', 'STR']
#     plus space-separated string variants.
#
# Both shapes are normalised here to a canonical set of uppercase tokens
# such as 'BUY_2809', 'ROCKET', 'BB_BRK', 'ABS', 'L34', 'TZ_BULL_FLIP'.
# ─────────────────────────────────────────────────────────────────────────────

# Live row flat-key → canonical token
_LIVE_KEY_TO_CANON = {
    # Breakout / trigger
    "buy_2809":     "BUY_2809",
    "rocket":       "ROCKET",
    "bb_brk":       "BB_BRK",
    "bx_up":        "BX_UP",
    "eb_bull":      "EB_BULL",
    "be_up":        "BE_UP",
    "bo_up":        "BO_UP",
    # Setup / accumulation
    "abs_sig":      "ABS",
    "va":           "VA",
    "svs_2809":     "SVS",
    "climb_sig":    "CLB",
    "load_sig":     "LD",
    "strong_sig":   "STR",
    "best_sig":     "BEST",
    "l34":          "L34",
    "fri34":        "FRI34",
    "tz_bull_flip": "TZ_BULL_FLIP",
    # Confirmation / quality
    "rs_strong":    "RS_STRONG",
    # Bonuses / context
    "already_extended": "EXTENDED",
}

# Stock Stat label → canonical token. Labels in stock_stat CSV columns
# (combo / vabs / l / b / f / g / fly / vol / wick / ultra) are emitted as
# uppercase tokens; arrows / unicode characters are preserved here.
_LABEL_TO_CANON = {
    "BUY_2809":  "BUY_2809",
    "BUY":       "BUY_2809",   # alternate compact label
    "ROCKET":    "ROCKET",
    "🚀":        "ROCKET",
    "BB↑":       "BB_BRK",
    "BX↑":       "BX_UP",
    "EB↑":       "EB_BULL",
    "BE↑":       "BE_UP",
    "BO↑":       "BO_UP",
    "ABS":       "ABS",
    "VA":        "VA",
    "SVS":       "SVS",
    "CLB":       "CLB",
    "LD":        "LD",
    "LOAD":      "LD",
    "STR":       "STR",
    "STRONG":    "STR",
    "BEST":      "BEST",
    "BEST★":     "BEST",
    "L34":       "L34",
    "FRI34":     "FRI34",
    "TZ→3":      "TZ_BULL_FLIP",
    "RS+":       "RS_STRONG",
    "RS":        "RS",          # plain RS, lower weight
    "EXT":       "EXTENDED",
}

# Stock Stat columns that carry list-of-labels (or space-separated strings)
_LABEL_COLUMNS = ("combo", "vabs", "vol", "l", "b", "f", "g", "fly",
                  "ultra", "wick")


def _to_iter(v) -> Iterable[str]:
    if v is None:
        return ()
    if isinstance(v, (list, tuple, set)):
        return (str(x) for x in v if x is not None and x != "")
    if isinstance(v, str):
        # Stock Stat sometimes flattens label lists with " ".join
        return (t for t in v.split() if t)
    return ()


def _truthy(v) -> bool:
    if v is None or v == "":
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0 and v == v  # excludes NaN
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "0.0", "false", "none", "null", "nan")
    return bool(v)


def _signal_set(row: dict) -> set:
    """Return the canonical uppercase set of active signals on this row.

    Recognises both live flat booleans (live ULTRA shape) and Stock Stat
    compact label columns (T/Z/L/Combo/VABS/ULT/...). Delegates to the
    shared `ultra_signal_parser` so live + historical scoring stay in
    lockstep.
    """
    if not isinstance(row, dict):
        return set()
    from ultra_signal_parser import parse_stock_stat_signals
    p = parse_stock_stat_signals(row)
    s: set = set()

    # Map parser-flag key → canonical token name expected by compute_ultra_score.
    # The parser uses the same tokens compute_ultra_score reads, with a few
    # aliases (e.g. parser 'four_bf' / 'l88' → score doesn't care).
    _PARSER_TO_CANON = {
        # Breakouts
        "buy_2809":  "BUY_2809",
        "rocket":    "ROCKET",
        "bb_brk":    "BB_BRK",
        "bx_up":     "BX_UP",
        "eb_bull":   "EB_BULL",
        "be_up":     "BE_UP",
        "bo_up":     "BO_UP",
        # Setups
        "abs_sig":     "ABS",
        "va":          "VA",
        "svs_2809":    "SVS",
        "climb_sig":   "CLB",
        "load_sig":    "LD",
        "strong_sig":  "STR",
        "l34":         "L34",
        "fri34":       "FRI34",
        "tz_bull_flip":"TZ_BULL_FLIP",
        # Quality
        "rs_strong":   "RS_STRONG",
    }
    for parser_key, canon in _PARSER_TO_CANON.items():
        if p.get(parser_key):
            s.add(canon)

    # Pass-through fallback for live rows that still use the legacy flat
    # boolean keys not covered by the parser (e.g. best_sig, already_extended).
    for k, canon in _LIVE_KEY_TO_CANON.items():
        if _truthy(row.get(k)):
            s.add(canon)

    # tz_sig like "T4" — keep as a token so `compute_ultra_score` can
    # surface it in reasons even though it's not used in any combo gate.
    tz = row.get("tz_sig") or row.get("tz") or p.get("t_signal")
    if isinstance(tz, str) and tz.strip():
        s.add(tz.strip().upper())

    return s


def _safe_float(v, default=0.0) -> float:
    try:
        if v is None or v == "":
            return default
        f = float(v)
        return default if (f != f or f in (float("inf"), float("-inf"))) else f
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Context lookups (TZ Intel role / pullback tier / rare tier / ABR cat)
# ─────────────────────────────────────────────────────────────────────────────

_INTEL_GO_READY    = frozenset({
    "BULL_A", "BULL_B", "BULL_CONTINUATION_A", "BULL_CONTINUATION_B",
    "PULLBACK_GO", "PULLBACK_CONFIRMING",
    "PULLBACK_READY_A", "PULLBACK_READY_B",
})
_INTEL_REJECT_LONG = frozenset({"REJECT_LONG", "REJECT"})
_INTEL_SHORT       = frozenset({"SHORT_WATCH", "SHORT_GO"})


def _intel_role(row: dict) -> str:
    intel = row.get("tz_intel")
    if isinstance(intel, dict):
        return (intel.get("role") or "").upper()
    # Stock Stat flat field
    return (row.get("tz_intel_role") or "").upper()


def _pullback(row: dict) -> tuple[str, str, bool]:
    """Return (tier, stage, active) for the row, accepting both shapes."""
    pb = row.get("pullback")
    if isinstance(pb, dict):
        return (
            (pb.get("evidence_tier") or "").upper(),
            (pb.get("pullback_stage") or "").upper(),
            bool(pb.get("is_currently_active")),
        )
    return (
        (row.get("pullback_evidence_tier") or "").upper(),
        (row.get("pullback_pullback_stage") or row.get("pullback_stage") or "").upper(),
        _truthy(row.get("pullback_is_currently_active")),
    )


def _rare(row: dict) -> tuple[str, bool]:
    rr = row.get("rare_reversal")
    if isinstance(rr, dict):
        return (
            (rr.get("evidence_tier") or "").upper(),
            bool(rr.get("is_currently_active")),
        )
    return (
        (row.get("rare_evidence_tier") or "").upper(),
        _truthy(row.get("rare_is_currently_active")),
    )


def _abr_category(row: dict) -> str:
    abr = row.get("abr")
    if isinstance(abr, dict):
        return (abr.get("category") or "").upper()
    return (row.get("abr_category") or "").upper()


def _profile_category(row: dict) -> str:
    return (row.get("profile_category") or "").upper()


# ─────────────────────────────────────────────────────────────────────────────
# Regime calibration (replay v2 — derived from SP500 1D historical replay).
# Strong regimes get the largest bonus; bearish regimes contribute a warning
# flag but no hard reject. Bonuses are additive on top of the existing A..F
# components.
# ─────────────────────────────────────────────────────────────────────────────

_STRONG_REGIMES = frozenset({"ACTIONABLE_SETUP", "SHAKEOUT_ABSORB", "CLEAN_ENTRY"})

_REGIME_BONUS = {
    "ACTIONABLE_SETUP":  (12, "REGIME:ACTIONABLE"),
    "SHAKEOUT_ABSORB":   (10, "REGIME:SHAKEOUT"),
    "CLEAN_ENTRY":       ( 8, "REGIME:CLEAN"),
    "REBOUND_SQUEEZE":   ( 5, "REGIME:REBOUND_SQUEEZE"),
    "RISK_REBOUND":      ( 3, "REGIME:RISK_REBOUND"),
    "ROCKET_WATCH":      ( 0, ""),
}

_BEARISH_REGIMES = frozenset({"BEARISH_PHASE", "BEARISH_CONTEXT"})


def _final_regime(row: dict) -> str:
    return (row.get("FINAL_REGIME") or row.get("final_regime") or "").upper()


def _safe_change_pct(row: dict) -> float | None:
    v = row.get("change_pct")
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main scoring function
# ─────────────────────────────────────────────────────────────────────────────

def compute_ultra_score(row: dict) -> dict:
    """Return ULTRA Score dict for the row.

    Output keys:
      ultra_score                     int 0..100
      ultra_score_band                'A' | 'B' | 'C' | 'D'
      ultra_score_reasons             list[str]  (deduped, capped at 12)
      ultra_score_flags               list[str]  (combo / context flags)
      ultra_score_raw_before_penalty  int   sum of A+B+C+D+F (clamped 0..100)
      ultra_score_penalty_total       int   absolute value of E component

    The function never raises on missing fields.
    """
    if not isinstance(row, dict):
        return _empty_result()

    sigs    = _signal_set(row)
    intel_r = _intel_role(row)
    pb_tier, pb_stage, pb_active = _pullback(row)
    rr_tier, rr_active           = _rare(row)
    abr_cat                      = _abr_category(row)
    cat                          = _profile_category(row)
    pf                           = _safe_float(row.get("profile_score"), default=-1)

    reasons: list[str] = []
    flags:   list[str] = []

    def has(*tokens) -> bool:
        return any(t in sigs for t in tokens)

    # ── A. Breakout / Trigger (cap 35) ──────────────────────────────────────
    a = 0
    if "BUY_2809" in sigs: a += 20; reasons.append("BUY_2809")
    if "ROCKET"   in sigs: a += 20; reasons.append("ROCKET")
    if "BB_BRK"   in sigs: a += 15; reasons.append("BB↑")
    if "BX_UP"    in sigs: a += 12; reasons.append("BX↑")
    if "EB_BULL"  in sigs: a += 10; reasons.append("EB↑")
    if "BE_UP"    in sigs: a += 10; reasons.append("BE↑")
    if "BO_UP"    in sigs: a += 10; reasons.append("BO↑")
    a = min(a, 35)
    has_breakout = a > 0

    # ── B. Setup / Accumulation (cap 25) ────────────────────────────────────
    b = 0
    if "ABS"          in sigs: b += 10; reasons.append("ABS")
    if "VA"           in sigs: b += 8;  reasons.append("VA")
    if "SVS"          in sigs: b += 8;  reasons.append("SVS")
    if "CLB"          in sigs: b += 7;  reasons.append("CLB")
    if "LD"           in sigs: b += 6;  reasons.append("LD")
    if "STR"          in sigs: b += 8;  reasons.append("STR")
    if "L34"          in sigs: b += 6;  reasons.append("L34")
    if "FRI34"        in sigs: b += 6;  reasons.append("FRI34")
    if "TZ_BULL_FLIP" in sigs: b += 10; reasons.append("TZ→3")
    b = min(b, 25)
    has_setup = b > 0

    # ── C. Confirmation / Quality (cap 25) ──────────────────────────────────
    c = 0
    if "RS_STRONG" in sigs:
        c += 8; reasons.append("RS+")
    if pf >= 0:
        if   pf >= 18: c += 12
        elif pf >= 12: c += 9
        elif pf >= 7:  c += 6
        elif pf >= 1:  c += 3
        if pf >= 1:
            reasons.append(f"PF={int(pf)}")
    if   cat == "SWEET_SPOT": c += 10; reasons.append("SWEET_SPOT")
    elif cat == "BUILDING":   c += 6;  reasons.append("BUILDING")
    elif cat == "WATCH":      c += 2
    c = min(c, 25)

    # ── D. Context (-20..+20) ───────────────────────────────────────────────
    d = 0
    if intel_r in _INTEL_GO_READY:
        d += 8; reasons.append(intel_r)
    elif intel_r == "BULL_WATCH":
        d += 6; reasons.append("BULL_WATCH")
    elif intel_r in ("PULLBACK_WATCH", "EXTENDED_WATCH",
                      "DEEP_PULLBACK_WATCH", "MIXED_WATCH"):
        d += 4

    if pb_tier == "CONFIRMED_PULLBACK":
        d += 10; reasons.append("CPB")
    elif "READY" in pb_stage or "GO" in pb_stage or pb_active:
        d += 8
        reasons.append("RPB" if "READY" in pb_stage else
                        "GPB" if "GO" in pb_stage else "PB-active")
    elif pb_tier == "ANECDOTAL_PULLBACK":
        d += 4; reasons.append("APB")
    elif pb_tier:
        d += 2

    if rr_tier in ("CONFIRMED_RARE", "CONFIRMED_PATTERN"):
        d += 8; reasons.append("CP")
    elif rr_active:
        d += 8; reasons.append("AP")
    elif "READY" in rr_tier:
        d += 6; reasons.append("RP")
    elif rr_tier in ("FORMING_PATTERN", "ANECDOTAL_RARE"):
        d += 2
    elif rr_tier == "WATCH_PATTERN":
        d += 1

    if   abr_cat == "B+": d += 6; reasons.append("ABR=B+")
    elif abr_cat == "B":  d += 4
    elif abr_cat == "A":  d += 3
    elif abr_cat == "R":  d -= 4

    d = max(min(d, 20), -20)

    # ── F. Combination bonuses ──────────────────────────────────────────────
    f = 0
    has_breakout_any = has("BUY_2809", "ROCKET", "BB_BRK", "BX_UP",
                           "EB_BULL", "BE_UP", "BO_UP")
    has_setup_any   = has("ABS", "VA", "SVS", "CLB", "LD")
    rs_plus         = "RS_STRONG" in sigs

    if (has("BUY_2809", "ROCKET")) and cat in ("SWEET_SPOT", "BUILDING"):
        f += 12; reasons.append("MOMO+CAT"); flags.append("MOMENTUM_A")
    if has_setup_any and has_breakout_any and rs_plus:
        f += 15; reasons.append("REV-GROW"); flags.append("REVERSAL_GROWTH_A")
    if "TZ_BULL_FLIP" in sigs and has_breakout_any and rs_plus:
        f += 12; reasons.append("TRANSITION"); flags.append("TRANSITION_A")
    if pb_tier == "CONFIRMED_PULLBACK" and has_breakout_any \
            and cat in ("SWEET_SPOT", "BUILDING"):
        f += 12; reasons.append("PB-ENTRY"); flags.append("PULLBACK_ENTRY_A")
    # ── L34 / FRI34 calibrated tiering (replay v2) ──────────────────────────
    # Standalone L34/FRI34 is weak (win 10D ≈48%). Only confluence matters:
    #   alone:                        +2
    #   + breakout:                   +5
    #   + breakout + PF good (>=12):  +7
    #   + breakout + PF good + strong regime: +10
    has_l34 = has("L34", "FRI34")
    if has_l34:
        pf_good_l34 = (pf >= 12)
        regime_now  = _final_regime(row)
        strong_reg  = (regime_now in _STRONG_REGIMES)
        if has_breakout_any and pf_good_l34 and strong_reg:
            f += 10; reasons.append("L34→TRIG+REG"); flags.append("L34_TRIGGER_A")
        elif has_breakout_any and pf_good_l34:
            f += 7;  reasons.append("L34→TRIG+PF");  flags.append("L34_TRIGGER_A")
        elif has_breakout_any:
            f += 5;  reasons.append("L34→TRIG");     flags.append("L34_TRIGGER_A")
        else:
            f += 2;  reasons.append("L34")
    if has_setup_any and not has_breakout_any:
        flags.append("SETUP_ONLY")
    if has_breakout_any and not has_setup_any:
        flags.append("BREAKOUT_ONLY")

    # ── REVERSAL_GROWTH_A_NO_RS calibrated tiering (replay v2) ──────────────
    # When setup + breakout combine without RS+, the existing REV-GROW bonus
    # already fires (cap-ish at +15). Replay shows this group is moderate
    # (avg 10D +1.17%, win 10D 51.5%), so we pre-bake additive bonus tiers
    # that only matter when this branch fires WITHOUT the strict RS+ branch.
    rev_growth_no_rs = (has_setup_any and has_breakout_any and not rs_plus)
    if rev_growth_no_rs:
        flags.append("REVERSAL_GROWTH_A_NO_RS")
        regime_now = _final_regime(row)
        pf_good    = (pf >= 12)
        strong_reg = (regime_now in _STRONG_REGIMES)
        # alone +8, +PF good +10, +strong regime +14, both +18
        if pf_good and strong_reg:
            f += 18; reasons.append("REV-GROW(NO_RS)+PF+REG")
        elif strong_reg:
            f += 14; reasons.append("REV-GROW(NO_RS)+REG")
        elif pf_good:
            f += 10; reasons.append("REV-GROW(NO_RS)+PF")
        else:
            f += 8;  reasons.append("REV-GROW(NO_RS)")

    # ── E. Penalties ────────────────────────────────────────────────────────
    e = 0
    if intel_r in _INTEL_REJECT_LONG:
        e -= 10; reasons.append("REJECT(-)"); flags.append("REJECT_CONTEXT")
    elif intel_r in _INTEL_SHORT:
        e -= 8;  reasons.append("SHORT_WATCH(-)"); flags.append("SHORT_CONTEXT")
    if cat == "WATCH" and pf >= 0 and pf < 5:
        e -= 4; flags.append("WATCH_LOW_PF")
    if not has_breakout and not has_setup:
        e -= 5; flags.append("ISOLATED")
    if "EXTENDED" in sigs:
        flags.append("EXTENDED_MOVE")
    if _truthy(row.get("rsi_extended")):
        flags.append("RSI_EXTENDED")
    if _truthy(row.get("cci_extended")):
        flags.append("CCI_EXTENDED")

    # ── G. Regime bonus (replay v2) ─────────────────────────────────────────
    # FINAL_REGIME contributes a measured additive bonus. ROCKET_WATCH gets
    # zero (already-running). BEARISH gets a warning flag but no hard reject.
    regime = _final_regime(row)
    g = 0
    regime_bonus_label = ""
    if regime in _REGIME_BONUS:
        g, label = _REGIME_BONUS[regime]
        if label:
            reasons.append(label); regime_bonus_label = label
    elif regime in _BEARISH_REGIMES:
        flags.append("BEARISH_CONTEXT_WARN")
    strong_regime = (regime in _STRONG_REGIMES)

    # ── Light extension penalty (warning, not rejection) ────────────────────
    chg_pct = _safe_change_pct(row)
    if chg_pct is not None and chg_pct >= 25 and not strong_regime:
        e -= 4; flags.append("EXTENDED_PENALTY_LIGHT")

    raw = a + b + c + d + f + g
    raw_clamped = max(0, min(100, int(round(raw))))
    total = raw + e
    score = max(0, min(100, int(round(total))))

    # ── Confluence-aware caps (replay v2) ───────────────────────────────────
    momentum_a    = ("MOMENTUM_A" in flags)
    setup_only    = ("SETUP_ONLY" in flags)
    breakout_only = ("BREAKOUT_ONLY" in flags)
    pf_good       = (pf >= 12)
    sweet_spot    = (cat == "SWEET_SPOT")

    confluence_count = 0
    if has_setup_any:                         confluence_count += 1
    if has_breakout_any:                      confluence_count += 1
    if pf_good:                               confluence_count += 1
    if sweet_spot:                            confluence_count += 1

    caps_applied: list[str] = []
    cap_reasons: list[str]  = []

    # Rule 1: MOMENTUM_A without strong regime can't exceed 89 unless ≥2
    # additional strong confluences exist (setup + breakout + PF + SWEET_SPOT).
    if momentum_a and not strong_regime and score > 89 and confluence_count < 4:
        # MOMENTUM_A already counts breakout; require ≥2 of the others (setup,
        # PF good, sweet_spot). Confluence sources beyond breakout: count them.
        extra_conf = (1 if has_setup_any else 0) + (1 if pf_good else 0) + (1 if sweet_spot else 0)
        if extra_conf < 2:
            score = min(score, 89)
            caps_applied.append("CAP_MOMENTUM_A_NO_REGIME")
            cap_reasons.append("momentum_a:no_strong_regime:no_double_confluence")

    # Rule 2: SETUP_ONLY caps at 49 unless PF good + strong regime present.
    if setup_only and not (pf_good and strong_regime) and score > 49:
        score = 49
        caps_applied.append("CAP_SETUP_ONLY")
        cap_reasons.append("setup_only:no_pf_or_regime")

    # Rule 3: BREAKOUT_ONLY caps at 59 unless PF good + strong regime present.
    if breakout_only and not (pf_good and strong_regime) and score > 59:
        score = 59
        caps_applied.append("CAP_BREAKOUT_ONLY")
        cap_reasons.append("breakout_only:no_pf_or_regime")

    band = compute_ultra_score_band(score)
    band_v2, priority = compute_ultra_score_priority(score)

    # Dedupe + cap reasons
    seen = set()
    out_reasons: list[str] = []
    for r in reasons:
        if r not in seen:
            out_reasons.append(r); seen.add(r)
        if len(out_reasons) >= 12:
            break
    seen_f = set()
    out_flags: list[str] = []
    for fg in flags:
        if fg not in seen_f:
            out_flags.append(fg); seen_f.add(fg)

    return {
        "ultra_score":                    score,
        "ultra_score_band":               band,
        "ultra_score_band_v2":            band_v2,
        "ultra_score_priority":           priority,
        "ultra_score_reasons":            out_reasons,
        "ultra_score_flags":              out_flags,
        "ultra_score_raw_before_penalty": raw_clamped,
        "ultra_score_penalty_total":      abs(int(round(e))),
        "ultra_score_regime_bonus":       int(g),
        "ultra_score_caps_applied":       list(caps_applied),
        "ultra_score_cap_reason":         "|".join(cap_reasons),
    }


def compute_ultra_score_band(score) -> str:
    s = _safe_float(score, default=0)
    if   s >= 80: return "A"
    elif s >= 65: return "B"
    elif s >= 50: return "C"
    else:         return "D"


def compute_ultra_score_priority(score) -> tuple[str, str]:
    """Return (band_v2, priority) using the replay v2 calibration.

    Bands:
      90+ → A+ / HIGH_PRIORITY
      80–89 → A / WATCH_A
      65–79 → B / STRONG_WATCH
      50–64 → C / CONTEXT_WATCH
      <50  → D / LOW
    """
    s = _safe_float(score, default=0)
    if   s >= 90: return ("A+", "HIGH_PRIORITY")
    elif s >= 80: return ("A",  "WATCH_A")
    elif s >= 65: return ("B",  "STRONG_WATCH")
    elif s >= 50: return ("C",  "CONTEXT_WATCH")
    else:         return ("D",  "LOW")


def compute_ultra_score_reasons(row: dict) -> list:
    return compute_ultra_score(row).get("ultra_score_reasons", [])


def compute_ultra_score_flags(row: dict) -> list:
    return compute_ultra_score(row).get("ultra_score_flags", [])


def _empty_result() -> dict:
    return {
        "ultra_score":                    0,
        "ultra_score_band":               "D",
        "ultra_score_band_v2":            "D",
        "ultra_score_priority":           "LOW",
        "ultra_score_reasons":            [],
        "ultra_score_flags":              [],
        "ultra_score_raw_before_penalty": 0,
        "ultra_score_penalty_total":      0,
        "ultra_score_regime_bonus":       0,
        "ultra_score_caps_applied":       [],
        "ultra_score_cap_reason":         "",
    }
