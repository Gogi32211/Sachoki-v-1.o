"""Tests for ultra_signal_parser + the parser-backed ULTRA Replay combos.

Covers:
  • parser robustness (empty / NaN / arrows / aliases / multi-column)
  • combo predicate logic incl. REVERSAL_GROWTH_A_NO_RS fallback
  • missing-dependency transparency for TRANSITION_A / PULLBACK_ENTRY_A
  • Stock Stat header / row writer shape (no live execution required)
"""
from __future__ import annotations

import math
import os
import sys

_backend = os.path.join(os.path.dirname(__file__), "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import ultra_signal_parser as usp
import replay_engine as re_eng


# ── Parser unit tests ────────────────────────────────────────────────────────

def test_parser_full_stock_stat_row():
    """Spec example row — every called-out flag must fire."""
    row = {
        "VABS":  "ABS CLB VBO↑ RS+",
        "Combo": "BUY SVS BB↑ HILO↑",
        "ULT":   "BO↑ BX↑ EB↑ 4BF 260308",
        "L":     "L34 FRI34 BL CCI",
        "T":     "T4",
        "Z":     "",
        "profile_category": "SWEET_SPOT",
    }
    p = usp.parse_stock_stat_signals(row)
    for f in ("abs_sig", "climb_sig", "vbo_up", "rs_strong",
              "buy_2809", "svs_2809", "bb_brk", "bo_up", "bx_up",
              "eb_bull", "l34", "fri34", "blue", "cci_ready",
              "four_bf", "sig_260308"):
        assert p[f] is True, f"expected {f}=True, got {p[f]}"
    assert p["t_signal"] == "T4"
    assert p["z_signal"] == ""


def test_parser_handles_empty_and_nan():
    nan = float("nan")
    row = {
        "VABS": "", "Combo": None, "ULT": nan,
        "L": "", "T": "", "Z": None,
    }
    p = usp.parse_stock_stat_signals(row)
    # No flag should fire on entirely-empty input
    for k in ("abs_sig", "buy_2809", "bb_brk", "bo_up", "rs_strong",
              "l34", "tz_bull_flip"):
        assert p[k] is False, f"{k} fired on empty row"
    # And the parser must not crash:
    assert p["t_signal"] == ""


def test_parser_arrow_tokens():
    """All arrow-bearing tokens (BO↑/BX↑/EB↑/BE↑/FBO↑/VBO↑) must parse from
    any of the columns they can appear in."""
    row = {"ULT": "BO↑ BX↑ EB↑ BE↑ FBO↑ 3↑", "VABS": "VBO↑"}
    p = usp.parse_stock_stat_signals(row)
    assert p["bo_up"] and p["bx_up"] and p["eb_bull"] and p["be_up"]
    assert p["fbo_bull"] and p["ultra_3up"] and p["vbo_up"]


def test_parser_separator_variants():
    """Spec-allowed separators all work: space, comma, pipe, semicolon."""
    row = {"VABS": "ABS,CLB|RS+;STR"}
    p = usp.parse_stock_stat_signals(row)
    assert p["abs_sig"] and p["climb_sig"] and p["rs_strong"] and p["strong_sig"]


def test_parser_alternate_aliases():
    """LOAD vs LD, STRONG vs STR, CLM vs CLB / CLIMB."""
    row = {"VABS": "LOAD STRONG CLIMB"}
    p = usp.parse_stock_stat_signals(row)
    assert p["load_sig"] and p["strong_sig"] and p["climb_sig"]


def test_parser_case_insensitive_columns():
    """Mixed-case column headers (Stock Stat) and lowercase (live) both work."""
    stock_stat_shape = {"Combo": "BUY", "VABS": "ABS"}
    live_shape       = {"combo": "BUY", "vabs": "ABS"}
    p1 = usp.parse_stock_stat_signals(stock_stat_shape)
    p2 = usp.parse_stock_stat_signals(live_shape)
    assert p1["buy_2809"] and p1["abs_sig"]
    assert p2["buy_2809"] and p2["abs_sig"]


def test_parser_live_flat_boolean_fallback():
    """Live ULTRA rows have flat booleans; parser still recognises them."""
    row = {"buy_2809": 1, "abs_sig": True, "rs_strong": 1, "tz_bull_flip": 1}
    p = usp.parse_stock_stat_signals(row)
    assert p["buy_2809"] and p["abs_sig"] and p["rs_strong"] and p["tz_bull_flip"]


def test_parser_tz_bull_flip_not_inferred_from_t_alone():
    """T4 / T6 / T1G alone must NOT be treated as a TZ flip."""
    row = {"T": "T4", "Combo": "", "ULT": ""}
    p = usp.parse_stock_stat_signals(row)
    assert p["tz_bull_flip"] is False
    assert p["tz_transition_present"] is False


def test_parser_tz_bull_flip_from_transition_token():
    """TZ→3 / TZ→2 inside Combo or ULT does count as a flip."""
    row = {"Combo": "BB↑ TZ→3"}
    p = usp.parse_stock_stat_signals(row)
    assert p["tz_bull_flip"] is True
    assert p["tz_transition_present"] is True


def test_has_token_and_has_any_token():
    row = {"Combo": "BUY ROCKET BB↑", "VABS": "ABS RS+"}
    assert usp.has_token(row, "Combo", "ROCKET")
    assert not usp.has_token(row, "Combo", "missing")
    assert usp.has_any_token(row, ["Combo", "VABS"], ["ABS"])
    assert not usp.has_any_token(row, ["Combo", "VABS"], ["foo"])


# ── Replay combo predicate / aggregation tests ───────────────────────────────

def _bar(score=70, band="B", **extra):
    base = {
        "ticker":           "AAPL",
        "date":             "2026-05-01",
        "close":             100.0,
        "ultra_score":       score,
        "ultra_score_band":  band,
        "_ret5":  3.0, "_ret10": 5.0, "_max5": 4.0, "_max10": 6.0,
    }
    base.update(extra)
    return base


def test_reversal_growth_a_fires_from_compact_columns():
    """Regression for the bug: Stock Stat row with ABS + BO↑ + RS+ should
    fire REVERSAL_GROWTH_A. Previous lowercase-column lookup missed all
    of these and the combo always counted 0."""
    row = _bar(VABS="ABS RS+", ULT="BO↑")
    preds = re_eng._ultra_combo_predicates(row)
    matches, missing, _zr = preds["REVERSAL_GROWTH_A"]
    assert matches is True
    assert missing == []


def test_reversal_growth_a_no_rs_fallback_when_rs_absent():
    """When Stock Stat has no RS+ but PF category is SWEET_SPOT/BUILDING,
    the fallback combo fires."""
    row = _bar(VABS="ABS", ULT="BO↑", profile_category="SWEET_SPOT")
    preds = re_eng._ultra_combo_predicates(row)
    matches, missing, _zr = preds["REVERSAL_GROWTH_A_NO_RS"]
    assert matches is True
    assert missing == []
    # Strict version stays False because RS+ is absent
    assert preds["REVERSAL_GROWTH_A"][0] is False


def test_transition_a_unavailable_when_tz_field_missing():
    """No tz_bull_flip column AND no TZ→ token — predicate must return None
    (= cannot evaluate), not False, so the aggregator distinguishes
    'missing required field' from a 'true zero'."""
    row = _bar(ULT="BO↑", profile_category="SWEET_SPOT")
    # No 'tz_bull_flip' key in dict, no TZ→ token
    preds = re_eng._ultra_combo_predicates(row)
    matches, missing, zero_reason = preds["TRANSITION_A"]
    assert matches is None
    assert missing == ["tz_bull_flip"]
    assert zero_reason == "missing_required_field"


def test_transition_a_fires_from_tz_arrow_token():
    """Combo token TZ→3 makes TRANSITION_A computable."""
    row = _bar(Combo="BB↑ TZ→3", VABS="RS+", profile_category="SWEET_SPOT")
    preds = re_eng._ultra_combo_predicates(row)
    matches, missing, _zr = preds["TRANSITION_A"]
    assert matches is True
    assert missing == []


def test_pullback_entry_a_marks_missing_dependency():
    """Stock Stat without pullback_evidence_tier — combo cannot be computed."""
    row = _bar(VABS="RS+", ULT="BO↑", profile_category="SWEET_SPOT")
    preds = re_eng._ultra_combo_predicates(row)
    matches, missing, zero_reason = preds["PULLBACK_ENTRY_A"]
    assert matches is None
    assert missing == ["pullback_evidence_tier"]
    assert zero_reason == "missing_required_field"


def test_pullback_entry_a_fires_when_field_present():
    row = _bar(
        VABS="RS+", ULT="BO↑", profile_category="SWEET_SPOT",
        pullback_evidence_tier="CONFIRMED_PULLBACK",
    )
    preds = re_eng._ultra_combo_predicates(row)
    assert preds["PULLBACK_ENTRY_A"][0] is True


def test_setup_only_and_breakout_only_mutually_exclusive():
    setup_only_row    = _bar(VABS="ABS")
    breakout_only_row = _bar(ULT="BO↑")
    p1 = re_eng._ultra_combo_predicates(setup_only_row)
    p2 = re_eng._ultra_combo_predicates(breakout_only_row)
    assert p1["SETUP_ONLY"][0]    is True
    assert p1["BREAKOUT_ONLY"][0] is False
    assert p2["SETUP_ONLY"][0]    is False
    assert p2["BREAKOUT_ONLY"][0] is True


def test_combo_perf_marks_unavailable_correctly():
    """When ALL rows are missing a required dependency, the combo row in
    ultra_combo_perf must say computed=false / missing_dependencies=...
    rather than count=0 silently."""
    rows = [_bar(VABS="ABS RS+", ULT="BO↑", profile_category="SWEET_SPOT")
            for _ in range(5)]
    perf = re_eng.ultra_combo_perf(rows)
    pe_a = next(r for r in perf if r["combo"] == "PULLBACK_ENTRY_A")
    assert pe_a["computed"]              is False
    assert pe_a["missing_dependencies"]  == "pullback_evidence_tier"
    assert "PULLBACK_ENTRY_A not computed" in pe_a["dependency_warning"]
    assert pe_a["zero_reason"] == "missing_required_field"
    # REVERSAL_GROWTH_A_NO_RS fallback is computable from PF=SWEET_SPOT +
    # setup (ABS) + breakout (BO↑).
    rg_no_rs = next(r for r in perf if r["combo"] == "REVERSAL_GROWTH_A_NO_RS")
    assert rg_no_rs["computed"]        is True
    assert rg_no_rs["count"]           == 5
    assert rg_no_rs["missing_dependencies"] == ""


def test_combo_perf_true_zero_distinct_from_missing():
    """A combo with all dependencies present but no matches → zero_reason
    'true_zero', not 'missing_required_field'."""
    rows = [_bar(VABS="ABS", profile_category="WATCH")]  # no breakout, no PF ok
    perf = re_eng.ultra_combo_perf(rows)
    rg_a = next(r for r in perf if r["combo"] == "REVERSAL_GROWTH_A")
    assert rg_a["count"] == 0
    assert rg_a["computed"] is True
    assert rg_a["zero_reason"] == "true_zero"


def test_signal_parser_audit_contains_required_flags():
    rows = [_bar(VABS="ABS RS+", Combo="BUY", ULT="BO↑", L="L34 FRI34")]
    audit = re_eng.ultra_signal_parser_audit(rows)
    by_flag = {r["flag_name"]: r for r in audit}
    for f in ("abs_sig", "rs_strong", "buy_2809", "bo_up", "l34",
              "fri34", "tz_bull_flip",
              "pullback_evidence_tier", "abr_category"):
        assert f in by_flag, f"audit missing {f}"
    # Numerics counted correctly
    assert by_flag["abs_sig"]["true_count"]  == 1
    assert by_flag["rs_strong"]["true_count"] == 1
    assert by_flag["bo_up"]["true_count"]    == 1
    assert by_flag["l34"]["true_count"]      == 1
    # Missing dependency counted on the right flag
    assert by_flag["pullback_evidence_tier"]["true_count"] == 0


# ── Stock Stat header check ──────────────────────────────────────────────────

def test_stock_stat_headers_include_new_replay_columns():
    """Stock Stat / Bulk Signal CSV header writer must list the new fields
    so Replay Analytics can read them in future runs."""
    main_py = os.path.join(_backend, "main.py")
    with open(main_py, encoding="utf-8") as f:
        src = f.read()
    for col in (
        "rs_strong", "rs", "tz_bull_flip", "tz_transition_present",
        "pullback_evidence_tier", "rare_evidence_tier",
        "tz_intel_role", "abr_category",
    ):
        assert f'"{col}"' in src, f"Stock Stat header is missing {col}"


def test_existing_stock_stat_endpoints_still_callable():
    from main import (
        api_stock_stat_trigger, api_stock_stat_status, api_stock_stat_download,
    )
    assert callable(api_stock_stat_trigger)
    assert callable(api_stock_stat_status)
    assert callable(api_stock_stat_download)


def test_existing_replay_endpoints_still_callable():
    from main import (
        api_replay_run, api_replay_status, api_replay_reports,
        api_replay_report, api_replay_export, api_replay_export_all,
    )
    for fn in (api_replay_run, api_replay_status, api_replay_reports,
               api_replay_report, api_replay_export, api_replay_export_all):
        assert callable(fn)
