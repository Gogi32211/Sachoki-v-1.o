"""Generate stock_stat_tz_wlnbb CSV with forward returns and sequence context."""
import csv
import logging
import time
import os
from datetime import datetime
from typing import Optional, Callable, List, Tuple
import pandas as pd

from .config import TZ_WLNBB_VERSION
from .signal_extraction import compute_signals_for_ticker

log = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
    "ticker", "date", "bar_datetime", "bar_index", "universe", "timeframe", "open", "high", "low", "close", "volume",
    "tz_wlnbb_version",
    "price_bucket", "is_sub_dollar", "is_penny_stock", "is_low_price", "is_high_price",
    "ema9", "ema20", "ema34", "ema50", "ema89", "ema200",
    "t_signal", "z_signal", "t_raw_signals", "z_raw_signals", "bull_priority_code", "bear_priority_code",
    "volume_bucket", "l_digits", "l_signal", "l34_active", "l43_active", "l64_active", "l22_active", "l_raw_signals",
    "preup_signal", "predn_signal", "preup_raw_signals", "predn_raw_signals",
    "ne_suffix", "wick_suffix",
    "penetration_suffix", "wick_penetration_upper", "wick_penetration_lower", "wick_penetration_both",
    "full_suffix",
    "wick_ext_up", "wick_ext_down", "wick_ext_both",
    "prev_body_top", "prev_body_bot", "prev_high", "prev_low",
    "composite_t_label", "composite_z_label", "composite_primary_label", "composite_all_labels",
    "composite_core", "composite_suffix", "composite_full_suffix", "composite_full_label",
    "lane1_label", "lane3_label", "combined_signal_text",
    "has_t_signal", "has_z_signal", "has_l_signal", "has_preup", "has_predn",
    "has_tz_l_combo", "has_bullish_context", "has_bearish_context",
    "prev_1_signal_summary", "prev_3_signal_summary", "prev_5_signal_summary",
    "t_after_z_confirmed", "z_after_t_confirmed", "l_after_z_confirmed",
    "preup_after_z_confirmed", "predn_after_t_confirmed",
    "ret_1d", "ret_3d", "ret_5d", "ret_10d",
    "max_high_5d", "max_high_10d", "max_drawdown_5d", "max_drawdown_10d",
    "mfe_5d", "mfe_10d", "mae_5d", "mae_10d",
    "clean_win_5d", "big_win_10d", "fail_5d", "fail_10d",
]


def classify_price_bucket(close) -> str:
    """Map a close price to a price-bucket label.
    LT1, 1_5, 5_20, 20_50, 50_150, 150_300, 300_PLUS — empty for invalid input.
    """
    try:
        c = float(close)
    except (TypeError, ValueError):
        return ""
    if c != c:  # NaN
        return ""
    if c < 1:    return "LT1"
    if c < 5:    return "1_5"
    if c < 20:   return "5_20"
    if c < 50:   return "20_50"
    if c < 150:  return "50_150"
    if c < 300:  return "150_300"
    return "300_PLUS"


def add_forward_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    df must be sorted by date ascending for a single ticker.
    All returns are close-to-close percentages.
    NEVER call this across multiple tickers.
    """
    c = df["close"]
    df["ret_1d"]  = (c.shift(-1)  / c - 1) * 100
    df["ret_3d"]  = (c.shift(-3)  / c - 1) * 100
    df["ret_5d"]  = (c.shift(-5)  / c - 1) * 100
    df["ret_10d"] = (c.shift(-10) / c - 1) * 100

    highs = df["high"]
    lows  = df["low"]

    for w, wk in [(5, "5d"), (10, "10d")]:
        mfe_vals, mae_vals, maxh_vals, mind_vals = [], [], [], []
        for i in range(len(df)):
            fut_h = highs.iloc[i+1:i+w+1]
            fut_l = lows.iloc[i+1:i+w+1]
            c0 = c.iloc[i]
            if len(fut_h) > 0 and c0 > 0:
                mh = fut_h.max()
                ml = fut_l.min()
                maxh_vals.append(round((mh - c0) / c0 * 100, 4))
                mind_vals.append(round((ml - c0) / c0 * 100, 4))
                mfe_vals.append(round((mh - c0) / c0 * 100, 4))
                mae_vals.append(round((ml - c0) / c0 * 100, 4))
            else:
                maxh_vals.append(None)
                mind_vals.append(None)
                mfe_vals.append(None)
                mae_vals.append(None)
        df[f"max_high_{wk}"]     = maxh_vals
        df[f"max_drawdown_{wk}"] = mind_vals
        df[f"mfe_{wk}"]          = mfe_vals
        df[f"mae_{wk}"]          = mae_vals

    # Outcome labels
    df["clean_win_5d"] = (df["ret_5d"]  >= 3.0).astype(int)
    df["big_win_10d"]  = (df["ret_10d"] >= 5.0).astype(int)
    df["fail_5d"]      = (df["ret_5d"]  <= -3.0).astype(int)
    df["fail_10d"]     = (df["ret_10d"] <= -5.0).astype(int)

    return df


def add_sequence_context(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add prev signal summaries and sequence confirmation flags.
    df must be sorted by date ascending for a single ticker.
    """
    def primary(row_dict):
        return (row_dict.get("t_signal") or row_dict.get("z_signal") or
                row_dict.get("l_signal") or row_dict.get("preup_signal") or
                row_dict.get("predn_signal") or "")

    summaries_1, summaries_3, summaries_5 = [], [], []
    t_after_z, z_after_t, l_after_z, preup_after_z, predn_after_t = [], [], [], [], []

    rows_list = [row._asdict() for row in df.itertuples(index=False)]

    for i in range(len(rows_list)):
        # prev 1 bar summary
        if i >= 1:
            summaries_1.append(primary(rows_list[i-1]))
        else:
            summaries_1.append("")

        # prev 3 bars summary
        prev3 = [primary(rows_list[j]) for j in range(max(0, i-3), i) if primary(rows_list[j])]
        summaries_3.append("|".join(prev3) if prev3 else "")

        # prev 5 bars summary
        prev5 = [primary(rows_list[j]) for j in range(max(0, i-5), i) if primary(rows_list[j])]
        summaries_5.append("|".join(prev5) if prev5 else "")

        # sequence confirmations: look back up to 5 bars for triggering signal
        curr = rows_list[i]
        curr_t = curr.get("t_signal", "")
        curr_z = curr.get("z_signal", "")
        curr_l = curr.get("l_signal", "")
        curr_preup = curr.get("preup_signal", "")
        curr_predn = curr.get("predn_signal", "")

        has_z_in_prev5 = any(rows_list[j].get("z_signal", "") for j in range(max(0, i-5), i))
        has_t_in_prev5 = any(rows_list[j].get("t_signal", "") for j in range(max(0, i-5), i))

        t_after_z.append(1 if (curr_t and has_z_in_prev5) else 0)
        z_after_t.append(1 if (curr_z and has_t_in_prev5) else 0)
        l_after_z.append(1 if (curr_l and has_z_in_prev5) else 0)
        preup_after_z.append(1 if (curr_preup and has_z_in_prev5) else 0)
        predn_after_t.append(1 if (curr_predn and has_t_in_prev5) else 0)

    df["prev_1_signal_summary"]   = summaries_1
    df["prev_3_signal_summary"]   = summaries_3
    df["prev_5_signal_summary"]   = summaries_5
    df["t_after_z_confirmed"]     = t_after_z
    df["z_after_t_confirmed"]     = z_after_t
    df["l_after_z_confirmed"]     = l_after_z
    df["preup_after_z_confirmed"] = preup_after_z
    df["predn_after_t_confirmed"] = predn_after_t

    return df


def generate_stock_stat(
    tickers: List[str],
    fetch_ohlcv_fn: Callable,  # callable(ticker, interval, calendar_days) -> pd.DataFrame or raises
    universe: str = "sp500",
    tf: str = "1d",
    bars: int = 500,  # now calendar_days (default 500 ≈ 320+ trading days)
    output_path: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    early_stop_fn: Optional[Callable[[], bool]] = None,
    min_price: float = 0,  # skip tickers whose latest close < min_price (e.g. 5 for nasdaq_gt5)
) -> Tuple[str, dict]:
    """Generate stock_stat CSV. Returns (output_path, audit_dict)."""
    if output_path is None:
        output_path = f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"

    t0 = time.time()
    total = len(tickers)
    audit = {
        "tickers_requested": total,
        "tickers_with_ohlcv": 0, "tickers_skipped_no_data": 0,
        "tickers_skipped_error": 0, "tickers_processed": 0,
        "rows_before_signals": 0, "rows_after_signals": 0, "rows_processed": 0,
        "rows_with_t_signal": 0, "rows_with_z_signal": 0,
        "rows_with_l_signal": 0, "rows_with_preup": 0,
        "rows_with_predn": 0, "rows_with_combos": 0,
        "skip_reasons": {},
    }

    log.info(
        "TZ_WLNBB_GENERATION_AUDIT: starting universe=%s tf=%s requested_tickers=%d output=%s",
        universe, tf, total, output_path,
    )

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)

        for ticker_idx, ticker in enumerate(tickers, start=1):
            if early_stop_fn and early_stop_fn():
                log.info("tz_wlnbb stock_stat: early stop requested after %d tickers", audit["tickers_processed"])
                break
            # Progress fires on every attempt so UI counter advances even when tickers are skipped
            if progress_callback:
                progress_callback(ticker_idx, total)
            try:
                df = fetch_ohlcv_fn(ticker, tf, bars)
                if df is None or len(df) < 2:
                    audit["tickers_skipped_no_data"] += 1
                    audit["skip_reasons"][ticker] = "no_data_or_too_short"
                    continue
                # Price filter: skip tickers below min_price (e.g. nasdaq_gt5 requires >= 5)
                if min_price > 0:
                    try:
                        latest_close = float(df["close"].iloc[-1])
                        if latest_close < min_price:
                            audit["tickers_skipped_no_data"] += 1
                            audit["skip_reasons"][ticker] = (
                                f"close_{latest_close:.2f}<min_price_{min_price}"
                            )
                            continue
                    except (KeyError, IndexError, ValueError):
                        pass  # close column unavailable — proceed, scanner will filter
                audit["tickers_with_ohlcv"] += 1
                audit["rows_before_signals"] += len(df)

                # Extract date/datetime from index BEFORE compute_signals_for_ticker
                # resets it to integer 0,1,2... via reset_index(drop=True).
                if "date" not in df.columns:
                    df["date"] = pd.to_datetime(df.index).strftime("%Y-%m-%d")
                # bar_datetime: full ISO datetime for intraday, date-only for daily
                if "bar_datetime" not in df.columns:
                    if tf in ("4h", "1h"):
                        df["bar_datetime"] = pd.to_datetime(df.index).strftime("%Y-%m-%d %H:%M")
                    else:
                        df["bar_datetime"] = df["date"]

                df = compute_signals_for_ticker(df, universe)
                # date column is now preserved as a regular column (not the index).

                # Sort chronologically using datetime parsing, not lexicographic string sort.
                df = df.sort_values(
                    by="date",
                    key=lambda s: pd.to_datetime(s, errors="coerce"),
                ).reset_index(drop=True)
                df["bar_index"] = range(len(df))

                # Add forward returns (single-ticker, close-to-close)
                df = add_forward_returns(df)

                # Add sequence context (single-ticker)
                df = add_sequence_context(df)

                audit["rows_after_signals"] += len(df)
                audit["tickers_processed"] += 1

                for _, row in df.iterrows():
                    date_val = row.get("date", "")
                    if not date_val:
                        continue
                    bar_datetime_val = row.get("bar_datetime") or date_val
                    t_raw_set = row.get("t_raw") or set()
                    z_raw_set = row.get("z_raw") or set()
                    preup_raw_set = row.get("preup_raw") or set()
                    predn_raw_set = row.get("predn_raw") or set()
                    t_raw_str = " ".join(sorted(t_raw_set)) if t_raw_set else ""
                    z_raw_str = " ".join(sorted(z_raw_set)) if z_raw_set else ""
                    preup_raw_str = " ".join(sorted(preup_raw_set)) if preup_raw_set else ""
                    predn_raw_str = " ".join(sorted(predn_raw_set)) if predn_raw_set else ""
                    l_raw_parts = []
                    for n in range(1, 7):
                        if row.get(f"l{n}_raw"):
                            l_raw_parts.append(f"L{n}")

                    def _val(v):
                        if v is None or (isinstance(v, float) and pd.isna(v)):
                            return ""
                        return v

                    close_val = row.get("close")
                    try:
                        cf = float(close_val) if close_val not in (None, "") else None
                        if cf != cf: cf = None  # NaN
                    except (TypeError, ValueError):
                        cf = None
                    price_bucket = classify_price_bucket(cf) if cf is not None else ""
                    is_sub_dollar  = int(cf is not None and cf < 1)
                    is_penny_stock = int(cf is not None and cf < 5)
                    is_low_price   = int(cf is not None and cf < 20)
                    is_high_price  = int(cf is not None and cf >= 150)

                    writer.writerow([
                        ticker, date_val, bar_datetime_val, int(row.get("bar_index", 0)), universe, tf,
                        _val(row.get("open")), _val(row.get("high")),
                        _val(row.get("low")), _val(row.get("close")),
                        _val(row.get("volume")),
                        TZ_WLNBB_VERSION,
                        price_bucket, is_sub_dollar, is_penny_stock, is_low_price, is_high_price,
                        _val(row.get("ema9")), _val(row.get("ema20")),
                        _val(row.get("ema34")), _val(row.get("ema50")),
                        _val(row.get("ema89")), _val(row.get("ema200")),
                        row.get("t_signal", ""), row.get("z_signal", ""),
                        t_raw_str, z_raw_str,
                        row.get("bull_priority_code", 0), row.get("bear_priority_code", 0),
                        row.get("volume_bucket", ""), row.get("l_digits", ""), row.get("l_signal", ""),
                        int(bool(row.get("l34_active"))), int(bool(row.get("l43_active"))),
                        int(bool(row.get("l64_active"))), int(bool(row.get("l22_active"))),
                        " ".join(l_raw_parts),
                        row.get("preup_signal", ""), row.get("predn_signal", ""),
                        preup_raw_str, predn_raw_str,
                        row.get("ne_suffix", ""), row.get("wick_suffix", ""),
                        row.get("penetration_suffix", ""),
                        int(bool(row.get("wick_penetration_upper"))),
                        int(bool(row.get("wick_penetration_lower"))),
                        int(bool(row.get("wick_penetration_both"))),
                        str(row.get("ne_suffix") or "") + str(row.get("wick_suffix") or "") + str(row.get("penetration_suffix") or ""),  # full_suffix
                        int(bool(row.get("wick_ext_up"))),
                        int(bool(row.get("wick_ext_down"))),
                        int(bool(row.get("wick_ext_both"))),
                        _val(row.get("prev_body_top")),
                        _val(row.get("prev_body_bot")),
                        _val(row.get("prev_high")),
                        _val(row.get("prev_low")),
                        row.get("composite_t_label", ""),
                        row.get("composite_z_label", ""),
                        row.get("composite_primary_label", ""),
                        row.get("composite_all_labels", ""),
                        row.get("composite_core", ""),
                        row.get("composite_suffix", ""),
                        row.get("composite_full_suffix", ""),
                        row.get("composite_full_label", ""),
                        row.get("lane1_label", ""), row.get("lane3_label", ""),
                        (row.get("lane1_label", "") + " " + row.get("lane3_label", "")).strip(),
                        int(bool(row.get("has_t_signal"))), int(bool(row.get("has_z_signal"))),
                        int(bool(row.get("has_l_signal"))), int(bool(row.get("has_preup"))),
                        int(bool(row.get("has_predn"))), int(bool(row.get("has_tz_l_combo"))),
                        int(bool(row.get("has_bullish_context"))), int(bool(row.get("has_bearish_context"))),
                        # sequence context
                        row.get("prev_1_signal_summary", ""),
                        row.get("prev_3_signal_summary", ""),
                        row.get("prev_5_signal_summary", ""),
                        row.get("t_after_z_confirmed", 0),
                        row.get("z_after_t_confirmed", 0),
                        row.get("l_after_z_confirmed", 0),
                        row.get("preup_after_z_confirmed", 0),
                        row.get("predn_after_t_confirmed", 0),
                        # forward returns
                        _val(row.get("ret_1d")), _val(row.get("ret_3d")),
                        _val(row.get("ret_5d")), _val(row.get("ret_10d")),
                        _val(row.get("max_high_5d")), _val(row.get("max_high_10d")),
                        _val(row.get("max_drawdown_5d")), _val(row.get("max_drawdown_10d")),
                        _val(row.get("mfe_5d")), _val(row.get("mfe_10d")),
                        _val(row.get("mae_5d")), _val(row.get("mae_10d")),
                        _val(row.get("clean_win_5d")), _val(row.get("big_win_10d")),
                        _val(row.get("fail_5d")), _val(row.get("fail_10d")),
                    ])
                    audit["rows_processed"] += 1
                    if row.get("has_t_signal"):    audit["rows_with_t_signal"] += 1
                    if row.get("has_z_signal"):    audit["rows_with_z_signal"] += 1
                    if row.get("has_l_signal"):    audit["rows_with_l_signal"] += 1
                    if row.get("has_preup"):       audit["rows_with_preup"] += 1
                    if row.get("has_predn"):       audit["rows_with_predn"] += 1
                    if row.get("has_tz_l_combo"):  audit["rows_with_combos"] += 1
            except Exception as exc:
                audit["tickers_skipped_error"] += 1
                audit["skip_reasons"][ticker] = str(exc)
                log.warning("tz_wlnbb stock_stat error for %s: %s", ticker, exc, exc_info=True)

    elapsed = round(time.time() - t0, 1)
    audit["elapsed_seconds"] = elapsed
    audit["output_path"] = output_path

    log.info(
        "TZ_WLNBB_GENERATION_AUDIT: universe=%s tf=%s "
        "requested=%d ohlcv_ok=%d skipped_no_data=%d skipped_error=%d processed=%d "
        "rows_before=%d rows_after=%d rows_written=%d "
        "t=%d z=%d l=%d preup=%d predn=%d combos=%d elapsed=%.1fs output=%s",
        universe, tf,
        audit["tickers_requested"], audit["tickers_with_ohlcv"],
        audit["tickers_skipped_no_data"], audit["tickers_skipped_error"],
        audit["tickers_processed"],
        audit["rows_before_signals"], audit["rows_after_signals"], audit["rows_processed"],
        audit["rows_with_t_signal"], audit["rows_with_z_signal"],
        audit["rows_with_l_signal"], audit["rows_with_preup"],
        audit["rows_with_predn"], audit["rows_with_combos"],
        elapsed, output_path,
    )

    if audit["rows_processed"] == 0:
        msg = (
            f"TZ_WLNBB_ANALYZER_FAILURE: stock_stat generation produced zero rows. "
            f"universe={universe} tf={tf} requested={total} "
            f"ohlcv_ok={audit['tickers_with_ohlcv']} errors={audit['tickers_skipped_error']} "
            f"no_data={audit['tickers_skipped_no_data']}. "
            f"Check ticker universe, OHLCV fetch, date range, and filters."
        )
        log.error(msg)
        # Include first few skip reasons in audit for debugging
        sample_errors = {k: v for k, v in list(audit["skip_reasons"].items())[:5]}
        audit["sample_skip_reasons"] = sample_errors

    return output_path, audit
