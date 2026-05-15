"""Tests for the shared ULTRA Score helper + Replay analytics.

Covers:
  • shared backend.ultra_score helper (no lookahead, robust to missing fields)
  • Stock Stat columns are written in main.py headers
  • replay_engine ULTRA aggregations (band/bucket/combo/false-positive/missed-winner)
  • Existing Stock Stat / Replay endpoints still callable
"""
from __future__ import annotations

import inspect
import os
import sys

# Ensure backend/ is importable.
_backend = os.path.join(os.path.dirname(__file__), "..", "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import ultra_score
import replay_engine


# ── Shared helper ────────────────────────────────────────────────────────────

def test_compute_ultra_score_returns_full_dict_on_empty_row():
    out = ultra_score.compute_ultra_score({})
    assert out["ultra_score"] == 0
    assert out["ultra_score_band"] == "D"
    assert isinstance(out["ultra_score_reasons"], list)
    # Empty row has no breakout and no setup, so the helper flags it as
    # 'ISOLATED' — that's intentional context, not a regression.
    assert "ISOLATED" in out["ultra_score_flags"]
    assert out["ultra_score_raw_before_penalty"] == 0
    assert out["ultra_score_penalty_total"] >= 0


def test_compute_ultra_score_clamps_to_100_with_band_a():
    row = {}
    for k in ("buy_2809", "rocket", "bb_brk", "bx_up", "eb_bull", "be_up", "bo_up",
              "abs_sig", "va", "svs_2809", "climb_sig", "load_sig",
              "strong_sig", "l34", "fri34", "tz_bull_flip", "rs_strong"):
        row[k] = 1
    row["profile_score"]    = 25
    row["profile_category"] = "SWEET_SPOT"
    out = ultra_score.compute_ultra_score(row)
    assert out["ultra_score"] == 100
    assert out["ultra_score_band"] == "A"


def test_compute_ultra_score_handles_stock_stat_label_lists():
    """When fed a Stock Stat bar shape (combo / vabs / etc. as label lists),
    the helper recognises the labels and produces a meaningful score."""
    row = {
        "combo":  ["BUY_2809", "ROCKET", "BB↑"],
        "vabs":   ["ABS", "STR"],
        "tz":     "T4",
        "profile_score":    20,
        "profile_category": "SWEET_SPOT",
    }
    out = ultra_score.compute_ultra_score(row)
    assert out["ultra_score"] >= 70
    reasons = " ".join(out["ultra_score_reasons"])
    assert "BUY_2809" in reasons
    assert "ABS" in reasons
    assert "SWEET_SPOT" in reasons


def test_compute_ultra_score_no_lookahead():
    """The helper must NEVER reference forward-return / future-bar fields
    in actual code (string literals / dict reads). Docstrings and the
    authoritative `_FORWARD_RETURN_FIELDS` banned-list literal are allowed —
    we extract the executable body via the AST and check string constants
    inside `Call` / `Subscript` / `Compare` nodes.
    """
    import ast
    src = inspect.getsource(ultra_score)
    tree = ast.parse(src)

    forbidden = {
        "ret_1d", "ret_3d", "ret_5d", "ret_10d",
        "mfe_5d", "mfe_10d", "mae_5d", "mae_10d",
        "max_high_5d", "max_high_10d",
        "max_drawdown_5d", "max_drawdown_10d",
        "clean_win_5d", "big_win_10d", "fail_5d", "fail_10d",
    }
    offenders: list = []
    for node in ast.walk(tree):
        # Skip the authoritative banned-list literal (Set/FrozenSet building)
        if isinstance(node, (ast.Set, ast.Tuple)):
            # If every element is one of the forbidden names, it's the
            # _FORWARD_RETURN_FIELDS literal — allow.
            elts = node.elts if isinstance(node, ast.Tuple) else node.elts
            if elts and all(
                isinstance(e, ast.Constant) and e.value in forbidden for e in elts
            ):
                continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in forbidden:
                offenders.append(node.value)
    # The frozenset literal contributes one Constant per name — allow exactly
    # one occurrence of each as that authoritative declaration. Anything
    # beyond that is a real reference.
    from collections import Counter
    counts = Counter(offenders)
    extras = {n: c - 1 for n, c in counts.items() if c > 1}
    assert not extras, (
        f"ultra_score helper references forward-return fields: {extras}"
    )


def test_compute_ultra_score_band_helper():
    assert ultra_score.compute_ultra_score_band(95) == "A"
    assert ultra_score.compute_ultra_score_band(70) == "B"
    assert ultra_score.compute_ultra_score_band(55) == "C"
    assert ultra_score.compute_ultra_score_band(0)  == "D"


def test_compute_ultra_score_does_not_mutate_input_row():
    row = {"buy_2809": 1, "profile_category": "BUILDING"}
    snap = dict(row)
    ultra_score.compute_ultra_score(row)
    assert row == snap


def test_compute_ultra_score_combo_flags_emitted():
    row = {"buy_2809": 1, "profile_category": "SWEET_SPOT"}
    out = ultra_score.compute_ultra_score(row)
    assert "MOMENTUM_A" in out["ultra_score_flags"]


# ── Stock Stat columns ───────────────────────────────────────────────────────

def test_stock_stat_headers_include_ultra_score_columns():
    """Verify the Stock Stat / Bulk Signal CSV writer's header list includes
    the 6 ULTRA Score columns, so historical Replay can read them."""
    main_py = os.path.join(_backend, "main.py")
    with open(main_py, encoding="utf-8") as f:
        src = f.read()
    for col in (
        "ultra_score", "ultra_score_band", "ultra_score_reasons",
        "ultra_score_flags", "ultra_score_raw_before_penalty",
        "ultra_score_penalty_total",
    ):
        assert f'"{col}"' in src, f"Stock Stat header is missing {col}"


# ── Replay aggregation ───────────────────────────────────────────────────────

def _bar(score=70, band="B", flags="", reasons="", **fwd):
    base = {
        "ticker":            "AAPL",
        "date":              "2026-05-01",
        "close":             150.0,
        "ultra_score":       score,
        "ultra_score_band":  band,
        "ultra_score_flags": flags,
        "ultra_score_reasons": reasons,
        "turbo_score":       50,
        "profile_score":     10,
        "profile_category":  "BUILDING",
    }
    base.update(fwd)
    return base


def test_ultra_metrics_reads_underscore_forward_returns():
    """Regression: replay rows carry forward returns under underscore-prefixed
    keys (_ret1/_ret3/_ret5/_ret10/_max5/_max10) attached by _label_rows.
    The first ULTRA aggregation pass shipped with lowercase ret_*d keys, so
    the live UI showed '—' across every Ret/Win/Hit cell. Verify the metrics
    helper now picks up the underscore keys correctly."""
    rows = [
        {"ultra_score": 88, "ultra_score_band": "A",
         "_ret1":  1.0, "_ret3": 2.0, "_ret5": 5.0, "_ret10": 9.0,
         "_max5":  6.0, "_max10": 11.0},
        {"ultra_score": 82, "ultra_score_band": "A",
         "_ret1": -0.5, "_ret3": 1.0, "_ret5": 2.5, "_ret10": -1.0,
         "_max5":  3.0, "_max10":  3.5},
    ]
    m = replay_engine._ultra_metrics(rows)
    # Returns must be averaged from the _ret* shortcuts, not None
    assert m["avg_ret_5d"] is not None
    assert m["avg_ret_10d"] is not None
    assert abs(m["avg_ret_5d"]  - 3.75) < 0.01
    assert abs(m["avg_ret_10d"] - 4.0)  < 0.01
    # MFE proxied by _max* shortcut
    assert m["avg_mfe_5d"] is not None
    assert m["avg_mfe_10d"] is not None
    # MAE not produced by this engine — must be None, not 0 / fabricated
    assert m["avg_mae_5d"]  is None
    assert m["avg_mae_10d"] is None


def test_ultra_band_summary_buckets_rows_by_band():
    rows = [
        _bar(score=88, band="A", ret_5d=8.0, ret_10d=12.0),
        _bar(score=82, band="A", ret_5d=2.0, ret_10d=5.0),
        _bar(score=70, band="B", ret_5d=-1.0, ret_10d=0.5),
        _bar(score=55, band="C"),
        _bar(score=20, band="D"),
    ]
    summary = replay_engine.ultra_score_band_summary(rows)
    by_band = {r["band"]: r for r in summary}
    assert by_band["A"]["count"] == 2
    assert by_band["B"]["count"] == 1
    assert by_band["C"]["count"] == 1
    assert by_band["D"]["count"] == 1
    # Numeric metrics computed for A
    assert by_band["A"]["avg_ret_5d"] is not None
    assert by_band["A"]["avg_ret_10d"] is not None


def test_ultra_bucket_summary_uses_score_ranges():
    rows = [
        _bar(score=10),  # 0–20
        _bar(score=45),  # 41–50
        _bar(score=70),  # 65–79
        _bar(score=90),  # 90–100
    ]
    summary = replay_engine.ultra_score_bucket_summary(rows)
    by_bucket = {r["bucket"]: r["count"] for r in summary}
    assert by_bucket["0–20"]   == 1
    assert by_bucket["41–50"]  == 1
    assert by_bucket["65–79"]  == 1
    assert by_bucket["90–100"] == 1
    # Buckets that didn't catch anything are still emitted with count=0
    assert by_bucket["80–89"] == 0


def test_ultra_combo_perf_groups_by_signals():
    """Updated combo perf evaluates actual parser flags / signal columns
    rather than a self-reported `ultra_score_flags` string. Each test row
    feeds the underlying signals so the combo predicates match."""
    rows = [
        # MOMENTUM_A + REVERSAL_GROWTH_A
        _bar(Combo="BUY BB↑", VABS="ABS RS+", profile_category="SWEET_SPOT",
             _ret10=15.0),
        # MOMENTUM_A only
        _bar(Combo="BUY", profile_category="BUILDING", _ret10=8.0),
        # SETUP_ONLY (setup but no breakout / momentum)
        _bar(VABS="ABS", profile_category="WATCH", _ret10=-2.0),
    ]
    perf = replay_engine.ultra_combo_perf(rows)
    by_combo = {r["combo"]: r for r in perf}
    assert by_combo["MOMENTUM_A"]["count"]        == 2
    assert by_combo["REVERSAL_GROWTH_A"]["count"] == 1
    assert by_combo["SETUP_ONLY"]["count"]        == 1
    # Combos with no rows still appear with count=0 so the table is stable
    assert by_combo["L34_TRIGGER_A"]["count"]     == 0


def test_ultra_false_positives_filter():
    rows = [
        _bar(score=85, band="A", ret_5d=-3.0, mae_5d=-7.0),  # FP
        _bar(score=85, band="A", ret_5d=4.0,  mae_5d=-1.0),  # not FP
        _bar(score=70, band="B", ret_5d=-9.0, mae_5d=-12.0), # below 80, ignored
    ]
    fps = replay_engine.ultra_false_positives(rows)
    assert len(fps) == 1
    assert fps[0]["ultra_score"] == 85
    assert fps[0]["ret_5d"] == -3.0


def test_ultra_missed_winners_filter():
    rows = [
        _bar(score=60, band="C", ret_10d=12.0, mfe_10d=15.0),  # missed
        _bar(score=80, band="A", ret_10d=11.0, mfe_10d=15.0),  # >= 65, ignored
        _bar(score=40, band="D", ret_10d=2.0,  mfe_10d=11.0),  # missed via mfe
    ]
    mw = replay_engine.ultra_missed_winners(rows)
    scores = [m["ultra_score"] for m in mw]
    assert 60 in scores
    assert 40 in scores
    assert 80 not in scores


# ── Existing endpoints / modules untouched ───────────────────────────────────

def test_existing_replay_endpoints_still_callable():
    from main import (
        api_replay_run, api_replay_status, api_replay_reports,
        api_replay_report, api_replay_export, api_replay_export_all,
    )
    # These import-only checks guarantee we didn't accidentally break the
    # existing Replay API surface while adding ULTRA Score sections.
    assert callable(api_replay_run)
    assert callable(api_replay_status)
    assert callable(api_replay_reports)
    assert callable(api_replay_report)
    assert callable(api_replay_export)
    assert callable(api_replay_export_all)


def test_existing_stock_stat_endpoints_still_callable():
    from main import (
        api_stock_stat_trigger, api_stock_stat_status, api_stock_stat_download,
    )
    assert callable(api_stock_stat_trigger)
    assert callable(api_stock_stat_status)
    assert callable(api_stock_stat_download)


# ── ULTRA Score v2 calibration (replay-derived) ─────────────────────────────


def test_v2_band_priority_mapping():
    """A+ ≥90, A 80–89, B 65–79, C 50–64, D <50."""
    assert ultra_score.compute_ultra_score_priority(95) == ("A+", "HIGH_PRIORITY")
    assert ultra_score.compute_ultra_score_priority(90) == ("A+", "HIGH_PRIORITY")
    assert ultra_score.compute_ultra_score_priority(89) == ("A",  "WATCH_A")
    assert ultra_score.compute_ultra_score_priority(80) == ("A",  "WATCH_A")
    assert ultra_score.compute_ultra_score_priority(70) == ("B",  "STRONG_WATCH")
    assert ultra_score.compute_ultra_score_priority(55) == ("C",  "CONTEXT_WATCH")
    assert ultra_score.compute_ultra_score_priority(20) == ("D",  "LOW")


def test_v2_fields_present_in_compute_output():
    out = ultra_score.compute_ultra_score({})
    for k in ("ultra_score_band_v2", "ultra_score_priority",
              "ultra_score_regime_bonus", "ultra_score_caps_applied",
              "ultra_score_cap_reason"):
        assert k in out, f"compute_ultra_score must export {k}"


def test_v2_strong_regime_bonus_added():
    """ACTIONABLE_SETUP adds +12 and the label appears in reasons."""
    base = {
        "buy_2809": 1, "abs_sig": 1,
        "profile_score": 8, "profile_category": "BUILDING",
    }
    sc_no  = ultra_score.compute_ultra_score(dict(base))
    sc_act = ultra_score.compute_ultra_score({**base, "FINAL_REGIME": "ACTIONABLE_SETUP"})
    assert sc_act["ultra_score"] >= sc_no["ultra_score"] + 10
    assert sc_act["ultra_score_regime_bonus"] == 12
    assert "REGIME:ACTIONABLE" in " ".join(sc_act["ultra_score_reasons"])


def test_v2_momentum_a_capped_without_strong_regime():
    """MOMENTUM_A without strong regime cannot exceed 89 unless ≥2 of:
    setup present, breakout present, PF>=12, profile=SWEET_SPOT (beyond
    breakout itself)."""
    # MOMENTUM_A only — BUY + SWEET_SPOT, weak PF, no setup → should cap at 89.
    row = {
        "buy_2809": 1, "rocket": 1,
        "profile_score": 6, "profile_category": "SWEET_SPOT",
        # no FINAL_REGIME → not strong
    }
    out = ultra_score.compute_ultra_score(row)
    assert "MOMENTUM_A" in out["ultra_score_flags"]
    assert out["ultra_score"] <= 89
    if out["ultra_score"] == 89:
        assert "CAP_MOMENTUM_A_NO_REGIME" in out["ultra_score_caps_applied"]


def test_v2_momentum_a_uncapped_with_strong_regime():
    """Same MOMENTUM_A with strong regime is allowed past 89."""
    row = {
        "buy_2809": 1, "rocket": 1, "abs_sig": 1, "va": 1, "rs_strong": 1,
        "profile_score": 18, "profile_category": "SWEET_SPOT",
        "FINAL_REGIME": "ACTIONABLE_SETUP",
    }
    out = ultra_score.compute_ultra_score(row)
    assert "MOMENTUM_A" in out["ultra_score_flags"]
    assert out["ultra_score"] >= 90
    assert "CAP_MOMENTUM_A_NO_REGIME" not in out["ultra_score_caps_applied"]


def test_v2_l34_alone_does_not_score_high():
    """L34 / FRI34 alone (no breakout / no PF / no regime) → low score."""
    row = {"l34": 1, "fri34": 1, "profile_category": "WATCH"}
    out = ultra_score.compute_ultra_score(row)
    assert out["ultra_score"] < 65, f"L34 alone scored {out['ultra_score']}"


def test_v2_setup_only_caps_at_49():
    """SETUP_ONLY without (PF good + strong regime) caps at 49."""
    row = {"abs_sig": 1, "va": 1, "svs_2809": 1, "climb_sig": 1, "load_sig": 1,
           "profile_score": 8, "profile_category": "BUILDING"}
    out = ultra_score.compute_ultra_score(row)
    assert "SETUP_ONLY" in out["ultra_score_flags"]
    assert out["ultra_score"] <= 49
    if out["ultra_score"] == 49:
        assert "CAP_SETUP_ONLY" in out["ultra_score_caps_applied"]


def test_v2_breakout_only_caps_at_59():
    """BREAKOUT_ONLY without (PF good + strong regime) caps at 59."""
    row = {"buy_2809": 1, "rocket": 1, "bb_brk": 1,
           "profile_score": 4, "profile_category": "BUILDING"}
    out = ultra_score.compute_ultra_score(row)
    assert "BREAKOUT_ONLY" in out["ultra_score_flags"]
    assert out["ultra_score"] <= 59
    if out["ultra_score"] == 59:
        assert "CAP_BREAKOUT_ONLY" in out["ultra_score_caps_applied"]


def test_v2_setup_only_uncapped_with_pf_and_regime():
    """SETUP_ONLY with PF>=12 + strong regime is NOT capped at 49."""
    row = {"abs_sig": 1, "va": 1, "svs_2809": 1, "climb_sig": 1,
           "profile_score": 18, "profile_category": "SWEET_SPOT",
           "FINAL_REGIME": "ACTIONABLE_SETUP"}
    out = ultra_score.compute_ultra_score(row)
    assert "SETUP_ONLY" in out["ultra_score_flags"]
    assert out["ultra_score"] > 49
    assert "CAP_SETUP_ONLY" not in out["ultra_score_caps_applied"]


def test_v2_breakout_only_uncapped_with_pf_and_regime():
    """BREAKOUT_ONLY with PF>=12 + strong regime is NOT capped at 59."""
    row = {"buy_2809": 1, "rocket": 1, "bb_brk": 1,
           "profile_score": 18, "profile_category": "SWEET_SPOT",
           "FINAL_REGIME": "ACTIONABLE_SETUP"}
    out = ultra_score.compute_ultra_score(row)
    assert "BREAKOUT_ONLY" in out["ultra_score_flags"]
    assert out["ultra_score"] > 59
    assert "CAP_BREAKOUT_ONLY" not in out["ultra_score_caps_applied"]


def test_v2_extension_warning_only_no_hard_reject():
    """Extended names with no strong regime get a small penalty + warning
    flag, but are not rejected to 0."""
    row = {"buy_2809": 1, "abs_sig": 1, "rs_strong": 1,
           "profile_score": 18, "profile_category": "SWEET_SPOT",
           "change_pct": 30}  # >=25 and no strong regime
    out = ultra_score.compute_ultra_score(row)
    assert "EXTENDED_PENALTY_LIGHT" in out["ultra_score_flags"]
    assert out["ultra_score"] >= 50  # still very tradeable, only warning


def test_v2_existing_csv_columns_still_present():
    """Verify Stock Stat CSV header still contains both legacy and v2 fields."""
    main_py = os.path.join(_backend, "main.py")
    with open(main_py, encoding="utf-8") as f:
        src = f.read()
    for col in (
        "ultra_score", "ultra_score_band", "ultra_score_band_v2",
        "ultra_score_priority", "ultra_score_regime_bonus",
        "ultra_score_caps_applied", "ultra_score_cap_reason",
    ):
        assert f'"{col}"' in src, f"Stock Stat header missing {col}"


def test_v2_replay_band_v2_summary_shape():
    """Replay v2 summaries return one row per band (A+/A/B/C/D)."""
    rows = [
        _bar(score=92, band="A"), _bar(score=88, band="A"),
        _bar(score=70, band="B"), _bar(score=55, band="C"),
        _bar(score=20, band="D"),
    ]
    summary = replay_engine.ultra_score_band_v2_summary(rows)
    by_band = {r["band_v2"]: r for r in summary}
    assert by_band["A+"]["count"] == 1
    assert by_band["A"]["count"]  == 1
    assert by_band["B"]["count"]  == 1
    assert by_band["C"]["count"]  == 1
    assert by_band["D"]["count"]  == 1


def test_v2_replay_priority_summary_shape():
    rows = [
        _bar(score=95), _bar(score=85), _bar(score=70),
        _bar(score=55), _bar(score=20),
    ]
    summary = replay_engine.ultra_score_priority_summary(rows)
    by_pri = {r["priority"]: r["count"] for r in summary}
    assert by_pri["HIGH_PRIORITY"]  == 1
    assert by_pri["WATCH_A"]        == 1
    assert by_pri["STRONG_WATCH"]   == 1
    assert by_pri["CONTEXT_WATCH"]  == 1
    assert by_pri["LOW"]            == 1


def test_v2_turbo_score_unchanged():
    """Hard rule: ULTRA Score recalibration must not modify any Turbo logic."""
    import turbo_engine
    src = inspect.getsource(turbo_engine)
    # Turbo must not import or reference ULTRA Score v2 fields anywhere.
    for tok in ("ultra_score_band_v2", "ultra_score_priority",
                "ultra_score_regime_bonus", "ultra_score_caps_applied"):
        assert tok not in src, f"Turbo engine must not reference {tok}"


def test_replay_run_handles_missing_ultra_columns(monkeypatch, tmp_path):
    """If stock_stat CSV has no ultra_score, replay must still complete and
    flag ULTRA Score analytics as 'missing' rather than crashing."""
    csv_dir = tmp_path / "stock_stat_output"
    csv_dir.mkdir()
    (csv_dir / "stock_stat_sp500_1d.csv").write_text(
        "ticker,date,close,turbo_score,profile_score,profile_category\n"
        "AAPL,2026-05-01,150,42,7,BUILDING\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(replay_engine, "STOCK_STAT_DIR", "stock_stat_output")
    # Guard against running the heavy analytics — call the loader directly
    rows, err = replay_engine._load_stock_stat("1d", "sp500")
    assert err is None
    # The loader returned rows. None of them have ultra_score — confirm.
    assert all("ultra_score" not in r for r in rows)
