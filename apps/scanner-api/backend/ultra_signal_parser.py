"""
ultra_signal_parser.py — stub for scanner-api.

Production ultra_signal_parser parses complex Stock Stat / Bulk Signal CSV
columns (T, Z, L, Combo, VABS, …) into canonical signal flags.

The scanner-api uses flat boolean keys only (buy_2809, rocket, bb_brk, …).
ultra_score._signal_set() already has a _LIVE_KEY_TO_CANON fallback that
handles those flat keys directly — so this stub returns an empty dict and
lets the fallback do the work.
"""
from __future__ import annotations


def parse_stock_stat_signals(row: dict) -> dict:
    """Return empty dict — scanner-api rows use flat boolean keys handled by _LIVE_KEY_TO_CANON."""
    return {}
