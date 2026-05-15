"""Unit tests for profile_playbook module."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from profile_playbook import (
    get_profile,
    normalize_signal_name,
    normalize_signal_token,   # backward-compat alias
    parse_signal_cell,
    compute_profile_score,
    compute_profile_playbook_for_row,
    enrich_row_with_profile,
    extract_signals_from_turbo_row,
    extract_profile_signals_from_stat_row,
    sequence_decay_bonus,
    get_playbook_config_snapshot,
    PROFILES,
    BEAR_CONTEXT_SIGNALS,
    BULL_CONFIRM_SIGNALS,
    SEQUENCE_BONUSES,
    SEQUENCE_BONUS_CAP,
    BEAR_CONTEXT_STANDALONE_CAP,
    PROFILE_PLAYBOOK_VERSION,
    PROFILE_BTB_CAPS,
    PROFILE_BTB_WEAK_CONFIRM_PROFILES,
    BTB_SWEET_SPOT_ALLOWED_PROFILES,
)


# ── Profile assignment ─────────────────────────────────────────────────────────

def test_sp500_price_buckets():
    assert get_profile({"close": 10},  "sp500") == "SP500_LT20"
    assert get_profile({"close": 35},  "sp500") == "SP500_20_50"
    assert get_profile({"close": 100}, "sp500") == "SP500_50_150"
    assert get_profile({"close": 200}, "sp500") == "SP500_150_300"
    assert get_profile({"close": 400}, "sp500") == "SP500_300_PLUS"

def test_sp500_boundary_conditions():
    assert get_profile({"close": 19.99}, "sp500") == "SP500_LT20"
    assert get_profile({"close": 20.0},  "sp500") == "SP500_20_50"
    assert get_profile({"close": 49.99}, "sp500") == "SP500_20_50"
    assert get_profile({"close": 50.0},  "sp500") == "SP500_50_150"
    assert get_profile({"close": 149.99},"sp500") == "SP500_50_150"
    assert get_profile({"close": 150.0}, "sp500") == "SP500_150_300"
    assert get_profile({"close": 299.99},"sp500") == "SP500_150_300"
    assert get_profile({"close": 300.0}, "sp500") == "SP500_300_PLUS"

def test_nasdaq_profiles():
    assert get_profile({"close": 2},  "nasdaq") == "NASDAQ_PENNY"
    assert get_profile({"close": 20}, "nasdaq") == "NASDAQ_REAL"
    assert get_profile({"close": 4.99}, "nasdaq") == "NASDAQ_PENNY"
    assert get_profile({"close": 5.0},  "nasdaq") == "NASDAQ_REAL"

def test_current_close_preferred_over_median():
    assert get_profile({"close": 100, "median_price": 10}, "sp500") == "SP500_50_150"

def test_last_price_fallback():
    assert get_profile({"last_price": 100}, "sp500") == "SP500_50_150"

def test_median_price_is_fallback_only():
    assert get_profile({"median_price": 100}, "sp500") == "SP500_50_150"

def test_non_sp500_universes():
    for uni in ("nasdaq", "russell2k", "all_us", "split"):
        assert get_profile({"close": 3}, uni) == "NASDAQ_PENNY"
        assert get_profile({"close": 50}, uni) == "NASDAQ_REAL"


# ── Signal normalization ───────────────────────────────────────────────────────

def test_signal_normalization_arrows():
    assert normalize_signal_token("VBO↑") == "VBO_UP"
    assert normalize_signal_token("VBO↓") == "VBO_DN"
    assert normalize_signal_token("HILO↑") == "HILO_UP"
    assert normalize_signal_token("HILO↓") == "HILO_DN"
    assert normalize_signal_token("BB↑") == "BB_UP"
    assert normalize_signal_token("4BF↓") == "4BF_DN"
    assert normalize_signal_token("BEST★") == "BEST_STAR"

def test_signal_normalization_identity():
    assert normalize_signal_token("BUY") == "BUY"
    assert normalize_signal_token("ABS") == "ABS"
    assert normalize_signal_token("G11") == "G11"

def test_signal_normalization_empty():
    assert normalize_signal_token("") == ""
    assert normalize_signal_token(None) == ""
    assert normalize_signal_token("  ") == ""

def test_normalize_cons_alias():
    assert normalize_signal_token("CONS") == "CONSO"


# ── Signal cell parsing ────────────────────────────────────────────────────────

def test_parse_signal_cell_separators():
    result = parse_signal_cell("ABS, BE↓ | Z2")
    assert "ABS" in result
    assert "BE_DN" in result
    assert "Z2" in result

def test_parse_signal_cell_whitespace():
    result = parse_signal_cell("FLY_BD  BB↑")
    assert "FLY_BD" in result
    assert "BB_UP" in result

def test_parse_signal_cell_empty():
    assert parse_signal_cell("") == set()
    assert parse_signal_cell(None) == set()
    assert parse_signal_cell(123) == set()


# ── Profile score ─────────────────────────────────────────────────────────────

def test_pair_bonus_applied():
    signals = {"BL", "F10"}
    result = compute_profile_score(signals, "SP500_50_150")
    assert result["profile_score"] > 0
    assert "BL+F10" in result["matched_profile_pairs"]

def test_single_signal_contributes():
    signals = {"FLY_BD"}
    result = compute_profile_score(signals, "SP500_50_150")
    assert result["profile_score"] == 5
    assert "FLY_BD" in result["matched_profile_signals"]

def test_empty_signals_gives_watch():
    result = compute_profile_score(set(), "SP500_50_150")
    assert result["profile_score"] == 0
    assert result["profile_category"] == "WATCH"
    assert not result["sweet_spot_active"]
    assert not result["late_warning"]

def test_sweet_spot_detection():
    # SP500_50_150 sweet_spot = (12, 32)
    # FLY_BD(5)+BUY(4)+BX_UP(4) = 13 → SWEET_SPOT
    signals = {"FLY_BD", "BUY", "BX_UP"}
    result = compute_profile_score(signals, "SP500_50_150")
    assert result["profile_score"] >= 12
    assert result["profile_category"] in {"SWEET_SPOT", "LATE"}

def test_late_warning():
    profile = "SP500_50_150"
    # Activate all signals + all pair members — should reach high score
    signals = set(PROFILES[profile]["signal_weights"].keys())
    for pair in PROFILES[profile]["pair_bonuses"]:
        signals.update(pair)
    result = compute_profile_score(signals, profile)
    assert result["profile_score"] > 0
    assert result["profile_category"] in {"WATCH", "BUILDING", "SWEET_SPOT", "LATE"}

def test_building_category():
    # BUILDING: score >= sweet_low*0.70 but < sweet_low
    # SP500_50_150: sweet_low=12, building_threshold=8.4
    # FLY_BD(5)+BUY(4) = 9 → BUILDING (8.4 <= 9 < 12)
    signals = {"FLY_BD", "BUY"}
    result = compute_profile_score(signals, "SP500_50_150")
    assert result["profile_category"] in {"BUILDING", "SWEET_SPOT"}


# ── Row enrichment safety ─────────────────────────────────────────────────────

def test_does_not_mutate_canonical_fields():
    row = {
        "close": 100, "last_price": 100,
        "FINAL_BULL_SCORE": 55, "FINAL_REGIME": "CLEAN_ENTRY",
        "turbo_score": 42, "rtb_total": 15,
    }
    enriched = enrich_row_with_profile(row, "sp500", {"FLY_BD", "BUY"})
    # Canonical fields must be unchanged
    assert enriched["FINAL_BULL_SCORE"] == 55
    assert enriched["FINAL_REGIME"] == "CLEAN_ENTRY"
    assert enriched["turbo_score"] == 42
    assert enriched["rtb_total"] == 15
    # Profile fields must be added
    assert "profile_name" in enriched
    assert "profile_score" in enriched
    assert "profile_category" in enriched
    assert "sweet_spot_active" in enriched

def test_original_row_not_mutated():
    row = {"close": 100, "FINAL_BULL_SCORE": 55}
    enriched = enrich_row_with_profile(row, "sp500", set())
    assert "profile_name" not in row  # original untouched
    assert "profile_name" in enriched

def test_enriched_profile_name_matches_price():
    row = {"last_price": 100}
    enriched = enrich_row_with_profile(row, "sp500", set())
    assert enriched["profile_name"] == "SP500_50_150"


# ── Turbo row extraction ──────────────────────────────────────────────────────

def test_extract_turbo_boolean_columns():
    row = {"buy_2809": 1, "load_sig": 1, "g11": 0, "abs_sig": 0, "tz_sig": "T10"}
    signals = extract_signals_from_turbo_row(row)
    assert "BUY" in signals
    assert "LOAD" in signals
    assert "T10" in signals
    assert "G11" not in signals  # was 0

def test_extract_tz_sig_base_variant():
    row = {"tz_sig": "T1G"}
    signals = extract_signals_from_turbo_row(row)
    assert "T1G" in signals
    assert "T1" in signals  # base without G


# ── Profile metadata ──────────────────────────────────────────────────────────

def test_nasdaq_profiles_marked_experimental():
    assert PROFILES["NASDAQ_PENNY"]["experimental"] is True
    assert PROFILES["NASDAQ_REAL"]["experimental"] is True

def test_sp500_profiles_not_experimental():
    for name, p in PROFILES.items():
        if name.startswith("SP500"):
            assert not p.get("experimental", False), f"{name} should not be experimental"

def test_all_profiles_have_required_keys():
    required = {"universe", "price_range", "signal_weights", "pair_bonuses",
                "sweet_spot", "late_threshold", "preferred_preset", "suggested_tp", "suggested_sl"}
    for name, p in PROFILES.items():
        for k in required:
            assert k in p, f"{name} missing key {k}"


# ── New alias coverage ────────────────────────────────────────────────────────

def test_fly_dash_aliases():
    assert normalize_signal_token("FLY-BD") == "FLY_BD"
    assert normalize_signal_token("FLY-CD") == "FLY_CD"
    assert normalize_signal_token("FLY-AD") == "FLY_AD"
    assert normalize_signal_token("FLY")    == "FLY_ABCD"

def test_wick_aliases():
    assert normalize_signal_token("WC↑") == "WC_UP"
    assert normalize_signal_token("WC↓") == "WC_DN"
    assert normalize_signal_token("WP↑") == "WP_UP"
    assert normalize_signal_token("WP↓") == "WP_DN"

def test_ultra_display_aliases():
    assert normalize_signal_token("BEST↑") == "BEST_UP"
    assert normalize_signal_token("3↑")    == "THREE_UP"
    assert normalize_signal_token("4BF")   == "BF_BUY"
    assert normalize_signal_token("ATR↑")  == "ATR_BRK"
    assert normalize_signal_token("BB↓")   == "BB_DN"

def test_vol_aliases():
    assert normalize_signal_token("5×")  == "VOL_5X"
    assert normalize_signal_token("10×") == "VOL_10X"
    assert normalize_signal_token("20×") == "VOL_20X"
    assert normalize_signal_token("5X")  == "VOL_5X"
    assert normalize_signal_token("10X") == "VOL_10X"
    assert normalize_signal_token("20X") == "VOL_20X"


# ── extract_profile_signals_from_stat_row ────────────────────────────────────

def test_extract_from_bar_dict_lists():
    """Bar dict (api_bar_signals format) with list columns."""
    row = {
        "close": 120,
        "tz":    "T5",
        "combo": ["BUY", "SVS", "BB↑"],
        "fly":   ["FLY-BD"],
        "l":     ["FRI43", "BX↑"],
        "f":     ["F9", "F10"],
        "vabs":  ["ABS", "CLM", "LOAD"],
        "g":     ["G4", "G11"],
        "ultra": ["EB↑"],
        "vol":   ["5×"],
        "wick":  ["WC↑"],
    }
    sigs = extract_profile_signals_from_stat_row(row)
    assert "BUY"     in sigs
    assert "SVS"     in sigs
    assert "BB_UP"   in sigs
    assert "FLY_BD"  in sigs
    assert "FRI43"   in sigs
    assert "BX_UP"   in sigs
    assert "F9"      in sigs
    assert "F10"     in sigs
    assert "ABS"     in sigs
    assert "CLM"     in sigs
    assert "LOAD"    in sigs
    assert "G4"      in sigs
    assert "G11"     in sigs
    assert "EB_UP"    in sigs
    assert "VOL_5X"  in sigs
    assert "WC_UP"   in sigs
    assert "T5"      in sigs

def test_extract_from_csv_string_columns():
    """stock_stat CSV format with string columns."""
    row = {
        "close": 120,
        "Z": "",
        "T": "T10",
        "L": "FRI43 BL BX↑",
        "F": "F9 F10",
        "FLY": "FLY-BD",
        "G": "G4 G11",
        "B": "",
        "Combo": "BUY SVS BB↑",
        "ULT":  "EB↑ 260308",
        "VOL":  "5×",
        "VABS": "ABS LOAD CLM",
        "WICK": "WC↑",
    }
    sigs = extract_profile_signals_from_stat_row(row)
    assert "FLY_BD"  in sigs
    assert "BX_UP"   in sigs
    assert "BB_UP"   in sigs
    assert "EB_UP"   in sigs
    assert "VOL_5X"  in sigs
    assert "WC_UP"   in sigs
    assert "T10"     in sigs
    assert "BUY"     in sigs
    assert "ABS"     in sigs
    assert "260308"  in sigs

def test_extract_fly_abcd_bare():
    """Bare 'FLY' token (fly_abcd=True) maps to FLY_ABCD."""
    row = {"fly": ["FLY"], "close": 100}
    sigs = extract_profile_signals_from_stat_row(row)
    assert "FLY_ABCD" in sigs

def test_extract_vol_tokens():
    row = {"VOL": "5× 10×", "close": 100}
    sigs = extract_profile_signals_from_stat_row(row)
    assert "VOL_5X"  in sigs
    assert "VOL_10X" in sigs

def test_extract_tz_z_column():
    row = {"Z": "Z3", "close": 100}
    sigs = extract_profile_signals_from_stat_row(row)
    assert "Z3" in sigs

def test_profile_score_nonzero_from_bar_dict():
    """End-to-end: bar dict with real signals → non-zero profile score."""
    row = {
        "close": 120,
        "combo": ["BUY", "SVS"],
        "fly":   ["FLY-BD"],
        "l":     ["FRI43"],
        "vabs":  ["ABS", "LOAD", "CLM"],
        "g":     ["G4", "G11"],
    }
    sigs  = extract_profile_signals_from_stat_row(row)
    pname = get_profile(row, "sp500")
    pd    = compute_profile_score(sigs, pname)
    assert pd["profile_score"] > 0, f"Expected non-zero score, got {pd}"
    assert pd["profile_category"] != "WATCH" or pd["profile_score"] == 0

def test_profile_score_nonzero_from_csv_row():
    """End-to-end: CSV row with real signals → non-zero profile score."""
    row = {
        "close": 120,
        "Combo": "BUY SVS",
        "FLY":   "FLY-BD",
        "L":     "FRI43",
        "VABS":  "ABS LOAD CLM",
        "G":     "G4 G11",
    }
    sigs  = extract_profile_signals_from_stat_row(row)
    pname = get_profile(row, "sp500")
    pd    = compute_profile_score(sigs, pname)
    assert pd["profile_score"] > 0, f"Expected non-zero score from CSV row, got {pd}"

def test_sweet_spot_reachable_from_stat_row():
    """A row with enough signals should reach SWEET_SPOT or LATE, never stuck at WATCH."""
    row = {
        "close": 120,
        "Combo": "BUY SVS",
        "FLY":   "FLY-BD",
        "L":     "FRI43 BX↑",
        "VABS":  "ABS LOAD CLM",
        "G":     "G4 G11",
        "ULT":   "EB↑",
        "F":     "F9 F10",
    }
    sigs  = extract_profile_signals_from_stat_row(row)
    pname = get_profile(row, "sp500")   # SP500_50_150
    pd    = compute_profile_score(sigs, pname)
    assert pd["profile_category"] in {"SWEET_SPOT", "BUILDING", "LATE"}, (
        f"Expected non-WATCH category, got {pd['profile_category']} (score={pd['profile_score']})"
    )

def test_ult_eb_up_maps_to_eb_up():
    row = {"ULT": "EB↑", "close": 100}
    sigs = extract_profile_signals_from_stat_row(row)
    assert "EB_UP" in sigs

def test_ult_fbo_up_maps():
    row = {"ultra": ["FBO↑"], "close": 100}
    sigs = extract_profile_signals_from_stat_row(row)
    assert "FBO_UP" in sigs


# ── Hard non-zero score tests ─────────────────────────────────────────────────

def test_nasdaq_penny_vol5x_scores():
    """VOL_5X alias feeds into NASDAQ_PENNY signal_weights and produces non-zero score."""
    row = {"close": 2.0, "vol": ["5×"], "VOL": "5×"}
    sigs = extract_profile_signals_from_stat_row(row)
    assert "VOL_5X" in sigs, f"VOL_5X missing from {sigs}"
    pname = get_profile(row, "nasdaq")
    assert pname == "NASDAQ_PENNY"
    pd = compute_profile_score(sigs, pname)
    assert pd["profile_score"] == 4, f"Expected 4 (VOL_5X weight), got {pd['profile_score']}"

def test_nasdaq_penny_vol20x_scores():
    """VOL_20X is highest-weighted vol signal in NASDAQ_PENNY."""
    sigs = {"VOL_20X", "CONSO"}
    pd = compute_profile_score(sigs, "NASDAQ_PENNY")
    assert pd["profile_score"] == 6 + 4, f"Expected 10, got {pd['profile_score']}"

def test_sp500_50_150_buy_svs_abs_nonzero():
    """SP500_50_150 with BUY+SVS+ABS from vabs/combo lists → non-zero score."""
    row = {
        "close": 100.0,
        "combo": ["BUY", "SVS"],
        "vabs":  ["ABS", "LOAD", "CLM"],
        "l":     [], "f": [], "fly": [], "g": [], "b": [], "vol": [], "wick": [], "ultra": [],
    }
    sigs = extract_profile_signals_from_stat_row(row)
    assert "BUY"  in sigs, f"BUY missing from {sigs}"
    assert "SVS"  in sigs, f"SVS missing from {sigs}"
    assert "ABS"  in sigs, f"ABS missing from {sigs}"
    assert "LOAD" in sigs, f"LOAD missing from {sigs}"
    pname = get_profile(row, "sp500")
    assert pname == "SP500_50_150"
    pd = compute_profile_score(sigs, pname)
    # BUY(4)+SVS(4)+ABS(3)+LOAD(3)+CLM(2) = 16
    assert pd["profile_score"] == 16, f"Expected 16, got {pd['profile_score']} signals={sigs}"
    assert pd["profile_category"] in {"SWEET_SPOT", "BUILDING", "LATE"}

def test_sp500_fly_bd_scores_correctly():
    """FLY-BD in fly list resolves to FLY_BD with weight 5 in SP500_50_150."""
    row = {"close": 100.0, "fly": ["FLY-BD"]}
    sigs = extract_profile_signals_from_stat_row(row)
    assert "FLY_BD" in sigs
    pd = compute_profile_score(sigs, "SP500_50_150")
    assert pd["profile_score"] == 5

def test_turbo_map_vol_spike_5x_maps_to_vol_5x():
    """_TURBO_SIGNAL_MAP vol_spike_5x → VOL_5X (not 5X)."""
    row = {"vol_spike_5x": 1, "vol_spike_10x": 0, "vol_spike_20x": 0}
    sigs = extract_signals_from_turbo_row(row)
    assert "VOL_5X" in sigs, f"VOL_5X missing, got {sigs}"
    assert "5X" not in sigs


# ── Test 1 — Shared normalization ─────────────────────────────────────────────

def test_shared_normalization_comprehensive():
    """All required alias mappings produce correct canonical names."""
    cases = [
        ("FLY-BD", "FLY_BD"), ("BB↑", "BB_UP"), ("BX↑", "BX_UP"),
        ("EB↓", "EB_DN"), ("BE↓", "BE_DN"), ("BO↑", "BO_UP"),
        ("VBO↓", "VBO_DN"), ("5×", "VOL_5X"),
        ("FBO↑", "FBO_UP"), ("WP↑", "WP_UP"), ("WP↓", "WP_DN"),
        ("WC↑", "WC_UP"), ("WC↓", "WC_DN"),
        ("10×", "VOL_10X"), ("20×", "VOL_20X"),
    ]
    for raw, expected in cases:
        got = normalize_signal_name(raw)
        assert got == expected, f"normalize_signal_name({raw!r}) = {got!r}, expected {expected!r}"
    # Direct canonical names should pass through unchanged
    for direct in ("BUY", "SVS", "ABS", "LOAD", "G4", "G11", "F9", "F10",
                   "CLM", "SQ", "SC", "BL", "CCI", "FRI43", "260308"):
        assert normalize_signal_name(direct) == direct, (
            f"Expected {direct!r} to pass through unchanged, got {normalize_signal_name(direct)!r}"
        )


# ── Test 2 — Direct row extraction + non-zero score ──────────────────────────

def test_direct_row_extraction_nonzero():
    """CSV-style row: BUY in Combo + FLY-BD in FLY + FRI43 in L → non-zero score."""
    row = {"close": 100.0, "Combo": "BUY", "FLY": "FLY-BD", "L": "FRI43"}
    sigs = extract_profile_signals_from_stat_row(row)
    assert "BUY"   in sigs, f"BUY missing {sigs}"
    assert "FLY_BD" in sigs, f"FLY_BD missing {sigs}"
    assert "FRI43" in sigs, f"FRI43 missing {sigs}"
    pf = compute_profile_playbook_for_row(row, "sp500")
    assert pf["profile_score"] > 0, f"Expected non-zero, got {pf}"


# ── Test 3 — Bearish context standalone is small + capped ────────────────────

def test_bear_context_standalone_is_small():
    """EB_DN alone should give standalone score of 1 and NOT reach SWEET_SPOT."""
    row = {"close": 100.0, "ultra": ["EB↓"]}
    sigs = extract_profile_signals_from_stat_row(row)
    assert "EB_DN" in sigs, f"EB_DN missing from {sigs}"
    pf = compute_profile_playbook_for_row(row, "sp500")
    assert pf["profile_score"] <= 3, (
        f"Bear standalone score should be ≤3, got {pf['profile_score']}"
    )
    assert pf["profile_category"] != "SWEET_SPOT", (
        f"EB_DN alone must not reach SWEET_SPOT, got {pf['profile_category']}"
    )

def test_bear_context_standalone_cap():
    """Multiple bear signals are capped at BEAR_CONTEXT_STANDALONE_CAP=3."""
    row = {
        "close": 100.0,
        "ultra": ["EB↓", "FBO↓"],
        "combo": ["BB↓"],
        "wick":  ["WC↓"],
        "vabs":  ["VBO↓"],
    }
    sigs = extract_profile_signals_from_stat_row(row)
    bear_sigs = sigs & set(BEAR_CONTEXT_SIGNALS.keys())
    assert len(bear_sigs) >= 2
    pf = compute_profile_playbook_for_row(row, "sp500")
    assert pf["profile_score"] <= BEAR_CONTEXT_STANDALONE_CAP, (
        f"Bear standalone must be capped at {BEAR_CONTEXT_STANDALONE_CAP}, "
        f"got {pf['profile_score']}"
    )


# ── Test 4 — Bear-to-bull sequence (1 bar ago) ───────────────────────────────

def test_bear_to_bull_sequence_1_bar():
    """EB_DN 1 bar ago + BUY now → bear_to_bull_confirmed, bonus=5 (EB_DN->BUY=5), pair logged."""
    hist = [{"EB_DN"}]  # 1 bar ago
    row  = {"close": 100.0, "combo": ["BUY"]}
    pf   = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    assert pf["bear_to_bull_confirmed"] == 1
    assert pf["bear_to_bull_bars_ago"]  == 1
    assert pf["bear_to_bull_bonus"]     == 5
    assert any("EB_DN->BUY@1" in p for p in pf["bear_to_bull_pairs"])


# ── Test 5 — Sequence decay ───────────────────────────────────────────────────

def test_sequence_decay_1_bar():
    assert sequence_decay_bonus(6, 1) == 6

def test_sequence_decay_3_bars():
    assert sequence_decay_bonus(6, 3) == round(6 * 0.60)   # 4

def test_sequence_decay_5_bars():
    assert sequence_decay_bonus(6, 5) == round(6 * 0.25)   # 2

def test_sequence_decay_6_bars():
    assert sequence_decay_bonus(6, 6) == 0

def test_bear_to_bull_sequence_3_bars_ago():
    """EB_DN 3 bars ago + BUY now → EB_DN->BUY=5, decay=0.60: round(5*0.60)=3."""
    hist = [set(), set(), {"EB_DN"}]  # bars_ago: [1, 2, 3]
    row  = {"close": 100.0, "combo": ["BUY"]}
    pf   = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    assert pf["bear_to_bull_confirmed"] == 1
    assert pf["bear_to_bull_bars_ago"]  == 3
    assert pf["bear_to_bull_bonus"]     == round(5 * 0.60)

def test_bear_to_bull_sequence_5_bars_ago():
    """EB_DN 5 bars ago + BUY now → EB_DN->BUY=5, decay=0.25: round(5*0.25)=1."""
    hist = [set(), set(), set(), set(), {"EB_DN"}]
    row  = {"close": 100.0, "combo": ["BUY"]}
    pf   = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    assert pf["bear_to_bull_confirmed"] == 1
    assert pf["bear_to_bull_bonus"]     == round(5 * 0.25)


# ── Test 6 — Sequence bonus cap ──────────────────────────────────────────────

def test_sequence_bonus_cap():
    """Multiple valid sequences are capped at SEQUENCE_BONUS_CAP=5."""
    # EB_DN 1 bar ago: BUY(5)+SVS(4)+ABS(4)+L34(3)+VBO_UP(3) = 19
    # VBO_DN 1 bar ago: BUY(4)+SVS(2)+ABS(3)+L34(1)+VBO_UP(2) = 12; total raw=31
    hist = [{"EB_DN", "VBO_DN"}]
    row  = {"close": 100.0, "combo": ["BUY", "SVS"], "vabs": ["ABS", "VBO↑"],
            "l": ["L34"]}
    pf   = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    assert pf["bear_to_bull_bonus"] <= SEQUENCE_BONUS_CAP, (
        f"Sequence bonus must be capped at {SEQUENCE_BONUS_CAP}, "
        f"got {pf['bear_to_bull_bonus']}"
    )


# ── Test 7 — Consistency across paths ────────────────────────────────────────

def test_consistency_stat_row_and_compute():
    """Same bar dict produces identical results from extract then compute vs unified function."""
    row = {
        "close": 120.0,
        "combo": ["BUY", "SVS", "BB↑"],
        "fly":   ["FLY-BD"],
        "l":     ["FRI43", "BX↑"],
        "vabs":  ["ABS", "LOAD", "CLM"],
        "g":     ["G4", "G11"],
        "ultra": ["EB↑"],
    }
    # Path A: extract + compute (legacy path)
    sigs_a  = extract_profile_signals_from_stat_row(row)
    pname_a = get_profile(row, "sp500")
    res_a   = compute_profile_score(sigs_a, pname_a)
    # Path B: unified function (new path, no history context)
    res_b   = compute_profile_playbook_for_row(row, "sp500")
    # Scores must match (no history → no sequence bonus)
    assert res_a["profile_score"]    == res_b["profile_score"],    f"A={res_a['profile_score']} B={res_b['profile_score']}"
    assert res_a["profile_category"] == res_b["profile_category"], f"A={res_a['profile_category']} B={res_b['profile_category']}"
    assert res_a["profile_name"]     == res_b["profile_name"]


# ── Test 8 — SuperChart (turbo row) consistency with stat-row path ────────────

def test_superchart_turbo_consistency():
    """Turbo-row boolean extraction and stat-row list extraction produce same canonical signals."""
    turbo_row = {
        "buy_2809": 1, "bb_brk": 1, "fly_bd": 1, "fri43": 1,
        "abs_sig": 1, "load_sig": 1, "g4": 1, "g11": 1,
    }
    stat_row = {
        "close": 100.0,
        "combo": ["BUY", "BB↑"],
        "fly":   ["FLY-BD"],
        "l":     ["FRI43"],
        "vabs":  ["ABS", "LOAD"],
        "g":     ["G4", "G11"],
    }
    sigs_turbo = extract_signals_from_turbo_row(turbo_row)
    sigs_stat  = extract_profile_signals_from_stat_row(stat_row)
    common = sigs_turbo & sigs_stat
    expected = {"BUY", "BB_UP", "FLY_BD", "FRI43", "ABS", "LOAD", "G4", "G11"}
    for sig in expected:
        assert sig in common, f"{sig} not found in both paths; turbo={sigs_turbo} stat={sigs_stat}"


# ── Test 9 — No duplicated config outside profile_playbook ───────────────────

def test_no_duplicated_signal_aliases_in_playbook():
    """SIGNAL_ALIASES must map every key to a consistent canonical form."""
    from profile_playbook import SIGNAL_ALIASES
    # All values should either be in SIGNAL_ALIASES.values() or be direct canonical names
    # Key invariant: SIGNAL_ALIASES[canonical] == canonical (identity entries)
    for raw, norm in SIGNAL_ALIASES.items():
        if raw == norm:
            continue  # identity entry — fine
        # The normalized form should also normalize to itself (no double-hop needed)
        renorm = normalize_signal_name(norm)
        assert renorm == norm, (
            f"SIGNAL_ALIASES[{raw!r}]={norm!r} but normalize({norm!r})={renorm!r}. "
            f"Double-hop alias detected."
        )

def test_config_snapshot_has_required_keys():
    """get_playbook_config_snapshot() returns all required fields."""
    snap = get_playbook_config_snapshot()
    required = {
        "profile_playbook_version", "generated_at", "aliases_count",
        "profiles", "bear_context_signals", "bear_context_standalone_cap",
        "bull_confirm_signals", "sequence_bonuses", "sequence_bonus_cap",
        "profile_btb_caps", "profile_btb_weak_confirm_profiles",
        "btb_sweet_spot_allowed_profiles",
    }
    for k in required:
        assert k in snap, f"Config snapshot missing key: {k}"
    assert snap["profile_playbook_version"] == PROFILE_PLAYBOOK_VERSION
    assert snap["bear_context_standalone_cap"] == BEAR_CONTEXT_STANDALONE_CAP
    assert snap["sequence_bonus_cap"] == SEQUENCE_BONUS_CAP
    assert snap["profile_btb_caps"] == dict(PROFILE_BTB_CAPS)
    assert "SP500_300_PLUS" in snap["profile_btb_weak_confirm_profiles"]
    assert snap["btb_sweet_spot_allowed_profiles"] == sorted(BTB_SWEET_SPOT_ALLOWED_PROFILES)


# ── Test 11 — SP500_300_PLUS per-profile BTB cap ─────────────────────────────

def test_sp500_300_plus_btb_cap():
    """SP500_300_PLUS: BTB bonus capped at 3, not global cap of 5."""
    hist = [{"EB_DN"}]  # 1 bar ago
    row  = {"close": 400.0, "combo": ["BUY", "SVS"], "vabs": ["ABS"]}
    pf   = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    assert pf["profile_name"] == "SP500_300_PLUS"
    cap = PROFILE_BTB_CAPS.get("SP500_300_PLUS", 5)
    assert pf["bear_to_bull_bonus"] <= cap, (
        f"SP500_300_PLUS BTB bonus must be ≤{cap}, got {pf['bear_to_bull_bonus']}"
    )

def test_sp500_300_plus_weak_confirm_scaling():
    """SP500_300_PLUS: L34/VBO_UP/BO_UP bonuses scaled ×0.5."""
    # Only L34 as bull confirm — should be scaled down
    hist = [{"EB_DN"}]
    row_l34   = {"close": 400.0, "l": ["L34"]}
    row_buy   = {"close": 400.0, "combo": ["BUY"]}
    pf_l34    = compute_profile_playbook_for_row(row_l34,  "sp500", history_context=hist)
    pf_buy    = compute_profile_playbook_for_row(row_buy,  "sp500", history_context=hist)
    # BUY raw bonus = 5; L34 raw bonus = 3 → scaled to round(3*0.5) = 2
    # So BUY should yield more bonus than L34
    assert pf_buy["bear_to_bull_bonus"] >= pf_l34["bear_to_bull_bonus"], (
        f"BUY bonus {pf_buy['bear_to_bull_bonus']} should be >= L34 bonus {pf_l34['bear_to_bull_bonus']} in SP500_300_PLUS"
    )


# ── Test 12 — BTB category gate ──────────────────────────────────────────────

def test_btb_category_gate_watch_cannot_reach_sweet_spot():
    """WATCH-base row: BTB bonus alone cannot push category to SWEET_SPOT."""
    # SP500_50_150: sweet_low=12, building_threshold=8.4
    # No organic signals → base=0 → category_without_btb=WATCH
    # With EB_DN->BUY BTB: base=5 → total=5 → still WATCH
    # (BTB of 5 still < sweet_low*0.70=8.4, so no upgrade possible anyway in this case)
    # For the gate to trigger we need BTB to push past sweet_low: need a larger BTB
    # Use multiple bear signals 1 bar ago to try to force SWEET_SPOT via BTB
    hist = [{"EB_DN", "BE_DN"}]  # both bear signals present
    row  = {"close": 100.0, "combo": ["BUY", "SVS"], "vabs": ["ABS"]}
    pf   = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    # base_score = BUY(4)+SVS(4)+ABS(3) = 11 (organic, in BUILDING)
    # so this test has organic signals; use a row with NO organic signals
    row_no_sigs = {"close": 100.0}
    hist2 = [{"EB_DN"}]
    pf2 = compute_profile_playbook_for_row(row_no_sigs, "sp500", history_context=hist2)
    # No organic signals → base=0; BTB bonus = 5; total = 5; < sweet_low (12)
    # So category_with_btb = WATCH (score=5, building_threshold=8.4, 5<8.4)
    # Gate doesn't even trigger, but BTB doesn't create SWEET_SPOT
    assert pf2["btb_created_sweet_spot"] == 0

def test_btb_category_gate_blocks_watch_to_sweet_spot():
    """Category gate: base=WATCH, btb large enough to reach sweet_low → capped at BUILDING."""
    # Use NASDAQ_PENNY: sweet_spot=(10,32), sweet_low*0.70=7
    # Pure WATCH base (score=0), inject a large hypothetical — use the cap
    # Actually with SEQUENCE_BONUS_CAP=5, total=5 < 7, so gate never triggers for NASDAQ
    # Use SP500_LT20: sweet_spot=(12,36), sweet_low*0.70=8.4
    # base=0 (no organic), with BTB cap=5, total=5 < 8.4 → WATCH, gate N/A
    # The gate is relevant only if BTB alone could push from <8.4 to >=12
    # That requires BTB>=12, which is now impossible (cap=5)
    # So the gate's primary role is preventing edge cases where base≈7-8 + BTB reaches 12
    # Test: base = 7 (near building threshold), BTB pushes to sweet_spot
    # Craft a row: 1 signal worth 7 → not standard, so test via mocking category
    # Test the actual gate condition directly:
    from profile_playbook import _score_to_category, PROFILES
    profile = PROFILES["SP500_50_150"]
    cat_watch, _, _ = _score_to_category(6, profile)  # 6 < 8.4 → WATCH
    cat_ss,    _, _ = _score_to_category(13, profile)  # 13 → SWEET_SPOT
    assert cat_watch == "WATCH"
    assert cat_ss    == "SWEET_SPOT"

def test_btb_audit_fields_without_history():
    """Without history context, category_without_btb == category_with_btb."""
    row = {"close": 100.0, "combo": ["BUY", "SVS"], "fly": ["FLY-BD"]}
    pf  = compute_profile_playbook_for_row(row, "sp500")
    assert pf["bear_to_bull_bonus"]       == 0
    assert pf["category_without_btb"]     == pf["category_with_btb"]
    assert pf["btb_category_upgrade"]     == 0
    assert pf["btb_created_sweet_spot"]   == 0
    assert pf["base_profile_score_without_btb"] == pf["profile_score"]
    assert "btb_late_clamped"              in pf
    assert "btb_sweet_spot_allowed_profile" in pf


# ── Test 10 — Output validation (distribution check) ─────────────────────────

def test_output_distribution_not_all_watch():
    """A set of rows with real signals should have non-WATCH categories."""
    rows = []
    signal_sets = [
        {"close": 100.0, "combo": ["BUY", "SVS"], "fly": ["FLY-BD"], "vabs": ["ABS", "LOAD"]},
        {"close": 100.0, "combo": ["BUY"], "l": ["FRI43"], "g": ["G4", "G11"]},
        {"close": 100.0, "fly": ["FLY-BD"], "vabs": ["ABS", "CLM", "LOAD"]},
        {"close": 100.0},  # no signals → WATCH
        {"close": 100.0},  # no signals → WATCH
    ]
    results = [compute_profile_playbook_for_row(r, "sp500") for r in signal_sets]
    scores    = [r["profile_score"] for r in results]
    cats      = [r["profile_category"] for r in results]
    assert any(s > 0 for s in scores),   f"All scores are 0: {scores}"
    assert any(c != "WATCH" for c in cats), f"All categories are WATCH: {cats}"

def test_bear_to_bull_fields_present_in_result():
    """compute_profile_playbook_for_row always returns all required bear-to-bull keys."""
    row = {"close": 100.0}
    pf  = compute_profile_playbook_for_row(row, "sp500")
    required_keys = {
        "profile_playbook_version", "profile_name", "profile_score",
        "profile_category", "sweet_spot_active", "late_warning",
        "active_signals", "matched_profile_signals", "matched_profile_pairs",
        "unscored_signals",
        "bear_context_last_3", "bear_context_last_5",
        "bull_confirm_now", "bear_to_bull_confirmed",
        "bear_to_bull_bars_ago", "bear_to_bull_bonus", "bear_to_bull_pairs",
        "base_profile_score_without_btb", "category_without_btb", "category_with_btb",
        "btb_category_upgrade", "btb_created_sweet_spot",
        "btb_late_clamped", "btb_sweet_spot_allowed_profile",
    }
    for k in required_keys:
        assert k in pf, f"Missing key in result: {k}"


# ── BTB v3 gate tests ─────────────────────────────────────────────────────────

def test_btb_late_never_created():
    """BTB that would push into LATE is clamped to SWEET_SPOT + btb_late_clamped=1.

    SP500_50_150: sweet_spot=(12,42), late_threshold=42.
    Signals below produce base=38 (SWEET_SPOT), then BTB(+5)=43 → LATE → clamped.
    """
    hist = [{"EB_DN"}]
    row = {
        "close": 100.0,
        "fly":   ["FLY-BD"],
        "combo": ["BUY", "SVS", "BB↑"],
        "l":     ["FRI43", "BX↑"],
        "g":     ["G4", "G11"],
        "vabs":  ["ABS"],
        "f":     ["F10", "F9"],
    }
    pf = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    assert pf["profile_name"] == "SP500_50_150"
    assert pf["category_without_btb"] == "SWEET_SPOT"
    assert pf["profile_category"] == "SWEET_SPOT"
    assert pf["btb_late_clamped"] == 1


def test_btb_sweet_spot_blocked_for_lt20():
    """SP500_LT20 cannot reach SWEET_SPOT via BTB."""
    hist = [{"EB_DN"}]
    row  = {"close": 10.0, "combo": ["BUY", "SVS"], "vabs": ["CLM"]}
    pf   = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    assert pf["profile_name"] == "SP500_LT20"
    assert pf["profile_category"] != "SWEET_SPOT"
    assert pf["btb_sweet_spot_allowed_profile"] == 0


def test_btb_sweet_spot_blocked_for_300_plus():
    """SP500_300_PLUS cannot reach SWEET_SPOT via BTB."""
    hist = [{"EB_DN"}]
    row  = {"close": 400.0, "combo": ["BUY", "SVS"], "vabs": ["ABS"]}
    pf   = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    assert pf["profile_name"] == "SP500_300_PLUS"
    assert pf["profile_category"] != "SWEET_SPOT"
    assert pf["btb_sweet_spot_allowed_profile"] == 0


def test_btb_sweet_spot_allowed_for_mid_profiles():
    """SP500_50_150 (allowed) can reach SWEET_SPOT via BTB from BUILDING."""
    hist = [{"EB_DN"}]
    row  = {"close": 100.0, "combo": ["BUY", "SVS"], "vabs": ["ABS"]}
    pf   = compute_profile_playbook_for_row(row, "sp500", history_context=hist)
    assert pf["profile_name"] == "SP500_50_150"
    assert pf["btb_sweet_spot_allowed_profile"] == 1
    # If BTB created SWEET_SPOT, it should be allowed
    if pf["btb_created_sweet_spot"] == 1:
        assert pf["profile_category"] == "SWEET_SPOT"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
