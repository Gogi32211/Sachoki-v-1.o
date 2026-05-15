"""
Unit tests for signal_engine.py.

Each test constructs a minimal OHLCV DataFrame that should produce a known
signal on the LAST bar (bar index -1), then asserts the correct sig_id.

Usage:
    python -m pytest test_signal_engine.py -v
    # or
    python test_signal_engine.py
"""

from __future__ import annotations

import sys
import traceback
from typing import Callable

import numpy as np
import pandas as pd

from signal_engine import (
    NONE, T1G, T1, T2G, T2, T3, T4, T5, T6, T9, T10, T11,
    Z1G, Z1, Z2G, Z2, Z3, Z4, Z5, Z6, Z7, Z8, Z9, Z10, Z11, Z12,
    SIG_NAMES, BULLISH_SIGS, BEARISH_SIGS,
    compute_signals, ok3,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}

def _df(*bars: dict) -> pd.DataFrame:
    """Build a DataFrame from a sequence of bar dicts."""
    return pd.DataFrame(list(bars))

def _last_sig(df: pd.DataFrame) -> int:
    return int(compute_signals(df)["sig_id"].iloc[-1])


# ---------------------------------------------------------------------------
# Test cases  (name → (df_builder, expected_sig_id))
# ---------------------------------------------------------------------------

CASES: list[tuple[str, Callable[[], pd.DataFrame], int]] = []

def _case(name: str, expected: int):
    """Decorator to register a test-case builder."""
    def decorator(fn: Callable[[], pd.DataFrame]):
        CASES.append((name, fn, expected))
        return fn
    return decorator


# ── NONE ────────────────────────────────────────────────────────────────────

@_case("NONE — ordinary bar, no pattern", NONE)
def _():
    return _df(
        _bar(100, 105, 99,  104),   # plain bull bar, prev needed
        _bar(104, 106, 103, 104.5), # small body, no pattern
    )


# ── DOJI / Z7 ───────────────────────────────────────────────────────────────

@_case("Z7 — doji: body/range <= 0.05", Z7)
def _():
    # range=2, body=0.05, pct=0.025 ≤ 0.05 ✓
    # body_top=100.05, rng_mid=(99+101)/2=100, body_top > rng_mid → T5 fails (not < rng_mid)
    # body_bottom=100, rng_mid=100, NOT strictly > rng_mid → T3 fails
    return _df(
        _bar(100, 110, 95, 102),
        _bar(100, 101, 99, 100.05), # doji: body=0.05, rng=2, pct=0.025
    )

@_case("Z7 — doji: body = 0 (open == close)", Z7)
def _():
    return _df(
        _bar(100, 110, 95, 102),
        _bar(100, 110, 90, 100),   # flat doji
    )

@_case("Z7 NOT fired when another signal present (T1 overrides)", T1)
def _():
    # prev bar has large body (body_pct=0.60) so T6 won't fire; T1 fires instead
    return _df(
        _bar(95, 105, 90, 104),    # prev: body=9, rng=15, pct=0.60 → T6 blocked
        _bar(100, 110, 99, 109),   # body=9, rng=11, pct=0.82 → T1
    )


# ── T1G ─────────────────────────────────────────────────────────────────────

@_case("T1G — bullish marubozu with gap-up", T1G)
def _():
    # prev close = 100; current open = 102 (gap-up)
    # body = 8, range = 9, body_pct = 0.89; upper_wick=0.5, lower_wick=0.5
    return _df(
        _bar(95, 101, 94, 100),
        _bar(102, 110.5, 101.5, 110), # gap-up, big body
    )


# ── T1 ──────────────────────────────────────────────────────────────────────

@_case("T1 — bullish marubozu no gap", T1)
def _():
    # open == prev close (no gap); big body
    return _df(
        _bar(95, 101, 94, 100),
        _bar(100, 110.5, 99.5, 110),  # no gap
    )


# ── T2G ─────────────────────────────────────────────────────────────────────

@_case("T2G — bullish engulfing with gap-down open", T2G)
def _():
    # prev: bear bar  open=110 close=100  (body_top=110, body_bottom=100)
    # current: open=98 (< prev_low=99), close=112 (> prev_body_top=110)
    return _df(
        _bar(110, 112, 99, 100),    # prev bear
        _bar(98, 115, 97, 112),     # bull engulfs, opens below prev low
    )


# ── T2 ──────────────────────────────────────────────────────────────────────

@_case("T2 — bullish engulfing no gap", T2)
def _():
    return _df(
        _bar(110, 112, 100, 102),   # prev bear (o=110, c=102 … wait, c>o → bull)
        # need prev bear: o < c is bull, o > c is bear
        _bar(110, 112, 100, 102),   # prev: open=110, close=102 → bear ✓
        _bar(101, 115, 100, 112),   # engulfs prev body [102-110], no gap
    )

@_case("T2 — bullish engulfing (clean)", T2)
def _():
    # prev bear: o=108, c=102
    # current bull: o=103, c=109  → c>prev_body_top(108), o<prev_body_bottom(102)? No
    # Let's be explicit:
    # prev bear_body_top = o = 108, prev_body_bottom = c = 102
    # current: o=101 < 102 ✓, c=109 > 108 ✓
    return _df(
        _bar(95, 110, 94, 100),
        _bar(108, 109, 101, 102),   # prev bear: o=108,c=102
        _bar(101, 112, 100.5, 109), # bull engulfing
    )


# ── T3 — Hammer ─────────────────────────────────────────────────────────────

@_case("T3 — hammer: bull bar, small body upper half, long lower wick", T3)
def _():
    # bull bar: o=16, c=18, h=18, l=0 → range=18, body=2, pct=0.11 ✓
    # body_bottom=16, rng_mid=(0+18)/2=9, 16>9 ✓
    # lower_wick=16-0=16 >= 2*2=4 ✓; upper_wick=18-18=0 ✓
    return _df(
        _bar(50, 60, 45, 55),
        _bar(16, 18, 0, 18),   # bull hammer: open=16,close=18
    )


# ── T4 ──────────────────────────────────────────────────────────────────────

@_case("T4 — piercing line: opens below prev low, closes above prev mid", T4)
def _():
    # prev bull bar: o=100, c=110, h=111, l=99  → prev_body=10
    # We need prev_bear: o=110, c=100, h=111, l=99
    # T4: bull bar opens below prev_l(99), closes above prev_c + prev_body*0.5 = 100+5=105, closes < prev_o=110
    return _df(
        _bar(110, 111, 99, 100),    # prev bear
        _bar(98, 112, 97, 107),     # T4 piercing
    )


# ── T5 — Inverted Hammer ────────────────────────────────────────────────────

@_case("T5 — inverted hammer: bull bar, small body lower half, long upper wick", T5)
def _():
    # bull bar: o=1, c=4, h=20, l=0 → range=20, body=3, pct=0.15 ✓
    # body_top=4 (bull: body_top=c), rng_mid=(0+20)/2=10, 4<10 ✓
    # upper_wick=20-4=16 >= 2*3=6 ✓; lower_wick=1-0=1, 1/20=0.05 ✓
    return _df(
        _bar(50, 60, 45, 55),
        _bar(1, 20, 0, 4),   # bull inverted hammer: open=1,close=4
    )


# ── T6 — Morning Star equivalent ────────────────────────────────────────────

@_case("T6 — strong bull after small-body bar, closes above prev open", T6)
def _():
    # prev: body=3, rng=14, pct=0.21 <= 0.30 ✓; o=100
    # current: big bull body >= 60%, c=114 > prev_o=100 ✓
    return _df(
        _bar(95, 110, 90, 102),
        _bar(100, 112, 98, 103),    # prev small body: body=3, rng=14, pct=0.21 ✓
        _bar(102, 115, 101.5, 114), # big bull, c=114 > prev_o=100 ✓
    )


# ── T9 — Bullish Harami ──────────────────────────────────────────────────────

@_case("T9 — bullish harami: current bar inside large prev bear body", T9)
def _():
    # prev bear: o=120, c=100, h=121, l=99  body=20, range=22, body_pct=0.91
    # current: body_top < 120, body_bottom > 100
    return _df(
        _bar(90, 100, 85, 95),
        _bar(120, 121, 99, 100),    # prev bear
        _bar(108, 115, 105, 112),   # harami inside
    )


# ── T10 — Tweezer Bottom ────────────────────────────────────────────────────

@_case("T10 — tweezer bottom: matching lows within 0.1%", T10)
def _():
    # current bar: body=7.95/rng=15 = 0.53 < 0.60 → T1 won't fire (small enough)
    # large upper wick ensures T1 fails the wick check
    return _df(
        _bar(95, 110, 90, 102),
        _bar(108, 110, 100.00, 102),    # prev bear, low=100
        _bar(100.05, 115, 100.00, 108), # bull, low≈100, body=7.95/rng=15=0.53 → no T1
    )


# ── T11 — Three-bar bullish ─────────────────────────────────────────────────

@_case("T11 — two prior bears then bull closing above bar[-3] open", T11)
def _():
    # bar[-1]: o=106 > prev body_bottom(105) → T2 blocked; upper_wick=6/22=0.27→T1 blocked
    return _df(
        _bar(95, 100, 90, 98),
        _bar(120, 121, 109, 110),   # bar[-3] bear, o=120
        _bar(110, 111, 104, 105),   # bar[-2] bear, body_bottom=105
        _bar(106, 128, 105, 122),   # bar[-1] bull; o=106>105→T2 blocked; upper=6/22=0.27→T1 blocked
    )


# ── Z1G ─────────────────────────────────────────────────────────────────────

@_case("Z1G — bearish marubozu with gap-down open", Z1G)
def _():
    # prev close = 100; current open = 98 < 100 (gap-down)
    return _df(
        _bar(105, 106, 99, 100),
        _bar(98, 98.5, 89.5, 90),  # gap-down big bear
    )


# ── Z1 ──────────────────────────────────────────────────────────────────────

@_case("Z1 — bearish marubozu no gap", Z1)
def _():
    return _df(
        _bar(105, 106, 99, 100),
        _bar(100, 100.5, 91.5, 92), # no gap, big bear body
    )


# ── Z2G ─────────────────────────────────────────────────────────────────────

@_case("Z2G — bearish engulfing with gap-up open", Z2G)
def _():
    # prev bull: o=100, c=110, h=111, l=99  body_top=110, body_bottom=100
    # current bear: o=113 > prev_h=111, c=98 < prev_body_bottom=100
    return _df(
        _bar(95, 105, 90, 102),
        _bar(100, 111, 99, 110),   # prev bull
        _bar(113, 114, 97, 98),    # bear engulfs, gap-up open
    )


# ── Z2 ──────────────────────────────────────────────────────────────────────

@_case("Z2 — bearish engulfing no gap", Z2)
def _():
    # Large upper wick on current bar → Z1 blocked (upper_wick/rng=4/17=0.24 > 0.20)
    return _df(
        _bar(95, 105, 90, 102),
        _bar(100, 111, 99, 110),   # prev bull: body_top=110, body_bottom=100
        _bar(111, 115, 98, 99),    # bear engulfs, upper_wick=4/17=0.24 → Z1 blocked
    )


# ── Z3 — Shooting Star ──────────────────────────────────────────────────────

@_case("Z3 — shooting star: bear bar, small body lower half, long upper wick", Z3)
def _():
    # bear bar: o=4, c=1, h=20, l=0 → range=20, body=3, pct=0.15 ✓
    # body_top=o=4 (bear), rng_mid=10, 4<10 ✓
    # upper_wick=20-4=16 >= 2*3=6 ✓; lower_wick=1/20=0.05 ✓
    # T5 won't fire (bear bar; T5 requires bull_bar)
    return _df(
        _bar(50, 60, 45, 55),
        _bar(4, 20, 0, 1),   # bear shooting star: open=4 > close=1
    )


# ── Z4 — Dark Cloud Cover ───────────────────────────────────────────────────

@_case("Z4 — dark cloud cover: opens above prev high, closes below prev mid", Z4)
def _():
    # prev bull: o=100, c=110, h=111, l=99  prev_body=10, prev_c=110, prev_o=100
    # current bear: o=113>111 ✓, c=103 < 110-5=105 ✓, c=103>100 ✓
    return _df(
        _bar(95, 105, 90, 102),
        _bar(100, 111, 99, 110),    # prev bull
        _bar(113, 114, 101, 103),   # dark cloud cover
    )


# ── Z5 — Hanging Man ────────────────────────────────────────────────────────

@_case("Z5 — hanging man: bear bar, small body upper half, long lower wick", Z5)
def _():
    # bear bar: o=19, c=17, h=20, l=0 → range=20, body=2, pct=0.10 ✓
    # body_bottom=c=17 (bear), rng_mid=10, 17>10 ✓
    # lower_wick=17-0=17 >= 2*2=4 ✓; upper_wick=20-19=1, 1/20=0.05 ✓
    # T3 won't fire (bear bar; T3 requires bull_bar)
    return _df(
        _bar(50, 60, 45, 55),
        _bar(19, 20, 0, 17),  # bear hanging man: open=19 > close=17
    )


# ── Z6 — Evening Star ───────────────────────────────────────────────────────

@_case("Z6 — evening star: small prev body, strong bear closing below prev open", Z6)
def _():
    return _df(
        _bar(95, 110, 90, 102),
        _bar(102, 112, 98, 106),   # prev small body
        _bar(108, 109, 97, 98),    # strong bear, c=98 < prev_o=102 ✓
    )


# ── Z8 — Bearish Harami ──────────────────────────────────────────────────────

@_case("Z8 — bearish harami: current inside large prev bull body", Z8)
def _():
    # prev bull: o=100, c=120, h=121, l=99  body=20, range=22, body_pct=0.91
    # current: body_top < 120, body_bottom > 100
    return _df(
        _bar(90, 100, 85, 95),
        _bar(100, 121, 99, 120),    # prev bull
        _bar(108, 115, 105, 112),   # harami inside (small bear or bull)
    )


# ── Z9 — Tweezer Top ────────────────────────────────────────────────────────

@_case("Z9 — tweezer top: matching highs within 0.1%", Z9)
def _():
    return _df(
        _bar(95, 110, 90, 102),
        _bar(100, 115.00, 99, 110),  # prev bull, high=115
        _bar(112, 115.00, 108, 109), # bear, matching high
    )


# ── Z10 ─────────────────────────────────────────────────────────────────────

@_case("Z10 — three-bar bearish: two prior bulls then bear closes below bar[-3] open", Z10)
def _():
    # bar[-2] large body → Z6 blocked; bar[-1] big upper wick (6/28=0.21) → Z1 blocked
    return _df(
        _bar(95, 100, 90, 98),
        _bar(100, 121, 99, 120),  # bar[-3] bull, o=100
        _bar(105, 122, 104, 119), # bar[-2] bull, body=14/rng=18=0.78 → Z6 blocked
        _bar(119, 125, 97, 98),   # bar[-1] bear, upper=6/28=0.21>0.20→Z1 blocked; c=98<100 ✓
    )


# ── Z11 ─────────────────────────────────────────────────────────────────────

@_case("Z11 — bearish abandoned baby: gap below prev low after big bull", Z11)
def _():
    # prev bull: o=100, c=120, h=121, l=99  body_pct=0.91
    # current: o < 99, c < 99
    return _df(
        _bar(90, 100, 85, 95),
        _bar(100, 121, 99, 120),  # prev bull
        _bar(95, 98, 85, 90),     # gap-down below prev_l=99
    )


# ── Z12 ─────────────────────────────────────────────────────────────────────

@_case("Z12 — weak bear bar: body < 30% range, close in lower half", Z12)
def _():
    # range=20, body=4(20%<30%) ✓, close=91 < mid=100 ✓
    return _df(
        _bar(100, 110, 95, 105),
        _bar(100, 110, 90, 91),  # bear: o=100,c=91, body=9/range=20=0.45 … too big
        # Let's fix: o=100,c=97 body=3, h=100,l=80 range=20, body_pct=0.15 ✓, mid=90, c=97>90 ✗
        # Need c < mid: o=100,c=82 body=18,range=20 body_pct=0.9 too big
        # o=100,h=100,l=80,c=83 body=17,range=20 → too big
        # Correct: range must be much bigger than body
        # o=100,h=101,l=79,c=80 body=20,range=22 still ~0.9
        # o=99,h=100,l=80,c=96 body=3,range=20,body_pct=0.15 ✓ mid=90, c=96>90 ✗
        # o=100,h=100,l=80,c=97 body=3,range=20,body_pct=0.15 ✓ mid=90, c=97>90 ✗
        # For c<mid, mid=(80+100)/2=90, c must be <90
        # bear: c<o, so c=85 o=87 body=2 range=20 body_pct=0.1 ✓ mid=90 c=85<90 ✓
    )

# Override with corrected df builder
CASES.pop()  # remove the broken one

@_case("Z12 — weak bear bar (corrected)", Z12)
def _():
    return _df(
        _bar(100, 110, 95, 105),
        _bar(87, 100, 80, 85),  # bear: o=87,c=85 body=2,range=20,pct=0.1 ✓ mid=90 c=85<90 ✓
    )


# ── ok3 helper ──────────────────────────────────────────────────────────────

def test_ok3():
    sig = pd.Series([0, 0, 0, T1, 0, 0, 0])
    result = ok3(sig)
    # bar 3 (T1), 4, 5 should be True (within 3-bar window)
    assert result.iloc[3] == True,  "bar with T1 should be ok3=True"
    assert result.iloc[4] == True,  "1 bar after T1 should be ok3=True"
    assert result.iloc[5] == True,  "2 bars after T1 should be ok3=True"
    assert result.iloc[6] == False, "3 bars after T1 should be ok3=False"
    print("  ok3 helper ... PASS")


# ── Signal ID / name consistency ────────────────────────────────────────────

def test_sig_names_complete():
    all_ids = set(range(26))
    assert set(SIG_NAMES.keys()) == all_ids, "SIG_NAMES must cover 0-25"
    print("  SIG_NAMES completeness ... PASS")

def test_bull_bear_partition():
    bull = BULLISH_SIGS
    bear = BEARISH_SIGS
    assert bull & bear == set(), "Bull and Bear sets must not overlap"
    expected = set(range(1, 26))
    assert bull | bear == expected, "Bull ∪ Bear must equal 1-25"
    print("  Bullish/Bearish partition ... PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    passed = 0
    failed = 0
    errors = 0

    print("\n=== Signal Engine Unit Tests ===\n")

    # Structural tests
    for fn in [test_sig_names_complete, test_bull_bear_partition, test_ok3]:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL [{fn.__name__}]: {exc}")
            failed += 1

    # Pattern tests
    for name, builder, expected in CASES:
        try:
            df   = builder()
            got  = _last_sig(df)
            exp_name = SIG_NAMES.get(expected, "?")
            got_name = SIG_NAMES.get(got, "?")
            if got == expected:
                print(f"  PASS  {name}")
                passed += 1
            else:
                print(f"  FAIL  {name}")
                print(f"        expected {exp_name}({expected}), got {got_name}({got})")
                failed += 1
        except Exception as exc:
            print(f"  ERROR {name}: {exc}")
            traceback.print_exc()
            errors += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed, {errors} errors")
    print(f"{'='*40}\n")

    if failed or errors:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
