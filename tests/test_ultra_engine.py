"""Tests for ULTRA v2 — Turbo-only Stage 1 + lazy subset enrichment Stage 2.

Hard rules verified:
  • no new score / category / context_score field is introduced
  • canonical stock_stat CSV is never overwritten by ULTRA
  • subset CSV is extracted from canonical when present, fresh-generated otherwise
  • secondary-module failure produces a warning, never empties the table
  • enrichment is incremental (a second enrich does not erase the first)
  • Turbo score / category fields are byte-identical to the canonical Turbo
"""
from __future__ import annotations

import os
import sys
from typing import Any

import pytest

# Ensure backend/ is importable (mirrors the other test files).
_backend = os.path.join(os.path.dirname(__file__), "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import ultra_orchestrator as uo


# ── Helpers ──────────────────────────────────────────────────────────────────

def _turbo_row(ticker="AAPL", score=42.0, tz_bull=1, **extra) -> dict:
    base = {
        "ticker": ticker,
        "turbo_score": score,
        "turbo_score_n3":  score, "turbo_score_n5":  score, "turbo_score_n10": score,
        "tz_bull": tz_bull,
        "last_price": 150.0,
        "avg_vol": 1_000_000.0,
        "rsi": 55.0, "cci": 45.0,
        "profile_score": 7,
        "profile_category": "BUILDING",
        "profile_name": "demo",
        "best_sig": 1,
    }
    base.update(extra)
    return base


def _stub_turbo_engine(monkeypatch, rows=None) -> list:
    rows = rows or [_turbo_row("AAPL"), _turbo_row("MSFT", score=10.0, tz_bull=0)]

    def fake_run_turbo_scan(*_a, **_kw):
        return len(rows)

    monkeypatch.setattr("turbo_engine.run_turbo_scan",          fake_run_turbo_scan)
    monkeypatch.setattr("turbo_engine.get_turbo_results",       lambda *a, **kw: list(rows))
    monkeypatch.setattr("turbo_engine.get_last_turbo_scan_time", lambda *a, **kw: "2026-05-08T07:00:00")
    monkeypatch.setattr("turbo_engine.get_turbo_progress",       lambda *a, **kw: {"done": len(rows), "total": len(rows)})
    return rows


def _reset_cache():
    uo._ultra_results_cache.clear()


# ── Stage 1 — Turbo only ─────────────────────────────────────────────────────

def test_stage1_runs_turbo_only_and_returns_unenriched_rows(monkeypatch):
    _reset_cache()
    rows = _stub_turbo_engine(monkeypatch)

    # Stage 1 must NOT call any secondary reader / stock_stat generator
    def _should_not_run(*a, **kw):
        raise AssertionError("Stage 1 must not invoke secondary modules")
    monkeypatch.setattr(uo, "_read_tz_wlnbb_latest_from", _should_not_run)
    monkeypatch.setattr("tz_intelligence.scanner.run_intelligence_scan", _should_not_run)
    monkeypatch.setattr("analyzers.pullback_miner.miner.run_pullback_scan", _should_not_run)
    monkeypatch.setattr("analyzers.rare_reversal.miner.run_rare_reversal_scan", _should_not_run)

    resp = uo.run_ultra_scan_job(universe="sp500", tf="1d")
    assert [r["ticker"] for r in resp["results"]] == [r["ticker"] for r in rows]
    for r in resp["results"]:
        assert r["ultra_enriched"] is False
        assert r["tz_wlnbb"]      is None
        assert r["tz_intel"]      is None
        assert r["abr"]           is None
        assert r["pullback"]      is None
        assert r["rare_reversal"] is None
        assert r["ultra_sources"] == {
            "has_turbo": True, "has_tz_wlnbb": False, "has_tz_intel": False,
            "has_pullback": False, "has_rare_reversal": False,
        }
    assert resp["meta"]["sources"]["turbo"]["ok"] is True
    assert resp["meta"]["phase"] == "turbo_done"


def test_stage1_applies_profile_playbook_enrichment(monkeypatch):
    """ULTRA Stage 1 must apply the same enrich_row_with_profile that
    /api/turbo-scan applies. Without this, PF Score / Category /
    sweet_spot_active / late_warning come through empty and the UI shows
    blank PF Score and Category columns."""
    _reset_cache()
    rows = _stub_turbo_engine(monkeypatch, rows=[
        # turbo_engine returns the raw row without profile fields
        _turbo_row("AAPL", profile_score=None, profile_category=None,
                   profile_name=None, sweet_spot_active=None, late_warning=None,
                   signal_score=None),
    ])

    captured: dict = {}

    def fake_enrich(row, universe):
        captured["universe"] = universe
        out = dict(row)
        out["profile_score"]      = 42
        out["profile_category"]   = "SWEET_SPOT"
        out["profile_name"]       = "playbook_v1"
        out["sweet_spot_active"]  = True
        out["late_warning"]       = False
        out["signal_score"]       = 71
        out["already_extended"]   = False
        return out
    monkeypatch.setattr("profile_playbook.enrich_row_with_profile", fake_enrich)

    uo.run_ultra_scan_job(universe="sp500", tf="1d")
    out = uo.get_ultra_results("sp500", "1d")["results"]
    assert captured.get("universe") == "sp500"
    aapl = next(r for r in out if r["ticker"] == "AAPL")
    assert aapl["profile_score"]      == 42
    assert aapl["profile_category"]   == "SWEET_SPOT"
    assert aapl["profile_name"]       == "playbook_v1"
    assert aapl["sweet_spot_active"]  is True
    assert aapl["late_warning"]       is False
    assert aapl["signal_score"]       == 71
    assert aapl["already_extended"]   is False


def test_enrich_does_not_erase_profile_fields(monkeypatch, canonical_csv):
    """Stage 2 enrich must only attach tz_wlnbb / tz_intel / abr / pullback /
    rare_reversal — never overwrite Stage 1 profile_score / profile_category
    / profile_name / sweet_spot_active / late_warning."""
    _reset_cache()
    _stub_turbo_engine(monkeypatch, rows=[_turbo_row("AAPL")])

    def fake_enrich(row, universe):
        out = dict(row)
        out["profile_score"]      = 88
        out["profile_category"]   = "SWEET_SPOT"
        out["profile_name"]       = "alpha"
        out["sweet_spot_active"]  = True
        out["late_warning"]       = False
        out["signal_score"]       = 99
        return out
    monkeypatch.setattr("profile_playbook.enrich_row_with_profile", fake_enrich)

    uo.run_ultra_scan_job(universe="sp500", tf="1d")

    monkeypatch.setattr(
        "tz_intelligence.scanner.run_intelligence_scan",
        lambda **_kw: {"results": [{"ticker": "AAPL", "role": "BULL_A"}]},
    )
    monkeypatch.setattr(
        "analyzers.pullback_miner.miner.run_pullback_scan",
        lambda **_kw: {"results": []},
    )
    monkeypatch.setattr(
        "analyzers.rare_reversal.miner.run_rare_reversal_scan",
        lambda **_kw: {"results": []},
    )
    uo.run_ultra_enrich_job(tickers=["AAPL"], universe="sp500", tf="1d")

    aapl = next(r for r in uo.get_ultra_results("sp500", "1d")["results"]
                if r["ticker"] == "AAPL")
    # Profile fields preserved through merge
    assert aapl["profile_score"]      == 88
    assert aapl["profile_category"]   == "SWEET_SPOT"
    assert aapl["profile_name"]       == "alpha"
    assert aapl["sweet_spot_active"]  is True
    assert aapl["late_warning"]       is False
    assert aapl["signal_score"]       == 99
    # Plus the new enrichment is attached
    assert aapl["tz_intel"] is not None
    assert aapl["tz_intel"]["role"] == "BULL_A"


def test_stage1_preserves_turbo_score_and_category(monkeypatch):
    _reset_cache()
    rows = _stub_turbo_engine(monkeypatch, rows=[
        _turbo_row("AAPL", score=88.5, profile_category="SWEET_SPOT"),
        _turbo_row("MSFT", score=12.0, profile_category="WATCH"),
    ])
    # This test verifies the orchestrator doesn't mutate fields it gets
    # from get_turbo_results / enrich_row_with_profile. Stub the playbook
    # to a passthrough so the input categories survive end-to-end.
    monkeypatch.setattr("profile_playbook.enrich_row_with_profile",
                         lambda r, u: dict(r))
    resp = uo.run_ultra_scan_job(universe="sp500", tf="1d")
    by_ticker = {r["ticker"]: r for r in resp["results"]}
    for src in rows:
        out = by_ticker[src["ticker"]]
        assert out["turbo_score"]      == src["turbo_score"]
        assert out["profile_category"] == src["profile_category"]
        assert out["profile_score"]    == src["profile_score"]


# ── Stage 2 — enrich a subset ────────────────────────────────────────────────

@pytest.fixture
def canonical_csv(tmp_path, monkeypatch):
    """Provide a canonical stock_stat CSV in a temp dir and chdir into it."""
    monkeypatch.chdir(tmp_path)
    canonical_path = tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv"
    import csv as _csv
    with open(canonical_path, "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["ticker", "universe", "date",
                                             "close", "volume", "t_signal",
                                             "z_signal", "l_signal",
                                             "preup_signal", "predn_signal",
                                             "lane1_label", "lane3_label",
                                             "volume_bucket", "wick_suffix"])
        w.writeheader()
        for t in ("AAPL", "MSFT", "NVDA", "TSLA"):
            w.writerow({
                "ticker": t, "universe": "sp500", "date": "2026-05-08",
                "close": 150, "volume": 1_000_000, "t_signal": "T4",
                "z_signal": "", "l_signal": "L34", "preup_signal": "",
                "predn_signal": "", "lane1_label": "L1", "lane3_label": "",
                "volume_bucket": "B", "wick_suffix": "H",
            })
    return tmp_path, canonical_path


def test_subset_extracted_from_canonical_does_not_overwrite(monkeypatch, canonical_csv):
    """When canonical CSV exists, ULTRA must extract a subset to a private
    path and leave canonical untouched."""
    _reset_cache()
    tmp_path, canonical_path = canonical_csv
    canonical_size_before = canonical_path.stat().st_size

    _stub_turbo_engine(monkeypatch, rows=[
        _turbo_row(t) for t in ("AAPL", "MSFT", "NVDA", "TSLA")
    ])
    uo.run_ultra_scan_job(universe="sp500", tf="1d")

    # Force fresh-generation path to be off-limits — must not be called
    def _should_not_fetch(*a, **kw):
        raise AssertionError("fresh stock_stat generation must be skipped when canonical exists")
    monkeypatch.setattr(uo, "_generate_subset_csv_fresh", _should_not_fetch)

    # Stub Phase 2 readers minimally
    monkeypatch.setattr("tz_intelligence.scanner.run_intelligence_scan",
                        lambda **_kw: {"results": []})
    monkeypatch.setattr("analyzers.pullback_miner.miner.run_pullback_scan",
                        lambda **_kw: {"results": []})
    monkeypatch.setattr("analyzers.rare_reversal.miner.run_rare_reversal_scan",
                        lambda **_kw: {"results": []})

    uo.run_ultra_enrich_job(tickers=["AAPL", "MSFT"], universe="sp500", tf="1d")

    # Canonical must be byte-for-byte unchanged
    assert canonical_path.stat().st_size == canonical_size_before
    # Subset CSV must exist on a NON-canonical path
    subset_files = [p for p in os.listdir(tmp_path)
                    if p.startswith("stock_stat_tz_wlnbb_ultra_sp500_1d_")
                    and p.endswith(".csv")]
    assert len(subset_files) == 1, f"expected one subset CSV, got {subset_files}"
    # And the subset must contain only the requested tickers
    import csv as _csv
    with open(tmp_path / subset_files[0]) as f:
        subset_tickers = sorted({row["ticker"] for row in _csv.DictReader(f)})
    assert subset_tickers == ["AAPL", "MSFT"]


def test_fresh_generation_used_when_canonical_missing(monkeypatch, tmp_path):
    """When no canonical CSV exists, ULTRA must fresh-generate to its private
    path (not write to the canonical path)."""
    _reset_cache()
    monkeypatch.chdir(tmp_path)
    _stub_turbo_engine(monkeypatch, rows=[_turbo_row("AAPL"), _turbo_row("MSFT")])
    uo.run_ultra_scan_job(universe="sp500", tf="1d")

    called: dict = {}

    def fake_fresh(universe, tf, tickers, bars, subset_path):
        called["subset_path"] = subset_path
        called["tickers"] = list(tickers)
        # Write a small CSV at the subset path
        import csv as _csv
        with open(subset_path, "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=["ticker", "universe", "date", "close",
                                                 "volume", "t_signal", "z_signal",
                                                 "l_signal"])
            w.writeheader()
            for t in tickers:
                w.writerow({"ticker": t, "universe": universe, "date": "2026-05-08",
                            "close": 100, "volume": 1, "t_signal": "T4",
                            "z_signal": "", "l_signal": ""})
        return len(tickers)
    monkeypatch.setattr(uo, "_generate_subset_csv_fresh", fake_fresh)

    monkeypatch.setattr("tz_intelligence.scanner.run_intelligence_scan",
                        lambda **_kw: {"results": []})
    monkeypatch.setattr("analyzers.pullback_miner.miner.run_pullback_scan",
                        lambda **_kw: {"results": []})
    monkeypatch.setattr("analyzers.rare_reversal.miner.run_rare_reversal_scan",
                        lambda **_kw: {"results": []})

    uo.run_ultra_enrich_job(tickers=["AAPL", "MSFT"], universe="sp500", tf="1d")

    assert "subset_path" in called, "fresh generation must run when canonical is missing"
    assert called["tickers"] == ["AAPL", "MSFT"]
    assert called["subset_path"].startswith("stock_stat_tz_wlnbb_ultra_sp500_1d_")
    assert called["subset_path"].endswith(".csv")
    # Canonical path must NOT have been written to
    assert not os.path.exists("stock_stat_tz_wlnbb_sp500_1d.csv")


def test_enrich_only_targets_requested_subset(monkeypatch, canonical_csv):
    """tz_intel / pullback / rare etc. only populate enrichments for the
    tickers passed to enrich; non-targeted tickers stay unenriched."""
    _reset_cache()
    tmp_path, _canon = canonical_csv

    _stub_turbo_engine(monkeypatch, rows=[
        _turbo_row(t) for t in ("AAPL", "MSFT", "NVDA", "TSLA")
    ])
    uo.run_ultra_scan_job(universe="sp500", tf="1d")

    # All four readers report enrichments for only the subset
    monkeypatch.setattr(
        "tz_intelligence.scanner.run_intelligence_scan",
        lambda **_kw: {"results": [{"ticker": "AAPL", "role": "BULL_A",
                                     "quality": "A", "abr_category": "B+"}]},
    )
    monkeypatch.setattr(
        "analyzers.pullback_miner.miner.run_pullback_scan",
        lambda **_kw: {"results": [{"ticker": "AAPL",
                                     "evidence_tier": "CONFIRMED_PULLBACK",
                                     "score": 7.0}]},
    )
    monkeypatch.setattr(
        "analyzers.rare_reversal.miner.run_rare_reversal_scan",
        lambda **_kw: {"results": [{"ticker": "AAPL",
                                     "evidence_tier": "CONFIRMED_RARE",
                                     "base4_key": "abcd", "score": 6.0,
                                     "is_currently_active": True}]},
    )

    uo.run_ultra_enrich_job(tickers=["AAPL"], universe="sp500", tf="1d")

    resp = uo.get_ultra_results("sp500", "1d")
    by_ticker = {r["ticker"]: r for r in resp["results"]}
    aapl = by_ticker["AAPL"]
    msft = by_ticker["MSFT"]
    assert aapl["ultra_enriched"] is True
    assert aapl["tz_wlnbb"] is not None and aapl["tz_wlnbb"]["t_signal"] == "T4"
    assert aapl["tz_intel"]["role"] == "BULL_A"
    assert aapl["abr"]["category"]   == "B+"
    assert aapl["pullback"]["evidence_tier"]      == "CONFIRMED_PULLBACK"
    assert aapl["rare_reversal"]["evidence_tier"] == "CONFIRMED_RARE"
    # MSFT was not in the enrich subset → still unenriched
    assert msft["ultra_enriched"] is False
    assert msft["tz_wlnbb"] is None and msft["tz_intel"] is None


def test_incremental_enrichment_does_not_overwrite_previous(monkeypatch, canonical_csv):
    """Enriching a second subset must add to the cache, not erase the first."""
    _reset_cache()
    tmp_path, _canon = canonical_csv

    _stub_turbo_engine(monkeypatch, rows=[
        _turbo_row(t) for t in ("AAPL", "MSFT", "NVDA", "TSLA")
    ])
    uo.run_ultra_scan_job(universe="sp500", tf="1d")

    monkeypatch.setattr("tz_intelligence.scanner.run_intelligence_scan",
                        lambda **_kw: {"results": []})
    monkeypatch.setattr("analyzers.pullback_miner.miner.run_pullback_scan",
                        lambda **_kw: {"results": []})
    monkeypatch.setattr("analyzers.rare_reversal.miner.run_rare_reversal_scan",
                        lambda **_kw: {"results": []})

    # First enrich: AAPL only
    uo.run_ultra_enrich_job(tickers=["AAPL"], universe="sp500", tf="1d")
    after_first = {r["ticker"]: r for r in uo.get_ultra_results("sp500", "1d")["results"]}
    assert after_first["AAPL"]["tz_wlnbb"] is not None
    assert after_first["MSFT"]["tz_wlnbb"] is None

    # Second enrich: NVDA only — AAPL must keep its enrichment
    uo.run_ultra_enrich_job(tickers=["NVDA"], universe="sp500", tf="1d")
    after_second = {r["ticker"]: r for r in uo.get_ultra_results("sp500", "1d")["results"]}
    assert after_second["AAPL"]["tz_wlnbb"] is not None, (
        "incremental enrichment must not erase previous AAPL enrichment"
    )
    assert after_second["NVDA"]["tz_wlnbb"] is not None
    assert after_second["MSFT"]["tz_wlnbb"] is None
    assert after_second["TSLA"]["tz_wlnbb"] is None


def test_enrich_secondary_failure_keeps_turbo_rows(monkeypatch, canonical_csv):
    """If a secondary module fails during enrich, Turbo rows must still be
    present, with a warning recorded."""
    _reset_cache()
    _stub_turbo_engine(monkeypatch, rows=[_turbo_row("AAPL"), _turbo_row("MSFT")])
    uo.run_ultra_scan_job(universe="sp500", tf="1d")

    def _boom(**_kw):
        raise RuntimeError("simulated TZ Intel failure")
    monkeypatch.setattr("tz_intelligence.scanner.run_intelligence_scan", _boom)
    monkeypatch.setattr("analyzers.pullback_miner.miner.run_pullback_scan",
                        lambda **_kw: {"results": []})
    monkeypatch.setattr("analyzers.rare_reversal.miner.run_rare_reversal_scan",
                        lambda **_kw: {"results": []})

    uo.run_ultra_enrich_job(tickers=["AAPL"], universe="sp500", tf="1d")
    resp = uo.get_ultra_results("sp500", "1d")
    assert len(resp["results"]) == 2
    assert any("TZ Intelligence unavailable" in w for w in resp["warnings"])


def test_enrich_updates_live_sources_for_status_badges(monkeypatch, canonical_csv):
    """Regression: after enrich completes, /api/ultra-scan/status must show
    the secondary sources as ok / count > 0 (or a clear error), NOT the
    stale 'unavailable' Stage 1 initialisation. Otherwise the UI badges keep
    saying 'unavailable' even though the rows in the cache are enriched,
    which makes users hammer the Enrich button thinking it failed."""
    _reset_cache()
    tmp_path, _canon = canonical_csv

    _stub_turbo_engine(monkeypatch, rows=[_turbo_row("AAPL"), _turbo_row("MSFT")])
    uo.run_ultra_scan_job(universe="sp500", tf="1d")

    # Before enrich, secondaries are 'unavailable' (Stage 1 initialisation)
    pre = uo.get_ultra_status()
    pre_sources = pre.get("sources") or {}
    assert pre_sources.get("tz_wlnbb", {}).get("ok") is False

    monkeypatch.setattr(
        "tz_intelligence.scanner.run_intelligence_scan",
        lambda **_kw: {"results": [{"ticker": "AAPL", "role": "BULL_A"}]},
    )
    monkeypatch.setattr(
        "analyzers.pullback_miner.miner.run_pullback_scan",
        lambda **_kw: {"results": [{"ticker": "AAPL",
                                     "evidence_tier": "CONFIRMED_PULLBACK",
                                     "score": 5}]},
    )
    monkeypatch.setattr(
        "analyzers.rare_reversal.miner.run_rare_reversal_scan",
        lambda **_kw: {"results": [{"ticker": "AAPL",
                                     "evidence_tier": "CONFIRMED_RARE",
                                     "base4_key": "abcd", "score": 6}]},
    )

    uo.run_ultra_enrich_job(tickers=["AAPL"], universe="sp500", tf="1d")
    post = uo.get_ultra_status()
    sources = post.get("sources") or {}
    # tz_wlnbb finds AAPL in the canonical-derived subset
    assert sources["tz_wlnbb"]["ok"]      is True
    assert sources["tz_wlnbb"]["count"]   >= 1
    assert sources["tz_intelligence"]["ok"] is True
    assert sources["pullback"]["ok"]        is True
    assert sources["rare_reversal"]["ok"]   is True
    # stock_stat path is also reported
    assert sources["stock_stat"]["ok"]   is True
    assert sources["stock_stat"]["path"].startswith(
        "stock_stat_tz_wlnbb_ultra_sp500_1d_"
    )


def test_enrich_stock_stat_failure_updates_live_sources(monkeypatch, tmp_path):
    """If subset stock_stat generation fails (no canonical, fresh-fetch
    blows up), /status must reflect stock_stat error rather than staying
    on the Stage 1 'unavailable' default."""
    _reset_cache()
    monkeypatch.chdir(tmp_path)

    _stub_turbo_engine(monkeypatch, rows=[_turbo_row("AAPL")])
    uo.run_ultra_scan_job(universe="sp500", tf="1d")

    def _boom(*a, **kw):
        raise RuntimeError("simulated stock_stat fetch failure")
    monkeypatch.setattr(uo, "_generate_subset_csv_fresh", _boom)

    uo.run_ultra_enrich_job(tickers=["AAPL"], universe="sp500", tf="1d")
    post = uo.get_ultra_status()
    sources = post.get("sources") or {}
    assert sources["stock_stat"]["ok"] is False
    assert "error" in sources["stock_stat"]


def test_no_new_category_or_context_score_fields(monkeypatch, canonical_csv):
    """Hard rule: ULTRA must NEVER produce ultra_category / ultra_context_score
    anywhere in the response. (ultra_score IS allowed and intentional — it's
    the additive ULTRA-only ranking column added per the v3 spec.)"""
    _reset_cache()
    _stub_turbo_engine(monkeypatch, rows=[_turbo_row("AAPL")])
    uo.run_ultra_scan_job(universe="sp500", tf="1d")
    monkeypatch.setattr("tz_intelligence.scanner.run_intelligence_scan",
                        lambda **_kw: {"results": []})
    monkeypatch.setattr("analyzers.pullback_miner.miner.run_pullback_scan",
                        lambda **_kw: {"results": []})
    monkeypatch.setattr("analyzers.rare_reversal.miner.run_rare_reversal_scan",
                        lambda **_kw: {"results": []})
    uo.run_ultra_enrich_job(tickers=["AAPL"], universe="sp500", tf="1d")
    resp = uo.get_ultra_results("sp500", "1d")
    forbidden = {"ultra_context_score", "ultra_category"}

    def _check(d):
        if isinstance(d, dict):
            assert forbidden.isdisjoint(d.keys()), f"forbidden key in {sorted(d.keys())}"
            for v in d.values():
                _check(v)
        elif isinstance(d, list):
            for v in d:
                _check(v)
    _check(resp)


# ── Backwards-compat: existing endpoints / modules untouched ─────────────────

def test_existing_turbo_module_intact():
    import turbo_engine
    assert hasattr(turbo_engine, "run_turbo_scan")
    assert hasattr(turbo_engine, "get_turbo_results")


def test_existing_secondary_endpoints_still_callable():
    from main import (
        api_tz_wlnbb_scan, api_tz_intelligence_scan,
        api_pullback_miner_scan, api_rare_reversal_scan,
    )
    r = api_tz_wlnbb_scan(universe="__nonexistent__", tf="1d")
    assert isinstance(r, dict) and "results" in r
    r = api_tz_intelligence_scan(universe="sp500", tf="1d")
    assert isinstance(r, dict) and "results" in r
    r = api_pullback_miner_scan(universe="sp500", tf="1d")
    assert isinstance(r, dict)
    r = api_rare_reversal_scan(universe="sp500", tf="1d")
    assert isinstance(r, dict)


def test_readers_default_canonical_path_unchanged(monkeypatch, tmp_path):
    """Adding stat_path=None must NOT change canonical path resolution.

    With stat_path omitted the readers should fall back to canonical (and
    return their existing 'no stock_stat' error string, not anything new)."""
    monkeypatch.chdir(tmp_path)
    from tz_intelligence.scanner import run_intelligence_scan
    from analyzers.pullback_miner.miner import run_pullback_scan
    from analyzers.rare_reversal.miner import run_rare_reversal_scan

    r1 = run_intelligence_scan(universe="sp500", tf="1d")
    r2 = run_pullback_scan(universe="sp500", tf="1d")
    r3 = run_rare_reversal_scan(universe="sp500", tf="1d")
    for r in (r1, r2, r3):
        assert isinstance(r, dict)
        # The exact text starts with "No stock_stat_tz_wlnbb CSV found" today
        assert "No stock_stat_tz_wlnbb" in (r.get("error") or "")


def test_ultra_engine_module_signal_engines_pristine():
    """ultra_engine.py must keep its ULTRA-v2 signal computation untouched."""
    import ultra_engine
    assert hasattr(ultra_engine, "compute_260308_l88")
    assert hasattr(ultra_engine, "compute_ultra_v2")
    assert not hasattr(ultra_engine, "run_ultra_scan")


# ── ULTRA Score (v3) — independent additive ranking ──────────────────────────

def test_compute_ultra_score_pure_function():
    """The score function never raises and returns the 6-field dict."""
    from ultra_score import compute_ultra_score
    out = compute_ultra_score({})
    assert out["ultra_score"] == 0
    assert out["ultra_score_band"] in ("A", "B", "C", "D")
    assert isinstance(out["ultra_score_reasons"], list)
    assert isinstance(out["ultra_score_flags"], list)
    assert isinstance(out["ultra_score_raw_before_penalty"], int)
    assert isinstance(out["ultra_score_penalty_total"], int)


def test_ultra_score_clamped_to_0_100_and_banded():
    """Even for a row with every possible flag, the score must clamp to 100,
    and the band must be 'A'."""
    from ultra_score import compute_ultra_score
    row = _turbo_row("AAPL")
    for k in ("buy_2809", "rocket", "bb_brk", "bx_up", "eb_bull", "be_up", "bo_up",
              "abs_sig", "va", "svs_2809", "climb_sig", "load_sig",
              "strong_sig", "l34", "fri34", "tz_bull_flip", "rs_strong"):
        row[k] = 1
    row["profile_score"]    = 25
    row["profile_category"] = "SWEET_SPOT"
    row["tz_intel"] = {"role": "BULL_A"}
    row["pullback"] = {"evidence_tier": "CONFIRMED_PULLBACK", "is_currently_active": True}
    row["rare_reversal"] = {"evidence_tier": "CONFIRMED_RARE", "is_currently_active": True}
    row["abr"] = {"category": "B+"}
    out = compute_ultra_score(row)
    assert out["ultra_score"] == 100, out["ultra_score"]
    assert out["ultra_score_band"] == "A"


def test_ultra_score_negative_context_penalises():
    """REJECT_LONG must drop the score sharply."""
    from ultra_score import compute_ultra_score
    row = _turbo_row("AAPL")
    row["bb_brk"] = 1
    row["tz_intel"] = {"role": "REJECT_LONG"}
    out = compute_ultra_score(row)
    assert out["ultra_score"] < 30, f"expected reject penalty, got {out['ultra_score']}"


def test_stage1_rows_have_ultra_score(monkeypatch):
    """Every Stage 1 row must already carry ultra_score / ultra_score_band /
    ultra_score_reasons (computed from Turbo flags only)."""
    _reset_cache()
    rows = _stub_turbo_engine(monkeypatch, rows=[
        _turbo_row("AAPL", buy_2809=1, abs_sig=1, rs_strong=1,
                    profile_category="SWEET_SPOT", profile_score=20),
        _turbo_row("MSFT"),
    ])
    monkeypatch.setattr("profile_playbook.enrich_row_with_profile",
                         lambda r, u: dict(r))
    uo.run_ultra_scan_job(universe="sp500", tf="1d")
    out = uo.get_ultra_results("sp500", "1d")["results"]
    by_ticker = {r["ticker"]: r for r in out}
    for r in by_ticker.values():
        assert "ultra_score" in r
        assert "ultra_score_band" in r
        assert "ultra_score_reasons" in r
        assert isinstance(r["ultra_score"], int)
        assert 0 <= r["ultra_score"] <= 100
    assert by_ticker["AAPL"]["ultra_score"] > by_ticker["MSFT"]["ultra_score"]


def test_enrich_recomputes_ultra_score(monkeypatch, canonical_csv):
    """Enriching with strong context must boost ultra_score above the Stage 1
    value for the same ticker."""
    _reset_cache()
    _stub_turbo_engine(monkeypatch, rows=[
        _turbo_row("AAPL", abs_sig=1, bb_brk=1, rs_strong=1,
                    profile_category="SWEET_SPOT", profile_score=20),
    ])
    monkeypatch.setattr("profile_playbook.enrich_row_with_profile",
                         lambda r, u: dict(r))
    uo.run_ultra_scan_job(universe="sp500", tf="1d")
    pre = uo.get_ultra_results("sp500", "1d")["results"][0]["ultra_score"]

    monkeypatch.setattr(
        "tz_intelligence.scanner.run_intelligence_scan",
        lambda **_kw: {"results": [{"ticker": "AAPL", "role": "BULL_A",
                                     "abr_category": "B+"}]},
    )
    monkeypatch.setattr(
        "analyzers.pullback_miner.miner.run_pullback_scan",
        lambda **_kw: {"results": [{"ticker": "AAPL",
                                     "evidence_tier": "CONFIRMED_PULLBACK",
                                     "score": 7}]},
    )
    monkeypatch.setattr(
        "analyzers.rare_reversal.miner.run_rare_reversal_scan",
        lambda **_kw: {"results": []},
    )
    uo.run_ultra_enrich_job(tickers=["AAPL"], universe="sp500", tf="1d")
    post = uo.get_ultra_results("sp500", "1d")["results"][0]["ultra_score"]
    assert post >= pre, f"enrichment should not lower score: pre={pre} post={post}"
    assert post > pre, f"enrichment with confluence should raise score: pre={pre} post={post}"


def test_ultra_score_does_not_modify_turbo_score(monkeypatch):
    """Hard rule: computing ultra_score must leave turbo_score / category /
    profile_score / signal_score untouched."""
    _reset_cache()
    rows = _stub_turbo_engine(monkeypatch, rows=[
        _turbo_row("AAPL", score=88.5, profile_category="SWEET_SPOT",
                    profile_score=20),
    ])
    monkeypatch.setattr("profile_playbook.enrich_row_with_profile",
                         lambda r, u: dict(r))
    uo.run_ultra_scan_job(universe="sp500", tf="1d")
    aapl = uo.get_ultra_results("sp500", "1d")["results"][0]
    assert aapl["turbo_score"]      == rows[0]["turbo_score"]
    assert aapl["profile_category"] == rows[0]["profile_category"]
    assert aapl["profile_score"]    == rows[0]["profile_score"]
