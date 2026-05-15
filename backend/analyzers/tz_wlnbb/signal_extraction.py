"""Vectorized computation of TZ/WLNBB signals over a DataFrame of OHLCV bars."""
import pandas as pd
import numpy as np
from .signal_logic import compute_tz_wlnbb_for_bar
from .config import WLNBB_MA_PERIOD, USE_WICK, MIN_BODY_RATIO, DOJI_THRESH


def compute_emas(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA columns to df. df must have 'close' column."""
    for p in [9, 20, 34, 50, 89, 200]:
        df[f"ema{p}"] = df["close"].ewm(span=p, adjust=False).mean()
    return df


def compute_wlnbb(df: pd.DataFrame, period: int = WLNBB_MA_PERIOD) -> pd.DataFrame:
    """Add WLNBB Bollinger Band columns (period, std=1) on volume."""
    vol = df["volume"].fillna(0.0)
    mid = vol.rolling(period).mean()
    std = vol.rolling(period).std(ddof=0)
    df["wlnbb_mid"] = mid
    df["wlnbb_up"]  = mid + std
    df["wlnbb_low"] = mid - std
    return df


def compute_signals_for_ticker(df: pd.DataFrame, universe: str = "sp500") -> pd.DataFrame:
    """
    Given a OHLCV DataFrame (sorted oldest-first) for a single ticker,
    compute all TZ/WLNBB signals and return an enriched DataFrame.
    Requires columns: open, high, low, close, volume.
    """
    df = df.copy().reset_index(drop=True)
    compute_emas(df)
    compute_wlnbb(df)

    results = []
    prev_is_doji = False

    for i in range(len(df)):
        if i == 0:
            results.append(_empty_result())
            prev_is_doji = False
            continue

        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if pd.isna(row["close"]) or pd.isna(prev["close"]):
            results.append(_empty_result())
            prev_is_doji = False
            continue

        r = compute_tz_wlnbb_for_bar(
            o=float(row["open"]), h=float(row["high"]),
            l=float(row["low"]), c=float(row["close"]),
            v=float(row.get("volume", 0) or 0),
            prev_o=float(prev["open"]), prev_h=float(prev["high"]),
            prev_l=float(prev["low"]), prev_c=float(prev["close"]),
            prev_v=float(prev.get("volume", 0) or 0),
            ema9=float(row["ema9"]), ema20=float(row["ema20"]),
            ema34=float(row["ema34"]), ema50=float(row["ema50"]),
            ema89=float(row["ema89"]), ema200=float(row["ema200"]),
            vol_mid=float(row["wlnbb_mid"]) if not pd.isna(row["wlnbb_mid"]) else 0.0,
            vol_up=float(row["wlnbb_up"])   if not pd.isna(row["wlnbb_up"])  else 0.0,
            vol_low=float(row["wlnbb_low"]) if not pd.isna(row["wlnbb_low"]) else 0.0,
            prev_vol_mid=float(prev["wlnbb_mid"]) if not pd.isna(prev["wlnbb_mid"]) else 0.0,
            prev_vol_up=float(prev["wlnbb_up"])   if not pd.isna(prev["wlnbb_up"])  else 0.0,
            prev_vol_low=float(prev["wlnbb_low"]) if not pd.isna(prev["wlnbb_low"]) else 0.0,
            prev_is_doji=prev_is_doji,
            use_wick=USE_WICK, min_body_ratio=MIN_BODY_RATIO, doji_thresh=DOJI_THRESH,
        )
        prev_is_doji = r["is_doji"]
        results.append(r)

    result_df = pd.DataFrame(results)
    for col in result_df.columns:
        df[col] = result_df[col].values
    return df


def _empty_result() -> dict:
    """Return a zeroed-out result dict for the first bar or error cases."""
    return {
        "is_bull": False, "is_bear": False, "is_doji": False,
        "t_raw": set(), "z_raw": set(),
        "t_signal": "", "z_signal": "",
        "bull_priority_code": 0, "bear_priority_code": 0,
        "preup_signal": "", "predn_signal": "",
        "preup_raw": set(), "predn_raw": set(),
        "volume_bucket": "", "vol_down_adapted": False, "vol_up_adapted": False,
        "l1_raw": False, "l2_raw": False, "l3_raw": False,
        "l4_raw": False, "l5_raw": False, "l6_raw": False,
        "l34_active": False, "l43_active": False, "l64_active": False, "l22_active": False,
        "l_digits": "", "l_signal": "",
        "ne_suffix": "", "wick_suffix": "",
        "penetration_suffix": "",
        "wick_penetration_upper": False, "wick_penetration_lower": False, "wick_penetration_both": False,
        "wick_ext_up": False, "wick_ext_down": False, "wick_ext_both": False,
        "prev_body_top": 0.0, "prev_body_bot": 0.0, "prev_high": 0.0, "prev_low": 0.0,
        "composite_t_label": "", "composite_z_label": "",
        "composite_primary_label": "", "composite_all_labels": "",
        "composite_core": "", "composite_suffix": "",
        "composite_full_suffix": "", "composite_full_label": "",
        "lane1_label": "", "lane3_label": "",
        "has_t_signal": False, "has_z_signal": False, "has_l_signal": False,
        "has_preup": False, "has_predn": False,
        "has_tz_l_combo": False, "has_bullish_context": False, "has_bearish_context": False,
    }
