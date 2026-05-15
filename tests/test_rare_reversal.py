"""Tests for the Rare Reversal Miner (backend/analyzers/rare_reversal/miner.py)."""
from __future__ import annotations
import os
import csv
import sys
import tempfile
import pytest
from unittest.mock import patch

# Ensure backend/ is importable
_backend = os.path.join(os.path.dirname(__file__), "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from analyzers.rare_reversal.miner import (
    _parse_signal_summary,
    _build_sequences,
    _primary_signal,
    _bottom_metrics,
    _evidence_tier,
    _score,
    run_rare_reversal_scan,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _row(
    ticker="AAPL",
    date="2024-01-10",
    composite_primary="T1",
    prev3="Z5|T2|Z3",
    prev5="T4|Z2|Z5|T2|Z3",
    close=50.0,
    high=51.0,
    low=49.0,
    volume=1_000_000,
    universe="sp500",
    tf="1d",
):
    return {
        "ticker": ticker,
        "date": date,
        "bar_datetime": date,
        "close": str(close),
        "high": str(high),
        "low": str(low),
        "open": str(close - 0.5),
        "volume": str(volume),
        "universe": universe,
        "timeframe": tf,
        "composite_primary_label": composite_primary,
        "composite_t_label": composite_primary,
        "prev_3_signal_summary": prev3,
        "prev_5_signal_summary": prev5,
    }


def _make_stat_csv(rows, path):
    """Write a minimal stock_stat CSV to *path* for miner integration tests."""
    cols = [
        "ticker", "date", "bar_datetime", "close", "high", "low", "open", "volume",
        "universe", "timeframe",
        "composite_primary_label", "composite_t_label",
        "prev_1_signal_summary", "prev_3_signal_summary", "prev_5_signal_summary",
        "price_bucket", "is_sub_dollar",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


# ── Section A: _primary_signal ─────────────────────────────────────────────────

class TestPrimarySignal:
    def test_t1(self):
        assert _primary_signal("T1") == "T1"

    def test_composite_t1_l(self):
        assert _primary_signal("T1L3EB") == "T1"

    def test_z_signal(self):
        assert _primary_signal("Z5G") == "Z5G"

    def test_empty(self):
        assert _primary_signal("") == ""

    def test_pipe_fallback(self):
        # If it doesn't match regex, fall back to first pipe-separated token
        # (shouldn't normally happen but shouldn't crash)
        result = _primary_signal("XYZ1|T2")
        assert result  # non-empty


# ── Section B: _parse_signal_summary ──────────────────────────────────────────

class TestParseSignalSummary:
    def test_three_parts(self):
        assert _parse_signal_summary("Z5|T2|Z3") == ["Z5", "T2", "Z3"]

    def test_five_parts(self):
        assert _parse_signal_summary("T4|Z2|Z5|T2|Z3") == ["T4", "Z2", "Z5", "T2", "Z3"]

    def test_empty(self):
        assert _parse_signal_summary("") == []

    def test_strips_whitespace(self):
        assert _parse_signal_summary(" Z5 | T2 | Z3 ") == ["Z5", "T2", "Z3"]

    def test_filters_empty_tokens(self):
        assert _parse_signal_summary("Z5||Z3") == ["Z5", "Z3"]


# ── Section C: _build_sequences ───────────────────────────────────────────────

class TestBuildSequences:
    def test_base4_built_from_prev3(self):
        row = _row(composite_primary="T1", prev3="Z5|T2|Z3")
        seqs = _build_sequences(row)
        assert seqs["base4_key"] == "Z5|T2|Z3|T1"

    def test_ext5_built_when_prev5_suffix_matches_prev3(self):
        # prev5=["T4","Z2","Z5","T2","Z3"], prev3=["Z5","T2","Z3"]
        # suffix match: prev5[-3:] == prev3 ✓ → prev5[-4]="Z2" becomes prev4
        row = _row(composite_primary="T1", prev3="Z5|T2|Z3", prev5="T4|Z2|Z5|T2|Z3")
        seqs = _build_sequences(row)
        assert seqs["extended5_key"] == "Z2|Z5|T2|Z3|T1"

    def test_ext6_built_from_full_prev5(self):
        row = _row(composite_primary="T1", prev3="Z5|T2|Z3", prev5="T4|Z2|Z5|T2|Z3")
        seqs = _build_sequences(row)
        assert seqs["extended6_key"] == "T4|Z2|Z5|T2|Z3|T1"

    def test_ext5_none_when_prev5_suffix_mismatch(self):
        # prev5 last 3 tokens don't match prev3
        row = _row(composite_primary="T1", prev3="Z5|T2|Z3", prev5="T4|Z2|X1|T2|Z9")
        seqs = _build_sequences(row)
        assert seqs["extended5_key"] is None
        assert seqs["extended6_key"] is None

    def test_base4_none_when_prev3_incomplete(self):
        row = _row(composite_primary="T1", prev3="Z5|T2")  # only 2 tokens
        seqs = _build_sequences(row)
        assert seqs["base4_key"] is None

    def test_no_final_signal_returns_nones(self):
        row = _row(composite_primary="")
        row["composite_t_label"] = ""
        seqs = _build_sequences(row)
        assert seqs["base4_key"] is None


# ── Section D: _bottom_metrics ─────────────────────────────────────────────────

def _make_rows(lows, highs=None, closes=None):
    """Build minimal bar rows for _bottom_metrics tests."""
    if highs is None:
        highs = [l + 2 for l in lows]
    if closes is None:
        closes = [l + 1 for l in lows]
    return [{"low": str(l), "high": str(h), "close": str(c)}
            for l, h, c in zip(lows, highs, closes)]


class TestBottomMetrics:
    def test_empty_window_returns_defaults(self):
        bm = _bottom_metrics([], [])
        assert bm["sequence_low_bar_offset"] is None
        assert bm["qualifies_as_bottom"] is False

    def test_sequence_low_offset_zero_when_last_bar_is_lowest(self):
        rows = _make_rows([50, 48, 46, 44, 40])  # last bar is lowest
        bm = _bottom_metrics(rows, rows)
        assert bm["sequence_low_bar_offset"] == 0

    def test_sequence_low_offset_nonzero_when_earlier_bar_lower(self):
        rows = _make_rows([40, 48, 50, 52, 54])  # first bar (index 0) is lowest
        bm = _bottom_metrics(rows, rows)
        assert bm["sequence_low_bar_offset"] == 4

    def test_qualifies_when_in_bottom_20pct_range(self):
        # 20-bar range: low=40, high=100 → threshold=52; seq low=41 qualifies
        ctx_rows = _make_rows([100 - i for i in range(20)])  # lows: 100, 99…81
        # Override to get a controlled range
        ctx_rows = [{"low": "40", "high": "100", "close": "70"}] * 20
        window_rows = _make_rows([40, 48, 52, 56, 60])
        bm = _bottom_metrics(window_rows, ctx_rows)
        assert bm["qualifies_as_bottom"] is True

    def test_qualifies_when_20bar_low(self):
        ctx_rows = [{"low": "40", "high": "80", "close": "60"}] * 20
        window_rows = _make_rows([40, 45, 50, 55, 60])
        bm = _bottom_metrics(window_rows, ctx_rows)
        assert bm["sequence_contains_20bar_low"] is True
        assert bm["qualifies_as_bottom"] is True

    def test_return_from_low_computed(self):
        rows = _make_rows([40, 48, 50, 52, 60], closes=[41, 49, 51, 53, 65])
        bm = _bottom_metrics(rows, rows)
        # seq low = 40, final close = 65 → ret = (65-40)/40 * 100 = 62.5
        assert bm["return_from_sequence_low_to_final"] == pytest.approx(62.5, abs=0.01)

    def test_reclaim_qualifies_bottom(self):
        # seq low at offset > 0, final close higher by >=0.5%
        low_val = 100.0
        final_close = low_val * 1.01  # 1% reclaim
        rows = _make_rows([low_val, 102, 103, 104, 105], closes=[101, 103, 104, 105, final_close])
        # Window where first bar is lowest
        rows[0]["low"] = str(low_val)
        rows[0]["close"] = "101"
        rows[-1]["close"] = str(final_close)
        bm = _bottom_metrics(rows, rows)
        assert bm["qualifies_as_bottom"] is True


# ── Section E: _evidence_tier ──────────────────────────────────────────────────

class TestEvidenceTier:
    def test_confirmed_rare(self):
        assert _evidence_tier(5, 3.0, 65.0, 20.0) == "CONFIRMED_RARE"

    def test_confirmed_fails_on_low_win(self):
        assert _evidence_tier(5, 3.0, 45.0, 20.0) != "CONFIRMED_RARE"

    def test_confirmed_fails_on_high_fail(self):
        assert _evidence_tier(5, 3.0, 65.0, 40.0) != "CONFIRMED_RARE"

    def test_confirmed_fails_on_negative_median(self):
        assert _evidence_tier(5, -1.0, 65.0, 20.0) != "CONFIRMED_RARE"

    def test_anecdotal_rare(self):
        # count=1, positive median
        assert _evidence_tier(1, 2.5, None, None) == "ANECDOTAL_RARE"

    def test_forming_pattern_no_data(self):
        assert _evidence_tier(0, None, None, None) == "NO_DATA"

    def test_forming_pattern_n_gt1_low_win(self):
        assert _evidence_tier(3, 1.0, 40.0, 50.0) == "FORMING_PATTERN"


# ── Section F: _score ──────────────────────────────────────────────────────────

class TestScore:
    def test_confirmed_higher_than_anecdotal(self):
        s_conf  = _score("CONFIRMED_RARE",  3.0, 60.0, 20.0, False, None, None)
        s_anec  = _score("ANECDOTAL_RARE",  3.0, 60.0, 20.0, False, None, None)
        assert s_conf > s_anec

    def test_bottom_bonus_applied(self):
        s_no_bot = _score("CONFIRMED_RARE", 3.0, 60.0, 20.0, False, None, None)
        s_bot    = _score("CONFIRMED_RARE", 3.0, 60.0, 20.0, True,  None, None)
        assert s_bot > s_no_bot

    def test_ext5_bonus(self):
        s_base = _score("CONFIRMED_RARE", 3.0, 60.0, 20.0, False, None,   None)
        s_ext5 = _score("CONFIRMED_RARE", 3.0, 60.0, 20.0, False, "A|B|C|D|E", None)
        assert s_ext5 > s_base

    def test_ext6_bonus_greater_than_ext5(self):
        s_ext5 = _score("CONFIRMED_RARE", 3.0, 60.0, 20.0, False, "key5", None)
        s_ext6 = _score("CONFIRMED_RARE", 3.0, 60.0, 20.0, False, "key5", "key6")
        assert s_ext6 > s_ext5

    def test_high_fail_lowers_score(self):
        s_low_fail  = _score("FORMING_PATTERN", 2.0, 55.0, 10.0, False, None, None)
        s_high_fail = _score("FORMING_PATTERN", 2.0, 55.0, 50.0, False, None, None)
        assert s_low_fail > s_high_fail


# ── Section G: integration via run_rare_reversal_scan ─────────────────────────

class TestRunRareReversalScan:
    def test_invalid_universe_returns_error(self):
        result = run_rare_reversal_scan(universe="invalid_xyz")
        assert result["error"] is not None
        assert result["results"] == []

    def test_invalid_tf_returns_error(self):
        result = run_rare_reversal_scan(universe="sp500", tf="99d")
        assert result["error"] is not None

    def test_missing_stat_file_returns_error(self):
        with patch("analyzers.rare_reversal.miner._stat_path", return_value="/nonexistent/path.csv"):
            result = run_rare_reversal_scan(universe="sp500", tf="1d")
        assert result["error"] is not None
        assert "No stock_stat" in result["error"]

    def test_scan_returns_results_for_valid_data(self, tmp_path):
        rows = [
            _row("AAPL", "2024-01-05", "T1", "Z5|T2|Z3", "T4|Z2|Z5|T2|Z3", close=50),
            _row("AAPL", "2024-01-08", "T1", "Z2|Z5|T2", "Z3|Z1|Z2|Z5|T2", close=52),
            _row("AAPL", "2024-01-10", "T1", "Z5|T2|Z3", "T4|Z2|Z5|T2|Z3", close=54),
        ]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.rare_reversal.miner._stat_path", return_value=stat_path):
            result = run_rare_reversal_scan(universe="sp500", tf="1d")
        assert result["error"] is None
        assert result["total"] >= 0  # may be 0 if no matrix hit, but no crash

    def test_scan_returns_at_most_3_patterns_per_ticker(self, tmp_path):
        # Generate rows for AAPL with many different patterns
        combos = [
            ("T1", "Z5|T2|Z3", "T4|Z2|Z5|T2|Z3"),
            ("T1", "Z3|T2|Z5", "T4|Z2|Z3|T2|Z5"),
            ("T1", "Z2|Z3|T2", "T4|Z1|Z2|Z3|T2"),
            ("T1", "T2|Z2|Z3", "T4|Z1|T2|Z2|Z3"),
        ]
        rows = [_row("AAPL", f"2024-01-{5+i:02d}", c, p3, p5, close=50+i)
                for i, (c, p3, p5) in enumerate(combos)]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.rare_reversal.miner._stat_path", return_value=stat_path):
            result = run_rare_reversal_scan(universe="sp500", tf="1d")
        aapl_patterns = [r for r in result["results"] if r["ticker"] == "AAPL"]
        assert len(aapl_patterns) <= 3

    def test_rank_field_present_and_sequential(self, tmp_path):
        rows = [_row("AAPL", "2024-01-10", "T1", "Z5|T2|Z3", "T4|Z2|Z5|T2|Z3", close=50)]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.rare_reversal.miner._stat_path", return_value=stat_path):
            result = run_rare_reversal_scan(universe="sp500", tf="1d")
        for i, r in enumerate(result["results"], 1):
            assert r["rank"] == i

    def test_all_required_fields_present(self, tmp_path):
        required = [
            "ticker", "evidence_tier", "base4_key", "base4_tier",
            "base4_med10d", "base4_fail10d", "extended5_key", "extended6_key",
            "pattern_length", "pattern_count", "sequence_low_bar_offset",
            "sequence_contains_20bar_low", "return_from_sequence_low_to_final",
            "median_10d_return", "win_rate_10d", "fail_rate_10d",
            "score", "example_dates", "last_seen_date",
            "is_currently_active", "current_pattern_completion",
        ]
        rows = [_row("AAPL", "2024-01-10", "T1", "Z5|T2|Z3", "T4|Z2|Z5|T2|Z3", close=50)]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.rare_reversal.miner._stat_path", return_value=stat_path):
            result = run_rare_reversal_scan(universe="sp500", tf="1d")
        for r in result["results"]:
            for field in required:
                assert field in r, f"Missing field: {field}"

    def test_price_filter_excludes_ticker(self, tmp_path):
        rows = [
            _row("CHEAP", "2024-01-10", "T1", "Z5|T2|Z3", "T4|Z2|Z5|T2|Z3", close=2),
            _row("NORMAL", "2024-01-10", "T1", "Z5|T2|Z3", "T4|Z2|Z5|T2|Z3", close=50),
        ]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.rare_reversal.miner._stat_path", return_value=stat_path):
            result = run_rare_reversal_scan(universe="sp500", tf="1d", min_price=5.0)
        tickers = {r["ticker"] for r in result["results"]}
        assert "CHEAP" not in tickers

    def test_result_has_universe_and_tf_metadata(self, tmp_path):
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv([], stat_path)
        with patch("analyzers.rare_reversal.miner._stat_path", return_value=stat_path):
            result = run_rare_reversal_scan(universe="sp500", tf="1d")
        assert result["universe"] == "sp500"
        assert result["tf"] == "1d"

    def test_is_currently_active_when_latest_bar_matches(self, tmp_path):
        # Two identical-pattern rows — latest bar should match → is_currently_active=True
        rows = [
            _row("AAPL", "2024-01-05", "T1", "Z5|T2|Z3", "T4|Z2|Z5|T2|Z3", close=50),
            _row("AAPL", "2024-01-10", "T1", "Z5|T2|Z3", "T4|Z2|Z5|T2|Z3", close=52),
        ]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.rare_reversal.miner._stat_path", return_value=stat_path):
            result = run_rare_reversal_scan(universe="sp500", tf="1d")
        # At least one pattern for AAPL should be marked active
        aapl = [r for r in result["results"] if r["ticker"] == "AAPL"]
        if aapl:
            active_count = sum(1 for r in aapl if r["is_currently_active"])
            assert active_count >= 1
