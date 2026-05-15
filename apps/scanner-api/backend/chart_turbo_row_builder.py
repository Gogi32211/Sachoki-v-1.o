"""
chart_turbo_row_builder.py — Phase 8I: flat-row builder for Turbo + RTB.

The verbatim-ported `compute_turbo_score(row, profile)` and `calc_rtb_v4(row, history, …)`
expect a flat row dict with specific boolean keys (load_sig, conso_2809, l34,
bo_up, fuchsia_rl, …) AND text keys (tz_sig, vol_bucket, rtb_phase) AND float
keys (close, open, high, low, rsi). The engine_registry has each engine's
output as a separate DataFrame.

This module is the **single point** where engine DataFrames → flat row dict
translation happens. Adding a new signal to the Turbo scoring just means
adding one line in _ENGINE_DF_KEYS or a custom branch — no edits elsewhere.

`build_turbo_row(pos, idf, tz_df, wl_df, vabs_df, combo_df, f_df, fly_df,
                  b_df, g_df, u260_df, uv2_df, gog_df)` returns the row for
bar at integer position `pos` (used so recent-N lookback flags can be
computed by slicing each engine df).
"""
from __future__ import annotations

import pandas as pd
from typing import Any


# ── Direct (column-name == row-key) copies, grouped by engine ────────────────
# When the engine DataFrame has a boolean column with the same name the
# scoring formula expects, just copy it. Engines with different naming
# (wlnbb uppercase, tz_df sig_name → tz_sig, etc.) are handled below.
_ENGINE_DF_KEYS: dict[str, tuple[str, ...]] = {
    "vabs": ("abs_sig", "climb_sig", "load_sig", "ns", "nd", "sc", "sq",
             "vbo_up", "vbo_dn", "best_sig", "strong_sig",
             "vol_spike_5x", "vol_spike_10x", "vol_spike_20x"),
    "combo": ("rocket", "buy_2809", "sig3g", "bb_brk", "atr_brk", "rtv",
              "preup3", "preup2", "preup50", "preup89",
              "hilo_buy", "hilo_sell", "bias_up", "bias_down",
              "cons_atr", "um_2809", "svs_2809", "conso_2809"),
    "f":  ("f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "any_f"),
    "fly":("fly_abcd", "fly_cd", "fly_bd", "fly_ad"),
    "b":  ("b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9", "b10", "b11"),
    "g":  ("g1", "g2", "g4", "g6", "g11"),
    "u260": ("sig_260308", "sig_l88"),
    "uv2":  ("eb_bull", "eb_bear", "fbo_bull", "fbo_bear",
             "bf_buy", "bf_sell",
             "ultra_sq", "ultra_ns", "ultra_nd", "ultra_3up", "ultra_3dn",
             "best_long", "best_short"),
}

# Renames for engines whose column names differ from the row-key turbo expects.
_WLNBB_RENAME: dict[str, str] = {
    "L34":  "l34",   "L43":  "l43",   "L64":  "l64",
    "L22":  "l22",   "L555": "l555",  "L1L2": "l1l2",
    "L2L5": "l2l5",  "ONLY_L2L4": "only_l2l4",
    "BLUE": "blue",
    "FRI34": "fri34", "FRI43": "fri43", "FRI64": "fri64",
    "UI":   "ui",
    "FUCHSIA_RH": "fuchsia_rh",
    "FUCHSIA_RL": "fuchsia_rl",
    "PRE_PUMP":   "pre_pump",
    "CCI_READY":  "cci_ready",
    "CCI_0_RETEST_OK": "cci_0_retest_ok",
    "CCI_BLUE_TURN":   "cci_blue_turn",
    "BO_UP": "bo_up", "BO_DN": "bo_dn",
    "BX_UP": "bx_up", "BX_DN": "bx_dn",
    "BE_UP": "be_up", "BE_DN": "be_dn",
}

# GOG output → row-key mapping. The old turbo formula reads composite
# "akan_sig / smx_sig / nnn_sig / mx_sig / gog_sig" booleans; the new GOG
# engine emits one boolean per tier. Map them as the old turbo expected:
#   A   -> akan_sig
#   SM  -> smx_sig
#   N   -> nnn_sig
#   MX  -> mx_sig
#   any GOG1/2/3 OR G*P/G*L/G*C -> gog_sig
_GOG_DIRECT_MAP: dict[str, str] = {"A": "akan_sig", "SM": "smx_sig",
                                    "N": "nnn_sig", "MX": "mx_sig"}
_GOG_TIER_COLS = ("G1P","G2P","G3P","G1L","G2L","G3L","G1C","G2C","G3C","GOG1","GOG2","GOG3")


# ── Recent-N flag spec ───────────────────────────────────────────────────────
# Each entry: (row_key, engine_df_alias, source_col_name_in_df, lookback)
# The flag is True if any of the last `lookback` bars (inclusive) had source_col=True.
_RECENT_FLAGS: list[tuple[str, str, str, int]] = [
    ("_l34_recent_3b",       "wlnbb", "L34", 3),
    ("_fri34_recent_3b",     "wlnbb", "FRI34", 3),
    ("_l34_recent_5b",       "wlnbb", "L34", 5),
    ("_l64_recent_5b",       "wlnbb", "L64", 5),
    ("_l43_recent_5b",       "wlnbb", "L43", 5),
    ("_l22_recent_5b",       "wlnbb", "L22", 5),
    ("_blue_recent_5b",      "wlnbb", "BLUE", 5),
    ("_l64_recent_15b",      "wlnbb", "L64", 15),
    ("_l22_recent_15b",      "wlnbb", "L22", 15),
    ("_l43_recent_10b",      "wlnbb", "L43", 10),
    ("_ns_recent_5b",        "vabs",  "ns",  5),
    ("_dabsorb_recent_5b",   "delta", "absorb_bull", 5),
    # Ztrap: Z9/Z10/Z11/Z12 in tz_df.sig_name. Handled inline below.
    ("_ztrap_recent_5b",     None,    None,  5),
    ("_ztrap_recent_15b",    None,    None, 15),
    # T10/T11/T12 in tz_df.sig_name. Handled inline below.
    ("_t10t11_recent_5b",    None,    None,  5),
]

_ZTRAP_SET   = {"Z9", "Z10", "Z11", "Z12"}
_T10T11_SET  = {"T10", "T11", "T12"}


def _scalar_bool(v) -> bool:
    try:
        return bool(v)
    except Exception:
        return False


def _df_col(df: pd.DataFrame | None, col: str, pos: int):
    if df is None or df.empty or col not in df.columns:
        return None
    if pos < 0 or pos >= len(df):
        return None
    return df.iloc[pos].get(col)


def _df_recent(df: pd.DataFrame | None, col: str, pos: int, n: int) -> bool:
    if df is None or df.empty or col not in df.columns:
        return False
    lo = max(0, pos - n + 1)
    return bool(df[col].iloc[lo:pos + 1].any())


def _tz_sig_recent(tz_df: pd.DataFrame | None, pos: int, n: int,
                   names: set) -> bool:
    if tz_df is None or tz_df.empty or "sig_name" not in tz_df.columns:
        return False
    lo = max(0, pos - n + 1)
    return bool(tz_df["sig_name"].iloc[lo:pos + 1].isin(names).any())


def build_turbo_row(
    pos: int,
    idf: pd.DataFrame,
    *,
    tz_df:    pd.DataFrame | None = None,
    wl_df:    pd.DataFrame | None = None,
    vabs_df:  pd.DataFrame | None = None,
    combo_df: pd.DataFrame | None = None,
    f_df:     pd.DataFrame | None = None,
    fly_df:   pd.DataFrame | None = None,
    b_df:     pd.DataFrame | None = None,
    g_df:     pd.DataFrame | None = None,
    u260_df:  pd.DataFrame | None = None,
    uv2_df:   pd.DataFrame | None = None,
    gog_df:   pd.DataFrame | None = None,
    delta_df: pd.DataFrame | None = None,
) -> dict:
    """
    Build the flat row dict for bar `pos` (integer location in idf.index).
    All boolean engine columns are coerced to plain `bool`; OHLCV / rsi /
    cci come from `idf`. Missing dfs (engine failed) produce False / None
    in their slots — never raises.
    """
    row: dict[str, Any] = {}

    # OHLCV + indicators
    ibar = idf.iloc[pos]
    for k in ("open", "high", "low", "close", "volume"):
        v = ibar.get(k)
        try:
            row[k] = float(v) if v is not None and v == v else 0.0
        except Exception:
            row[k] = 0.0
    for k in ("rsi", "cci"):
        v = ibar.get(k)
        try:
            row[k] = float(v) if v is not None and v == v else 50.0
        except Exception:
            row[k] = 50.0

    # TZ: sig_name → tz_sig, is_bull → tz_bull
    if tz_df is not None and not tz_df.empty:
        tz_row = tz_df.iloc[pos] if pos < len(tz_df) else None
        if tz_row is not None:
            sig = tz_row.get("sig_name")
            row["tz_sig"]  = str(sig) if sig and sig != "NONE" else ""
            row["tz_bull"] = _scalar_bool(tz_row.get("is_bull"))
            row["tz_bear"] = _scalar_bool(tz_row.get("is_bear"))
    row.setdefault("tz_sig",  "")
    row.setdefault("tz_bull", False)

    # WLNBB: rename UPPER → lower keys + vol_bucket / cci_sma
    if wl_df is not None and not wl_df.empty and pos < len(wl_df):
        wbar = wl_df.iloc[pos]
        for src, dst in _WLNBB_RENAME.items():
            row[dst] = _scalar_bool(wbar.get(src))
        vb = wbar.get("vol_bucket")
        row["vol_bucket"] = str(vb) if vb is not None and vb == vb else ""
        # rsi from wlnbb is generally same as idf.rsi; idf wins. cci_sma
        # is the CCI variant turbo references via cci_ready boolean, not
        # the numeric value.
    row.setdefault("vol_bucket", "")

    # Direct-copy engine boolean groups
    _df_groups = {
        "vabs": vabs_df, "combo": combo_df,
        "f":    f_df,    "fly":   fly_df,
        "b":    b_df,    "g":     g_df,
        "u260": u260_df, "uv2":   uv2_df,
    }
    for grp, df in _df_groups.items():
        keys = _ENGINE_DF_KEYS.get(grp, ())
        if df is None or df.empty or pos >= len(df):
            for k in keys:
                row.setdefault(k, False)
            continue
        bar = df.iloc[pos]
        for k in keys:
            row[k] = _scalar_bool(bar.get(k))

    # Delta → row keys with d_ prefix (mapping in chart_delta_engine.DELTA_TO_TURBO_ROW)
    if delta_df is not None and not delta_df.empty and pos < len(delta_df):
        dbar = delta_df.iloc[pos]
        try:
            from .chart_delta_engine import DELTA_TO_TURBO_ROW
        except Exception:
            DELTA_TO_TURBO_ROW = {}
        for src, dst in DELTA_TO_TURBO_ROW.items():
            row[dst] = _scalar_bool(dbar.get(src))
        # Also expose raw delta value for diagnostics
        try:
            row["delta"] = float(dbar.get("delta") or 0)
        except Exception:
            row["delta"] = 0.0
    else:
        from .chart_delta_engine import DELTA_TO_TURBO_ROW
        for dst in DELTA_TO_TURBO_ROW.values():
            row.setdefault(dst, False)

    # GOG → akan_sig / smx_sig / nnn_sig / mx_sig / gog_sig
    if gog_df is not None and not gog_df.empty and pos < len(gog_df):
        gbar = gog_df.iloc[pos]
        for src, dst in _GOG_DIRECT_MAP.items():
            row[dst] = _scalar_bool(gbar.get(src))
        row["gog_sig"] = any(_scalar_bool(gbar.get(c)) for c in _GOG_TIER_COLS)
    else:
        for dst in _GOG_DIRECT_MAP.values():
            row.setdefault(dst, False)
        row.setdefault("gog_sig", False)

    # Recent-N lookback flags
    _eng_to_df = {"wlnbb": wl_df, "vabs": vabs_df, "delta": delta_df,
                  "combo": combo_df}
    for key, eng, src, n in _RECENT_FLAGS:
        df_for_eng = _eng_to_df.get(eng)
        row[key] = _df_recent(df_for_eng, src, pos, n) if df_for_eng is not None else False
    # Ztrap (Z9/Z10/Z11/Z12) + T10/11/12 from tz_df.sig_name
    row["_ztrap_recent_5b"]  = _tz_sig_recent(tz_df, pos,  5, _ZTRAP_SET)
    row["_ztrap_recent_15b"] = _tz_sig_recent(tz_df, pos, 15, _ZTRAP_SET)
    row["_t10t11_recent_5b"] = _tz_sig_recent(tz_df, pos,  5, _T10T11_SET)

    # Engines we have NOT yet ported — keys default to False so scoring formulas
    # that read them treat them as "absent" (= no contribution).
    # Delta keys are now filled by the delta_df branch above.
    for k in ("x1_wick", "x1g_wick", "x2_wick", "x2g_wick", "x3_wick",
              "wick_bull",
              "para_retest", "para_plus", "para_start", "para_prep",
              "preup55", "preup66",
              "tz_bull_flip", "tz_attempt", "tz_weak_bull",
              "seq_bcont", "rs", "rs_strong",
              "cd", "ca", "cw",
              ):
        row.setdefault(k, False)

    # pp alias for RTB build base (line 121 in old rtb_engine)
    row.setdefault("pp", row.get("pre_pump", False))

    return row


__all__ = ["build_turbo_row"]
