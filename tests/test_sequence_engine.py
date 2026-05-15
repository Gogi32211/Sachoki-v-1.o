"""Tests for backend.sequence_engine — universe-wide T/Z sequence analyzer."""
from __future__ import annotations

import csv
import math
import os
import sys

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import sequence_engine as se


# ── Pool / classifier sanity ────────────────────────────────────────────────

def test_excluded_signals_not_in_pool():
    """Spec excludes T7, T8, Z8 from the sequence pool."""
    for excluded in ("T7", "T8", "Z8"):
        assert excluded not in se.ALLOWED_SIGNALS, (
            f"{excluded} must NOT appear in the sequence pool"
        )


def test_pool_contains_required_signals():
    for tok in ("T1G", "T1", "T2G", "T2", "T3", "T4", "T5", "T6",
                "T9", "T10", "T11", "T12"):
        assert tok in se._BULL_SET
    for tok in ("Z1G", "Z1", "Z2G", "Z2", "Z3", "Z4", "Z5", "Z6",
                "Z7", "Z9", "Z10", "Z11", "Z12"):
        assert tok in se._BEAR_SET


def test_classify_recognises_bull_and_bear_and_excludes_t7_t8_z8():
    assert se._classify("T4", "")  == ("T", "T4")
    assert se._classify("T1G", "") == ("T", "T1G")
    assert se._classify("", "Z3")  == ("Z", "Z3")
    # Excluded
    assert se._classify("T7", "")  is None
    assert se._classify("T8", "")  is None
    assert se._classify("",  "Z8") is None
    # Empty
    assert se._classify("",  "")   is None


# ── End-to-end run with a synthetic CSV ────────────────────────────────────

def _write_stock_stat(path, rows):
    cols = ["ticker", "date", "bar_datetime", "t_signal", "z_signal", "ret_1d"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_run_sequence_scan_no_csv_returns_no_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = se.run_sequence_scan(universe="__none__", tf="1d")
    assert out["status"] == "no_data"
    assert "No Stock Stat CSV" in out["error"]
    assert out["results"] == []
    assert out["tried_paths"] == []


def test_run_sequence_scan_basic_aggregation(tmp_path, monkeypatch):
    """Two tickers each fire T4 → Z3 → T1G → Z2 with positive forward returns
    on the last bar. seq_len=4 mode='type' should fire 'TZTZ' twice."""
    monkeypatch.chdir(tmp_path)
    rows = []
    for ticker in ("AAPL", "MSFT"):
        for i, (t, z, ret) in enumerate([
            ("T4",  "",   0.5),
            ("",    "Z3", -0.1),
            ("T1G", "",   0.3),
            ("",    "Z2", 0.8),    # last bar — used for window's ret_1d
        ], start=1):
            rows.append({
                "ticker": ticker,
                "date":   f"2026-01-0{i}",
                "bar_datetime": f"2026-01-0{i}T00:00:00",
                "t_signal": t, "z_signal": z, "ret_1d": ret,
            })
    _write_stock_stat(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv", rows)

    out = se.run_sequence_scan(universe="sp500", tf="1d", seq_len=4,
                                min_count=1, mode="type")
    assert out["status"] == "ok"
    assert out["tickers_seen"] == 2
    by_seq = {r["sequence"]: r for r in out["results"]}
    assert "TZTZ" in by_seq
    tztz = by_seq["TZTZ"]
    assert tztz["count"] == 2
    assert tztz["wins"]  == 2          # last-bar ret_1d=0.8 > 0 for both
    assert tztz["win_rate"] == 1.0
    assert tztz["ticker_count"] == 2
    # score = wr * log1p(count) = 1.0 * log(3)
    assert abs(tztz["score"] - round(math.log1p(2), 4)) < 1e-4


def test_run_sequence_scan_excludes_t7_t8_z8_from_windows(tmp_path, monkeypatch):
    """T7 / T8 / Z8 bars must be skipped — they should NOT consume window slots."""
    monkeypatch.chdir(tmp_path)
    rows = [
        {"ticker": "AAPL", "date": "2026-01-01",
         "t_signal": "T4", "z_signal": "", "ret_1d": 0.1},
        {"ticker": "AAPL", "date": "2026-01-02",
         "t_signal": "T7", "z_signal": "", "ret_1d": 0.0},     # skipped
        {"ticker": "AAPL", "date": "2026-01-03",
         "t_signal": "",   "z_signal": "Z3", "ret_1d": 0.5},
        {"ticker": "AAPL", "date": "2026-01-04",
         "t_signal": "",   "z_signal": "Z8", "ret_1d": 0.0},   # skipped
        {"ticker": "AAPL", "date": "2026-01-05",
         "t_signal": "T1G","z_signal": "", "ret_1d": 0.2},
    ]
    _write_stock_stat(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv", rows)
    out = se.run_sequence_scan(universe="sp500", tf="1d", seq_len=3,
                                min_count=1, mode="type")
    assert out["status"] == "ok"
    by_seq = {r["sequence"]: r for r in out["results"]}
    # After excluding T7/Z8: events are T4, Z3, T1G → only one 3-bar window 'TZT'
    assert "TZT" in by_seq
    assert by_seq["TZT"]["count"] == 1
    # Make sure no sequence containing the excluded labels sneaks in
    for r in out["results"]:
        assert "T7" not in r["sequence"]
        assert "T8" not in r["sequence"]
        assert "Z8" not in r["sequence"]


def test_run_sequence_scan_full_mode_label_keys(tmp_path, monkeypatch):
    """mode='full' should emit pipe-delimited full labels, type_seq should
    still describe T/Z type."""
    monkeypatch.chdir(tmp_path)
    rows = [
        {"ticker": "AAPL", "date": "2026-01-01",
         "t_signal": "T4", "z_signal": "", "ret_1d": 0.1},
        {"ticker": "AAPL", "date": "2026-01-02",
         "t_signal": "",   "z_signal": "Z3", "ret_1d": 0.2},
        {"ticker": "AAPL", "date": "2026-01-03",
         "t_signal": "T1G","z_signal": "", "ret_1d": 0.5},
    ]
    _write_stock_stat(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv", rows)
    out = se.run_sequence_scan(universe="sp500", tf="1d", seq_len=3,
                                min_count=1, mode="full")
    assert out["status"] == "ok"
    by_seq = {r["sequence"]: r for r in out["results"]}
    assert "T4|Z3|T1G" in by_seq
    assert by_seq["T4|Z3|T1G"]["type_seq"] == "TZT"


def test_run_sequence_scan_min_count_filter(tmp_path, monkeypatch):
    """Sequences below min_count must be filtered out."""
    monkeypatch.chdir(tmp_path)
    rows = [
        {"ticker": "AAPL", "date": "2026-01-01",
         "t_signal": "T4", "z_signal": "", "ret_1d": 0.1},
        {"ticker": "AAPL", "date": "2026-01-02",
         "t_signal": "",   "z_signal": "Z3", "ret_1d": 0.2},
    ]
    _write_stock_stat(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv", rows)
    out = se.run_sequence_scan(universe="sp500", tf="1d", seq_len=2,
                                min_count=10, mode="type")
    assert out["status"] == "ok"
    assert out["results"] == []     # 1 < min_count=10


def test_run_sequence_scan_validates_seq_len_and_mode():
    bad = se.run_sequence_scan(seq_len=1)
    assert bad["status"] == "error"
    bad = se.run_sequence_scan(seq_len=7)
    assert bad["status"] == "error"
    bad = se.run_sequence_scan(mode="not-a-mode")
    assert bad["status"] == "error"


def test_run_sequence_scan_progress_callback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rows = []
    for ticker in ("AAPL", "MSFT", "NVDA"):
        rows.append({"ticker": ticker, "date": "2026-01-01",
                     "t_signal": "T4", "z_signal": "", "ret_1d": 0.1})
        rows.append({"ticker": ticker, "date": "2026-01-02",
                     "t_signal": "",   "z_signal": "Z3", "ret_1d": 0.2})
    _write_stock_stat(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv", rows)

    progress_calls: list = []
    def cb(done, total):
        progress_calls.append((done, total))

    se.run_sequence_scan(universe="sp500", tf="1d", seq_len=2,
                          min_count=1, mode="type", progress_cb=cb)
    # Initial (0, 3) + one call per ticker (1..3)
    assert progress_calls[0]   == (0, 3)
    assert progress_calls[-1]  == (3, 3)


def test_run_sequence_scan_reads_bulk_stock_stat_csv(tmp_path, monkeypatch):
    """Regression: the bulk Stock Stat (Admin tab → /api/stock-stat/trigger)
    writes ``stock_stat_output/stock_stat_<universe>_<tf>.csv`` with compact
    UPPERCASE T/Z columns and NO forward returns — only `close`. The engine
    must accept that layout, parse T/Z from the compact tokens, and derive
    ret_1d from close.
    """
    monkeypatch.chdir(tmp_path)
    bulk_dir = tmp_path / "stock_stat_output"
    bulk_dir.mkdir()
    bulk_path = bulk_dir / "stock_stat_sp500_1d.csv"
    cols = ["ticker", "date", "close", "T", "Z"]
    with open(bulk_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        # AAPL: T4 → Z3 → T1G → Z2; closes rise 100 → 101 → 103 → 106 → 108.
        for d, c, t, z in (
            ("2026-01-01", 100, "T4",  ""),
            ("2026-01-02", 101, "",    "Z3"),
            ("2026-01-03", 103, "T1G", ""),
            ("2026-01-04", 106, "",    "Z2"),
            ("2026-01-05", 108, "",    ""),
        ):
            w.writerow({"ticker": "AAPL", "date": d,
                        "close": c, "T": t, "Z": z})

    out = se.run_sequence_scan(universe="sp500", tf="1d", seq_len=4,
                                min_count=1, mode="type")
    assert out["status"] == "ok"
    assert out["stat_path"].endswith("stock_stat_sp500_1d.csv")
    by_seq = {r["sequence"]: r for r in out["results"]}
    assert "TZTZ" in by_seq
    tztz = by_seq["TZTZ"]
    assert tztz["count"] == 1
    # Last bar of window is Z2 on 01-04 (close=106), next close 108 →
    # forward 1d return ≈ 1.887%. Must be positive → wins=1.
    assert tztz["wins"] == 1
    assert tztz["avg_ret_1d"] > 1.0


def test_run_sequence_scan_prefers_tz_wlnbb_when_both_present(tmp_path, monkeypatch):
    """If both CSVs exist, prefer TZ/WLNBB (it carries pre-computed
    forward returns)."""
    monkeypatch.chdir(tmp_path)
    bulk_dir = tmp_path / "stock_stat_output"
    bulk_dir.mkdir()
    # Bulk CSV present, but populated so it isn't skipped for being empty
    with open(bulk_dir / "stock_stat_sp500_1d.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "date", "close", "T", "Z"])
        w.writeheader()
        w.writerow({"ticker": "MSFT", "date": "2026-01-01",
                    "close": 100, "T": "T4", "Z": ""})
    # TZ/WLNBB CSV present too
    _write_stock_stat(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv", [
        {"ticker": "AAPL", "date": "2026-01-01",
         "t_signal": "T4", "z_signal": "", "ret_1d": 0.5},
    ])
    out = se.run_sequence_scan(universe="sp500", tf="1d", seq_len=2,
                                min_count=1, mode="type")
    assert out["stat_path"].endswith("stock_stat_tz_wlnbb_sp500_1d.csv")


def test_run_sequence_scan_falls_through_empty_tz_wlnbb_to_bulk(tmp_path, monkeypatch):
    """Regression for the user-reported 'briefly Scanning… then back to
    not_run' bug: an EMPTY (header-only) leftover TZ/WLNBB CSV in cwd was
    being preferred over the freshly-generated bulk Stock Stat. Engine must
    detect 'no ticker rows' and fall through to the next candidate path."""
    monkeypatch.chdir(tmp_path)
    # Empty TZ/WLNBB CSV (header only)
    _write_stock_stat(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv", [])
    # Populated bulk Stock Stat
    bulk_dir = tmp_path / "stock_stat_output"
    bulk_dir.mkdir()
    with open(bulk_dir / "stock_stat_sp500_1d.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "date", "close", "T", "Z"])
        w.writeheader()
        for d, c, t, z in (("2026-01-01", 100, "T4",  ""),
                            ("2026-01-02", 101, "",    "Z3"),
                            ("2026-01-03", 103, "T1G", ""),
                            ("2026-01-04", 106, "",    "Z2"),
                            ("2026-01-05", 108, "",    "")):
            w.writerow({"ticker": "AAPL", "date": d,
                        "close": c, "T": t, "Z": z})
    out = se.run_sequence_scan(universe="sp500", tf="1d", seq_len=4,
                                min_count=1, mode="type")
    assert out["status"] == "ok"
    assert out["stat_path"].endswith("stock_stat_output/stock_stat_sp500_1d.csv"), \
        f"engine should fall through to bulk CSV when TZ/WLNBB is empty; got {out['stat_path']}"
    assert out["tickers_seen"] == 1
    assert any(r["sequence"] == "TZTZ" for r in out["results"])


def test_run_sequence_scan_no_data_when_all_csvs_empty(tmp_path, monkeypatch):
    """If every candidate CSV exists but is empty, engine returns no_data
    with a list of paths it tried — not a silent ok."""
    monkeypatch.chdir(tmp_path)
    _write_stock_stat(tmp_path / "stock_stat_tz_wlnbb_sp500_1d.csv", [])
    bulk_dir = tmp_path / "stock_stat_output"
    bulk_dir.mkdir()
    with open(bulk_dir / "stock_stat_sp500_1d.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "date", "close", "T", "Z"])
        w.writeheader()
    out = se.run_sequence_scan(universe="sp500", tf="1d", seq_len=2,
                                min_count=1, mode="type")
    assert out["status"] == "no_data"
    assert "0 ticker rows" in out["error"]
    assert isinstance(out["tried_paths"], list)
    assert len(out["tried_paths"]) >= 1


# ── API smoke tests ──────────────────────────────────────────────────────────

def test_sequence_scan_routes_registered():
    import main
    paths = sorted(getattr(r, "path", "")
                   for r in main.app.routes
                   if "sequence" in getattr(r, "path", ""))
    assert "/api/sequence-scan/trigger" in paths
    assert "/api/sequence-scan/status"  in paths
    assert "/api/sequence-scan/results" in paths


def test_sequence_scan_status_handles_no_run(monkeypatch, tmp_path):
    """With no prior run for these params, /status returns not_run."""
    monkeypatch.chdir(tmp_path)
    # Force a fresh DB for the test by pointing DB_PATH at the temp dir.
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    # Reload db so DB_PATH change takes effect for this test's connection.
    import importlib
    import db as _db
    importlib.reload(_db)
    import main as _m
    importlib.reload(_m)

    resp = _m.api_sequence_scan_status(
        universe="sp500", tf="1d", seq_len=4, min_count=10, mode="type",
        nasdaq_batch="",
    )
    assert resp["status"] == "not_run"
    assert resp["progress"] == 0
    assert resp["pct"]      == 0


def test_bg_run_completes_visible_in_status_and_results(tmp_path, monkeypatch):
    """Regression: simulate the user-reported flow — start the bg worker,
    let it finish, then call /status + /results. The earlier SQLite-backed
    implementation lost the run after the worker completed (Postgres syntax
    issue) → /status returned 'not_run' even though the scan ran fine.
    The pure in-memory store keeps the state visible end-to-end.
    """
    monkeypatch.chdir(tmp_path)
    # Populated bulk Stock Stat with 4-bar TZTZ pattern
    bulk_dir = tmp_path / "stock_stat_output"
    bulk_dir.mkdir()
    with open(bulk_dir / "stock_stat_sp500_1d.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "date", "close", "T", "Z"])
        w.writeheader()
        for d, c, t, z in (("2026-01-01", 100, "T4", ""),
                            ("2026-01-02", 101, "", "Z3"),
                            ("2026-01-03", 103, "T1G", ""),
                            ("2026-01-04", 106, "", "Z2"),
                            ("2026-01-05", 108, "", "")):
            w.writerow({"ticker": "AAPL", "date": d,
                        "close": c, "T": t, "Z": z})

    import importlib, main as _m
    importlib.reload(_m)
    cache_key = _m._seq_cache_key("sp500", "1d", 4, "type", 1, "")
    # Run the worker synchronously (BackgroundTasks would defer it; this
    # mirrors the same code path).
    _m._run_sequence_scan_bg(cache_key, "sp500", "1d", 4, "type", 1, "")

    # /status must now show the completed run, not 'not_run'
    status = _m.api_sequence_scan_status(
        universe="sp500", tf="1d", seq_len=4, min_count=1, mode="type",
        nasdaq_batch="",
    )
    assert status["status"] == "done", f"expected done, got {status}"
    assert status["error"] is None
    assert status["stat_path"].endswith("stock_stat_sp500_1d.csv")

    # /results must return the merged sequences
    results = _m.api_sequence_scan_results(
        universe="sp500", tf="1d", seq_len=4, min_count=1, mode="type",
        nasdaq_batch="", limit=50, sort_by="score",
    )
    assert results["status"] == "done"
    assert any(r["sequence"] == "TZTZ" for r in results["results"])
    assert results["tickers_total"] == 1


def test_run_sequence_scan_emits_multi_horizon_stats(tmp_path, monkeypatch):
    """Engine must emit win_rate_{3,5,9}d / avg_ret_{3,5,9}d /
    med_ret_{3,5,9}d / count_{3,5,9}d per result row, derived from the
    last bar of the window. Horizons that fall past the dataset end
    contribute None."""
    monkeypatch.chdir(tmp_path)
    bulk_dir = tmp_path / "stock_stat_output"
    bulk_dir.mkdir()
    cols = ["ticker", "date", "close", "T", "Z"]
    # 12-bar series so every event has at least 1d/3d/5d available; only
    # the very last few events lack 9d.
    series = [
        ("2026-01-01", 100, "T4",  ""),
        ("2026-01-02", 101, "",    "Z3"),
        ("2026-01-03", 103, "T1G", ""),
        ("2026-01-04", 106, "",    "Z2"),
        ("2026-01-05", 108, "T2",  ""),
        ("2026-01-06", 110, "",    "Z3"),
        ("2026-01-07", 112, "T6",  ""),
        ("2026-01-08", 115, "",    "Z2"),
        ("2026-01-09", 117, "T2G", ""),
        ("2026-01-10", 120, "",    "Z3"),
        ("2026-01-11", 122, "T4",  ""),
        ("2026-01-12", 125, "",    "Z2"),
    ]
    with open(bulk_dir / "stock_stat_sp500_1d.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for d, c, t, z in series:
            w.writerow({"ticker": "AAPL", "date": d, "close": c, "T": t, "Z": z})

    out = se.run_sequence_scan(universe="sp500", tf="1d", seq_len=4,
                                min_count=1, mode="type")
    assert out["status"] == "ok"
    rows = out["results"]
    assert any(r["sequence"] == "TZTZ" for r in rows)
    tztz = next(r for r in rows if r["sequence"] == "TZTZ")
    # All multi-horizon keys present
    for n in (3, 5, 9):
        assert f"win_rate_{n}d" in tztz, f"missing win_rate_{n}d"
        assert f"avg_ret_{n}d"  in tztz, f"missing avg_ret_{n}d"
        assert f"med_ret_{n}d"  in tztz, f"missing med_ret_{n}d"
        assert f"count_{n}d"    in tztz, f"missing count_{n}d"
    # 1d/3d/5d should be populated; 9d may legitimately be None on a 12-bar
    # series (need ≥ 9 bars after the window's last bar).
    assert tztz["win_rate"]    is not None
    assert tztz["avg_ret_3d"]  is not None and tztz["avg_ret_3d"] > 0
    assert tztz["avg_ret_5d"]  is not None and tztz["avg_ret_5d"] > 0
    # All test bars rise → returns positive at every horizon that had data.
    assert tztz["win_rate_3d"] == 1.0
    assert tztz["win_rate_5d"] == 1.0


def test_results_cap_evicts_oldest(monkeypatch, tmp_path):
    """The in-memory completed-runs cache must be bounded so successive
    scans across many param combos don't leak memory."""
    monkeypatch.chdir(tmp_path)
    import importlib, main as _m
    importlib.reload(_m)

    # Fill the cache past its cap with synthetic completed runs
    cap = _m._SEQ_RESULTS_CAP
    for i in range(cap + 3):
        _m._seq_store_completed(f"key{i}", {
            "status": "done",
            "completed_at": f"2026-01-01T00:00:{i:02d}",
            "results": [],
        })
    assert len(_m._seq_results) <= cap
    # Most recent keys retained
    assert f"key{cap+2}" in _m._seq_results
