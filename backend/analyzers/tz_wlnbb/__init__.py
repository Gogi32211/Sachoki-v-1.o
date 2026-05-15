from .config import TZ_WLNBB_VERSION
from .signal_logic import compute_tz_wlnbb_for_bar


def compute_signals_for_ticker(df, universe: str = "sp500"):
    """Lazy wrapper — defers pandas import until called."""
    from .signal_extraction import compute_signals_for_ticker as _fn
    return _fn(df, universe)


def compute_tz_wlnbb_for_row(row: dict, universe: str = "sp500", timeframe: str = "1d") -> dict:
    """
    Public single-row API. Computes signals for a bar dict that already has EMA/BB columns.
    Used by API debug endpoint. For bulk processing use compute_signals_for_ticker.
    """
    from .signal_logic import compute_tz_wlnbb_for_bar
    from .config import USE_WICK, MIN_BODY_RATIO, DOJI_THRESH
    import math

    def _f(key, default=0.0):
        v = row.get(key, default)
        try:
            v = float(v)
            return 0.0 if math.isnan(v) or math.isinf(v) else v
        except Exception:
            return default

    return compute_tz_wlnbb_for_bar(
        o=_f("open"), h=_f("high"), l=_f("low"), c=_f("close"), v=_f("volume"),
        prev_o=_f("prev_open"), prev_h=_f("prev_high"), prev_l=_f("prev_low"),
        prev_c=_f("prev_close"), prev_v=_f("prev_volume"),
        ema9=_f("ema9"), ema20=_f("ema20"), ema34=_f("ema34"),
        ema50=_f("ema50"), ema89=_f("ema89"), ema200=_f("ema200"),
        vol_mid=_f("wlnbb_mid"), vol_up=_f("wlnbb_up"), vol_low=_f("wlnbb_low"),
        prev_vol_mid=_f("prev_wlnbb_mid"), prev_vol_up=_f("prev_wlnbb_up"),
        prev_vol_low=_f("prev_wlnbb_low"),
        prev_is_doji=bool(row.get("prev_is_doji", False)),
        use_wick=USE_WICK, min_body_ratio=MIN_BODY_RATIO, doji_thresh=DOJI_THRESH,
    )


__all__ = [
    "TZ_WLNBB_VERSION",
    "compute_tz_wlnbb_for_bar",
    "compute_signals_for_ticker",
    "compute_tz_wlnbb_for_row",
]
