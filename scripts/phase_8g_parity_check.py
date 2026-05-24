"""
phase_8g_parity_check.py — direct old-vs-new engine parity harness.

Loads old backend/*_engine.py AND new apps/scanner-api/backend/chart_*_engine.py
in the same Python process, feeds them identical multi-regime synthetic OHLCV,
and compares output dataframes column-by-column, bar-by-bar.

This is the strongest formula-parity check feasible without network/API access.
For a verbatim port, every column must agree on every bar.

Run:
    python3 scripts/phase_8g_parity_check.py
"""
from __future__ import annotations

import os
import sys
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

# Old root backend uses absolute imports (`from indicators import ...`,
# `from signal_engine import ...`). Put it on sys.path FIRST so old modules
# resolve to old indicators.py / signal_engine.py.
sys.path.insert(0, os.path.join(ROOT, "backend"))
# Then add scanner-api for new modules.
sys.path.insert(0, os.path.join(ROOT, "apps", "scanner-api"))

import importlib


# ── Synthetic OHLCV generators ────────────────────────────────────────────────

def gen_trending(seed: int, n: int = 250) -> pd.DataFrame:
    """Random walk with upward drift — exercises T-side."""
    rng = np.random.RandomState(seed)
    drift = np.linspace(0, 30, n)
    noise = np.cumsum(rng.randn(n)) * 0.5
    base = 100 + drift + noise
    return _ohlcv_from_base(base, rng)


def gen_choppy(seed: int, n: int = 250) -> pd.DataFrame:
    """Sideways with volatility spikes — exercises VABS / WICK / SQ."""
    rng = np.random.RandomState(seed)
    base = 100 + np.cumsum(rng.randn(n)) * 0.3
    return _ohlcv_from_base(base, rng, vol_scale=2.0)


def gen_reversal(seed: int, n: int = 250) -> pd.DataFrame:
    """Down trend then sharp reversal — exercises Z/T/COMBO/FLY."""
    rng = np.random.RandomState(seed)
    down = np.linspace(0, -25, n // 2)
    up   = np.linspace(-25, 15, n - n // 2)
    base = 100 + np.concatenate([down, up]) + np.cumsum(rng.randn(n)) * 0.3
    return _ohlcv_from_base(base, rng)


def _ohlcv_from_base(base: np.ndarray, rng: np.random.RandomState, vol_scale: float = 1.0) -> pd.DataFrame:
    n = len(base)
    df = pd.DataFrame({
        "open":  base + rng.randn(n) * 0.2,
        "high":  base + np.abs(rng.randn(n)) * 0.5 + 0.3,
        "low":   base - np.abs(rng.randn(n)) * 0.5 - 0.3,
        "close": base + rng.randn(n) * 0.2,
        "volume": np.abs(rng.randn(n) * 3e5 + 1.5e6) * vol_scale,
    }, index=pd.date_range("2024-01-01", periods=n, freq="D"))
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"]  = df[["low",  "open", "close"]].min(axis=1)
    return df


# ── Engine-pair definitions ───────────────────────────────────────────────────
# Each entry: name, old (module, fn), new (module, fn), input deps (None = OHLCV df only)

ENGINE_PAIRS = [
    {
        "name":   "signal_engine (T/Z)",
        "old":    ("signal_engine", "compute_signals"),
        "new":    ("backend.engine_api.chart_signal_engine", "compute_signals"),
        "compare_cols": ["bc", "zc", "sig_id", "sig_name"],
    },
    {
        "name":   "wlnbb",
        "old":    ("wlnbb_engine", "compute_wlnbb"),
        "new":    ("backend.engine_api.chart_wlnbb_engine", "compute_wlnbb"),
        "compare_cols": ["L34", "L43", "L64", "L22", "L555", "BLUE", "BO_UP", "BE_UP"],
    },
    {
        "name":   "vabs",
        "old":    ("vabs_engine", "compute_vabs"),
        "new":    ("backend.engine_api.chart_vabs_engine", "compute_vabs"),
        "compare_cols": ["abs_sig", "climb_sig", "load_sig", "ns", "nd", "sq", "bc", "vbo_up", "vbo_dn"],
    },
    {
        "name":   "wick",
        "old":    ("wick_engine", "compute_wick"),
        "new":    ("backend.engine_api.chart_wick_engine", "compute_wick"),
        "compare_cols": ["WICK_PATTERN", "WICK_BULL_PATTERN", "WICK_BEAR_PATTERN",
                         "WICK_BULL_CONFIRM", "WICK_BEAR_CONFIRM"],
    },
    {
        "name":   "combo",
        "old":    ("combo_engine", "compute_combo"),
        "new":    ("backend.engine_api.chart_combo_engine", "compute_combo"),
        "compare_cols": ["rocket", "buy_2809", "sig3g", "bb_brk", "atr_brk", "rtv",
                         "hilo_buy", "preup3", "preup2", "preup50", "preup89"],
    },
    {
        "name":   "f_engine",
        "old":    ("f_engine", "compute_f_signals"),
        "new":    ("backend.engine_api.chart_f_engine", "compute_f_signals"),
        "compare_cols": ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "any_f"],
    },
    {
        "name":   "fly_engine",
        "old":    ("fly_engine", "compute_fly_series"),
        "new":    ("backend.engine_api.chart_fly_engine", "compute_fly_series"),
        "compare_cols": ["fly_abcd", "fly_cd", "fly_bd", "fly_ad"],
    },
    {
        "name":   "b_engine (B1–B11)",
        "old":    ("signal_engine", "compute_b_signals"),
        "new":    ("backend.engine_api.chart_b_engine", "compute_b_signals"),
        "compare_cols": [f"b{i}" for i in range(1, 12)],
    },
    {
        "name":   "g_engine (G1/G2/G4/G6/G11)",
        "old":    ("signal_engine", "compute_g_signals"),
        "new":    ("backend.engine_api.chart_b_engine", "compute_g_signals"),
        "compare_cols": ["g1", "g2", "g4", "g6", "g11"],
    },
    {
        "name":   "ultra_engine.compute_260308_l88",
        "old":    ("ultra_engine", "compute_260308_l88"),
        "new":    ("backend.engine_api.chart_ultra_engine", "compute_260308_l88"),
        "compare_cols": ["sig_260308", "sig_l88"],
    },
    {
        "name":   "ultra_engine.compute_ultra_v2",
        "old":    ("ultra_engine", "compute_ultra_v2"),
        "new":    ("backend.engine_api.chart_ultra_engine", "compute_ultra_v2"),
        "compare_cols": ["eb_bull", "eb_bear", "fbo_bull", "fbo_bear",
                         "bf_buy", "bf_sell", "ultra_sq", "ultra_ns", "ultra_nd",
                         "ultra_3up", "ultra_3dn", "best_long", "best_short"],
    },
]


# ── Special case: GOG (takes 7 engine outputs as args) ────────────────────────

def parity_gog(df: pd.DataFrame) -> dict:
    """GOG is fed engine outputs; we compute them both ways and compare GOG output."""
    import signal_engine as old_sig
    import wlnbb_engine  as old_wl
    import vabs_engine   as old_vabs
    import combo_engine  as old_combo
    import f_engine      as old_f
    import ultra_engine  as old_ult
    import gog_engine    as old_gog

    from backend.engine_api.chart_signal_engine import compute_signals as new_sig
    from backend.engine_api.chart_wlnbb_engine  import compute_wlnbb   as new_wl
    from backend.engine_api.chart_vabs_engine   import compute_vabs    as new_vabs
    from backend.engine_api.chart_combo_engine  import compute_combo   as new_combo
    from backend.engine_api.chart_f_engine      import compute_f_signals as new_f
    from backend.engine_api.chart_ultra_engine  import compute_260308_l88, compute_ultra_v2
    from backend.engine_api.chart_gog_engine    import compute_gog_signals as new_gog

    # Old pipeline
    old_sig_df = old_sig.compute_signals(df)
    old_wl_df  = old_wl.compute_wlnbb(df)
    old_v_df   = old_vabs.compute_vabs(df)
    old_f_df   = old_f.compute_f_signals(df)
    old_u260   = old_ult.compute_260308_l88(df)
    old_uv2    = old_ult.compute_ultra_v2(df)
    old_c_df   = old_combo.compute_combo(df)
    old_out    = old_gog.compute_gog_signals(df, old_wl_df, old_sig_df, old_f_df,
                                              old_v_df, old_u260, old_uv2, old_c_df)

    # New pipeline
    new_sig_df = new_sig(df)
    new_wl_df  = new_wl(df)
    new_v_df   = new_vabs(df)
    new_f_df   = new_f(df)
    new_u260   = compute_260308_l88(df)
    new_uv2    = compute_ultra_v2(df)
    new_c_df   = new_combo(df)
    new_out    = new_gog(df, new_wl_df, new_sig_df, new_f_df,
                          new_v_df, new_u260, new_uv2, new_c_df)

    # Compare a representative slice of GOG output columns
    cols = ["A", "SM", "N", "MX", "GOG1", "GOG2", "GOG3",
            "G1P", "G2P", "G3P", "G1L", "G2L", "G3L", "G1C", "G2C", "G3C",
            "LD", "LDS", "LDC", "LDP", "LRC", "LRP", "WRC", "F8C",
            "SQB", "BCT", "SVS",
            "GOG_TIER", "GOG_SCORE"]
    return _diff(old_out, new_out, cols, name="gog_engine")


# ── Generic compare ───────────────────────────────────────────────────────────

def _diff(old_df, new_df, cols, name: str) -> dict:
    """Return dict per column: {col: {"status": MATCH|DIFF, "diff_bars": int}}"""
    out = {}
    for col in cols:
        if col not in old_df.columns or col not in new_df.columns:
            out[col] = {"status": "MISSING_COL",
                        "old_has": col in old_df.columns,
                        "new_has": col in new_df.columns}
            continue
        o = old_df[col]
        n = new_df[col]
        if pd.api.types.is_numeric_dtype(o) or pd.api.types.is_bool_dtype(o):
            # NaN-tolerant numeric/bool compare
            mask = ~((o.isna() & n.isna()) | (o == n))
            diff_n = int(mask.sum())
        else:
            mask = (o.fillna("") != n.fillna(""))
            diff_n = int(mask.sum())
        status = "MATCH" if diff_n == 0 else "FORMULA_DIFF"
        out[col] = {"status": status, "diff_bars": diff_n, "total_bars": len(o)}
    return {"name": name, "cols": out}


def _summarize(report: dict) -> tuple[int, int, int]:
    total = match = diff = 0
    for col, info in report["cols"].items():
        total += 1
        if info["status"] == "MATCH":
            match += 1
        else:
            diff += 1
    return total, match, diff


# ── Main driver ───────────────────────────────────────────────────────────────

def main() -> int:
    scenarios = [
        ("trending_seed7",  gen_trending(7)),
        ("trending_seed42", gen_trending(42)),
        ("choppy_seed3",    gen_choppy(3)),
        ("choppy_seed99",   gen_choppy(99)),
        ("reversal_seed13", gen_reversal(13)),
        ("reversal_seed55", gen_reversal(55)),
    ]

    all_reports: list[tuple[str, dict]] = []
    overall_match = 0
    overall_total = 0

    for scenario_name, df in scenarios:
        print(f"\n=== scenario: {scenario_name}  (n={len(df)} bars) ===")
        for pair in ENGINE_PAIRS:
            old_mod_name, old_fn_name = pair["old"]
            new_mod_name, new_fn_name = pair["new"]
            try:
                old_mod = importlib.import_module(old_mod_name)
                new_mod = importlib.import_module(new_mod_name)
                old_fn = getattr(old_mod, old_fn_name)
                new_fn = getattr(new_mod, new_fn_name)
                old_out = old_fn(df)
                new_out = new_fn(df)
            except Exception as exc:
                print(f"  {pair['name']:40s}  RUN_ERROR: {type(exc).__name__}: {exc}")
                continue
            rep = _diff(old_out, new_out, pair["compare_cols"], name=pair["name"])
            t, m, d = _summarize(rep)
            overall_total += t
            overall_match += m
            mark = "✅" if d == 0 else "❌"
            print(f"  {mark} {pair['name']:40s}  cols={t}  match={m}  diff={d}")
            if d > 0:
                for col, info in rep["cols"].items():
                    if info["status"] != "MATCH":
                        print(f"      {col}: {info}")
            all_reports.append((scenario_name, rep))

        # GOG (special)
        try:
            gog_rep = parity_gog(df)
            t, m, d = _summarize(gog_rep)
            overall_total += t
            overall_match += m
            mark = "✅" if d == 0 else "❌"
            print(f"  {mark} {'gog_engine':40s}  cols={t}  match={m}  diff={d}")
            if d > 0:
                for col, info in gog_rep["cols"].items():
                    if info["status"] != "MATCH":
                        print(f"      {col}: {info}")
            all_reports.append((scenario_name, gog_rep))
        except Exception as exc:
            print(f"  gog_engine                                RUN_ERROR: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 60)
    print(f"OVERALL: {overall_match}/{overall_total} columns MATCH "
          f"({100.0 * overall_match / max(overall_total, 1):.2f}%)")
    print("=" * 60)
    return 0 if overall_match == overall_total else 1


if __name__ == "__main__":
    sys.exit(main())
