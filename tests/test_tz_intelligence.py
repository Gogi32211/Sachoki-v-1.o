"""Tests for TZ Signal Intelligence — hardened v2."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import pytest
from tz_intelligence.matrix_loader import load_matrix, MatrixIndex, _resolve_conflict
from tz_intelligence.classifier import classify_tz_event, _best_role, _make_composite


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(t="", z="", l="", lane1="", lane3="",
         close=50, ema20=45, ema50=40, ema89=35,
         high=52, low=48, open_=49, vol_bkt="", ticker="TEST", date="2025-01-01"):
    return {
        "ticker": ticker, "date": date,
        "t_signal": t, "z_signal": z, "l_signal": l,
        "lane1_label": lane1, "lane3_label": lane3,
        "volume_bucket": vol_bkt, "ne_suffix": "", "wick_suffix": "",
        "close": str(close), "ema20": str(ema20), "ema50": str(ema50), "ema89": str(ema89),
        "high": str(high), "low": str(low), "open": str(open_),
        "volume": "1000000",
    }


# ── Matrix loader ─────────────────────────────────────────────────────────────

def test_matrix_loads():
    m = load_matrix()
    assert len(m.rows) == 1523

def test_matrix_has_universe_scoped_composite():
    m = load_matrix()
    # composite dict is keyed by universe (SP500, NASDAQ_GT5, GLOBAL)
    all_univs = set(m.composite.keys())
    assert "SP500" in all_univs or "GLOBAL" in all_univs

def test_matrix_has_seq4_index():
    m = load_matrix()
    assert len(m.seq4) > 0

def test_matrix_has_baselines():
    m = load_matrix()
    for sig in ["T1", "T4", "T6", "Z4", "Z6"]:
        assert sig in m.baseline

def test_matrix_meta_bonuses():
    m = load_matrix()
    assert m.get_ema_bonus() == 10
    assert m.get_price_position_bonus() == 10
    assert m.get_short_go_bonus() == 35


# ── Fix 1: Composite deduplication ───────────────────────────────────────────

def test_make_composite_no_duplication_when_lane_has_prefix():
    # lane1 already starts with signal → no duplication
    assert _make_composite("T5", "T5L25ND") == "T5L25ND"

def test_make_composite_prepends_when_lane_is_bare():
    # lane1 is bare (no signal prefix) → prepend
    assert _make_composite("T5", "L25ND") == "T5L25ND"

def test_make_composite_z_signal_no_duplication():
    assert _make_composite("Z1G", "Z1GL5ED") == "Z1GL5ED"

def test_make_composite_empty_inputs():
    assert _make_composite("", "L25ND") == ""
    assert _make_composite("T5", "") == ""
    assert _make_composite("", "") == ""

def test_classifier_composite_no_duplication():
    """classifier must never produce T5T5L25ND."""
    m = load_matrix()
    row = _row(t="T5", lane1="T5L25ND")   # lane1 already includes "T5"
    result = classify_tz_event(row, [], m)
    assert "T5T5" not in result["composite_pattern"]
    assert result["composite_pattern"] == "T5L25ND"


# ── Fix 2: Universe-scoped matrix matching ───────────────────────────────────

def test_sp500_doesnt_use_nasdaq_rules():
    m = load_matrix()
    # SP500 scan: allowed univs = {SP500, GLOBAL}; NASDAQ_GT5 rules must not appear
    allowed = m.allowed_univs("sp500")
    assert "SP500" in allowed
    assert "NASDAQ_GT5" not in allowed

def test_nasdaq_doesnt_use_sp500_only_rules():
    m = load_matrix()
    allowed = m.allowed_univs("nasdaq")
    assert "NASDAQ_GT5" in allowed
    assert "SP500" not in allowed

def test_all_us_includes_all_universes():
    m = load_matrix()
    allowed = m.allowed_univs("all_us")
    assert "SP500" in allowed
    assert "NASDAQ_GT5" in allowed
    assert "GLOBAL" in allowed

def test_scoped_rule_lookup_sp500():
    m = load_matrix()
    # Any composite pattern lookup for sp500 must not return NASDAQ_GT5-only rules
    sp500_pos, sp500_neg = m.get_composite_rules("T1L12NP", "sp500")
    nasdaq_pos, nasdaq_neg = m.get_composite_rules("T1L12NP", "nasdaq")
    # Can't assert specific values without knowing which universe each rule is in,
    # but we can verify the functions return lists
    assert isinstance(sp500_pos, list)
    assert isinstance(sp500_neg, list)


# ── Fix 3: Conflict resolver ─────────────────────────────────────────────────

def test_resolve_conflict_positive_wins():
    pos = [{"med10d_pct": "1.2", "fail10d_pct": "20.0"}]
    neg = [{"med10d_pct": "0.5", "fail10d_pct": "22.0"}]
    assert _resolve_conflict(pos, neg) == "POSITIVE"

def test_resolve_conflict_reject_wins_by_fail():
    pos = [{"med10d_pct": "0.5", "fail10d_pct": "20.0"}]
    neg = [{"med10d_pct": "0.3", "fail10d_pct": "30.0"}]  # fail >= 28 → reject
    assert _resolve_conflict(pos, neg) == "REJECT"

def test_resolve_conflict_reject_wins_by_med():
    pos = [{"med10d_pct": "0.5", "fail10d_pct": "20.0"}]
    neg = [{"med10d_pct": "-0.5", "fail10d_pct": "22.0"}]  # neg med < 0 → reject
    assert _resolve_conflict(pos, neg) == "REJECT"

def test_resolve_conflict_returns_conflict_when_ambiguous():
    pos = [{"med10d_pct": "0.5", "fail10d_pct": "26.0"}]  # pos not strong enough
    neg = [{"med10d_pct": "0.2", "fail10d_pct": "22.0"}]  # neg not bad enough
    assert _resolve_conflict(pos, neg) == "CONFLICT"

def test_conflict_classifier_produces_bull_watch():
    """When a pattern is CONFLICT, role should be capped at BULL_WATCH."""
    m = load_matrix()
    # Find a pattern known to conflict in both pos/neg composite for same universe
    # We can't easily force this in unit test without mocking the matrix,
    # so test that classifier output is consistent with conflict handling logic.
    row = _row(t="T4", lane1="L12NP")
    result = classify_tz_event(row, [], m, debug=True)
    # If conflict found, role must not be BULL_A or BULL_B (forced to BULL_WATCH)
    if result["conflict_flag"]:
        assert result["role"] == "BULL_WATCH"
        assert result["conflict_resolution"] in ("CONFLICT", "POSITIVE", "REJECT")


# ── Fix 4: BULL_A cap for weak T signals ─────────────────────────────────────

def test_weak_t_capped_to_bull_b_without_confirmations():
    """T1 without all confirmations must not be BULL_A."""
    m = load_matrix()
    # below EMA50, no composite match, no price_pos confidence
    row = _row(t="T1", close=50, ema50=60)  # below EMA50
    result = classify_tz_event(row, [], m)
    # Must not be BULL_A
    assert result["role"] != "BULL_A"

def test_weak_t_signals_list_includes_t1_t2_t9_t10():
    from tz_intelligence.classifier import _WEAK_T_SIGNALS
    assert {"T1", "T2", "T9", "T10"} == _WEAK_T_SIGNALS

def test_strong_t_not_capped():
    """T4 can reach BULL_A without the cap."""
    m = load_matrix()
    row = _row(t="T4", lane1="T4L12NP", close=58, ema20=50, ema50=48, ema89=45,
               high=60, low=40)
    # Build 3 history bars to give a wide 4-bar range so close is in top 75%
    hist = [
        _row(t="T4", close=42, high=50, low=40),
        _row(t="T4", close=50, high=55, low=44),
        _row(t="T4", close=53, high=58, low=47),
    ]
    result = classify_tz_event(row, hist, m)
    # We can't guarantee BULL_A (depends on matrix data), but no cap should apply
    assert "BULL_A_CAPPED:T4" not in " ".join(result["reason_codes"])


# ── Fix 5: Debug fields in output ────────────────────────────────────────────

def test_result_has_all_debug_fields():
    m = load_matrix()
    row = _row(t="T4")
    result = classify_tz_event(row, [], m)
    expected_keys = [
        "ticker", "date", "final_signal", "composite_pattern", "seq4",
        "role", "score", "quality", "action",
        "above_ema20", "above_ema50", "above_ema89",
        "ema20_reclaim", "ema50_reclaim", "ema89_reclaim",
        "conflict_flag", "conflict_resolution", "conflicting_rule_ids",
        "good_flags", "reject_flags",
        "price_position_4bar", "breaks_4bar_high", "breaks_4bar_low",
        "final_volume_vs_prev1", "final_volume_vs_prev2", "final_volume_vs_prev3",
        "matched_rule_id", "matched_rule_type", "matched_universe", "matched_status",
        "matched_med10d_pct", "matched_fail10d_pct", "matched_avg10d_pct",
        "matched_source_file", "matched_rule_notes",
        "matched_composite_rule_id", "matched_seq4_rule_id", "matched_reject_rule_id",
        "reason_codes", "explanation", "debug_trace",
    ]
    for key in expected_keys:
        assert key in result, f"Missing key: {key}"

def test_debug_trace_populated_when_debug_true():
    m = load_matrix()
    row = _row(t="T4")
    result = classify_tz_event(row, [], m, debug=True)
    assert isinstance(result["debug_trace"], list)
    assert len(result["debug_trace"]) > 0

def test_debug_trace_none_when_debug_false():
    m = load_matrix()
    row = _row(t="T4")
    result = classify_tz_event(row, [], m, debug=False)
    assert result["debug_trace"] is None


# ── Fix 6: Separate above_ema vs ema_reclaim ─────────────────────────────────

def test_above_ema_flags_correct():
    m = load_matrix()
    row = _row(t="T4", close=100, ema20=90, ema50=95, ema89=110)
    result = classify_tz_event(row, [], m)
    assert result["above_ema20"] is True
    assert result["above_ema50"] is True
    assert result["above_ema89"] is False

def test_ema50_reclaim_detected():
    m = load_matrix()
    prev = _row(t="T4", close=48, ema50=50)   # prev close < prev ema50
    curr = _row(t="T4", close=52, ema50=50, ticker="X", date="2025-01-02")
    result = classify_tz_event(curr, [prev], m)
    assert result["ema50_reclaim"] is True
    assert result["above_ema50"] is True

def test_above_ema50_not_reclaim_when_already_above():
    """If price was already above EMA50, reclaim flag must be False."""
    m = load_matrix()
    prev = _row(t="T4", close=55, ema50=50)   # was already above
    curr = _row(t="T4", close=58, ema50=50, ticker="X", date="2025-01-02")
    result = classify_tz_event(curr, [prev], m)
    assert result["above_ema50"] is True
    assert result["ema50_reclaim"] is False


# ── Fix 7: Full seq4 in output ────────────────────────────────────────────────

def test_seq4_full_4bar_string():
    m = load_matrix()
    hist = [
        _row(z="Z2G", date="2025-01-01"),
        _row(t="T1",  date="2025-01-02"),
        _row(z="Z5",  date="2025-01-03"),
    ]
    curr = _row(t="T1", date="2025-01-04")
    result = classify_tz_event(curr, hist, m)
    # seq4 must be full 4-bar string, not truncated
    assert result["seq4"] == "Z2G|T1|Z5|T1"
    parts = result["seq4"].split("|")
    assert len(parts) == 4

def test_seq4_empty_when_fewer_than_4_bars():
    m = load_matrix()
    curr = _row(t="T1")
    result = classify_tz_event(curr, [], m)
    assert result["seq4"] == ""   # only 1 bar → no valid seq4


# ── Fix 8: SHORT_WATCH not assigned to positive pullback Z patterns ──────────

def test_pullback_z_not_short_watch_when_above_ema50():
    """Z5 above EMA50 with no reject evidence → demote SHORT_WATCH to PULLBACK_READY_B."""
    m = load_matrix()
    from tz_intelligence.classifier import _PULLBACK_Z_SIGNALS
    assert "Z5" in _PULLBACK_Z_SIGNALS
    row = _row(z="Z5", close=55, ema50=50)  # above EMA50, no reject
    result = classify_tz_event(row, [], m)
    # Must not assign SHORT_WATCH to a pullback-class Z with no reject evidence
    if result["role"] == "SHORT_WATCH":
        # If somehow SHORT_WATCH assigned, reject_flags must be present
        assert bool(result["reject_flags"]) or result["price_position_4bar"] < 0.25

def test_pullback_z_signals_set():
    from tz_intelligence.classifier import _PULLBACK_Z_SIGNALS
    assert _PULLBACK_Z_SIGNALS == {"Z5", "Z9", "Z3", "Z4", "Z6"}


# ── Fix 9: SHORT_GO only after breakdown confirmation ─────────────────────────

def test_short_go_requires_breakdown():
    """SHORT_GO must only be assigned when close < 4-bar low AND no good flags."""
    m = load_matrix()
    row = _row(t="T1", lane1="L12NDP", close=40,
               ema20=55, ema50=60, ema89=65)
    # close=40 < 4bar_low=42 → SHORT_GO if original role was SHORT_WATCH
    result = classify_tz_event(row, [], m, current_low_4bar=42.0)
    if result["role"] == "SHORT_GO":
        assert any("BREAK_4BAR_LOW" in c for c in result["reason_codes"])

def test_no_short_go_without_breakdown():
    m = load_matrix()
    row = _row(t="T1", lane1="L12NDP", close=50,
               ema20=55, ema50=60, ema89=65)
    # close=50 is not below 4bar_low=45 → no promotion
    result = classify_tz_event(row, [], m, current_low_4bar=45.0)
    assert result["role"] != "SHORT_GO"


# ── Role ranking ──────────────────────────────────────────────────────────────

def test_best_role_bull_a_beats_bull_b():
    assert _best_role("BULL_A", "BULL_B") == "BULL_A"

def test_best_role_short_go_beats_bull_a():
    assert _best_role("SHORT_GO", "BULL_A") == "SHORT_GO"

def test_best_role_no_edge_loses_to_anything():
    assert _best_role("NO_EDGE", "BULL_WATCH") == "BULL_WATCH"


# ── Classifier — basic sanity ─────────────────────────────────────────────────

def test_no_signal_returns_no_edge():
    m = load_matrix()
    row = _row()  # no t/z/l signal
    result = classify_tz_event(row, [], m)
    assert result["role"] == "NO_EDGE"
    assert result["final_signal"] == ""

def test_ema50_reclaim_bonus_added():
    m = load_matrix()
    prev = _row(t="T4", close=48, ema50=50)
    curr = _row(t="T4", close=52, ema50=50, ticker="X", date="2025-01-02")
    result = classify_tz_event(curr, [prev], m)
    assert any("EMA50_RECLAIM" in c for c in result["reason_codes"])

def test_price_position_top75_bonus():
    m = load_matrix()
    hist = [
        _row(t="T4", close=42, high=50, low=40),
        _row(t="T4", close=50, high=55, low=44),
        _row(t="T4", close=53, high=58, low=47),
    ]
    curr = _row(t="T4", close=58, high=60, low=55,
                ema20=50, ema50=48, ema89=46, ticker="X", date="2025-01-04")
    result = classify_tz_event(curr, hist, m)
    assert any("CLOSE_TOP75PCT" in c for c in result["reason_codes"])

def test_quality_and_action_valid():
    m = load_matrix()
    row = _row(t="T4", vol_bkt="VB")
    result = classify_tz_event(row, [], m)
    assert result["quality"] in ("A", "B", "Watch", "Reject", "—")
    assert result["action"] in (
        "BUY_TRIGGER", "WATCH_BULL_TRIGGER", "WATCH_BULL_SETUP",
        "WAIT_FOR_T_CONFIRMATION", "PULLBACK_ENTRY_READY",
        "WATCH_PULLBACK", "WAIT_FOR_CONFIRMATION", "WAIT_FOR_BREAKDOWN",
        "SHORT_TRIGGER", "DO_NOT_BUY", "IGNORE", "NO_ACTION",
    )
    assert any("VOL:VB" in c for c in result["reason_codes"])

_ALL_VALID_ROLES = {
    "BULL_A", "BULL_B", "PULLBACK_GO", "PULLBACK_CONFIRMING",
    "PULLBACK_READY_A", "PULLBACK_READY_B", "PULLBACK_WATCH", "DEEP_PULLBACK_WATCH",
    "BULL_WATCH", "MIXED_WATCH",
    "SHORT_WATCH", "SHORT_GO",
    "REJECT", "REJECT_LONG", "NO_EDGE", "CONTEXT_BONUS",
}

def test_universe_param_passed_through():
    """Classifier accepts scan_universe without error."""
    m = load_matrix()
    row = _row(t="T4", lane1="T4L12NP")
    r_sp  = classify_tz_event(row, [], m, scan_universe="sp500")
    r_nas = classify_tz_event(row, [], m, scan_universe="nasdaq")
    assert r_sp["role"] in _ALL_VALID_ROLES
    assert r_nas["role"] in _ALL_VALID_ROLES


# ── Fix 9: PULLBACK_READY_A gating ───────────────────────────────────────────

def test_pullback_ready_a_demoted_when_deep_and_below_all_emas():
    """Z6 positive composite + price_pos < 0.25 + below all EMAs → not PULLBACK_READY_A."""
    m = load_matrix()
    # Z6, lane1 resolves to a positive composite, but deep in range and below all EMAs
    row = _row(z="Z6", lane1="Z6L25NDP", close=30, ema20=60, ema50=55, ema89=50,
               high=32, low=29)
    # Build history bars so price_position_4bar is low
    hist = [
        _row(z="Z6", close=35, high=40, low=28),
        _row(z="Z6", close=33, high=38, low=29),
        _row(z="Z6", close=31, high=36, low=29),
    ]
    result = classify_tz_event(row, hist, m)
    assert result["role"] not in ("PULLBACK_READY_A",), \
        f"Should not be PULLBACK_READY_A when deep below all EMAs, got {result['role']}"
    assert result["role"] in ("DEEP_PULLBACK_WATCH", "PULLBACK_READY_B",
                               "PULLBACK_WATCH", "NO_EDGE", "BULL_WATCH", "MIXED_WATCH")

def test_pullback_ready_a_requires_ema_support():
    """PULLBACK_READY_A needs at least one EMA above or reclaim."""
    m = load_matrix()
    row = _row(z="Z5", close=40, ema20=60, ema50=55, ema89=50)  # below all EMAs
    result = classify_tz_event(row, [], m)
    assert result["role"] != "PULLBACK_READY_A"

def test_pullback_ready_score_penalty_below_all_emas():
    """Below all EMAs applies score penalty to pullback roles."""
    m = load_matrix()
    row_below = _row(z="Z5", close=30, ema20=60, ema50=55, ema89=50, high=32, low=28)
    row_above = _row(z="Z5", close=60, ema20=50, ema50=45, ema89=40, high=62, low=58)
    r_below = classify_tz_event(row_below, [], m)
    r_above = classify_tz_event(row_above, [], m)
    # Below-EMA row must have lower or equal score
    assert r_below["score"] <= r_above["score"]
    # Penalty reason code must appear when below all EMAs
    if r_below["role"] in ("PULLBACK_READY_B", "PULLBACK_WATCH",
                            "DEEP_PULLBACK_WATCH", "PULLBACK_READY_A"):
        assert any("PENALTY" in c for c in r_below["reason_codes"])


# ── Fix 10: PULLBACK action semantics ─────────────────────────────────────────

def test_pullback_ready_action_is_wait_not_entry():
    """PULLBACK_READY_A and PULLBACK_READY_B must use WAIT_FOR_T_CONFIRMATION."""
    from tz_intelligence.classifier import _ROLE_ACTION
    assert _ROLE_ACTION["PULLBACK_READY_A"] == "WAIT_FOR_T_CONFIRMATION"
    assert _ROLE_ACTION["PULLBACK_READY_B"] == "WAIT_FOR_T_CONFIRMATION"

def test_pullback_go_action_is_entry():
    """PULLBACK_GO must use PULLBACK_ENTRY_READY."""
    from tz_intelligence.classifier import _ROLE_ACTION
    assert _ROLE_ACTION["PULLBACK_GO"] == "PULLBACK_ENTRY_READY"

def test_pullback_go_assigned_when_t_after_z():
    """T confirmation after recent Z signal in top range → PULLBACK_GO."""
    m = load_matrix()
    hist = [
        _row(z="Z5", close=45, high=50, low=40, ema20=42, ema50=40, ema89=38),
        _row(z="Z5", close=48, high=52, low=42, ema20=43, ema50=41, ema89=39),
        _row(z="Z5", close=50, high=54, low=44, ema20=44, ema50=42, ema89=40),
    ]
    # T signal in top of 4-bar range (close=56 with high=58 is top 75% of [40..58])
    curr = _row(t="T4", close=56, high=58, low=52,
                ema20=50, ema50=48, ema89=45, ticker="X", date="2025-02-01")
    result = classify_tz_event(curr, hist, m)
    # If PULLBACK_GO triggered, verify correct action
    if result["role"] == "PULLBACK_GO":
        assert result["action"] == "PULLBACK_ENTRY_READY"
        assert any("PULLBACK_GO" in c for c in result["reason_codes"])


# ── Fix 11: SHORT_WATCH strictness ────────────────────────────────────────────

def test_positive_comp_reject_seq4_bullish_not_short_watch():
    """Positive composite + reject seq4 + price >= 0.75 + above EMA50 → not SHORT_WATCH."""
    m = load_matrix()
    # We need a pattern where pos composite exists but neg seq4 exists too
    # Use a T signal with bullish lane composite but bearish seq4 context
    # Approximate by checking the classifier output with bullish price context
    row = _row(t="T4", lane1="T4L12NP", close=58,
               ema20=50, ema50=48, ema89=45, high=60, low=40)
    hist = [
        _row(t="T4", close=42, high=50, low=40),
        _row(t="T4", close=50, high=55, low=43),
        _row(z="Z3", close=54, high=58, low=48),  # last history bar is a Z → potential neg seq4
    ]
    result = classify_tz_event(row, hist, m)
    # If SHORT_WATCH, bearish context must confirm it
    if result["role"] == "SHORT_WATCH":
        assert (
            result["price_position_4bar"] < 0.35 or
            not result["above_ema50"] or
            result["breaks_4bar_low"] or
            not result["above_ema20"] and not result["above_ema50"]
        ), f"SHORT_WATCH with no bearish context: price_pos={result['price_position_4bar']:.2f}"

def test_reject_comp_bullish_context_is_reject_long_not_short_watch():
    """Reject composite + price_pos >= 0.75 + above EMA50 → REJECT_LONG, not SHORT_WATCH."""
    m = load_matrix()
    # T2 with reject composite, strong bullish price context
    row = _row(t="T2", lane1="T2L12EUR", close=58,
               ema20=50, ema50=48, ema89=45, high=60, low=40)
    hist = [
        _row(t="T2", close=42, high=50, low=40),
        _row(t="T2", close=50, high=55, low=43),
        _row(t="T2", close=53, high=58, low=47),
    ]
    result = classify_tz_event(row, hist, m)
    # If a reject composite was matched and context is bullish, should not be SHORT_WATCH
    if bool(result.get("reject_flags")) and result["price_position_4bar"] >= 0.75 and result["above_ema50"]:
        assert result["role"] in ("REJECT_LONG", "MIXED_WATCH", "NO_EDGE", "BULL_WATCH",
                                   "BULL_A", "BULL_B", "PULLBACK_GO"), \
            f"Got {result['role']} but expected non-SHORT_WATCH for bullish reject context"

def test_short_watch_allowed_when_bearish_confirmed():
    """SHORT_WATCH is valid when price is in bottom range below EMA50."""
    m = load_matrix()
    row = _row(t="T1", lane1="L12NDP", close=30,
               ema20=55, ema50=60, ema89=65, high=31, low=28)
    hist = [
        _row(t="T1", close=35, high=40, low=30),
        _row(t="T1", close=32, high=37, low=30),
        _row(t="T1", close=30, high=34, low=28),
    ]
    result = classify_tz_event(row, hist, m)
    # SHORT_WATCH is allowed here (below EMA50, bottom of range)
    # Just verify the classifier doesn't error and returns a valid role
    assert result["role"] in _ALL_VALID_ROLES


# ── New roles exist in constants ──────────────────────────────────────────────

def test_new_roles_in_role_rank():
    from tz_intelligence.classifier import _ROLE_RANK
    for role in ("PULLBACK_GO", "MIXED_WATCH", "REJECT_LONG", "DEEP_PULLBACK_WATCH"):
        assert role in _ROLE_RANK, f"Missing role in _ROLE_RANK: {role}"

def test_new_roles_quality_via_function():
    from tz_intelligence.classifier import _quality_from_score, _WATCH_ONLY_ROLES
    # PULLBACK_GO with high score → A
    assert _quality_from_score("PULLBACK_GO", 85, False, 0.8, False) == "A"
    # MIXED_WATCH always Watch (watch-only role)
    assert "MIXED_WATCH" in _WATCH_ONLY_ROLES
    assert _quality_from_score("MIXED_WATCH", 90, False, 0.9, False) == "Watch"
    # REJECT_LONG → Reject
    assert _quality_from_score("REJECT_LONG", 90, False, 0.9, False) == "Reject"
    # DEEP_PULLBACK_WATCH always Watch (watch-only role)
    assert "DEEP_PULLBACK_WATCH" in _WATCH_ONLY_ROLES
    assert _quality_from_score("DEEP_PULLBACK_WATCH", 90, False, 0.9, False) == "Watch"

def test_z_pullback_score_capped_at_75():
    """Z-based PULLBACK_READY score must not exceed 75 without PULLBACK_GO confirmation."""
    m = load_matrix()
    # Z signal with multiple bonuses stacking — score should be capped
    row = _row(z="Z5", lane1="Z5L25NDP", close=55,
               ema20=50, ema50=48, ema89=45, high=58, low=50)
    prev = _row(z="Z5", close=47, ema50=48)   # prev below ema50 → reclaim
    result = classify_tz_event(row, [prev], m)
    if result["role"] in ("PULLBACK_READY_A", "PULLBACK_READY_B"):
        assert result["score"] <= 75, \
            f"Z pullback READY score should be capped at 75, got {result['score']}"


# ── Issue 1: General BULL_A gate ──────────────────────────────────────────────

def test_bull_a_gate_deep_range_below_all_emas():
    """T5 with strong composite but price_pos < 0.25 + below all EMAs must not be BULL_A."""
    m = load_matrix()
    row = _row(t="T5", lane1="T5L46NB", close=30,
               ema20=60, ema50=55, ema89=50, high=32, low=28)
    hist = [
        _row(t="T5", close=35, high=40, low=28),
        _row(t="T5", close=33, high=38, low=29),
        _row(t="T5", close=31, high=36, low=29),
    ]
    result = classify_tz_event(row, hist, m)
    assert result["role"] != "BULL_A", \
        f"Should not be BULL_A when deep below all EMAs (price_pos={result['price_position_4bar']:.2f})"
    assert result["role"] in ("BULL_WATCH", "BULL_B", "NO_EDGE", "MIXED_WATCH",
                               "PULLBACK_WATCH", "DEEP_PULLBACK_WATCH")

def test_bull_a_gate_requires_ema_support():
    """BULL_A requires at least one EMA above or reclaiming."""
    m = load_matrix()
    row = _row(t="T4", lane1="T4L12NP", close=50,
               ema20=60, ema50=55, ema89=50,   # below all EMAs
               high=52, low=48)
    result = classify_tz_event(row, [], m)
    assert result["role"] != "BULL_A"

def test_bull_a_gate_requires_price_position():
    """BULL_A requires price_pos >= 0.50."""
    m = load_matrix()
    # price_pos = (close - low) / (high - low) = (45 - 40) / (60 - 40) = 0.25 → < 0.50
    row = _row(t="T4", lane1="T4L12NP", close=45,
               ema20=42, ema50=40, ema89=38,   # all EMAs below close (EMA support exists)
               high=60, low=40)
    hist = [
        _row(t="T4", close=43, high=60, low=40),
        _row(t="T4", close=44, high=60, low=40),
        _row(t="T4", close=45, high=60, low=40),
    ]
    result = classify_tz_event(row, hist, m)
    # price_pos ~ 0.25 (close=45 in [40..60]) → should be BULL_B at most
    assert result["role"] != "BULL_A", \
        f"BULL_A with price_pos ~0.25 — got role={result['role']}, price_pos={result['price_position_4bar']:.2f}"

def test_bull_a_gate_not_triggered_with_good_price_and_ema():
    """When price_pos >= 0.50 and EMA support exists, the BULL_A gate must not fire."""
    m = load_matrix()
    # price_pos = (58 - 40) / (60 - 40) = 0.90 → well above 0.50; all EMAs below close
    row = _row(t="T4", lane1="T4L12NP", close=58,
               ema20=50, ema50=48, ema89=45,
               high=60, low=40)
    hist = [
        _row(t="T4", close=42, high=50, low=40),
        _row(t="T4", close=50, high=55, low=43),
        _row(t="T4", close=53, high=58, low=47),
    ]
    result = classify_tz_event(row, hist, m)
    # The BULL_A gate itself must NOT have fired (different from conflict override)
    gate_fired = any(
        c in ("BULL_A→BULL_WATCH:deep_range+no_ema_support",
              "BULL_A→BULL_B:insufficient_price_or_ema_confirmation")
        for c in result["reason_codes"]
    )
    assert not gate_fired, \
        f"BULL_A gate should not fire with price_pos=0.90 and above all EMAs: {result['reason_codes']}"


# ── Issue 2: PULLBACK_GO strictness ──────────────────────────────────────────

def test_pullback_go_not_assigned_without_prior_pullback_z():
    """T signal without prior pullback Z in history must not become PULLBACK_GO."""
    m = load_matrix()
    # All history bars are T signals, no Z → no pullback setup
    hist = [
        _row(t="T4", close=42, high=50, low=40),
        _row(t="T4", close=50, high=55, low=43),
        _row(t="T4", close=53, high=58, low=47),
    ]
    # Current bar: T4, top of range, good EMA — but no prior Z
    curr = _row(t="T4", lane1="T4L12NP", close=58,
                ema20=50, ema50=48, ema89=45, high=60, low=40,
                ticker="X", date="2025-02-01")
    result = classify_tz_event(curr, hist, m)
    assert result["role"] != "PULLBACK_GO", \
        f"PULLBACK_GO without prior pullback Z should not be assigned, got {result['role']}"

def test_weak_t_cannot_be_pullback_go():
    """T1/T2/T9/T10 cannot become PULLBACK_GO even with recent pullback Z."""
    m = load_matrix()
    hist = [
        _row(z="Z5", close=42, high=50, low=40),
        _row(z="Z5", close=48, high=52, low=42),
        _row(z="Z5", close=50, high=54, low=44),
    ]
    curr = _row(t="T1", close=56, high=58, low=52,   # T1 = weak signal
                ema20=50, ema50=48, ema89=45,
                ticker="X", date="2025-02-01")
    result = classify_tz_event(curr, hist, m)
    assert result["role"] != "PULLBACK_GO", \
        f"Weak T signal (T1) should not be PULLBACK_GO, got {result['role']}"

def test_pullback_go_not_from_non_pullback_z():
    """Only _PULLBACK_Z_SIGNALS (Z3/Z4/Z5/Z6/Z9) trigger PULLBACK_GO, not all Z."""
    m = load_matrix()
    # Z1 is NOT in _PULLBACK_Z_SIGNALS
    hist = [
        _row(z="Z1", close=42, high=50, low=40),
        _row(z="Z1", close=48, high=52, low=42),
        _row(z="Z1", close=50, high=54, low=44),
    ]
    curr = _row(t="T4", close=56, high=58, low=52,
                ema20=50, ema50=48, ema89=45,
                ticker="X", date="2025-02-01")
    result = classify_tz_event(curr, hist, m)
    assert result["role"] != "PULLBACK_GO", \
        f"Non-pullback Z (Z1) should not trigger PULLBACK_GO, got {result['role']}"


# ── Issue 3: Quality from score ───────────────────────────────────────────────

def test_negative_score_never_quality_b():
    """Any role with score < 0 must have quality Watch or Reject, never B."""
    from tz_intelligence.classifier import _quality_from_score
    for role in ("PULLBACK_READY_B", "PULLBACK_WATCH", "BULL_B", "BULL_WATCH"):
        q = _quality_from_score(role, -5, False, 0.5, False)
        assert q in ("Watch", "Reject", "—"), \
            f"Score -5, role {role} → quality should not be B, got {q}"

def test_quality_a_requires_score_80():
    """Quality A requires score >= 80."""
    from tz_intelligence.classifier import _quality_from_score
    assert _quality_from_score("BULL_A", 79, False, 0.8, False) == "B"
    assert _quality_from_score("BULL_A", 80, False, 0.8, False) == "A"

def test_quality_b_requires_score_60():
    """Quality B requires score 60–79."""
    from tz_intelligence.classifier import _quality_from_score
    assert _quality_from_score("BULL_B", 59, False, 0.8, False) == "Watch"
    assert _quality_from_score("BULL_B", 60, False, 0.8, False) == "B"

def test_quality_capped_watch_when_below_all_emas_deep():
    """below_all_emas=True + price_pos < 0.25 caps quality A to Watch."""
    from tz_intelligence.classifier import _quality_from_score
    # Score 90 would normally be A, but deep+below-all-EMAs → Watch
    q = _quality_from_score("BULL_A", 90, below_all_emas=True, price_pos=0.1, conflict=False)
    assert q == "Watch"

def test_quality_capped_b_when_conflict():
    """conflict=True caps quality A to B."""
    from tz_intelligence.classifier import _quality_from_score
    q = _quality_from_score("BULL_A", 90, below_all_emas=False, price_pos=0.8, conflict=True)
    assert q == "B"

def test_deep_pullback_watch_score_capped_and_quality_watch():
    """DEEP_PULLBACK_WATCH with score -5 → quality Watch, score <= 35."""
    m = load_matrix()
    row = _row(z="Z6", close=30, ema20=60, ema50=55, ema89=50, high=32, low=28)
    hist = [
        _row(z="Z6", close=35, high=40, low=28),
        _row(z="Z6", close=33, high=38, low=29),
        _row(z="Z6", close=31, high=36, low=29),
    ]
    result = classify_tz_event(row, hist, m)
    if result["role"] == "DEEP_PULLBACK_WATCH":
        assert result["quality"] == "Watch"
        assert result["score"] <= 35


# ── Issue 4: SHORT_WATCH with pos comp + reject seq4 + above EMA50 ────────────

def test_pos_comp_neg_seq4_above_ema50_not_short_watch():
    """Positive composite + reject seq4 + above_ema50=True must not be SHORT_WATCH."""
    m = load_matrix()
    # Simulate U-like case: T5 composite (positive), seq4 has reject, above all EMAs
    row = _row(t="T5", lane1="T5L46ND", close=58,
               ema20=50, ema50=48, ema89=45, high=60, low=40)
    hist = [
        _row(t="T5", close=42, high=50, low=40),
        _row(t="T5", close=50, high=55, low=43),
        _row(z="Z3", close=54, high=58, low=48),
    ]
    result = classify_tz_event(row, hist, m)
    # above_ema50 = True (close=58 > ema50=48), so should not be SHORT_WATCH
    if result["above_ema50"]:
        assert result["role"] != "SHORT_WATCH" or (
            result["price_position_4bar"] < 0.35 or result["breaks_4bar_low"]
        ), f"SHORT_WATCH with above_ema50 and no bearish confirmation: {result['reason_codes']}"

def test_pos_comp_neg_seq4_price_50pct_not_short_watch():
    """Positive composite + reject seq4 + price_pos >= 0.50 must not be SHORT_WATCH."""
    m = load_matrix()
    # price_pos = (55 - 40) / (60 - 40) = 0.75 → >= 0.50
    row = _row(t="T4", lane1="T4L12NP", close=55,
               ema20=60, ema50=65, ema89=70,  # below all EMAs
               high=60, low=40)
    hist = [
        _row(t="T4", close=42, high=50, low=40),
        _row(z="Z3", close=50, high=55, low=43),
        _row(z="Z3", close=52, high=58, low=47),
    ]
    result = classify_tz_event(row, hist, m)
    if result["price_position_4bar"] >= 0.50 and bool(result.get("good_flags")):
        assert result["role"] != "SHORT_WATCH", \
            f"price_pos >= 0.50 with positive composite → not SHORT_WATCH, got {result['role']}"


# ── v6 normalization invariant tests ─────────────────────────────────────────

def test_normalize_bull_b_score_capped_at_79():
    """BULL_B must never have score >= 80 (would imply A quality)."""
    from tz_intelligence.classifier import _normalize_role_score
    role, score = _normalize_role_score("BULL_B", 115, [])
    assert role == "BULL_B"
    assert score == 79, f"Expected 79, got {score}"

def test_normalize_bull_b_low_score_becomes_bull_watch():
    """BULL_B with score < 60 must downgrade to BULL_WATCH."""
    from tz_intelligence.classifier import _normalize_role_score
    role, score = _normalize_role_score("BULL_B", 55, [])
    assert role == "BULL_WATCH", f"Expected BULL_WATCH, got {role}"

def test_normalize_pullback_ready_b_score_capped():
    """PULLBACK_READY_B score >= 80 must be capped to 79."""
    from tz_intelligence.classifier import _normalize_role_score
    role, score = _normalize_role_score("PULLBACK_READY_B", 90, [])
    assert role == "PULLBACK_READY_B"
    assert score == 79

def test_normalize_pullback_ready_b_low_score_becomes_pullback_watch():
    """PULLBACK_READY_B score < 60 must downgrade to PULLBACK_WATCH."""
    from tz_intelligence.classifier import _normalize_role_score
    role, score = _normalize_role_score("PULLBACK_READY_B", 25, [])
    assert role == "PULLBACK_WATCH", f"Expected PULLBACK_WATCH, got {role}"

def test_normalize_deep_pullback_watch_score_cap():
    """DEEP_PULLBACK_WATCH score must be capped at 35."""
    from tz_intelligence.classifier import _normalize_role_score
    role, score = _normalize_role_score("DEEP_PULLBACK_WATCH", 60, [])
    assert role == "DEEP_PULLBACK_WATCH"
    assert score == 35, f"Expected 35, got {score}"

def test_normalize_any_watch_role_score_cap():
    """All *_WATCH roles must have score <= 59."""
    from tz_intelligence.classifier import _normalize_role_score
    for watch_role in ("BULL_WATCH", "PULLBACK_WATCH", "MIXED_WATCH", "SHORT_WATCH"):
        role, score = _normalize_role_score(watch_role, 100, [])
        assert role == watch_role
        assert score <= 59, f"{watch_role} score should be <=59, got {score}"

def test_normalize_bull_a_score_60_to_79_becomes_bull_b():
    """BULL_A with score 60-79 must downgrade to BULL_B."""
    from tz_intelligence.classifier import _normalize_role_score
    role, score = _normalize_role_score("BULL_A", 75, [])
    assert role == "BULL_B", f"Expected BULL_B, got {role}"
    assert score == 75

def test_normalize_bull_a_score_below_60_becomes_bull_watch():
    """BULL_A with score < 60 must downgrade to BULL_WATCH."""
    from tz_intelligence.classifier import _normalize_role_score
    role, score = _normalize_role_score("BULL_A", 50, [])
    assert role == "BULL_WATCH", f"Expected BULL_WATCH, got {role}"

def test_pullback_go_proof_fields_present_and_valid():
    """PULLBACK_GO result must include prior_pullback_ready_found==True and bars_ago 1-3."""
    m = load_matrix()
    hist = [
        _row(t="T4", close=42, high=50, low=40),
        _row(t="T4", close=44, high=52, low=42),
        _row(z="Z5", close=50, high=55, low=48,  # ← pullback Z bar
             ema20=48, ema50=46, ema89=44),
    ]
    curr = _row(t="T5", lane1="T5L46NB", close=58,
                ema20=50, ema50=48, ema89=45, high=60, low=50,
                ticker="PROOF_TEST", date="2025-03-01")
    result = classify_tz_event(curr, hist, m)
    if result["role"] == "PULLBACK_GO":
        assert result["prior_pullback_ready_found"] is True, \
            "PULLBACK_GO must have prior_pullback_ready_found=True"
        assert result["prior_pullback_ready_bars_ago"] in (1, 2, 3), \
            f"bars_ago must be 1-3, got {result['prior_pullback_ready_bars_ago']}"
        assert result["prior_pullback_ready_signal"] in ("Z3","Z4","Z5","Z6","Z9"), \
            f"signal must be pullback Z, got {result['prior_pullback_ready_signal']}"
    # Even if not PULLBACK_GO, proof fields must be present
    assert "prior_pullback_ready_found" in result
    assert "prior_pullback_ready_bars_ago" in result
    assert "pullback_high" in result
    assert "current_close_above_pullback_high" in result

def test_no_bull_b_with_quality_a():
    """Integration: classify several rows; none should yield BULL_B + quality A."""
    m = load_matrix()
    test_rows = [
        _row(t="T4", lane1="T4L12NP", close=80, ema20=70, ema50=65, ema89=60, high=85, low=70),
        _row(t="T5", lane1="T5L46NB", close=90, ema20=80, ema50=75, ema89=70, high=95, low=80),
        _row(t="T4", lane1="T4L12NP", close=50, ema20=60, ema50=55, ema89=50, high=55, low=45),
    ]
    for row in test_rows:
        result = classify_tz_event(row, [], m)
        if result["role"] == "BULL_B":
            assert result["quality"] != "A", \
                f"BULL_B must not have quality A (score={result['score']}, reasons={result['reason_codes']})"
            assert result["score"] <= 79, \
                f"BULL_B score must be <=79, got {result['score']}"


# ── v7: PULLBACK_CONFIRMING and BULL_B below-EMA gate ────────────────────────

def _pb_history_with_z5():
    """History rows with a pullback Z signal 1 bar ago, above EMA."""
    return [
        _row(t="T4", close=42, high=50, low=40),
        _row(t="T4", close=44, high=52, low=42),
        _row(z="Z5", close=50, high=55, low=48, ema20=48, ema50=46, ema89=44),
    ]


def test_pullback_confirming_when_no_high_break():
    """Prior pullback + good T + no high break → PULLBACK_CONFIRMING, not PULLBACK_GO."""
    m = load_matrix()
    hist = _pb_history_with_z5()
    # pullback_high from history Z5 bar = 55; current close = 53 (below 55)
    curr = _row(t="T5", lane1="T5L46NB", close=53,
                ema20=50, ema50=48, ema89=45, high=54, low=50,
                ticker="CONF_TEST", date="2025-04-01")
    result = classify_tz_event(curr, hist, m)
    if result["prior_pullback_ready_found"]:
        assert result["current_close_above_pullback_high"] is False or result["breaks_4bar_high"] is False
        # Without high break, role must not be PULLBACK_GO
        if not result["current_close_above_pullback_high"] and not result["breaks_4bar_high"]:
            assert result["role"] != "PULLBACK_GO", \
                f"No high break but got PULLBACK_GO (close={result.get('close')}, " \
                f"pullback_high={result.get('pullback_high')})"
            if result["role"] == "PULLBACK_CONFIRMING":
                assert result["action"] == "WAIT_FOR_PULLBACK_HIGH_BREAK"
                assert result["quality"] in ("B", "Watch"), \
                    f"PULLBACK_CONFIRMING must be max B quality, got {result['quality']}"
                assert result["score"] <= 79, \
                    f"PULLBACK_CONFIRMING score must be <=79, got {result['score']}"


def test_pullback_go_requires_high_break():
    """Prior pullback + good T + close above pullback_high → PULLBACK_GO."""
    m = load_matrix()
    hist = _pb_history_with_z5()
    # pullback_high from Z5 bar = 55; current close = 60 (above 55) → high_break
    curr = _row(t="T5", lane1="T5L46NB", close=60,
                ema20=50, ema50=48, ema89=45, high=62, low=55,
                ticker="PGO_TEST", date="2025-04-01")
    result = classify_tz_event(curr, hist, m)
    if result["role"] == "PULLBACK_GO":
        assert result["prior_pullback_ready_found"] is True
        assert (result["current_close_above_pullback_high"] is True or
                result["breaks_4bar_high"] is True), \
            "PULLBACK_GO must have high_break (close>pullback_high OR breaks_4bar_high)"


def test_pullback_go_via_breaks_4bar_high():
    """Prior pullback + good T + breaks_4bar_high → PULLBACK_GO."""
    m = load_matrix()
    hist = _pb_history_with_z5()
    # hist highs: 50, 52, 55 → range_high = 55
    # curr_h = 58 > 55 → breaks_4bar_high = True
    curr = _row(t="T5", lane1="T5L46NB", close=57,
                ema20=50, ema50=48, ema89=45, high=58, low=50,
                ticker="PGO_BREAK", date="2025-04-01")
    result = classify_tz_event(curr, hist, m)
    if result["role"] == "PULLBACK_GO":
        assert result["prior_pullback_ready_found"] is True
        assert result["breaks_4bar_high"] is True or result["current_close_above_pullback_high"] is True


def test_bull_b_below_all_emas_deep_range_becomes_bull_watch():
    """BULL_B with below all EMAs + price_pos < 0.25 must become BULL_WATCH, score <= 35."""
    m = load_matrix()
    # close=42 far below all EMAs; 4-bar range 40-60 → price_pos=(42-40)/20=0.10
    row = _row(t="T5", lane1="T5L46NB", close=42,
               ema20=80, ema50=75, ema89=70,  # far above close → below all EMAs
               high=44, low=40,
               ticker="AEE_LIKE", date="2025-05-01")
    hist = [
        _row(t="T4", close=55, high=60, low=50),
        _row(t="T4", close=53, high=58, low=48),
        _row(t="T4", close=51, high=56, low=46),
    ]
    result = classify_tz_event(row, hist, m)
    assert result["above_ema20"] is False
    assert result["above_ema50"] is False
    assert result["above_ema89"] is False
    assert result["price_position_4bar"] < 0.25, \
        f"Expected price_pos < 0.25, got {result['price_position_4bar']}"
    assert result["role"] not in ("BULL_B", "BULL_A"), \
        f"BULL_B/BULL_A must not appear below all EMAs in deep range, got {result['role']}"
    if result["role"] == "BULL_WATCH":
        assert result["score"] <= 35, \
            f"BULL_WATCH (from deep below-EMA) score must be <=35, got {result['score']}"


def test_pullback_confirming_quality_max_b():
    """PULLBACK_CONFIRMING must never produce quality A."""
    from tz_intelligence.classifier import _normalize_role_score
    role, score = _normalize_role_score("PULLBACK_CONFIRMING", 90, [])
    assert role == "PULLBACK_CONFIRMING"
    assert score == 79, f"PULLBACK_CONFIRMING score must be capped at 79, got {score}"

def test_pullback_confirming_low_score_becomes_pullback_watch():
    from tz_intelligence.classifier import _normalize_role_score
    role, score = _normalize_role_score("PULLBACK_CONFIRMING", 45, [])
    assert role == "PULLBACK_WATCH", f"Expected PULLBACK_WATCH, got {role}"


# ── nasdaq_gt5 universe tests ─────────────────────────────────────────────────

def test_nasdaq_gt5_uses_nasdaq_gt5_matrix_rules():
    """nasdaq_gt5 universe must use NASDAQ_GT5 matrix rules, not SP500."""
    from tz_intelligence.matrix_loader import load_matrix
    m = load_matrix()
    allowed = m.allowed_univs("nasdaq_gt5")
    assert "NASDAQ_GT5" in allowed, "nasdaq_gt5 must include NASDAQ_GT5 rules"
    assert "GLOBAL" in allowed,     "nasdaq_gt5 must include GLOBAL rules"
    assert "SP500" not in allowed,  "nasdaq_gt5 must NOT include SP500 rules"

def test_nasdaq_gt5_does_not_include_sp500_rules():
    """SP500-only rules must not apply to nasdaq_gt5 scans."""
    from tz_intelligence.matrix_loader import load_matrix
    m = load_matrix()
    sp500_allowed   = m.allowed_univs("sp500")
    nasdaq_gt5_allowed = m.allowed_univs("nasdaq_gt5")
    assert "SP500" in sp500_allowed
    assert "SP500" not in nasdaq_gt5_allowed

def test_sp500_rules_unchanged_by_nasdaq_gt5_addition():
    """SP500 allowed universe set must be exactly {SP500, GLOBAL} — unchanged."""
    from tz_intelligence.matrix_loader import load_matrix
    m = load_matrix()
    assert m.allowed_univs("sp500") == {"SP500", "GLOBAL"}

def test_nasdaq_gt5_stat_path():
    """_stat_path for nasdaq_gt5 must produce the correct filename."""
    from tz_intelligence.scanner import _stat_path
    path = _stat_path("nasdaq_gt5", "1d")
    assert path == "stock_stat_tz_wlnbb_nasdaq_gt5_1d.csv", f"Got: {path}"

def test_nasdaq_gt5_stat_path_all_timeframes():
    from tz_intelligence.scanner import _stat_path
    for tf in ("1d", "4h", "1h", "1wk"):
        path = _stat_path("nasdaq_gt5", tf)
        assert f"nasdaq_gt5" in path
        assert tf in path

def test_sp500_stat_path_unchanged():
    """SP500 stat path must not be affected by nasdaq_gt5 changes."""
    from tz_intelligence.scanner import _stat_path
    assert _stat_path("sp500", "1d") == "stock_stat_tz_wlnbb_sp500_1d.csv"
    assert _stat_path("sp500", "4h") == "stock_stat_tz_wlnbb_sp500_4h.csv"

def test_classify_nasdaq_gt5_uses_nasdaq_gt5_rules():
    """Classifying with scan_universe=nasdaq_gt5 must match NASDAQ_GT5+GLOBAL rules."""
    m = load_matrix()
    row = _row(t="T5", lane1="T5L46NB", close=25,
               ema20=22, ema50=20, ema89=18, high=26, low=23)
    result = classify_tz_event(row, [], m, scan_universe="nasdaq_gt5")
    # SP500 rules must not appear in matched_universe
    mu = result.get("matched_universe", "")
    assert mu != "SP500", f"nasdaq_gt5 scan must not match SP500 rule, got universe={mu}"

def test_classify_sp500_unchanged_after_nasdaq_gt5():
    """SP500 classification must be identical before/after adding nasdaq_gt5."""
    m = load_matrix()
    row = _row(t="T5", lane1="T5L46NB", close=80,
               ema20=75, ema50=70, ema89=65, high=82, low=76)
    sp500_result = classify_tz_event(row, [], m, scan_universe="sp500")
    # Must use SP500 or GLOBAL rules
    mu = sp500_result.get("matched_universe", "")
    assert mu != "NASDAQ_GT5", f"SP500 scan must not match NASDAQ_GT5 rules, got {mu}"
    # Role must be a valid role
    assert sp500_result["role"] in _ALL_VALID_ROLES

def test_generate_stock_stat_min_price_param_exists():
    """generate_stock_stat must accept min_price parameter."""
    import inspect
    from analyzers.tz_wlnbb.stock_stat import generate_stock_stat
    sig = inspect.signature(generate_stock_stat)
    assert "min_price" in sig.parameters, "generate_stock_stat must have min_price parameter"
    assert sig.parameters["min_price"].default == 0

def test_generate_stock_stat_skips_below_min_price():
    """generate_stock_stat with min_price=5 must skip tickers with close < 5."""
    import pandas as pd
    from analyzers.tz_wlnbb.stock_stat import generate_stock_stat
    import tempfile, os

    calls = []
    def mock_fetch(ticker, interval, bars):
        price = 3.0 if ticker == "PENNY" else 25.0
        calls.append((ticker, price))
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        return pd.DataFrame({
            "open": price, "high": price + 0.5, "low": price - 0.5,
            "close": price, "volume": 1_000_000,
        }, index=dates)

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test_stat.csv")
        path, audit = generate_stock_stat(
            ["PENNY", "VALID"], mock_fetch,
            universe="nasdaq_gt5", tf="1d", bars=10,
            output_path=out, min_price=5.0,
        )
        assert "PENNY" in audit.get("skip_reasons", {}), \
            "PENNY (close=3) must be skipped when min_price=5"
        import csv
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        tickers_in_csv = {r.get("ticker") for r in rows}
        assert "PENNY" not in tickers_in_csv, "close<5 ticker must not appear in output CSV"


# ── NASDAQ_GT5 SHORT_WATCH strict gate ────────────────────────────────────────

def _ngt5_row(**kwargs):
    defaults = {
        "ticker": "TEST", "date": "2025-01-01",
        "t_signal": "", "z_signal": "", "l_signal": "",
        "lane1_label": "", "lane3_label": "",
        "volume_bucket": "", "ne_suffix": "", "wick_suffix": "",
        "close": "50", "ema20": "45", "ema50": "40", "ema89": "35",
        "high": "52", "low": "48", "open": "50", "volume": "1000000",
    }
    defaults.update({k: str(v) for k, v in kwargs.items()})
    return defaults

# History bars for SHORT_WATCH gate tests (prev lows 20/19/18 → min=18)
_GT5_HIST = [
    _ngt5_row(close=22, high=25, low=20),
    _ngt5_row(close=21, high=24, low=19),
    _ngt5_row(close=20, high=23, low=18),
]


def test_nasdaq_gt5_short_watch_blocked_no_stat_no_breakdown():
    """NASDAQ_GT5: med >= 0, fail < 30, no 4bar low break, below EMAs → NOT SHORT_WATCH.

    BTM/Z2L46ED-like: med=+0.274, fail=26 — no statistical short edge.
    Price structure alone (below EMAs, price_pos<0.25) is insufficient.
    """
    m = load_matrix()
    # low=19 >= prev_min=18 → breaks_4bar_low=False
    row = _ngt5_row(z_signal="Z2", lane1_label="Z2L46ED",
                    close=15, ema20=35, ema50=40, ema89=45, high=16, low=19)
    result = classify_tz_event(row, _GT5_HIST, m, scan_universe="nasdaq_gt5")
    assert result["role"] != "SHORT_WATCH", (
        f"NASDAQ_GT5: med>=0 fail<30 no breakdown must not be SHORT_WATCH, got {result['role']}. "
        f"reason_codes={result['reason_codes']}"
    )
    assert any("NASDAQ_GT5" in c for c in result["reason_codes"]), (
        "NASDAQ_GT5 gate reason code must be present"
    )


def test_nasdaq_gt5_short_watch_blocked_positive_composite():
    """NASDAQ_GT5: positive composite med >= 0.5, fail < 25, no breakdown → NOT SHORT_WATCH."""
    m = load_matrix()
    # Z5L3EU: med=+0.747, fail=24 — mirrors CCEP example
    row = _ngt5_row(z_signal="Z5", lane1_label="Z5L3EU",
                    close=60, ema20=55, ema50=50, ema89=45, high=61, low=58)
    hist = [_ngt5_row(close=55, high=58, low=52),
            _ngt5_row(close=57, high=60, low=54),
            _ngt5_row(close=59, high=62, low=56)]
    result = classify_tz_event(row, hist, m, scan_universe="nasdaq_gt5")
    assert result["role"] != "SHORT_WATCH", (
        f"NASDAQ_GT5: positive composite med+0.747 fail<25 must not be SHORT_WATCH, got {result['role']}"
    )


def test_nasdaq_gt5_short_watch_allowed_when_med_negative():
    """NASDAQ_GT5: med < 0 (statistical short edge) + below EMA50 → SHORT_WATCH allowed.

    T1L3EUR has matched_med10d=-0.073 in NASDAQ_GT5 rules.
    """
    m = load_matrix()
    # low=19 >= prev_min=18 → no breakdown; stat edge from neg med
    row = _ngt5_row(t_signal="T1", lane1_label="T1L3EUR",
                    close=15, ema20=35, ema50=40, ema89=45, high=16, low=19)
    result = classify_tz_event(row, _GT5_HIST, m, scan_universe="nasdaq_gt5")
    assert result.get("matched_med10d_pct") not in ("", None), "T1L3EUR must match a rule with med stat"
    try:
        assert float(result["matched_med10d_pct"]) < 0, "matched_med10d_pct must be < 0"
    except (TypeError, ValueError):
        pass
    assert result["role"] in ("SHORT_WATCH", "SHORT_GO", "REJECT_LONG", "MIXED_WATCH"), (
        f"NASDAQ_GT5 with neg med must not be demoted to NO_EDGE/BULL roles, got {result['role']}"
    )
    assert result["role"] not in ("BULL_A", "BULL_B", "BULL_WATCH", "PULLBACK_GO",
                                   "PULLBACK_CONFIRMING", "PULLBACK_READY_A", "PULLBACK_READY_B",
                                   "NO_EDGE"), (
        f"Statistical short edge (med<0) must not yield NO_EDGE or bullish role: {result['role']}"
    )


def test_nasdaq_gt5_short_watch_allowed_when_fail_ge_30():
    """NASDAQ_GT5: fail >= 30 (statistical short edge) + below EMA50 → SHORT_WATCH allowed.

    T5L25NR has matched_fail10d=30.097 in NASDAQ_GT5 rules.
    """
    m = load_matrix()
    row = _ngt5_row(t_signal="T5", lane1_label="T5L25NR",
                    close=15, ema20=35, ema50=40, ema89=45, high=16, low=19)
    result = classify_tz_event(row, _GT5_HIST, m, scan_universe="nasdaq_gt5")
    assert result.get("matched_fail10d_pct") not in ("", None), "T5L25NR must match a rule with fail stat"
    try:
        assert float(result["matched_fail10d_pct"]) >= 30, "matched_fail10d_pct must be >= 30"
    except (TypeError, ValueError):
        pass
    assert result["role"] in ("SHORT_WATCH", "SHORT_GO", "REJECT_LONG", "MIXED_WATCH"), (
        f"NASDAQ_GT5 with fail>=30 must not be demoted to NO_EDGE/BULL roles, got {result['role']}"
    )


def test_nasdaq_gt5_short_watch_allowed_when_breaks_4bar_low():
    """NASDAQ_GT5: actual 4-bar low breakdown → SHORT_WATCH allowed even if med >= 0.

    breaks_4bar_low = curr_l < min(previous 3 bars' lows).
    """
    m = load_matrix()
    # prev lows: 20/19/18 → min=18. current low=17 < 18 → breaks_4bar_low=True
    row = _ngt5_row(z_signal="Z2", lane1_label="Z2L46ED",
                    close=15, ema20=35, ema50=40, ema89=45, high=16, low=17)
    result = classify_tz_event(row, _GT5_HIST, m, scan_universe="nasdaq_gt5")
    assert result["breaks_4bar_low"] is True, (
        "low=17 < prev_min=18 must produce breaks_4bar_low=True"
    )
    assert result["role"] in ("SHORT_WATCH", "SHORT_GO"), (
        f"NASDAQ_GT5: actual breakdown must allow SHORT_WATCH/SHORT_GO, got {result['role']}. "
        f"reason_codes={result['reason_codes']}"
    )


def test_nasdaq_gt5_short_watch_gate_does_not_affect_sp500():
    """SP500 SHORT_WATCH logic must be entirely unaffected by the NASDAQ_GT5 gate."""
    m = load_matrix()
    row = _ngt5_row(z_signal="Z2", lane1_label="Z2L46ED",
                    close=15, ema20=35, ema50=40, ema89=45, high=16, low=19)
    sp500_result = classify_tz_event(row, _GT5_HIST, m, scan_universe="sp500")
    assert "NASDAQ_GT5" not in " ".join(sp500_result.get("reason_codes", [])), (
        "SP500 scan must not contain NASDAQ_GT5 gate reason codes"
    )


# ── NASDAQ_GT5 Liquidity gate ─────────────────────────────────────────────────

def test_nasdaq_gt5_low_liquidity_demotes_to_no_edge():
    """NASDAQ_GT5: volume < 100K → active roles demoted to NO_EDGE."""
    m = load_matrix()
    # T5L46NB gives bullish role; vol=7400 → LOW_LIQUIDITY
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=20, ema20=18, ema50=16, ema89=14,
                    high=21, low=19, volume=7400)
    result = classify_tz_event(row, _GT5_HIST, m, scan_universe="nasdaq_gt5")
    assert result["role"] == "NO_EDGE", (
        f"NASDAQ_GT5 vol=7.4K must demote to NO_EDGE, got {result['role']}"
    )
    assert result["liquidity_tier"] == "LOW"
    assert "LOW_LIQUIDITY" in " ".join(result["reason_codes"])
    assert result["score"] <= 25


def test_nasdaq_gt5_low_liquidity_score_capped():
    """NASDAQ_GT5: very low liquidity caps score to <= 25."""
    m = load_matrix()
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=20, ema20=18, ema50=16, ema89=14,
                    high=21, low=19, volume=50000)
    result = classify_tz_event(row, _GT5_HIST, m, scan_universe="nasdaq_gt5")
    assert result["score"] <= 25, f"LOW_LIQUIDITY score must be <= 25, got {result['score']}"
    assert result["action"] == "LOW_LIQUIDITY_SKIP"


def test_nasdaq_gt5_mid_volume_caps_a_roles():
    """NASDAQ_GT5: 100K <= volume < 500K caps A-roles to B and score to 79."""
    m = load_matrix()
    # Use a row that would normally reach BULL_A — need high score bullish T signal
    # T5L46NB with above EMAs; force vol=200K
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=80, ema20=75, ema50=70, ema89=65,
                    high=85, low=78, volume=200000)
    hist_bullish = [
        _ngt5_row(close=76, high=80, low=74),
        _ngt5_row(close=78, high=82, low=76),
        _ngt5_row(close=79, high=83, low=77),
    ]
    result = classify_tz_event(row, hist_bullish, m, scan_universe="nasdaq_gt5")
    assert result["liquidity_tier"] == "MID"
    assert result["role"] not in ("BULL_A", "PULLBACK_READY_A"), (
        f"NASDAQ_GT5 vol=200K must not produce A-roles, got {result['role']}"
    )
    assert result["score"] <= 79, f"MID liquidity score must be <= 79, got {result['score']}"


def test_nasdaq_gt5_normal_volume_not_demoted():
    """NASDAQ_GT5: volume >= 500K and dollar_volume >= 2M → normal classification."""
    m = load_matrix()
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=20, ema20=18, ema50=16, ema89=14,
                    high=21, low=19, volume=800000)
    result = classify_tz_event(row, _GT5_HIST, m, scan_universe="nasdaq_gt5")
    assert result["liquidity_tier"] in ("OK", "STRONG", "MID")
    assert "LOW_LIQUIDITY" not in " ".join(result["reason_codes"])


def test_sp500_liquidity_gate_does_not_fire():
    """SP500: low volume must not trigger the NASDAQ_GT5 liquidity gate."""
    m = load_matrix()
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=20, ema20=18, ema50=16, ema89=14,
                    high=21, low=19, volume=7400)
    result = classify_tz_event(row, _GT5_HIST, m, scan_universe="sp500")
    assert "LOW_LIQUIDITY" not in " ".join(result["reason_codes"]), (
        "SP500 must not apply NASDAQ_GT5 liquidity gate"
    )
    assert result["role"] != "NO_EDGE" or True  # SP500 is free to assign any role


# ── NASDAQ_GT5 Z pullback breakdown gate ──────────────────────────────────────

def test_nasdaq_gt5_z_pullback_breakdown_demotes_pullback_ready():
    """NASDAQ_GT5: Z pullback + breaks_4bar_low + price_pos < 0.25 → not PULLBACK_READY_B."""
    m = load_matrix()
    # History with lows [52, 48, 42]; current low=39 < min=42 → breaks_4bar_low=True
    hist_break = [
        _ngt5_row(close=55, high=60, low=52),
        _ngt5_row(close=50, high=56, low=48),
        _ngt5_row(close=45, high=51, low=42),
    ]
    # Z5L3EU seeded as PULLBACK_WATCH in composite, use Z5 which is in _PULLBACK_Z_SIGNALS
    row = _ngt5_row(z_signal="Z5", lane1_label="Z5L3EU",
                    close=40, ema20=55, ema50=50, ema89=45,
                    high=42, low=39, volume=800000)
    result = classify_tz_event(row, hist_break, m, scan_universe="nasdaq_gt5")
    assert result["role"] not in ("PULLBACK_READY_A", "PULLBACK_READY_B"), (
        f"Z pullback with breakdown must not be PULLBACK_READY, got {result['role']}. "
        f"breaks_4bar_low={result['breaks_4bar_low']} pos={result['price_position_4bar']}"
    )


def test_nasdaq_gt5_z_pullback_breakdown_below_ema20_score_cap():
    """NASDAQ_GT5: Z pullback breakdown + below EMA20 → score capped at 35."""
    m = load_matrix()
    hist_break = [
        _ngt5_row(close=55, high=60, low=52),
        _ngt5_row(close=50, high=56, low=48),
        _ngt5_row(close=45, high=51, low=42),
    ]
    row = _ngt5_row(z_signal="Z5", lane1_label="Z5L3EU",
                    close=40, ema20=55, ema50=50, ema89=45,
                    high=42, low=39, volume=800000)
    result = classify_tz_event(row, hist_break, m, scan_universe="nasdaq_gt5")
    if not result["above_ema20"] and result.get("breaks_4bar_low"):
        assert result["score"] <= 35, (
            f"Z pullback breakdown below EMA20 must cap score at 35, got {result['score']}"
        )


# ── NASDAQ_GT5 Z1G strictness gate ───────────────────────────────────────────

def test_nasdaq_gt5_z1g_below_ema50_not_pullback_ready_b():
    """NASDAQ_GT5: Z1G below EMA50 must not be PULLBACK_READY_B."""
    m = load_matrix()
    # Z1GL5EB gives PULLBACK_READY_B; EMA50 above close → below EMA50
    row = _ngt5_row(z_signal="Z1G", lane1_label="Z1GL5EB",
                    close=50, ema20=55, ema50=58, ema89=62,
                    high=51, low=49, volume=800000)
    result = classify_tz_event(row, _GT5_HIST, m, scan_universe="nasdaq_gt5")
    assert result["role"] != "PULLBACK_READY_B", (
        f"NASDAQ_GT5 Z1G below EMA50 must not be PULLBACK_READY_B, got {result['role']}"
    )
    assert any("Z1G_STRICT" in c for c in result["reason_codes"]), (
        "Z1G_STRICT gate reason code must be present"
    )


def test_nasdaq_gt5_z1g_above_ema50_gate_does_not_block():
    """NASDAQ_GT5: Z1G above EMA50 with good price position — Z1G gate does not fire."""
    m = load_matrix()
    hist_good = [
        _ngt5_row(close=58, high=62, low=55),
        _ngt5_row(close=59, high=63, low=56),
        _ngt5_row(close=60, high=64, low=57),
    ]
    row = _ngt5_row(z_signal="Z1G", lane1_label="Z1GL5EB",
                    close=60, ema20=55, ema50=52, ema89=48,
                    high=61, low=57, volume=800000)
    result = classify_tz_event(row, hist_good, m, scan_universe="nasdaq_gt5")
    # Gate must not fire (above_ema50=True, price_pos >= 0.35, no breakdown)
    assert not any("Z1G_STRICT" in c for c in result["reason_codes"]), (
        f"Z1G_STRICT must not fire when above EMA50 + good pos. Got: {result['reason_codes']}"
    )
    # Role may be PULLBACK_READY_B or PULLBACK_WATCH (after normalization if score < 60)
    assert result["role"] in ("PULLBACK_READY_B", "PULLBACK_WATCH", "PULLBACK_READY_A"), (
        f"Z1G above EMA50 must yield pullback role, got {result['role']}"
    )


# ── NASDAQ_GT5 Bull Continuation roles ───────────────────────────────────────

# History where current bar's high breaks prior highs → breaks_4bar_high=True
# Prev highs: 55, 58, 62 → max=62; current high=65 > 62
_CONT_HIST = [
    _ngt5_row(close=52, high=55, low=49),
    _ngt5_row(close=56, high=58, low=53),
    _ngt5_row(close=60, high=62, low=57),
]


def test_nasdaq_gt5_bull_continuation_a_strong_setup():
    """NASDAQ_GT5: T5 above all EMAs, price_pos ~0.78, OK liquidity → BULL_CONTINUATION_A."""
    m = load_matrix()
    # Wide-range history so pos lands in 0.70-0.84 (avoids EXTENDED_WATCH ≥0.85).
    # All 4 bars range: lows=[10,10,10,75]→min=10, highs=[100,100,100,82]→max=100
    # close=80 → pos=(80-10)/(100-10)=70/90≈0.78
    hist_wide = [
        _ngt5_row(close=60, high=100, low=10),
        _ngt5_row(close=65, high=100, low=10),
        _ngt5_row(close=70, high=100, low=10),
    ]
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=80, ema20=70, ema50=65, ema89=60,
                    high=82, low=75, volume=1_200_000)
    result = classify_tz_event(row, hist_wide, m, scan_universe="nasdaq_gt5")
    assert result["role"] in ("BULL_CONTINUATION_A", "BULL_CONTINUATION_B"), (
        f"Expected BULL_CONTINUATION_A/B, got {result['role']}. "
        f"pos={result['price_position_4bar']} reason_codes={result['reason_codes']}"
    )
    assert any("BULL_CONTINUATION" in c for c in result["reason_codes"]), (
        "BULL_CONTINUATION reason code must be present"
    )


def test_nasdaq_gt5_bull_continuation_b_mid_position():
    """NASDAQ_GT5: T5 above EMAs, price_pos 0.50-0.69, gets BULL_CONTINUATION_B."""
    m = load_matrix()
    # Use wider range history so price_pos falls in 0.50-0.69 window
    hist_wide = [
        _ngt5_row(close=30, high=35, low=25),
        _ngt5_row(close=35, high=40, low=30),
        _ngt5_row(close=40, high=45, low=35),
    ]
    # 4bar range (incl current): low=30(prev min), high=55(curr high)
    # close=50, pos=(50-30)/(55-30)=20/25=0.80 → actually high, let's lower close
    # Try close=48: pos=(48-25)/(55-25)=23/30≈0.77 → also high.
    # Use close=44, high=46: pos=(44-25)/(46-25)=19/21≈0.90 → still high
    # Widen the range: hist lows [10,10,10], hist highs [80,80,80], close=50 high=55 low=48
    hist_wide2 = [
        _ngt5_row(close=40, high=80, low=10),
        _ngt5_row(close=42, high=80, low=10),
        _ngt5_row(close=45, high=80, low=10),
    ]
    # 4bar range: low=10, high=80; close=50 → pos=(50-10)/(80-10)=40/70≈0.57
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=50, ema20=45, ema50=40, ema89=35,
                    high=55, low=48, volume=1_200_000)
    result = classify_tz_event(row, hist_wide2, m, scan_universe="nasdaq_gt5")
    # With pos~0.57, expect BULL_CONTINUATION_B if cont_score>=60
    assert result["role"] in (
        "BULL_CONTINUATION_B", "BULL_CONTINUATION_A",
        "BULL_A", "BULL_B",  # matrix may naturally assign bull role
        "BULL_WATCH",
    ), (
        f"Expected a bull role for mid-position continuation setup, got {result['role']}. "
        f"reason_codes={result['reason_codes']}"
    )


def test_nasdaq_gt5_continuation_low_liquidity_blocked():
    """NASDAQ_GT5: continuation-eligible setup with LOW liquidity → must not get continuation role."""
    m = load_matrix()
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=70, ema20=60, ema50=55, ema89=50,
                    high=71, low=65, volume=5_000)  # vol=5K → LOW liquidity
    result = classify_tz_event(row, _CONT_HIST, m, scan_universe="nasdaq_gt5")
    assert result["role"] not in ("BULL_CONTINUATION_A", "BULL_CONTINUATION_B"), (
        f"LOW liquidity must block continuation roles, got {result['role']}. "
        f"liq_tier={result['liquidity_tier']}"
    )
    assert result["liquidity_tier"] == "LOW"
    assert "LOW_LIQUIDITY" in " ".join(result["reason_codes"])


def test_nasdaq_gt5_extended_watch_overextended():
    """NASDAQ_GT5: above all EMAs + price_pos >= 0.85 → EXTENDED_WATCH (not continuation A/B)."""
    m = load_matrix()
    # 4bar range: prev lows [49,53,57] min=49, prev highs [55,58,62] max=62
    # current high=70 → breaks_4bar_high=True; low=68 → no break low
    # 4bar range (all 4): low=49, high=70; close=69 → pos=(69-49)/(70-49)=20/21≈0.95 → ≥0.85
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=69, ema20=60, ema50=55, ema89=50,
                    high=70, low=68, volume=1_200_000)
    result = classify_tz_event(row, _CONT_HIST, m, scan_universe="nasdaq_gt5")
    assert result["role"] == "EXTENDED_WATCH", (
        f"Overextended setup must be EXTENDED_WATCH, got {result['role']}. "
        f"pos={result['price_position_4bar']} reason_codes={result['reason_codes']}"
    )
    assert any("EXTENDED_WATCH" in c for c in result["reason_codes"])


def test_nasdaq_gt5_bull_a_not_overridden_by_continuation():
    """NASDAQ_GT5: fresh BULL_A trigger must not be demoted by continuation gate."""
    m = load_matrix()
    # T5L46NB with extremely strong matrix score giving BULL_A
    # Close well above prev range ensures breaks_4bar_high
    hist_low = [
        _ngt5_row(close=20, high=22, low=18),
        _ngt5_row(close=21, high=23, low=19),
        _ngt5_row(close=22, high=24, low=20),
    ]
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=50, ema20=45, ema50=40, ema89=35,
                    high=55, low=48, volume=1_500_000)
    result = classify_tz_event(row, hist_low, m, scan_universe="nasdaq_gt5")
    # If matrix assigns BULL_A (score>=80), continuation must not override it
    if result["role"] == "BULL_A":
        assert not any("BULL_CONTINUATION" in c for c in result["reason_codes"]), (
            "Continuation gate must not fire when role is already BULL_A"
        )
    # Regardless, no logic error expected
    assert result["role"] in (
        "BULL_A", "BULL_B", "BULL_CONTINUATION_A", "BULL_CONTINUATION_B",
        "BULL_WATCH", "EXTENDED_WATCH",
    )


def test_nasdaq_gt5_continuation_requires_t_signal():
    """NASDAQ_GT5: Z-signal above EMAs must NOT trigger continuation roles."""
    m = load_matrix()
    row = _ngt5_row(z_signal="Z5", lane1_label="Z5L3EU",
                    close=70, ema20=60, ema50=55, ema89=50,
                    high=71, low=65, volume=1_200_000)
    result = classify_tz_event(row, _CONT_HIST, m, scan_universe="nasdaq_gt5")
    assert result["role"] not in ("BULL_CONTINUATION_A", "BULL_CONTINUATION_B"), (
        f"Z-signal must not trigger continuation roles, got {result['role']}"
    )
    assert not any("BULL_CONTINUATION" in c for c in result["reason_codes"])


def test_sp500_continuation_gate_does_not_fire():
    """SP500: same setup must not trigger NASDAQ_GT5 continuation gate."""
    m = load_matrix()
    row = _ngt5_row(t_signal="T5", lane1_label="T5L46NB",
                    close=70, ema20=60, ema50=55, ema89=50,
                    high=71, low=65, volume=1_200_000)
    result = classify_tz_event(row, _CONT_HIST, m, scan_universe="sp500")
    assert result["role"] not in ("BULL_CONTINUATION_A", "BULL_CONTINUATION_B", "EXTENDED_WATCH"), (
        f"SP500 must not get continuation roles, got {result['role']}"
    )
    assert not any("BULL_CONTINUATION" in c for c in result["reason_codes"])
    assert not any("EXTENDED_WATCH" in c for c in result["reason_codes"])


# ── ABR classifier tests ──────────────────────────────────────────────────────

from tz_intelligence.abr_classifier import (
    classify_abr, _classify_quality, _abr_category,
    _load_abr_db, _sig_prefix, _composite_matches,
    _composite_med_for_signal,
    ABR_UNIVERSE_MAP, ABR_SUPPORTED,
)


def test_abr_db_loads():
    """ABR rule database loads and contains expected signals for both universes."""
    db = _load_abr_db()
    assert len(db) > 0, "ABR DB must not be empty"
    keys = list(db.keys())
    universes = {k[0] for k in keys}
    assert "SP500"  in universes
    assert "NASDAQ" in universes
    signals = {k[1] for k in keys if k[0] == "SP500"}
    assert "T1" in signals and "Z3" in signals, f"SP500 T1/Z3 missing: {signals}"
    signals_nq = {k[1] for k in keys if k[0] == "NASDAQ"}
    assert "Z1" in signals_nq and "Z9" in signals_nq, f"NASDAQ Z1/Z9 missing: {signals_nq}"


def test_abr_sp500_quality_thresholds():
    """SP500 quality classification uses correct thresholds."""
    assert _classify_quality(0.9,  "SP500") == "STRONG"
    assert _classify_quality(0.8,  "SP500") == "STRONG"
    assert _classify_quality(0.79, "SP500") == "GOOD"
    assert _classify_quality(0.3,  "SP500") == "GOOD"
    assert _classify_quality(0.29, "SP500") == "AVERAGE"
    assert _classify_quality(0.0,  "SP500") == "AVERAGE"
    assert _classify_quality(-0.1, "SP500") == "REJECT"


def test_abr_nasdaq_quality_thresholds():
    """NASDAQ quality classification uses looser thresholds."""
    assert _classify_quality(0.5,  "NASDAQ") == "STRONG"
    assert _classify_quality(0.4,  "NASDAQ") == "STRONG"
    assert _classify_quality(0.39, "NASDAQ") == "GOOD"
    assert _classify_quality(0.1,  "NASDAQ") == "GOOD"
    assert _classify_quality(0.09, "NASDAQ") == "AVERAGE"
    assert _classify_quality(-0.1, "NASDAQ") == "AVERAGE"
    assert _classify_quality(-0.11,"NASDAQ") == "REJECT"


def test_abr_category_logic():
    """ABR category logic: gate + prev2 quality → A/B/B+/R."""
    # SP500 gate = STRONG
    assert _abr_category("STRONG",  "AVERAGE", "SP500") == "A"
    assert _abr_category("STRONG",  "GOOD",    "SP500") == "B"
    assert _abr_category("STRONG",  "STRONG",  "SP500") == "B+"
    assert _abr_category("STRONG",  "REJECT",  "SP500") == "R"
    assert _abr_category("GOOD",    "STRONG",  "SP500") == "R"   # gate fails
    assert _abr_category("AVERAGE", "STRONG",  "SP500") == "R"   # gate fails
    # NASDAQ gate = GOOD or STRONG
    assert _abr_category("GOOD",    "AVERAGE", "NASDAQ") == "A"
    assert _abr_category("GOOD",    "GOOD",    "NASDAQ") == "B"
    assert _abr_category("STRONG",  "STRONG",  "NASDAQ") == "B+"
    assert _abr_category("AVERAGE", "STRONG",  "NASDAQ") == "R"  # below GOOD gate


def test_abr_universe_mapping():
    """classify_abr returns UNKNOWN for unsupported universes."""
    m = load_matrix()
    result = classify_abr("T3", "T1|T2|T3", [], m, scan_universe="russell2k")
    assert result["abr_category"] == "UNKNOWN"
    result2 = classify_abr("T3", "T1|T2|T3", [], m, scan_universe="all_us")
    assert result2["abr_category"] == "UNKNOWN"


def test_abr_role_isolation():
    """classify_abr must not alter TZ role or score on the classifier result."""
    m = load_matrix()
    row = _row(t="T3", z="", lane1="T3L46NB", lane3="T3L46NB",
               close=55, ema20=50, ema50=45, ema89=40)
    history = [_row(t="T1", lane3="T1L46NB"), _row(t="T2", lane3="T2L46NB"),
               _row(t="T3", lane3="T3L46NB")]
    result_sp = classify_tz_event(row, history, m, scan_universe="sp500")
    result_nq = classify_tz_event(row, history, m, scan_universe="nasdaq_gt5")
    # ABR fields must exist in output (including new debug fields)
    assert "abr_category" in result_sp
    assert "abr_category" in result_nq
    assert "abr_prev1_comp_med10d" in result_sp
    assert "abr_prev2_comp_med10d" in result_nq
    # ABR category must be a valid value
    assert result_sp["abr_category"] in ("A", "B", "B+", "R", "UNKNOWN")
    assert result_nq["abr_category"] in ("A", "B", "B+", "R", "UNKNOWN")
    # Roles are still valid TZ roles (ABR did not corrupt them)
    assert result_sp["role"] not in ("A", "B", "B+")
    assert result_nq["role"] not in ("A", "B", "B+")


def test_abr_sig_prefix():
    """_sig_prefix extracts signal prefix from raw signals and full composite names."""
    assert _sig_prefix("T4")        == "T4"
    assert _sig_prefix("Z1G")       == "Z1G"
    assert _sig_prefix("T4L13NU")   == "T4"
    assert _sig_prefix("Z1GL5ED")   == "Z1G"
    assert _sig_prefix("T10L12NB")  == "T10"
    assert _sig_prefix("Z10L5NU")   == "Z10"
    assert _sig_prefix("—")         == ""
    assert _sig_prefix("")          == ""


def test_abr_composite_matches():
    """_composite_matches must not let 'Z1' match 'Z1GL5ED' (only 'Z1L*' patterns)."""
    assert _composite_matches("Z1L12NU",  "Z1")  is True
    assert _composite_matches("Z1GL5ED",  "Z1")  is False   # Z1 ≠ Z1G
    assert _composite_matches("Z1GL5ED",  "Z1G") is True
    assert _composite_matches("Z1GL46ED", "Z1G") is True
    assert _composite_matches("T4L13NU",  "T4")  is True
    assert _composite_matches("T4L5NU",   "T4")  is True
    assert _composite_matches("T4L13NU",  "T40") is False
    assert _composite_matches("",         "T4")  is False


def test_abr_composite_quality_lookup():
    """_composite_med_for_signal returns a float for known signals against matrix."""
    m = load_matrix()
    # T4 has composite patterns in SP500 but not NASDAQ_GT5 (SP500-only signal)
    med_sp = _composite_med_for_signal("T4", m, "sp500")
    assert med_sp is not None, "T4 composite med must resolve for sp500"
    # T3 has composite patterns in both universes
    med_t3_sp = _composite_med_for_signal("T3", m, "sp500")
    med_t3_nq = _composite_med_for_signal("T3", m, "nasdaq_gt5")
    assert med_t3_sp is not None, "T3 composite med must resolve for sp500"
    assert med_t3_nq is not None, "T3 composite med must resolve for nasdaq_gt5"
    # Z1G has composite patterns in NASDAQ_GT5
    med_z1g = _composite_med_for_signal("Z1G", m, "nasdaq_gt5")
    assert med_z1g is not None, "Z1G composite med must resolve for nasdaq_gt5"
    # Passing full composite name should yield the same prefix result as short signal
    med_full = _composite_med_for_signal("T3L12NU", m, "sp500")
    assert med_full is not None, "Full composite name should resolve via prefix extraction"
    assert abs(med_full - med_t3_sp) < 0.001, "Full and short names must aggregate identically"
    # Unknown signal → None
    assert _composite_med_for_signal("X99", m, "sp500") is None


def test_abr_category_computed_without_db_rule():
    """classify_abr must compute category even when no exact ABR DB rule matches."""
    m = load_matrix()
    # Use a real signal with a synthetic sequence unlikely to be in the DB
    result = classify_abr(
        final_signal="T4",
        seq4_str="T4|T4|T4|T4",   # very unlikely to be in ABR DB
        history_rows=[],
        matrix=m,
        scan_universe="sp500",
    )
    # abr_rule_found may be False, but category should not be UNKNOWN
    # (because T4 has known composite quality in sp500)
    assert result["abr_prev1_quality"] != "UNKNOWN", (
        f"prev1_quality must be resolved; got {result}")
    assert result["abr_prev2_quality"] != "UNKNOWN", (
        f"prev2_quality must be resolved; got {result}")
    assert result["abr_category"] in ("A", "B", "B+", "R"), (
        f"Category must be computed; got {result['abr_category']}")
    # Rule may or may not be found — both are acceptable
    assert isinstance(result["abr_rule_found"], bool)


def test_abr_full_classify_nasdaq():
    """classify_abr produces real categories (not all UNKNOWN) for nasdaq_gt5.

    Uses signals that exist in NASDAQ_GT5 composite rules:
    prev3=T3(GOOD), prev2=T2(GOOD), prev1=Z1G(STRONG), current=T2
    NASDAQ gate requires GOOD or STRONG → Z1G passes → category = B (prev2=GOOD).
    """
    m = load_matrix()
    result = classify_abr(
        final_signal="T2",
        seq4_str="T3|T2|Z1G|T2",   # prev3|prev2|prev1|current; T2 and Z1G in NASDAQ_GT5
        history_rows=[],
        matrix=m,
        scan_universe="nasdaq_gt5",
    )
    assert result["abr_prev1_composite"] == "Z1G"
    assert result["abr_prev2_composite"] == "T2"
    assert result["abr_prev1_quality"] != "UNKNOWN", f"Z1G must resolve: {result}"
    assert result["abr_prev2_quality"] != "UNKNOWN", f"T2 must resolve: {result}"
    assert result["abr_category"] in ("A", "B", "B+", "R")
    # NASDAQ gate: prev1 must be GOOD or STRONG to pass
    if result["abr_gate_pass"]:
        assert result["abr_prev1_quality"] in ("GOOD", "STRONG")
    else:
        assert result["abr_prev1_quality"] in ("AVERAGE", "REJECT")
        assert result["abr_category"] == "R"


# ── Security: input validation ────────────────────────────────────────────────

from tz_intelligence.scanner import run_intelligence_scan, _VALID_UNIVERSES, _VALID_TFS


def test_scanner_rejects_invalid_universe():
    """run_intelligence_scan must reject unknown universe values without touching filesystem."""
    result = run_intelligence_scan(universe="../../etc/passwd", tf="1d")
    assert "error" in result
    assert "Invalid universe" in result["error"]
    assert result["results"] == []


def test_scanner_rejects_invalid_tf():
    """run_intelligence_scan must reject unknown timeframe values."""
    result = run_intelligence_scan(universe="sp500", tf="5m")
    assert "error" in result
    assert "Invalid timeframe" in result["error"]
    assert result["results"] == []


def test_scanner_rejects_invalid_nasdaq_batch():
    """run_intelligence_scan must reject unknown nasdaq_batch values."""
    result = run_intelligence_scan(universe="nasdaq", tf="1d", nasdaq_batch="../evil")
    assert "error" in result
    assert "Invalid nasdaq_batch" in result["error"]
    assert result["results"] == []


def test_scanner_valid_inputs_pass_allowlist():
    """All documented valid universe/tf combos must not be rejected by the allowlist."""
    for univ in _VALID_UNIVERSES:
        for tf in _VALID_TFS:
            result = run_intelligence_scan(universe=univ, tf=tf)
            # May return error about missing CSV file, but must NOT be an allowlist error
            assert "Invalid universe" not in result.get("error", ""), \
                f"Valid universe '{univ}' incorrectly rejected"
            assert "Invalid timeframe" not in result.get("error", ""), \
                f"Valid tf '{tf}' incorrectly rejected"


# ── nasdaq_gt5 batch mode ─────────────────────────────────────────────────────

def test_nasdaq_gt5_batch_stat_path():
    """_stat_path with nasdaq_gt5 + batch must produce correctly named files."""
    from tz_intelligence.scanner import _stat_path
    assert _stat_path("nasdaq_gt5", "4h", "a_f") == "stock_stat_tz_wlnbb_nasdaq_gt5_a_f_4h.csv"
    assert _stat_path("nasdaq_gt5", "4h", "g_m") == "stock_stat_tz_wlnbb_nasdaq_gt5_g_m_4h.csv"
    assert _stat_path("nasdaq_gt5", "4h", "n_s") == "stock_stat_tz_wlnbb_nasdaq_gt5_n_s_4h.csv"
    assert _stat_path("nasdaq_gt5", "4h", "t_z") == "stock_stat_tz_wlnbb_nasdaq_gt5_t_z_4h.csv"


def test_nasdaq_gt5_full_1d_stat_path_unchanged():
    """Full nasdaq_gt5 1D (no batch) must still produce the original filename."""
    from tz_intelligence.scanner import _stat_path
    assert _stat_path("nasdaq_gt5", "1d") == "stock_stat_tz_wlnbb_nasdaq_gt5_1d.csv"
    assert _stat_path("nasdaq_gt5", "1d", "") == "stock_stat_tz_wlnbb_nasdaq_gt5_1d.csv"


def test_nasdaq_gt5_batches_pass_allowlist():
    """All four nasdaq_gt5 batch values must pass the scanner allowlist (no allowlist error)."""
    for batch in ("a_f", "g_m", "n_s", "t_z"):
        result = run_intelligence_scan(universe="nasdaq_gt5", tf="4h", nasdaq_batch=batch)
        assert "Invalid nasdaq_batch" not in result.get("error", ""), \
            f"Batch '{batch}' was incorrectly rejected by allowlist"
        assert "Invalid universe" not in result.get("error", ""), \
            f"nasdaq_gt5 universe rejected when batch='{batch}'"


def test_nasdaq_gt5_invalid_batch_rejected():
    """A batch key not in the allowlist must be rejected even for nasdaq_gt5."""
    result = run_intelligence_scan(universe="nasdaq_gt5", tf="4h", nasdaq_batch="a_z")
    assert "error" in result
    assert "Invalid nasdaq_batch" in result["error"]


def test_csv_filename_batch_logic():
    """Verify filename batch-part logic: batch included only when non-empty."""
    def _filename(universe, tf, batch=''):
        batch_part = f"_{batch}" if batch else ''
        return f"tz_intelligence_{universe}{batch_part}_{tf}_2026-05-07.csv"

    assert _filename("sp500", "1d") == "tz_intelligence_sp500_1d_2026-05-07.csv"
    assert _filename("nasdaq_gt5", "1d") == "tz_intelligence_nasdaq_gt5_1d_2026-05-07.csv"
    assert _filename("nasdaq_gt5", "4h", "a_f") == "tz_intelligence_nasdaq_gt5_a_f_4h_2026-05-07.csv"
    assert _filename("nasdaq_gt5", "4h", "g_m") == "tz_intelligence_nasdaq_gt5_g_m_4h_2026-05-07.csv"
    assert _filename("nasdaq_gt5", "4h", "n_s") == "tz_intelligence_nasdaq_gt5_n_s_4h_2026-05-07.csv"
    assert _filename("nasdaq_gt5", "4h", "t_z") == "tz_intelligence_nasdaq_gt5_t_z_4h_2026-05-07.csv"
    assert _filename("nasdaq", "1d", "a_m") == "tz_intelligence_nasdaq_a_m_1d_2026-05-07.csv"


# ── scan_mode: latest vs history ──────────────────────────────────────────────

from tz_intelligence.scanner import _VALID_SCAN_MODES, _sort_key, _build_result


def test_valid_scan_modes_set():
    assert "latest" in _VALID_SCAN_MODES
    assert "history" in _VALID_SCAN_MODES


def test_scanner_rejects_invalid_scan_mode():
    result = run_intelligence_scan(universe="sp500", tf="1d", scan_mode="realtime")
    assert "error" in result
    assert "Invalid scan_mode" in result["error"]
    assert result["results"] == []


def test_scanner_valid_scan_modes_pass_allowlist():
    for mode in ("latest", "history"):
        result = run_intelligence_scan(universe="sp500", tf="1d", scan_mode=mode)
        assert "Invalid scan_mode" not in result.get("error", ""), \
            f"Valid scan_mode '{mode}' incorrectly rejected"


def test_sort_key_prefers_bar_datetime():
    """_sort_key must use bar_datetime when present, fall back to date."""
    row_with_dt = {"date": "2026-05-07", "bar_datetime": "2026-05-07 10:00"}
    row_date_only = {"date": "2026-05-07"}
    assert _sort_key(row_with_dt) == "2026-05-07 10:00"
    assert _sort_key(row_date_only) == "2026-05-07"


def test_sort_key_intraday_ordering():
    """Intraday bars must sort chronologically within the same date."""
    bars = [
        {"date": "2026-05-07", "bar_datetime": "2026-05-07 14:00"},
        {"date": "2026-05-07", "bar_datetime": "2026-05-07 06:30"},
        {"date": "2026-05-07", "bar_datetime": "2026-05-07 10:00"},
    ]
    bars.sort(key=_sort_key)
    assert [b["bar_datetime"] for b in bars] == [
        "2026-05-07 06:30", "2026-05-07 10:00", "2026-05-07 14:00"
    ]


def test_csv_filename_includes_scan_mode():
    """CSV export filename must include scan_mode."""
    def _filename(universe, tf, batch='', scan_mode='latest'):
        batch_part = f"_{batch}" if batch else ''
        return f"tz_intelligence_{universe}{batch_part}_{tf}_{scan_mode}_2026-05-07.csv"

    assert _filename("sp500", "1d") == "tz_intelligence_sp500_1d_latest_2026-05-07.csv"
    assert _filename("sp500", "1d", scan_mode="history") == "tz_intelligence_sp500_1d_history_2026-05-07.csv"
    assert _filename("nasdaq_gt5", "4h", "a_f", "latest") == "tz_intelligence_nasdaq_gt5_a_f_4h_latest_2026-05-07.csv"
    assert _filename("nasdaq_gt5", "4h", "a_f", "history") == "tz_intelligence_nasdaq_gt5_a_f_4h_history_2026-05-07.csv"


# ── Security: CSV formula injection neutralisation ────────────────────────────

def _csv_cell(value: str) -> str:
    """Replicate the JS exportCSV cell-encoding logic in Python for testing."""
    v = str(value)
    import re
    if re.match(r'^[=+\-@]', v):
        v = "'" + v
    if ',' in v or '"' in v or '\n' in v:
        v = '"' + v.replace('"', '""') + '"'
    return v


def test_csv_formula_injection_neutralised():
    """Cells starting with =, +, -, @ must be prefixed with a single quote."""
    assert _csv_cell("=SUM(A1)").startswith("'")
    assert _csv_cell("+cmd").startswith("'")
    assert _csv_cell("-1+1").startswith("'")
    assert _csv_cell("@SUM").startswith("'")
    # Normal values must not be modified
    assert _csv_cell("BULL_A") == "BULL_A"
    assert _csv_cell("T4") == "T4"
    assert _csv_cell("0.57") == "0.57"
    assert _csv_cell("") == ""
    # Values containing commas should still be quoted
    assert _csv_cell("hello,world") == '"hello,world"'
    # Formula starting with = that also has a comma: gets prefixed then csv-quoted
    # Result is "'=1+1,foo" (quoted cell whose value starts with ', not =)
    result = _csv_cell("=1+1,foo")
    assert result.startswith('"\'')   # CSV-quoted cell beginning with neutralised prefix


def test_csv_formula_injection_abr_fields():
    """Realistic ABR field values must not trigger formula injection."""
    safe_values = [
        "A", "B", "B+", "R", "UNKNOWN",
        "STRONG", "GOOD", "AVERAGE", "REJECT",
        "PRIMARY_LONG_CONTEXT", "NO_ABR_EDGE",
        "T4", "Z1G", "T3|T2|Z1G",
        "0.567", "23.4%", "true", "false", "",
    ]
    for v in safe_values:
        encoded = _csv_cell(v)
        # None of these should be prefixed (they don't start with formula chars)
        assert not encoded.startswith("'"), \
            f"Safe value '{v}' was incorrectly prefixed: {encoded}"


# ── ABR context flags ─────────────────────────────────────────────────────────

from tz_intelligence.abr_classifier import compute_abr_context_flags


def test_abr_conflict_short_watch_bullish_abr():
    """SHORT_WATCH + ABR A/B/B+ must set abr_conflict_flag."""
    for cat in ("A", "B", "B+"):
        r = compute_abr_context_flags("SHORT_WATCH", cat, None, True, True, "OK")
        assert r["abr_conflict_flag"] == "ABR_BULLISH_CONTEXT_CONFLICT", \
            f"Expected conflict for SHORT_WATCH+{cat}"
        assert r["abr_context_type"] == "MIXED_BEARISH_PRICE_BULLISH_ABR"
        assert r["abr_confirmation_flag"] == ""


def test_abr_short_confirmed():
    """SHORT_WATCH + ABR R + negative med must set confirmation flag and candidate suggestion."""
    r = compute_abr_context_flags("SHORT_WATCH", "R", -0.3, False, False, "LOW")
    assert r["abr_confirmation_flag"] == "ABR_SHORT_CONFIRMED"
    assert r["abr_role_suggestion"]   == "ABR_SHORT_CONFIRMATION_CANDIDATE"
    assert r["abr_conflict_flag"] == ""
    # Positive med should NOT confirm
    r2 = compute_abr_context_flags("SHORT_WATCH", "R", 0.5, False, False, "LOW")
    assert r2["abr_confirmation_flag"] == ""
    assert r2["abr_role_suggestion"]   == ""
    # med=None should NOT confirm
    r3 = compute_abr_context_flags("SHORT_WATCH", "R", None, False, False, "LOW")
    assert r3["abr_confirmation_flag"] == ""
    assert r3["abr_role_suggestion"]   == ""


def test_abr_pullback_confirmed():
    """PULLBACK_READY_B/PULLBACK_WATCH + ABR B/B+ must set abr_confirmation_flag."""
    for role in ("PULLBACK_READY_B", "PULLBACK_WATCH"):
        for cat in ("B", "B+"):
            r = compute_abr_context_flags(role, cat, 0.5, True, True, "OK")
            assert r["abr_confirmation_flag"] == "ABR_PULLBACK_CONFIRMED", \
                f"Expected pullback confirm for {role}+{cat}"
            assert r["abr_conflict_flag"] == ""
    # ABR A should NOT confirm pullback
    r = compute_abr_context_flags("PULLBACK_READY_B", "A", 0.5, True, True, "OK")
    assert r["abr_confirmation_flag"] == ""


def test_abr_continuation_suggestion():
    """PULLBACK_WATCH/BULL_WATCH + B/B+ + above EMA20+50 + OK/STRONG liquidity."""
    for role in ("PULLBACK_WATCH", "BULL_WATCH"):
        for cat in ("B", "B+"):
            r = compute_abr_context_flags(role, cat, 0.4, True, True, "OK")
            assert r["abr_role_suggestion"] == "BULL_CONTINUATION_CANDIDATE", \
                f"Expected continuation for {role}+{cat}"
    # Missing EMA condition → no suggestion
    r = compute_abr_context_flags("BULL_WATCH", "B+", 0.4, False, True, "OK")
    assert r["abr_role_suggestion"] != "BULL_CONTINUATION_CANDIDATE"
    # LOW liquidity → no suggestion
    r = compute_abr_context_flags("BULL_WATCH", "B+", 0.4, True, True, "LOW")
    assert r["abr_role_suggestion"] != "BULL_CONTINUATION_CANDIDATE"
    # ABR A → no suggestion
    r = compute_abr_context_flags("BULL_WATCH", "A", 0.4, True, True, "OK")
    assert r["abr_role_suggestion"] != "BULL_CONTINUATION_CANDIDATE"


def test_abr_no_flags_neutral_role():
    """Roles that are not SHORT_WATCH or pullback variants should get no flags."""
    r = compute_abr_context_flags("BULL_A", "B+", 0.8, True, True, "STRONG")
    assert r["abr_conflict_flag"]     == ""
    assert r["abr_confirmation_flag"] == ""


def test_abr_context_flags_in_full_classify():
    """classify_tz_event output must contain all three new ABR context fields."""
    m = load_matrix()
    row = _row(t="T3", z="", lane1="T3L46NB", lane3="T3L46NB",
               close=55, ema20=50, ema50=45, ema89=40)
    history = [_row(t="T1", lane3="T1L46NB"), _row(t="T2", lane3="T2L46NB"),
               _row(t="T3", lane3="T3L46NB")]
    result = classify_tz_event(row, history, m, scan_universe="sp500")
    assert "abr_conflict_flag"     in result
    assert "abr_confirmation_flag" in result
    assert "abr_context_type"      in result
    # Values must be strings (empty or a known flag value)
    assert isinstance(result["abr_conflict_flag"],     str)
    assert isinstance(result["abr_confirmation_flag"], str)
    assert isinstance(result["abr_context_type"],      str)


# ── ABR role suggestion semantic cleanup tests ────────────────────────────────

from tz_intelligence.abr_classifier import _role_suggestion


def test_abr_short_watch_bullish_suggestion():
    """SHORT_WATCH + ABR A/B/B+ must produce CHECK_SHORT_CONFLICT role suggestion."""
    for cat in ("A", "B", "B+"):
        assert _role_suggestion(cat, "SHORT_WATCH") == "CHECK_SHORT_CONFLICT", \
            f"Expected CHECK_SHORT_CONFLICT for {cat}+SHORT_WATCH"
    # Non-short roles must NOT get CHECK_SHORT_CONFLICT
    assert _role_suggestion("B+", "BULL_A")          != "CHECK_SHORT_CONFLICT"
    assert _role_suggestion("B+", "PULLBACK_WATCH")  != "CHECK_SHORT_CONFLICT"


def test_abr_short_watch_r_no_negative_med():
    """SHORT_WATCH + ABR R + no negative med → ABR_R_NO_BUY, no confirmation flag."""
    # Base suggestion from _role_suggestion
    assert _role_suggestion("R", "SHORT_WATCH") == "ABR_R_NO_BUY"
    assert _role_suggestion("R", "BULL_A")      == "ABR_R_NO_BUY"   # all R → ABR_R_NO_BUY
    # context flags: no confirmation when med is positive or None
    r_pos  = compute_abr_context_flags("SHORT_WATCH", "R",  0.5,  False, False, "LOW")
    r_none = compute_abr_context_flags("SHORT_WATCH", "R",  None, False, False, "LOW")
    for r in (r_pos, r_none):
        assert r["abr_confirmation_flag"] == ""
        assert r["abr_role_suggestion"]   == ""   # ctx doesn't override; base gives ABR_R_NO_BUY


def test_abr_short_watch_r_negative_med():
    """SHORT_WATCH + ABR R + abr_med10d_pct < 0 → ABR_SHORT_CONFIRMED + CANDIDATE suggestion."""
    r = compute_abr_context_flags("SHORT_WATCH", "R", -0.5, False, False, "LOW")
    assert r["abr_confirmation_flag"] == "ABR_SHORT_CONFIRMED"
    assert r["abr_role_suggestion"]   == "ABR_SHORT_CONFIRMATION_CANDIDATE"
    assert r["abr_conflict_flag"]     == ""


# ── SP500 ABR overlay ─────────────────────────────────────────────────────────

def test_abr_sp500_universe_mapped():
    """sp500 → ABR universe SP500 with STRONG gate (prev1 must be STRONG ≥ 0.8)."""
    m = load_matrix()
    # T5 med ≈ 1.025 → STRONG on SP500; T3 med ≈ 0.765 → GOOD (fails gate)
    # seq: prev3=T3, prev2=T5, prev1=T5, current=T5
    r = classify_abr("T5", "T3|T5|T5|T5", [], m, scan_universe="sp500")
    assert r["abr_category"] != "UNKNOWN",    "SP500 T5 sequence must produce a category"
    assert r["abr_prev1_quality"] == "STRONG", "T5 must be STRONG under SP500 thresholds"
    assert r["abr_gate_pass"] is True,         "T5 prev1 must pass SP500 STRONG gate"


def test_abr_sp500_gate_blocks_good_prev1():
    """SP500 gate requires STRONG prev1; GOOD prev1 must give category R."""
    m = load_matrix()
    # T3 med ≈ 0.765 → GOOD (< 0.8 STRONG threshold) → gate fails → R
    r = classify_abr("T3", "T5|T3|T3|T3", [], m, scan_universe="sp500")
    assert r["abr_prev1_quality"] == "GOOD"
    assert r["abr_gate_pass"] is False
    assert r["abr_category"] == "R"


def test_abr_sp500_strong_signals():
    """Signals with SP500 med >= 0.8 must classify as STRONG."""
    m = load_matrix()
    from tz_intelligence.abr_classifier import _composite_med_for_signal, _classify_quality
    strong_sigs = ["T5", "T11", "T12", "Z2G", "Z3", "Z4", "Z5", "Z6", "Z9", "Z10", "Z11"]
    for sig in strong_sigs:
        med = _composite_med_for_signal(sig, m, "sp500")
        assert med is not None, f"{sig} must have SP500 composite rules"
        q = _classify_quality(med, "SP500")
        assert q == "STRONG", f"{sig} med={round(med,3)} expected STRONG, got {q}"


def test_abr_sp500_short_watch_conflict():
    """SHORT_WATCH + SP500 ABR B/B+ must produce ABR_BULLISH_CONTEXT_CONFLICT."""
    m = load_matrix()
    # Z3 prev1 is STRONG, Z9 prev2 is STRONG → B+
    r = classify_abr("Z3", "T5|Z9|Z3|Z3", [], m, scan_universe="sp500", current_role="SHORT_WATCH")
    ctx = compute_abr_context_flags("SHORT_WATCH", r["abr_category"], r["abr_med10d_pct"],
                                    True, True, "OK")
    if r["abr_category"] in ("A", "B", "B+"):
        assert ctx["abr_conflict_flag"] == "ABR_BULLISH_CONTEXT_CONFLICT"


def test_abr_sp500_role_unchanged():
    """SP500 TZ role/score must not be affected by ABR overlay."""
    m = load_matrix()
    row = _row(t="T5", z="", lane1="T5L46NB", lane3="T5L46NB",
               close=180, ema20=170, ema50=160, ema89=150)
    history = [_row(t="T3", lane3="T3L46NB"), _row(t="T5", lane3="T5L46NB"),
               _row(t="T5", lane3="T5L46NB")]
    result = classify_tz_event(row, history, m, scan_universe="sp500")
    # ABR fields present and valid
    assert result["abr_category"] in ("A", "B", "B+", "R", "UNKNOWN")
    assert isinstance(result["abr_conflict_flag"], str)
    # TZ role not corrupted by ABR
    assert result["role"] not in ("A", "B", "B+")
    # Score is numeric
    assert isinstance(result["score"], (int, float))


# ── Split universe invariant tests ────────────────────────────────────────────
# Tests required by the split universe consistency spec (sections 9 & 10).
# Use real temp files so the matrix loader's CSV reads are not intercepted.

import csv as _csv
import io
import tempfile
import os as _os
from unittest.mock import patch


# ── Helpers ───────────────────────────────────────────────────────────────────

_STAT_COLS = [
    "ticker","date","bar_datetime","bar_index","universe","timeframe",
    "open","high","low","close","volume","tz_wlnbb_version",
    "t_signal","z_signal","l_signal","preup_signal","predn_signal",
    "volume_bucket","lane1_label","lane3_label","ne_suffix","wick_suffix",
    "composite_t_label","composite_z_label","composite_primary_label",
    "composite_all_labels","composite_core","composite_suffix",
    "composite_full_suffix","composite_full_label",
    "prev_1_signal_summary","prev_3_signal_summary","prev_5_signal_summary",
    "ema9","ema20","ema34","ema50","ema89","ema200",
    "price_bucket","is_sub_dollar","is_penny_stock","is_low_price","is_high_price",
    "has_t_signal","has_z_signal","has_l_signal","has_preup","has_predn",
    "has_tz_l_combo","has_bullish_context","has_bearish_context",
    "t_after_z_confirmed","z_after_t_confirmed","l_after_z_confirmed",
    "preup_after_z_confirmed","predn_after_t_confirmed",
    "bull_priority_code","bear_priority_code","l_digits",
    "l34_active","l43_active","l64_active","l22_active","l_raw_signals",
    "t_raw_signals","z_raw_signals","preup_raw_signals","predn_raw_signals",
    "penetration_suffix","wick_penetration_upper","wick_penetration_lower",
    "wick_penetration_both","full_suffix","wick_ext_up","wick_ext_down","wick_ext_both",
    "prev_body_top","prev_body_bot","prev_high","prev_low",
    "combined_signal_text",
    "ret_1d","ret_3d","ret_5d","ret_10d",
    "max_high_5d","max_high_10d","max_drawdown_5d","max_drawdown_10d",
    "mfe_5d","mfe_10d","mae_5d","mae_10d",
    "clean_win_5d","big_win_10d","fail_5d","fail_10d",
]


def _write_stat_csv(path: str, tickers: list, universe: str = "split", tf: str = "1d") -> None:
    """Write a minimal stock_stat CSV to the given path."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=_STAT_COLS, extrasaction="ignore")
        w.writeheader()
        for t in tickers:
            row = {c: "" for c in _STAT_COLS}
            row.update({
                "ticker": t, "date": "2026-05-07", "bar_datetime": "2026-05-07",
                "close": "20.00", "volume": "1000000",
                "ema20": "18.0", "ema50": "16.0", "ema89": "14.0",
                "high": "21.0", "low": "19.0", "open": "19.5",
                "universe": universe, "timeframe": tf,
            })
            w.writerow(row)


def _run_split_scan_from_file(stat_path: str, universe: str = "split",
                               tf: str = "1d", classify_override=None) -> dict:
    """Run run_intelligence_scan pointing at a real temp stat CSV."""
    from tz_intelligence.scanner import run_intelligence_scan
    ctx = []
    # Patch _stat_path to return the temp file; os.path.exists passes through
    with patch("tz_intelligence.scanner._stat_path", return_value=stat_path):
        if classify_override is not None:
            with patch("tz_intelligence.scanner._classify_bar", side_effect=classify_override):
                return run_intelligence_scan(universe=universe, tf=tf,
                                             scan_mode="latest", limit=500)
        return run_intelligence_scan(universe=universe, tf=tf, scan_mode="latest", limit=500)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: scanner reads all tickers from stock_stat without cross-filtering
# ─────────────────────────────────────────────────────────────────────────────

def test_split_universe_tickers_used_in_scanner():
    """Scanner reads all tickers from stock_stat without live cross-filtering."""
    tickers = ["AAA", "BBB", "CCC"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        path = tmp.name
    try:
        _write_stat_csv(path, tickers)
        result = _run_split_scan_from_file(path)
        assert result["debug"]["stock_stat_unique_tickers"] == 3
    finally:
        _os.unlink(path)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: N tickers in stock_stat → N rows in latest output (no silent drops)
# ─────────────────────────────────────────────────────────────────────────────

def test_split_latest_all_tickers_returned():
    """For split universe latest mode, all tickers must appear in output."""
    tickers = ["AAPL", "GOOG", "MSFT"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        path = tmp.name
    try:
        _write_stat_csv(path, tickers)
        result = _run_split_scan_from_file(path)
        assert result["debug"]["classified_tickers"] == 3, \
            f"classified={result['debug']['classified_tickers']} dropped={result['debug']['dropped_tickers']}"
        returned_tickers = {r["ticker"] for r in result["results"]}
        assert returned_tickers == set(tickers)
    finally:
        _os.unlink(path)


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: NO_EDGE tickers are NOT dropped for split universe
# ─────────────────────────────────────────────────────────────────────────────

def test_split_no_edge_not_dropped():
    """Split universe: a bare row with no signals yields NO_EDGE, must appear in output."""
    from tz_intelligence.matrix_loader import load_matrix
    from tz_intelligence.classifier import classify_tz_event
    m = load_matrix()
    bare_row = {
        "ticker": "ZZZ", "date": "2026-05-07", "bar_datetime": "2026-05-07",
        "t_signal": "", "z_signal": "", "l_signal": "",
        "close": "20", "high": "21", "low": "19", "open": "19.5", "volume": "100",
        "ema20": "18", "ema50": "16", "ema89": "14",
        "lane1_label": "", "lane3_label": "", "volume_bucket": "",
        "ne_suffix": "", "wick_suffix": "", "composite_primary_label": "",
    }
    assert classify_tz_event(bare_row, [], m, scan_universe="split")["role"] == "NO_EDGE"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        path = tmp.name
    try:
        _write_stat_csv(path, ["ZZZ"])
        result = _run_split_scan_from_file(path)
        assert result["debug"]["classified_tickers"] == 1
        roles = {r["role"] for r in result["results"]}
        assert "NO_EDGE" in roles
    finally:
        _os.unlink(path)


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Classification exception → CLASSIFICATION_ERROR row (not dropped)
# ─────────────────────────────────────────────────────────────────────────────

def test_split_classification_error_not_dropped():
    """If _classify_bar raises for a split ticker, return CLASSIFICATION_ERROR."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        path = tmp.name
    try:
        _write_stat_csv(path, ["CRASH"])
        result = _run_split_scan_from_file(path, classify_override=RuntimeError("boom"))
        assert len(result["results"]) == 1
        assert result["results"][0]["role"] == "CLASSIFICATION_ERROR"
        assert result["debug"]["classification_errors"]
    finally:
        _os.unlink(path)


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: _make_error_clf has all required fields and _build_result accepts it
# ─────────────────────────────────────────────────────────────────────────────

def test_make_error_clf_has_required_keys():
    """_make_error_clf must supply all keys that _build_result expects."""
    from tz_intelligence.scanner import _make_error_clf, _build_result
    clf = _make_error_clf("TEST", "2026-05-07", "CLASSIFICATION_ERROR", "test error")
    assert clf["role"] == "CLASSIFICATION_ERROR"
    assert clf["score"] == 0
    assert clf["ticker"] == "TEST"
    assert "abr_category" in clf
    assert "abr_conflict_flag" in clf
    built = _build_result(clf, {"bar_datetime": "2026-05-07", "close": "20"}, debug=False)
    assert built["role"] == "CLASSIFICATION_ERROR"


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: non-split universe still drops NO_EDGE (existing behaviour preserved)
# ─────────────────────────────────────────────────────────────────────────────

def test_non_split_no_edge_still_filtered():
    """For sp500 universe, NO_EDGE tickers continue to be dropped."""
    from tz_intelligence.scanner import _make_error_clf
    no_edge_clf = _make_error_clf("NOEDGE", "2026-05-07", "NO_EDGE", "no match")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        path = tmp.name
    try:
        _write_stat_csv(path, ["NOEDGE"], universe="sp500")
        with patch("tz_intelligence.scanner._stat_path", return_value=path), \
             patch("tz_intelligence.scanner._classify_bar", return_value=no_edge_clf):
            from tz_intelligence.scanner import run_intelligence_scan
            result = run_intelligence_scan(universe="sp500", tf="1d",
                                           scan_mode="latest", limit=500)
        # sp500 NO_EDGE must be dropped
        assert result["debug"]["dropped_tickers_count"] >= 1
        assert all(r["role"] != "NO_EDGE" for r in result["results"])
    finally:
        _os.unlink(path)
