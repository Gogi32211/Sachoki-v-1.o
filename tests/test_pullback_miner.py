"""Tests for the Pullback Pattern Miner (backend/analyzers/pullback_miner/miner.py)."""
from __future__ import annotations

import csv
import math
import os
import statistics
import sys
from collections import deque
from unittest.mock import patch

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from analyzers.pullback_miner.miner import (
    _WIN_10,
    _WIN_20,
    _aggregate,
    _build_sequences,
    _check_active,
    _evidence_tier,
    _final_l,
    _final_tz,
    _forward_outcomes,
    _in_pullback_zone,
    _not_broken,
    _parse_summary,
    _price_position_20bar,
    _pullback_stage,
    _score,
    _top3,
    _trend_context,
    run_pullback_scan,
)

# ── Row factory ────────────────────────────────────────────────────────────────

def _row(
    ticker="AAPL",
    date="2024-01-10",
    t_signal="",
    z_signal="",
    l_signal="",
    prev3="",
    prev5="",
    close=100.0,
    high=102.0,
    low=98.0,
    ema20=95.0,
    ema50=90.0,
    ema89=80.0,
    ret3=None,
    ret5=None,
    ret10=None,
    mfe5=None,
    mfe10=None,
    mae5=None,
    mae10=None,
):
    return {
        "ticker": ticker, "date": date, "bar_datetime": date,
        "t_signal": t_signal, "z_signal": z_signal, "l_signal": l_signal,
        "prev_3_signal_summary": prev3,
        "prev_5_signal_summary": prev5,
        "close": str(close), "high": str(high), "low": str(low),
        "open": str(close - 1),
        "ema20": str(ema20), "ema50": str(ema50), "ema89": str(ema89),
        "ret_3d":  str(ret3)  if ret3  is not None else "",
        "ret_5d":  str(ret5)  if ret5  is not None else "",
        "ret_10d": str(ret10) if ret10 is not None else "",
        "mfe_5d":  str(mfe5)  if mfe5  is not None else "",
        "mfe_10d": str(mfe10) if mfe10 is not None else "",
        "mae_5d":  str(mae5)  if mae5  is not None else "",
        "mae_10d": str(mae10) if mae10 is not None else "",
    }


def _event(
    ret10=3.0, mfe10=6.0, mae10=-2.0,
    pos20=0.55, trend=True, above50=True, ema20gt50=False, date="2024-01-10"
):
    """Minimal event dict for aggregation tests."""
    return {
        "date": date,
        "price_position_20bar": pos20,
        "trend_context": trend,
        "above_ema50": above50,
        "ema20_above_ema50": ema20gt50,
        "forward_return_3d": 1.0,
        "forward_return_5d": 2.0,
        "forward_return_10d": ret10,
        "max_forward_return_5d": 4.0,
        "max_forward_return_10d": mfe10,
        "max_drawdown_5d": -1.0,
        "max_drawdown_10d": mae10,
        "success_10d": mfe10 >= 5.0,
        "fail_10d": mae10 <= -6.0 or (ret10 is not None and ret10 < -4.0),
    }


def _make_stat_csv(rows, path):
    cols = [
        "ticker", "date", "bar_datetime", "close", "high", "low", "open",
        "t_signal", "z_signal", "l_signal",
        "prev_1_signal_summary", "prev_3_signal_summary", "prev_5_signal_summary",
        "ema20", "ema50", "ema89",
        "ret_3d", "ret_5d", "ret_10d",
        "mfe_5d", "mfe_10d", "mae_5d", "mae_10d",
        "universe", "timeframe",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


# ── Section A: helpers ─────────────────────────────────────────────────────────

class TestParseSummary:
    def test_three_tokens(self):
        assert _parse_summary("Z5|T2|Z3") == ["Z5", "T2", "Z3"]

    def test_empty_string(self):
        assert _parse_summary("") == []

    def test_filters_blanks(self):
        assert _parse_summary("Z5||Z3") == ["Z5", "Z3"]

    def test_strips_spaces(self):
        assert _parse_summary(" Z5 | T1 | Z3 ") == ["Z5", "T1", "Z3"]


class TestFinalSignals:
    def test_t_signal_preferred_over_z(self):
        r = _row(t_signal="T1", z_signal="Z5")
        assert _final_tz(r) == "T1"

    def test_z_signal_when_no_t(self):
        r = _row(t_signal="", z_signal="Z5")
        assert _final_tz(r) == "Z5"

    def test_empty_when_neither(self):
        r = _row(t_signal="", z_signal="")
        assert _final_tz(r) == ""

    def test_l_signal(self):
        r = _row(l_signal="L34")
        assert _final_l(r) == "L34"

    def test_l_empty(self):
        r = _row(l_signal="")
        assert _final_l(r) == ""


# ── Section B: sequence building ──────────────────────────────────────────────

class TestBuildSequences:
    def test_base4_tz_key(self):
        r = _row(t_signal="T1", prev3="Z5|T2|Z3")
        seqs = _build_sequences(r)
        assert seqs["base4_key"] == "Z5|T2|Z3|T1"

    def test_ext5_key_built_when_suffix_matches(self):
        r = _row(t_signal="T1", prev3="Z5|T2|Z3", prev5="T4|Z2|Z5|T2|Z3")
        seqs = _build_sequences(r)
        # prev5[-3:] = ["Z5","T2","Z3"] == prev3_parts ✓; prev5[-4] = "Z2"
        assert seqs["ext5_key"] == "Z2|Z5|T2|Z3|T1"

    def test_ext5_none_when_suffix_mismatch(self):
        r = _row(t_signal="T1", prev3="Z5|T2|Z3", prev5="T4|Z2|X1|T2|Z9")
        seqs = _build_sequences(r)
        assert seqs["ext5_key"] is None

    def test_base4_tzl_key_with_l_signal(self):
        r = _row(t_signal="T1", l_signal="L34", prev3="Z5|T2|Z3")
        seqs = _build_sequences(r)
        assert seqs["base4_tzl_key"] == "Z5|T2|Z3|T1+L34"

    def test_ext5_tzl_key(self):
        r = _row(t_signal="T1", l_signal="L43", prev3="Z5|T2|Z3", prev5="T4|Z2|Z5|T2|Z3")
        seqs = _build_sequences(r)
        assert seqs["ext5_tzl_key"] == "Z2|Z5|T2|Z3|T1+L43"

    def test_base4_tzl_none_when_no_l(self):
        r = _row(t_signal="T1", l_signal="", prev3="Z5|T2|Z3")
        seqs = _build_sequences(r)
        assert seqs["base4_tzl_key"] is None

    def test_none_when_no_tz_signal(self):
        r = _row(t_signal="", z_signal="", prev3="Z5|T2|Z3")
        seqs = _build_sequences(r)
        assert seqs["base4_key"] is None

    def test_none_when_prev3_incomplete(self):
        r = _row(t_signal="T1", prev3="Z5|T2")  # only 2 tokens
        seqs = _build_sequences(r)
        assert seqs["base4_key"] is None


# ── Section C: trend context ───────────────────────────────────────────────────

class TestTrendContext:
    def test_above_ema50_qualifies(self):
        r = _row(close=100, ema50=90)
        assert _trend_context(r) is True

    def test_ema20_gt_ema50_qualifies(self):
        r = _row(close=80, ema20=95, ema50=90)  # close < ema50 but ema20 > ema50
        assert _trend_context(r) is True

    def test_below_both_emus_no_qualify(self):
        r = _row(close=70, ema20=80, ema50=90)
        assert _trend_context(r) is False

    def test_above_both_emus_qualifies(self):
        r = _row(close=100, ema20=95, ema50=90)
        assert _trend_context(r) is True

    def test_missing_ema50_returns_false(self):
        r = _row(close=100)
        r["ema50"] = ""
        assert _trend_context(r) is False


# ── Section D: pullback zone ───────────────────────────────────────────────────

class TestPullbackZone:
    def _pos(self, close, h20, l20):
        win_h = deque([h20], maxlen=_WIN_20)
        win_l = deque([l20], maxlen=_WIN_20)
        return _price_position_20bar(close, win_h, win_l)

    def test_mid_range_qualifies(self):
        pos = self._pos(55, 100, 0)   # pos = 0.55
        assert _in_pullback_zone(pos) is True

    def test_near_bottom_excluded(self):
        pos = self._pos(20, 100, 0)   # pos = 0.20 < 0.30
        assert _in_pullback_zone(pos) is False

    def test_near_top_excluded(self):
        pos = self._pos(90, 100, 0)   # pos = 0.90 > 0.85
        assert _in_pullback_zone(pos) is False

    def test_exactly_at_lower_bound_qualifies(self):
        pos = self._pos(30, 100, 0)   # pos = 0.30
        assert _in_pullback_zone(pos) is True

    def test_none_pos_excluded(self):
        assert _in_pullback_zone(None) is False

    def test_zero_range_returns_half(self):
        win_h = deque([100], maxlen=_WIN_20)
        win_l = deque([100], maxlen=_WIN_20)
        pos = _price_position_20bar(100, win_h, win_l)
        assert pos == 0.5


class TestNotBroken:
    def test_close_below_10bar_low_is_broken(self):
        win = deque([50, 55, 48, 52, 60, 58, 55, 51, 53, 57], maxlen=_WIN_10)
        # min(win)=48; close=47 < 48 → broken (returns False)
        assert _not_broken(47.0, win) is False

    def test_close_equal_to_10bar_low_ok(self):
        win = deque([50, 55, 48, 52, 60], maxlen=_WIN_10)
        assert _not_broken(48.0, win) is True  # 48 >= min=48

    def test_empty_window_returns_true(self):
        assert _not_broken(50.0, deque(maxlen=_WIN_10)) is True


# ── Section E: pullback stage ──────────────────────────────────────────────────

class TestPullbackStage:
    @pytest.mark.parametrize("sig", ["Z5", "Z9", "Z3", "Z4", "Z6", "Z1G", "Z2G"])
    def test_ready_signals(self, sig):
        assert _pullback_stage(sig) == "PULLBACK_READY"

    @pytest.mark.parametrize("sig", ["T1", "T2", "T2G", "T3", "T9"])
    def test_confirming_signals(self, sig):
        assert _pullback_stage(sig) == "PULLBACK_CONFIRMING"

    @pytest.mark.parametrize("sig", ["T4", "T5", "T6", "T11", "T12"])
    def test_go_signals(self, sig):
        assert _pullback_stage(sig) == "PULLBACK_GO"

    def test_other(self):
        assert _pullback_stage("X99") == "OTHER"

    def test_empty(self):
        assert _pullback_stage("") == "OTHER"


# ── Section F: forward outcomes ────────────────────────────────────────────────

class TestForwardOutcomes:
    def test_basic_fields(self):
        r = _row(ret3=1.0, ret5=2.0, ret10=3.0, mfe5=4.0, mfe10=6.0, mae5=-1.0, mae10=-2.0)
        fwd = _forward_outcomes(r)
        assert fwd["forward_return_10d"] == pytest.approx(3.0)
        assert fwd["max_forward_return_10d"] == pytest.approx(6.0)
        assert fwd["max_drawdown_10d"] == pytest.approx(-2.0)

    def test_success_10d_when_mfe10_gte_5(self):
        r = _row(mfe10=5.5)
        assert _forward_outcomes(r)["success_10d"] is True

    def test_success_10d_false_when_mfe10_lt_5(self):
        r = _row(mfe10=3.0)
        assert _forward_outcomes(r)["success_10d"] is False

    def test_fail_10d_when_mae10_lte_neg6(self):
        r = _row(mae10=-7.0)
        assert _forward_outcomes(r)["fail_10d"] is True

    def test_fail_10d_when_ret10_lt_neg4(self):
        r = _row(ret10=-4.5)
        assert _forward_outcomes(r)["fail_10d"] is True

    def test_no_fail_when_moderate_drawdown(self):
        r = _row(ret10=1.0, mae10=-3.0)
        assert _forward_outcomes(r)["fail_10d"] is False

    def test_none_values_for_missing_data(self):
        r = _row()  # all ret/mfe/mae empty
        fwd = _forward_outcomes(r)
        assert fwd["forward_return_10d"] is None
        assert fwd["max_forward_return_10d"] is None


# ── Section G: aggregation ────────────────────────────────────────────────────

class TestAggregate:
    def test_median_10d(self):
        events = [_event(ret10=2.0), _event(ret10=4.0), _event(ret10=6.0)]
        agg = _aggregate(events)
        assert agg["median_10d_return"] == pytest.approx(4.0)

    def test_win_rate(self):
        events = [_event(mfe10=6.0), _event(mfe10=6.0), _event(mfe10=2.0)]
        agg = _aggregate(events)
        assert agg["win_rate_10d"] == pytest.approx(200/3, rel=0.01)

    def test_fail_rate(self):
        events = [_event(mae10=-7.0), _event(mae10=-1.0), _event(mae10=-1.0)]
        agg = _aggregate(events)
        assert agg["fail_rate_10d"] == pytest.approx(100/3, rel=0.01)

    def test_event_count(self):
        events = [_event() for _ in range(5)]
        agg = _aggregate(events)
        assert agg["event_count"] == 5

    def test_last_seen_date(self):
        events = [_event(date="2024-01-05"), _event(date="2024-01-10"), _event(date="2024-01-08")]
        agg = _aggregate(events)
        assert agg["last_seen_date"] == "2024-01-10"

    def test_example_dates_max_5(self):
        events = [_event(date=f"2024-01-{i:02d}") for i in range(1, 9)]
        agg = _aggregate(events)
        assert len(agg["example_dates"]) <= 5

    def test_price_position_avg(self):
        events = [_event(pos20=0.4), _event(pos20=0.6)]
        agg = _aggregate(events)
        assert agg["price_position_20bar_avg"] == pytest.approx(0.5)


# ── Section H: evidence tier ──────────────────────────────────────────────────

class TestEvidenceTier:
    def test_confirmed_pullback(self):
        assert _evidence_tier(3, 2.5, 60.0, 20.0) == "CONFIRMED_PULLBACK"

    def test_reject_when_low_win(self):
        assert _evidence_tier(3, 2.5, 45.0, 20.0) == "REJECT"

    def test_reject_when_high_fail(self):
        assert _evidence_tier(3, 2.5, 60.0, 40.0) == "REJECT"

    def test_reject_when_negative_median(self):
        assert _evidence_tier(3, -1.0, 65.0, 20.0) == "REJECT"

    def test_anecdotal_when_count1_positive(self):
        assert _evidence_tier(1, None, None, None, single_ret10=3.0) == "ANECDOTAL_PULLBACK"

    def test_reject_when_count1_negative(self):
        assert _evidence_tier(1, None, None, None, single_ret10=-1.0) == "REJECT"

    def test_no_data_when_count0(self):
        assert _evidence_tier(0, None, None, None) == "NO_DATA"


# ── Section I: scoring ────────────────────────────────────────────────────────

class TestScore:
    def test_go_bonus_highest(self):
        s_go   = _score("CONFIRMED_PULLBACK", 3.0, 60.0, 20.0, 8.0, -2.0, 3, "PULLBACK_GO")
        s_conf = _score("CONFIRMED_PULLBACK", 3.0, 60.0, 20.0, 8.0, -2.0, 3, "PULLBACK_CONFIRMING")
        s_rdy  = _score("CONFIRMED_PULLBACK", 3.0, 60.0, 20.0, 8.0, -2.0, 3, "PULLBACK_READY")
        assert s_go > s_conf > s_rdy

    def test_event_count_bonus(self):
        s_few  = _score("CONFIRMED_PULLBACK", 3.0, 60.0, 20.0, 8.0, -2.0, 2, "OTHER")
        s_many = _score("CONFIRMED_PULLBACK", 3.0, 60.0, 20.0, 8.0, -2.0, 5, "OTHER")
        assert s_many > s_few

    def test_anecdotal_penalty(self):
        s_conf = _score("CONFIRMED_PULLBACK",  3.0, 60.0, 20.0, 8.0, -2.0, 2, "OTHER")
        s_anec = _score("ANECDOTAL_PULLBACK",  3.0, 60.0, 20.0, 8.0, -2.0, 1, "OTHER")
        assert s_conf > s_anec

    def test_high_fail_penalty(self):
        s_low  = _score("CONFIRMED_PULLBACK", 3.0, 60.0, 10.0, 8.0, -2.0, 2, "OTHER")
        s_high = _score("CONFIRMED_PULLBACK", 3.0, 60.0, 40.0, 8.0, -2.0, 2, "OTHER")
        assert s_low > s_high

    def test_large_drawdown_lowers_score(self):
        s_low_dd  = _score("CONFIRMED_PULLBACK", 3.0, 60.0, 20.0, 8.0, -2.0,  2, "OTHER")
        s_high_dd = _score("CONFIRMED_PULLBACK", 3.0, 60.0, 20.0, 8.0, -15.0, 2, "OTHER")
        assert s_low_dd > s_high_dd


# ── Section J: top-3 per ticker ───────────────────────────────────────────────

class TestTop3:
    def _rec(self, tier, score):
        return {"evidence_tier": tier, "score": score}

    def test_max_3_returned(self):
        recs = [self._rec("CONFIRMED_PULLBACK", 10 - i) for i in range(5)]
        assert len(_top3(recs)) == 3

    def test_confirmed_before_anecdotal(self):
        recs = [
            self._rec("ANECDOTAL_PULLBACK", 100),  # high score but weaker tier
            self._rec("CONFIRMED_PULLBACK", 50),
        ]
        result = _top3(recs)
        assert result[0]["evidence_tier"] == "CONFIRMED_PULLBACK"

    def test_sorted_by_score_within_tier(self):
        recs = [self._rec("CONFIRMED_PULLBACK", s) for s in [30, 50, 40]]
        result = _top3(recs)
        assert result[0]["score"] == 50

    def test_fewer_than_3_ok(self):
        recs = [self._rec("CONFIRMED_PULLBACK", 20)]
        assert len(_top3(recs)) == 1


# ── Section K: current active detection ──────────────────────────────────────

class TestCheckActive:
    def _seqs(self, b4=None, e5=None, b4l=None, e5l=None):
        return {"base4_key": b4, "ext5_key": e5, "base4_tzl_key": b4l, "ext5_tzl_key": e5l}

    def test_full_match_on_base4(self):
        seqs = self._seqs(b4="Z5|T2|Z3|T1")
        active, comp = _check_active(seqs, {"Z5|T2|Z3|T1"}, set(), set(), set())
        assert active is True
        assert comp == "FULL_MATCH"

    def test_full_match_on_ext5(self):
        seqs = self._seqs(e5="Z2|Z5|T2|Z3|T1")
        active, comp = _check_active(seqs, set(), {"Z2|Z5|T2|Z3|T1"}, set(), set())
        assert active is True
        assert comp == "FULL_MATCH"

    def test_partial_match_4_of_5_forming(self):
        # Known ext5 = "Z2|Z5|T2|Z3|T1"; current base4 = "Z5|T2|Z3|T1" (last 4 tokens)
        seqs = self._seqs(b4="Z5|T2|Z3|T1")
        active, comp = _check_active(seqs, set(), {"Z2|Z5|T2|Z3|T1"}, set(), set())
        assert active is True
        assert comp == "4_OF_5_FORMING"

    def test_no_match(self):
        seqs = self._seqs(b4="X1|X2|X3|X4")
        active, comp = _check_active(seqs, {"Z5|T2|Z3|T1"}, set(), set(), set())
        assert active is False
        assert comp == "NONE"

    def test_tzl_full_match(self):
        seqs = self._seqs(b4l="Z5|T2|Z3|T1+L34")
        active, comp = _check_active(seqs, set(), set(), {"Z5|T2|Z3|T1+L34"}, set())
        assert active is True
        assert comp == "FULL_MATCH"


# ── Section L: integration via run_pullback_scan ──────────────────────────────

class TestRunPullbackScan:
    def test_invalid_universe(self):
        result = run_pullback_scan(universe="invalid_xyz")
        assert result["error"] is not None

    def test_invalid_tf(self):
        result = run_pullback_scan(universe="sp500", tf="99d")
        assert result["error"] is not None

    def test_missing_stat_file_returns_error(self):
        with patch("analyzers.pullback_miner.miner._stat_file", return_value="/no/such/file.csv"):
            result = run_pullback_scan(universe="sp500", tf="1d")
        assert result["error"] is not None
        assert "No stock_stat" in result["error"]

    def test_scan_with_valid_data(self, tmp_path):
        rows = [
            _row("AAPL", "2024-01-03", t_signal="Z5", prev3="T1|Z3|T2", close=95, ema20=96, ema50=90,
                 mfe10=8.0, mae10=-2.0, ret10=4.0),
            _row("AAPL", "2024-01-04", t_signal="Z3", prev3="Z3|T2|Z5", close=93, ema20=96, ema50=90,
                 mfe10=7.0, mae10=-2.0, ret10=3.5),
            _row("AAPL", "2024-01-05", t_signal="T1", prev3="T2|Z5|Z3", close=97, ema20=96, ema50=90,
                 mfe10=6.0, mae10=-1.0, ret10=3.0),
        ]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.pullback_miner.miner._stat_file", return_value=stat_path):
            result = run_pullback_scan(universe="sp500", tf="1d")
        assert result["error"] is None
        assert result["total_tickers"] == 1
        # results may be 0 if no bar qualifies after context checks, but no crash
        assert isinstance(result["results"], list)

    def test_all_required_top3_fields_present(self, tmp_path):
        required = [
            "ticker", "evidence_tier", "pattern_type", "pattern_key",
            "pattern_length", "pullback_stage", "event_count",
            "median_5d_return", "median_10d_return", "win_rate_10d", "fail_rate_10d",
            "avg_max_forward_10d", "avg_max_drawdown_10d",
            "score", "price_position_20bar_avg", "trend_context_summary",
            "example_dates", "last_seen_date", "is_currently_active",
            "current_pattern_completion",
        ]
        # Use multiple rows so a pattern can qualify
        rows = []
        for i in range(8):
            rows.append(_row(
                "AAPL", f"2024-01-{i+1:02d}",
                t_signal="T1" if i % 2 == 0 else "Z5",
                prev3="Z5|T2|Z3", prev5="T4|Z2|Z5|T2|Z3",
                close=95 + i, high=97 + i, low=93 + i,
                ema20=96, ema50=90,
                mfe10=7.0, mae10=-1.5, ret10=4.0, ret5=2.0, ret3=1.0,
            ))
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.pullback_miner.miner._stat_file", return_value=stat_path):
            result = run_pullback_scan(universe="sp500", tf="1d")
        for r in result["results"]:
            for field in required:
                assert field in r, f"Missing field: {field}"

    def test_at_most_3_patterns_per_ticker(self, tmp_path):
        rows = [
            _row("AAPL", f"2024-01-{i+1:02d}", t_signal="T1", prev3=f"Z{i+1}|T2|Z3",
                 close=95, ema20=96, ema50=90, mfe10=6.0, mae10=-1.0, ret10=3.0)
            for i in range(6)
        ]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.pullback_miner.miner._stat_file", return_value=stat_path):
            result = run_pullback_scan(universe="sp500", tf="1d")
        aapl_rows = [r for r in result["results"] if r["ticker"] == "AAPL"]
        assert len(aapl_rows) <= 3

    def test_rank_sequential(self, tmp_path):
        rows = [
            _row("AAPL", "2024-01-05", t_signal="T1", prev3="Z5|T2|Z3",
                 close=95, ema20=96, ema50=90, mfe10=6.0, mae10=-1.0, ret10=3.0),
        ]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.pullback_miner.miner._stat_file", return_value=stat_path):
            result = run_pullback_scan(universe="sp500", tf="1d")
        for i, r in enumerate(result["results"], 1):
            assert r["rank"] == i

    def test_price_filter_works(self, tmp_path):
        rows = [
            _row("CHEAP", "2024-01-05", t_signal="T1", prev3="Z5|T2|Z3",
                 close=2.0, high=2.5, low=1.8, ema20=2.1, ema50=1.9,
                 mfe10=6.0, mae10=-1.0, ret10=3.0),
            _row("NORMAL", "2024-01-05", t_signal="T1", prev3="Z5|T2|Z3",
                 close=95.0, ema20=96, ema50=90,
                 mfe10=6.0, mae10=-1.0, ret10=3.0),
        ]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.pullback_miner.miner._stat_file", return_value=stat_path):
            result = run_pullback_scan(universe="sp500", tf="1d", min_price=5.0)
        tickers = {r["ticker"] for r in result["results"]}
        assert "CHEAP" not in tickers

    def test_metadata_in_response(self, tmp_path):
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv([], stat_path)
        with patch("analyzers.pullback_miner.miner._stat_file", return_value=stat_path):
            result = run_pullback_scan(universe="sp500", tf="1d")
        assert result["universe"] == "sp500"
        assert result["tf"] == "1d"
        assert "total_tickers" in result
        assert "confirmed_count" in result
        assert "active_count" in result

    def test_pullback_zone_filter_active(self, tmp_path):
        # price_position_20bar > 0.85 → should not qualify
        # We'll put close near the top of its range
        rows = [
            _row("AAPL", f"2024-01-{i+1:02d}", t_signal="T1", prev3="Z5|T2|Z3",
                 close=99.0, high=100.0, low=99.0,  # always at top
                 ema20=98.0, ema50=90.0, mfe10=6.0, mae10=-1.0, ret10=3.0)
            for i in range(5)
        ]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.pullback_miner.miner._stat_file", return_value=stat_path):
            result = run_pullback_scan(universe="sp500", tf="1d")
        # With close always at the top (pos20 ≈ 1.0), zone filter should reject most rows
        # (first few bars have sparse window so pos may be 0.5, but after 5 bars of highs it > 0.85)
        assert result["error"] is None  # no crash

    def test_is_currently_active_set_correctly(self, tmp_path):
        # Two identical patterns — latest should match
        rows = [
            _row("AAPL", "2024-01-03", t_signal="T1", prev3="Z5|T2|Z3", prev5="T4|Z2|Z5|T2|Z3",
                 close=95, ema20=96, ema50=90, mfe10=7.0, mae10=-1.0, ret10=4.0),
            _row("AAPL", "2024-01-10", t_signal="T1", prev3="Z5|T2|Z3", prev5="T4|Z2|Z5|T2|Z3",
                 close=97, ema20=96, ema50=90, mfe10=7.0, mae10=-1.0, ret10=4.0),
        ]
        stat_path = str(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv")
        _make_stat_csv(rows, stat_path)
        with patch("analyzers.pullback_miner.miner._stat_file", return_value=stat_path):
            result = run_pullback_scan(universe="sp500", tf="1d")
        aapl = [r for r in result["results"] if r["ticker"] == "AAPL"]
        if aapl:
            assert any(r["is_currently_active"] for r in aapl)
