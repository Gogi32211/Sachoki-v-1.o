"""Tests for split_universe — stock-only filter v2, lifecycle, cache versioning."""
import sys
import os
from datetime import date as date_t, timedelta, datetime as dt_cls
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from split_universe import (
    is_stock_like_split_event,
    classify_split_lifecycle,
    normalize_split_symbol,
    SplitUniverseService,
    SplitUniverseResult,
    SPLIT_CACHE_VERSION,
    SPLIT_HISTORY_DAYS,
    SPLIT_FUTURE_DAYS,
    _PRODUCT_PHRASES,
    _ISSUER_BRANDS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(company_name="", security_name="", asset_type="", issue_type="", ticker="TEST"):
    return {
        "ticker":       ticker,
        "companyName":  company_name,
        "securityName": security_name,
        "assetType":    asset_type,
        "issueType":    issue_type,
    }


def _excluded(row):
    ok, reason = is_stock_like_split_event(row)
    return not ok, reason


def _included(row):
    ok, _ = is_stock_like_split_event(row)
    return ok


# ── Ratio parser ──────────────────────────────────────────────────────────────

def test_ratio_1_colon_10():
    assert SplitUniverseService._parse_ratio("1:10") == 10.0

def test_ratio_1_for_10():
    assert SplitUniverseService._parse_ratio("1-for-10") == 10.0

def test_ratio_1_slash_20():
    assert SplitUniverseService._parse_ratio("1/20") == 20.0

def test_ratio_forward_split_lt_1():
    r = SplitUniverseService._parse_ratio("10:1")
    assert r == 0.1  # filtered out by MIN_RATIO >= 2.0

def test_ratio_forward_filtered():
    svc = SplitUniverseService()
    assert svc._parse_ratio("10:1") < svc.MIN_RATIO


# ── Stock-only filter: EXCLUDED cases ────────────────────────────────────────

def test_direxion_bear_excluded():
    exc, reason = _excluded(_row("Direxion Daily Semiconductor Bear 3X Shares"))
    assert exc, reason
    # Direxion is caught by issuer_keyword OR bear 3x product_phrase — either is correct
    assert reason is not None

def test_proshares_excluded():
    exc, reason = _excluded(_row("ProShares UltraPro Short QQQ"))
    assert exc, reason

def test_ishares_etf_excluded():
    exc, reason = _excluded(_row("iShares Bitcoin Trust ETF"))
    assert exc, reason

def test_yieldmax_etf_excluded():
    exc, reason = _excluded(_row("YieldMax XYZ Option Income Strategy ETF"))
    assert exc, reason

def test_graniteshares_excluded():
    exc, reason = _excluded(_row("GraniteShares 2x Long NVDA Daily ETF"))
    assert exc, reason

def test_etf_suffix_excluded():
    assert not _included(_row("Some Random ETF"))

def test_etn_excluded():
    assert not _included(_row("Barclays ETN+ FI Enhanced Europe 50 ETN"))

def test_fund_excluded():
    assert not _included(_row("Templeton Global Income Fund"))

def test_closed_end_fund_excluded():
    assert not _included(_row("Royce Total Return Closed-End Fund"))

def test_trust_excluded():
    assert not _included(_row("SPDR Gold Trust"))

def test_preferred_excluded():
    assert not _included(_row("Citigroup Capital XIII Preferred Securities"))

def test_warrant_excluded():
    assert not _included(_row("Acme Holdings Warrants"))

def test_rights_excluded():
    assert not _included(_row("XYZ Rights Offering"))

def test_units_excluded():
    assert not _included(_row("SomePLP Units"))

def test_inverse_excluded():
    assert not _included(_row("Inverse VIX Short Term Futures ETF"))

def test_senior_notes_excluded():
    assert not _included(_row("Senior Notes due 2028"))

def test_2x_long_excluded():
    assert not _included(_row("GraniteShares 2x Long AAPL Daily ETF"))

def test_3x_short_excluded():
    assert not _included(_row("3x Short NASDAQ ETF"))

def test_ultrapro_excluded():
    assert not _included(_row("ProShares UltraPro QQQ"))

def test_ultrashort_excluded():
    assert not _included(_row("ProShares UltraShort Dow30"))

def test_preferred_stock_excluded():
    assert not _included(_row("Preferred Stock Series A"))

def test_depositary_shares_excluded():
    assert not _included(_row("ADS Depositary Shares"))

def test_grayscale_trust_excluded():
    assert not _included(_row("Grayscale Bitcoin Trust"))

def test_wisdomtree_excluded():
    assert not _included(_row("WisdomTree U.S. Quality Dividend Growth ETF"))

def test_closed_end_no_hyphen_excluded():
    assert not _included(_row("Royce Closed End Fund Inc"))

def test_asset_type_etf_excluded():
    ok, reason = is_stock_like_split_event(_row(company_name="SomeName Inc", asset_type="ETF"))
    assert not ok
    assert "explicit_type" in reason

def test_issue_type_fund_excluded():
    ok, reason = is_stock_like_split_event(_row(company_name="SomeName Inc", issue_type="fund"))
    assert not ok
    assert "explicit_type" in reason

def test_security_name_etf_excluded():
    assert not _included(_row(security_name="Global X Uranium ETF"))


# ── Stock-only filter: INCLUDED (false-positive prevention) ──────────────────

def test_plain_corp_included():
    assert _included(_row("ABC Corp"))

def test_therapeutics_included():
    assert _included(_row("XYZ Therapeutics Inc."))

def test_bear_creek_mining_included():
    """'Bear' alone must not trigger exclusion — only 'bear 2x/3x' compounds."""
    assert _included(_row("Bear Creek Mining Corp"))

def test_ultragenyx_included():
    """'Ultra' alone must not trigger exclusion — only ultrashort/ultrapro."""
    assert _included(_row("Ultragenyx Pharmaceutical Inc."))

def test_bond_street_included():
    """'Bond' standalone must not trigger exclusion."""
    assert _included(_row("Bond Street Holdings Inc."))

def test_notable_labs_included():
    """'Note' as prefix of a word (notable) must not trigger exclusion."""
    assert _included(_row("Notable Labs Ltd"))

def test_banknote_included():
    """'Note' inside compound word 'banknote' must not trigger exclusion."""
    assert _included(_row("Banknote Corp"))

def test_trustco_bank_included():
    """'TrustCo' is one word — \\btrust\\b must NOT match 'trustco'."""
    assert _included(_row("TrustCo Bank Corp"))

def test_empty_name_included():
    assert _included(_row(company_name="", security_name="", asset_type="", issue_type=""))

def test_none_fields_included():
    row = {"ticker": "TEST", "companyName": None, "securityName": None,
           "assetType": None, "issueType": None}
    assert _included(row)

def test_missing_fields_included():
    assert _included({"ticker": "TEST"})

def test_case_insensitive_exclusion():
    assert not _included(_row("direxion daily technology bear 3x shares"))


# ── Return type: is tuple[bool, str|None] ────────────────────────────────────

def test_return_tuple_included():
    ok, reason = is_stock_like_split_event(_row("ABC Corp"))
    assert ok is True
    assert reason is None

def test_return_tuple_excluded():
    ok, reason = is_stock_like_split_event(_row("iShares Bitcoin Trust ETF"))
    assert ok is False
    assert reason is not None
    assert isinstance(reason, str)

def test_excluded_reason_has_rule_type():
    _, reason = is_stock_like_split_event(_row("Direxion Daily Bear 3X ETF"))
    assert any(rt in reason for rt in ("explicit_type", "product_phrase", "issuer_keyword", "security_class"))


# ── Keyword list not empty ────────────────────────────────────────────────────

def test_product_phrases_not_empty():
    assert len(_PRODUCT_PHRASES) >= 10

def test_issuer_brands_not_empty():
    assert len(_ISSUER_BRANDS) >= 10


# ── Lifecycle classification ──────────────────────────────────────────────────

def _lc(days_offset, ratio=5.0):
    """Helper: compute lifecycle for a split at today + days_offset (negative=future)."""
    today = date_t.today()
    split_date = today - timedelta(days=days_offset)  # days_offset positive = past
    return classify_split_lifecycle(split_date.isoformat(), ratio, today)

def test_lifecycle_pre_split():
    lc = _lc(-3)  # 3 days in future
    assert lc["phase"] == "PRE_SPLIT"
    assert lc["wave"]  == "PRE"
    assert lc["days_offset"] == -3

def test_lifecycle_split_day():
    lc = _lc(0)
    assert lc["phase"] == "SPLIT_DAY"
    assert lc["wave"]  == "D0"
    assert lc["days_offset"] == 0

def test_lifecycle_wave1():
    lc = _lc(3)
    assert lc["phase"] == "WAVE_1"
    assert lc["wave"]  == "W1"

def test_lifecycle_wave2():
    lc = _lc(12)
    assert lc["phase"] == "WAVE_2_SETUP"
    assert lc["wave"]  == "W2"

def test_lifecycle_wave3():
    lc = _lc(30)
    assert lc["phase"] == "WAVE_3_SETUP"
    assert lc["wave"]  == "W3"

def test_lifecycle_post_monitor():
    lc = _lc(55)
    assert lc["phase"] == "POST_MONITOR"
    assert lc["wave"]  == "POST"

def test_lifecycle_extended_monitor_high_ratio():
    lc = _lc(80, ratio=20.0)  # ratio>=20 → watch_days=90
    assert lc["phase"] == "EXTENDED_MONITOR"
    assert lc["wave"]  == "EXT"

def test_lifecycle_expired():
    lc = _lc(100)  # ratio=5 → watch_days=60 → expired
    assert lc["phase"] == "EXPIRED"

def test_lifecycle_upcoming_far():
    lc = _lc(-10)  # 10 days in future → UPCOMING_FAR
    assert lc["phase"] == "UPCOMING_FAR"

def test_lifecycle_watch_days_ratio_lt10():
    lc = _lc(0, ratio=5.0)
    assert lc["watch_days"] == 60

def test_lifecycle_watch_days_ratio_10():
    lc = _lc(0, ratio=10.0)
    assert lc["watch_days"] == 75

def test_lifecycle_watch_days_ratio_20():
    lc = _lc(0, ratio=20.0)
    assert lc["watch_days"] == 90

def test_lifecycle_heat_wave1():
    lc = _lc(3, ratio=5.0)
    assert lc["heat_score"] == 5  # 4 (WAVE_1) + 1 (ratio>=5)

def test_lifecycle_heat_clamped():
    # WAVE_1(4) + ratio>=20(3) = 7; clamp 0-10 applies
    lc = _lc(3, ratio=25.0)
    assert lc["heat_score"] == 7
    # heat score always within [0, 10]
    lc2 = _lc(0, ratio=20.0)
    assert 0 <= lc2["heat_score"] <= 10

def test_lifecycle_next_wave_pre():
    lc = _lc(-1)
    assert lc["next_wave_label"] == "WAVE_1"
    assert lc["next_wave_start_date"] is not None

def test_lifecycle_next_wave_w1():
    lc = _lc(5)
    assert lc["next_wave_label"] == "WAVE_2_SETUP"

def test_lifecycle_next_wave_none_post():
    lc = _lc(55)
    assert lc["next_wave_label"] is None

def test_lifecycle_watch_until_str():
    lc = _lc(0, ratio=5.0)
    today = date_t.today()
    assert lc["watch_until"] == (today + timedelta(days=60)).isoformat()

def test_lifecycle_notes_field():
    lc = _lc(3, ratio=10.0)
    assert "ratio=" in lc["notes"]
    assert "phase=" in lc["notes"]


# ── Dedupe: prefer active lifecycle ──────────────────────────────────────────

def test_dedupe_active_beats_expired():
    today = date_t.today()
    active_row = {
        "ticker": "FOO", "split_date": (today - timedelta(days=5)).isoformat(),
        "ratio": 5.0, "ratio_str": "1:5", "source": "nasdaq",
        "companyName": "Foo Corp", "securityName": "", "assetType": "", "issueType": "",
        "phase": "WAVE_1", "wave": "W1", "days_offset": 5,
    }
    expired_row = {
        "ticker": "FOO", "split_date": (today - timedelta(days=100)).isoformat(),
        "ratio": 5.0, "ratio_str": "1:5", "source": "nasdaq",
        "companyName": "Foo Corp", "securityName": "", "assetType": "", "issueType": "",
        "phase": "EXPIRED", "wave": "EXPIRED", "days_offset": 100,
    }
    result = SplitUniverseService._dedupe_by_ticker([expired_row, active_row])
    assert len(result) == 1
    assert result[0]["phase"] == "WAVE_1"

def test_dedupe_closest_daysoffset_wins():
    today = date_t.today()
    near = {
        "ticker": "BAR", "split_date": today.isoformat(), "ratio": 5.0,
        "phase": "SPLIT_DAY", "wave": "D0", "days_offset": 0,
    }
    far = {
        "ticker": "BAR", "split_date": (today - timedelta(days=15)).isoformat(), "ratio": 5.0,
        "phase": "WAVE_2_SETUP", "wave": "W2", "days_offset": 15,
    }
    result = SplitUniverseService._dedupe_by_ticker([far, near])
    assert len(result) == 1
    assert result[0]["days_offset"] == 0


# ── Cache versioning ──────────────────────────────────────────────────────────

def test_cache_version_constant_exists():
    assert SPLIT_CACHE_VERSION == "split_lifecycle_stock_filter_v2"

def test_cache_version_mismatch_forces_rebuild():
    from unittest.mock import patch
    from datetime import datetime as dt_cls
    svc = SplitUniverseService()
    svc._cache         = [{"ticker": "OLD"}]
    svc._cache_time    = dt_cls.now()
    svc._cache_version = "old_version"     # deliberate mismatch
    assert not svc._is_cache_valid()

def test_cache_valid_with_correct_version():
    from unittest.mock import patch
    from datetime import datetime as dt_cls
    svc = SplitUniverseService()
    svc._cache         = [{"ticker": "NEW"}]
    svc._cache_time    = dt_cls.now()
    svc._cache_version = SPLIT_CACHE_VERSION
    assert svc._is_cache_valid()

def test_cache_invalid_after_expiry():
    from datetime import datetime as dt_cls
    svc = SplitUniverseService()
    svc._cache         = [{"ticker": "X"}]
    svc._cache_version = SPLIT_CACHE_VERSION
    svc._cache_time    = dt_cls(2000, 1, 1)  # ancient timestamp
    assert not svc._is_cache_valid()


# ── A: Single source of truth ─────────────────────────────────────────────────

def _make_svc_with_rows(rows):
    """Return a SplitUniverseService with _fetch_nasdaq_splits mocked to return rows."""
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    return svc, result


def _active_split_row(ticker, days_ago=5, ratio=5.0, company_name="Acme Corp"):
    today = date_t.today()
    split_date = (today - timedelta(days=days_ago)).isoformat()
    return {
        "ticker":       ticker,
        "split_date":   split_date,
        "ratio":        ratio,
        "ratio_str":    f"1:{int(ratio)}",
        "source":       "nasdaq",
        "companyName":  company_name,
        "securityName": "",
        "assetType":    "",
        "issueType":    "",
    }


def test_single_source_same_tickers_two_calls():
    """Two callers get identical ticker lists from the same service instance."""
    rows = [_active_split_row("AAPL"), _active_split_row("TSLA")]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        call1 = svc.get_split_tickers(force_refresh=True)
        call2 = svc.get_split_tickers()  # cache hit
    assert call1 == call2

def test_single_source_rows_and_tickers_consistent():
    """tickers in SplitUniverseResult == set of ticker fields in rows."""
    rows = [_active_split_row("GOOG"), _active_split_row("MSFT")]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    ticker_set_from_rows = {r["ticker"] for r in result.rows}
    assert set(result.tickers) == ticker_set_from_rows

def test_split_service_singleton_importable():
    """Module-level singleton split_service exists and is a SplitUniverseService."""
    from split_universe import split_service
    assert isinstance(split_service, SplitUniverseService)


# ── B: Symbol normalisation ───────────────────────────────────────────────────

def test_normalize_lowercase_to_upper():
    assert normalize_split_symbol("aapl") == "AAPL"

def test_normalize_strips_outer_whitespace():
    assert normalize_split_symbol("  TSLA  ") == "TSLA"

def test_normalize_collapses_internal_whitespace():
    assert normalize_split_symbol("BRK  B") == "BRK B"

def test_normalize_empty_string():
    assert normalize_split_symbol("") == ""

def test_normalize_none():
    assert normalize_split_symbol(None) == ""

def test_normalize_dotted_ticker_preserved():
    assert normalize_split_symbol("brk.b") == "BRK.B"

def test_normalize_mixed_whitespace():
    assert normalize_split_symbol("\tAMZN\n") == "AMZN"

def test_dedupe_normalised_tickers_unique():
    """Rows with same ticker under different casings dedupe to one ticker."""
    rows = [
        _active_split_row("aapl"),
        _active_split_row("AAPL"),
        _active_split_row("Aapl"),
    ]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert result.tickers.count("AAPL") == 1
    assert len(result.tickers) == 1

def test_tickers_sorted():
    """Returned tickers list is lexicographically sorted."""
    rows = [_active_split_row("TSLA"), _active_split_row("AAPL"), _active_split_row("MSFT")]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert result.tickers == sorted(result.tickers)


# ── C: Stock-only filter counters ─────────────────────────────────────────────

def test_filtered_non_stock_counter():
    """filtered_non_stock counts rows rejected by is_stock_like_split_event."""
    rows = [
        _active_split_row("AAPL"),                          # stock → included
        _active_split_row("XYZ", company_name="iShares Bitcoin Trust ETF"),  # ETF → excluded
        _active_split_row("FOO", company_name="Direxion Bear 3x Fund"),      # fund → excluded
    ]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert result.filtered_non_stock == 2

def test_stock_like_events_counter():
    """stock_like_events counts rows that pass the stock filter."""
    rows = [
        _active_split_row("AAPL"),
        _active_split_row("GOOG"),
        _active_split_row("ETF1", company_name="Some ETF"),
    ]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert result.stock_like_events == 2

def test_missing_symbol_not_in_tickers():
    """Rows with blank/None ticker are excluded silently."""
    rows = [
        _active_split_row("AAPL"),
        {"ticker": "", "split_date": date_t.today().isoformat(), "ratio": 5.0,
         "ratio_str": "1:5", "source": "nasdaq", "companyName": "Unknown Corp",
         "securityName": "", "assetType": "", "issueType": ""},
    ]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert "" not in result.tickers

def test_excluded_examples_populated():
    """excluded_examples contains details for filtered-out rows."""
    rows = [
        _active_split_row("ETF1", company_name="iShares Some ETF"),
    ]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert len(result.excluded_examples) >= 1
    ex = result.excluded_examples[0]
    assert "ticker" in ex and "reason" in ex

def test_excluded_examples_max_20():
    """excluded_examples is capped at 20 entries."""
    rows = [_active_split_row(f"ETF{i}", company_name="iShares Some Fund") for i in range(30)]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert len(result.excluded_examples) <= 20


# ── D: Date window ─────────────────────────────────────────────────────────────

def test_date_window_start_and_end():
    """start_date and end_date match SPLIT_HISTORY_DAYS / SPLIT_FUTURE_DAYS."""
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=[]):
        result = svc.get_split_universe_result(force_refresh=True)
    today = date_t.today()
    assert result.start_date == (today - timedelta(days=SPLIT_HISTORY_DAYS)).isoformat()
    assert result.end_date   == (today + timedelta(days=SPLIT_FUTURE_DAYS)).isoformat()

def test_date_window_consistent_across_instances():
    """Two fresh service instances produce the same date window."""
    svc1 = SplitUniverseService()
    svc2 = SplitUniverseService()
    with patch.object(svc1, "_fetch_nasdaq_splits", return_value=[]):
        r1 = svc1.get_split_universe_result(force_refresh=True)
    with patch.object(svc2, "_fetch_nasdaq_splits", return_value=[]):
        r2 = svc2.get_split_universe_result(force_refresh=True)
    assert r1.start_date == r2.start_date
    assert r1.end_date   == r2.end_date


# ── E: Ratio parsing ──────────────────────────────────────────────────────────

def test_ratio_parse_1_colon_5():
    assert SplitUniverseService._parse_ratio("1:5") == 5.0

def test_ratio_parse_1_for_4():
    assert SplitUniverseService._parse_ratio("1-for-4") == 4.0

def test_ratio_parse_1_slash_8():
    assert SplitUniverseService._parse_ratio("1/8") == 8.0

def test_ratio_parse_formats_equivalent():
    """Same reverse split expressed as colon / for / slash gives same result."""
    r_colon = SplitUniverseService._parse_ratio("1:10")
    r_for   = SplitUniverseService._parse_ratio("1-for-10")
    r_slash = SplitUniverseService._parse_ratio("1/10")
    assert r_colon == r_for == r_slash == 10.0

def test_ratio_below_min_not_in_results():
    """A forward split (ratio < MIN_RATIO) does not appear in final tickers."""
    rows = [
        _active_split_row("AAPL"),    # ratio=5 (reverse)
        {**_active_split_row("SPLIT2"), "ratio": 1.5},  # below MIN_RATIO
    ]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert "SPLIT2" not in result.tickers

def test_ratio_parse_failed_count():
    """ratio_parse_failed_count tracks rows with ratio < MIN_RATIO after parsing."""
    rows = [
        _active_split_row("AAPL"),
        {**_active_split_row("FWD"), "ratio": 0.5},   # forward split
    ]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert result.ratio_parse_failed_count >= 1


# ── F: Cache behaviour ────────────────────────────────────────────────────────

def test_same_cache_key_for_same_params():
    """Two calls with same date window produce identical cache_key."""
    svc1 = SplitUniverseService()
    svc2 = SplitUniverseService()
    with patch.object(svc1, "_fetch_nasdaq_splits", return_value=[]):
        r1 = svc1.get_split_universe_result(force_refresh=True)
    with patch.object(svc2, "_fetch_nasdaq_splits", return_value=[]):
        r2 = svc2.get_split_universe_result(force_refresh=True)
    assert r1.cache_key == r2.cache_key

def test_cache_key_contains_version():
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=[]):
        result = svc.get_split_universe_result(force_refresh=True)
    assert SPLIT_CACHE_VERSION in result.cache_key

def test_force_refresh_bypasses_cache():
    """force_refresh=True causes _fetch_nasdaq_splits to be called again."""
    rows = [_active_split_row("AAPL")]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows) as m:
        svc.get_split_universe_result(force_refresh=True)
        svc.get_split_universe_result(force_refresh=False)  # should use cache
        svc.get_split_universe_result(force_refresh=True)   # should fetch again
    assert m.call_count == 2

def test_cache_hit_returns_same_object():
    """Second call without force_refresh returns cached _last_result."""
    rows = [_active_split_row("AAPL")]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        r1 = svc.get_split_universe_result(force_refresh=True)
        r2 = svc.get_split_universe_result(force_refresh=False)
    assert r1 is r2

def test_get_split_universe_delegates_to_result():
    """get_split_universe() returns .rows from get_split_universe_result()."""
    rows = [_active_split_row("MSFT")]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
        rows_direct = svc.get_split_universe()
    assert rows_direct == result.rows

def test_generated_at_is_recent():
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=[]):
        result = svc.get_split_universe_result(force_refresh=True)
    gen = dt_cls.fromisoformat(result.generated_at)
    assert (dt_cls.now() - gen).total_seconds() < 5


# ── G: SplitUniverseResult dataclass ─────────────────────────────────────────

def test_result_dataclass_has_required_fields():
    r = SplitUniverseResult()
    for attr in ("tickers", "rows", "total_events", "reverse_split_events",
                 "stock_like_events", "filtered_non_stock", "missing_symbol",
                 "duplicate_symbols_removed", "ratio_parse_failed_count",
                 "date_mode", "start_date", "end_date", "source",
                 "cache_key", "generated_at", "excluded_examples"):
        assert hasattr(r, attr), f"missing field: {attr}"

def test_result_tickers_list_default_empty():
    r = SplitUniverseResult()
    assert r.tickers == []

def test_result_duplicate_symbols_removed_tracked():
    """duplicate_symbols_removed > 0 when same ticker appears in multiple active rows."""
    today = date_t.today()
    rows = [
        _active_split_row("DUPL", days_ago=3),
        _active_split_row("DUPL", days_ago=10),
    ]
    svc = SplitUniverseService()
    with patch.object(svc, "_fetch_nasdaq_splits", return_value=rows):
        result = svc.get_split_universe_result(force_refresh=True)
    assert result.duplicate_symbols_removed >= 1
    assert result.tickers.count("DUPL") == 1


if __name__ == "__main__":
    tests = [
        test_ratio_1_colon_10, test_ratio_1_for_10, test_ratio_1_slash_20,
        test_ratio_forward_split_lt_1, test_ratio_forward_filtered,
        test_direxion_bear_excluded, test_proshares_excluded, test_ishares_etf_excluded,
        test_yieldmax_etf_excluded, test_graniteshares_excluded, test_etf_suffix_excluded,
        test_etn_excluded, test_fund_excluded, test_closed_end_fund_excluded,
        test_trust_excluded, test_preferred_excluded, test_warrant_excluded,
        test_rights_excluded, test_units_excluded, test_inverse_excluded,
        test_senior_notes_excluded, test_2x_long_excluded, test_3x_short_excluded,
        test_ultrapro_excluded, test_ultrashort_excluded,
        test_preferred_stock_excluded, test_depositary_shares_excluded,
        test_grayscale_trust_excluded, test_wisdomtree_excluded,
        test_closed_end_no_hyphen_excluded,
        test_asset_type_etf_excluded, test_issue_type_fund_excluded,
        test_security_name_etf_excluded,
        test_plain_corp_included, test_therapeutics_included,
        test_bear_creek_mining_included, test_ultragenyx_included,
        test_bond_street_included, test_notable_labs_included,
        test_banknote_included, test_trustco_bank_included,
        test_empty_name_included, test_none_fields_included,
        test_missing_fields_included, test_case_insensitive_exclusion,
        test_return_tuple_included, test_return_tuple_excluded,
        test_excluded_reason_has_rule_type,
        test_product_phrases_not_empty, test_issuer_brands_not_empty,
        test_lifecycle_pre_split, test_lifecycle_split_day,
        test_lifecycle_wave1, test_lifecycle_wave2, test_lifecycle_wave3,
        test_lifecycle_post_monitor, test_lifecycle_extended_monitor_high_ratio,
        test_lifecycle_expired, test_lifecycle_upcoming_far,
        test_lifecycle_watch_days_ratio_lt10, test_lifecycle_watch_days_ratio_10,
        test_lifecycle_watch_days_ratio_20,
        test_lifecycle_heat_wave1, test_lifecycle_heat_clamped,
        test_lifecycle_next_wave_pre, test_lifecycle_next_wave_w1,
        test_lifecycle_next_wave_none_post, test_lifecycle_watch_until_str,
        test_lifecycle_notes_field,
        test_dedupe_active_beats_expired, test_dedupe_closest_daysoffset_wins,
        test_cache_version_constant_exists, test_cache_version_mismatch_forces_rebuild,
        test_cache_valid_with_correct_version, test_cache_invalid_after_expiry,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} tests passed")
