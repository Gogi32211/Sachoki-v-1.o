"""
test_signals.py — unit tests for signal_engine.py.

Tests use hardcoded OHLCV bars whose expected signal can be derived
directly from the Pine Script conditions.

Run:
    python test_signals.py
    # or: python -m pytest test_signals.py -v
"""

from __future__ import annotations
import sys
import pandas as pd
from signal_engine import (
    NONE, T1G, T1, T2G, T2, T3, T4, T5, T6, T9, T10, T11,
    Z1G, Z1, Z2G, Z2, Z3, Z4, Z5, Z6, Z7, Z8, Z9, Z10, Z11, Z12,
    SIG_NAMES, compute_signals, ok3,
)


def _df(*rows):
    """Build DataFrame from (o, h, l, c) tuples."""
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def _sig(df):
    return int(compute_signals(df)["sig_id"].iloc[-1])


def check(name, got, expected):
    ok = got == expected
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}  expected={SIG_NAMES[expected]}  got={SIG_NAMES[got]}")
    return ok


# ---------------------------------------------------------------------------
# T4 — engulf bull  (p1Bear & isBull & engOk)
#
# Pine engOk (use_wick=False):
#   cBody/safe >= 1.0  AND  cTop >= pTop  AND  pBot >= cBot
#
# prev bear: o=110 c=100 → pTop=110 pBot=100 pBody=10
# curr bull: o=99  c=112 → cBody=13 cTop=112 cBot=99
#   13/10=1.3>=1 ✓  cTop(112)>=pTop(110) ✓  pBot(100)>=cBot(99) ✓
# ---------------------------------------------------------------------------
def test_T4():
    df = _df(
        (100, 105, 98, 102),
        (110, 112, 99, 100),  # prev bear
        ( 99, 115, 98, 112),  # curr bull engulfs
    )
    return check("T4 — bullish engulf", _sig(df), T4)


# ---------------------------------------------------------------------------
# Z4 — engulf bear  (p1Bull & isBear & engOk)
#
# prev bull: o=100 c=110 → pTop=110 pBot=100 pBody=10
# curr bear: o=112 c=98  → cBody=14 cTop=112 cBot=98
#   14/10=1.4 ✓  cTop(112)>=pTop(110) ✓  pBot(100)>=cBot(98) ✓
# ---------------------------------------------------------------------------
def test_Z4():
    df = _df(
        (100, 105, 98, 102),
        (100, 112, 99, 110),  # prev bull
        (112, 114, 97,  98),  # curr bear engulfs
    )
    return check("Z4 — bearish engulf", _sig(df), Z4)


# ---------------------------------------------------------------------------
# T1G — gap-up bull after bear bar
#
# Pine T1G: p1Bear & (o>c[1]) & (o>o[1]) & (c>o[1]) & isBull
#
# prev bear: o=110 c=100
# curr: o=115>c[1]=100 ✓  o(115)>o[1](110) ✓  c(120)>o[1](110) ✓  isBull ✓
# T4 blocked: cBody=5 < pBody=10 → cBody/safe=0.5<1 → engOk fails
# ---------------------------------------------------------------------------
def test_T1G():
    df = _df(
        (100, 105, 98, 102),
        (110, 112, 99, 100),   # prev bear
        (115, 121, 114, 120),  # gap-up bull, small body vs prev → engOk fails
    )
    return check("T1G — gap-up bull after bear", _sig(df), T1G)


# ---------------------------------------------------------------------------
# Z7 (doji) — isDoji & ~anyB & ~anyZ
#
# A flat doji (o==c) with rng>0 always satisfies body/rng=0 <= 0.05.
# isBull=False and isBear=False, so no T or Z pattern fires.
# After a plain bull prev bar, p1Bull=True but no bear Z fires (not isBear).
# ---------------------------------------------------------------------------
def test_Z7():
    df = _df(
        ( 90,  95, 88,  92),
        (100, 110, 99, 105),  # prev bull
        (102, 107, 97, 102),  # flat doji: o=c=102, rng=10, bdy=0
    )
    return check("Z7 — flat doji (no other signal)", _sig(df), Z7)


# ---------------------------------------------------------------------------
# Z7 NOT fired when a bullish signal is present
#
# Same doji-like bar, but T1G conditions are also met:
# prev bear o=110 c=100; curr o=115>c[1] ✓ o>o[1] ✓ c=115.1>o[1]=110 ✓ isBull ✓
# T1G fires (bc=3) → Z7 suppressed.
# ---------------------------------------------------------------------------
def test_Z7_suppressed():
    df = _df(
        (100, 105, 98, 102),
        (110, 112, 99, 100),    # prev bear
        (115, 121, 114, 115.1), # tiny bull body (0.1 / 7 = 1.4% < 5%) → isDoji
                                # but T1G fires, so sig = T1G not Z7
    )
    got = _sig(df)
    ok = got == T1G
    print(f"  [{'PASS' if ok else 'FAIL'}] Z7 suppressed by T1G  "
          f"expected=T1G  got={SIG_NAMES[got]}")
    return ok


# ---------------------------------------------------------------------------
# T2 — p1Bull & (o>=o[1]) & (o<=c[1]) & (c>c[1]) & isBull
#
# prev bull: o=100 c=108 → pTop=108 pBot=100
# curr bull: o=105, c=115
#   o(105)>=o[1](100) ✓  o(105)<=c[1](108) ✓  c(115)>c[1](108) ✓  isBull ✓
# T6/T4 blocked: pBot(100) >= cBot(105)?  100>=105=False → engOk fails
# ---------------------------------------------------------------------------
def test_T2():
    df = _df(
        (95, 100, 93, 97),
        (100, 110, 99, 108),   # prev bull o=100 c=108
        (105, 116, 104, 115),  # curr bull continuation
    )
    return check("T2 — bullish continuation", _sig(df), T2)


# ---------------------------------------------------------------------------
# Z2 — p1Bear & (o<=o[1]) & (o>=c[1]) & (c<c[1]) & isBear
#
# prev bear: o=108 c=100 → pTop=108 pBot=100
# curr bear: o=106, c=95
#   o(106)<=o[1](108) ✓  o(106)>=c[1](100) ✓  c(95)<c[1](100) ✓  isBear ✓
# Z6 blocked: cTop(106) >= pTop(108)?  106>=108=False → engOk fails
# ---------------------------------------------------------------------------
def test_Z2():
    df = _df(
        (95, 100, 93, 97),
        (108, 110, 99, 100),   # prev bear o=108 c=100
        (106, 107, 94,  95),   # curr bear continuation
    )
    return check("Z2 — bearish continuation", _sig(df), Z2)


# ---------------------------------------------------------------------------
# Z1G — p1Bull & (o<c[1]) & (o<o[1]) & (c<o[1]) & isBear
#
# prev bull: o=100 c=110 → pTop=110 pBot=100
# curr bear: o=98<c[1]=110 ✓  o(98)<o[1](100) ✓  c(90)<o[1](100) ✓  isBear ✓
# Z4 blocked: cTop(max(98,90)=98) >= pTop(110)?  No → engOk fails
# ---------------------------------------------------------------------------
def test_Z1G():
    df = _df(
        (95, 105, 90, 102),
        (100, 112, 99, 110),   # prev bull
        ( 98,  99, 89,  90),   # gap-down bear
    )
    return check("Z1G — gap-down bear after bull", _sig(df), Z1G)


# ---------------------------------------------------------------------------
# Z9 — p1Bull & isBear & insOk
#
# insOk = cTop<=pTop AND cBot>=pBot  (body-based, use_wick=False)
#
# prev bull: o=100 c=110 → pTop=110 pBot=100
# curr bear: o=108 c=106 → cTop=108<=110 ✓  cBot=106>=100 ✓
# Z4 blocked: cTop(108) >= pTop(110)?  No → engOk fails
# ---------------------------------------------------------------------------
def test_Z9():
    df = _df(
        (95, 105, 90, 102),
        (100, 112, 99, 110),   # prev bull pTop=110 pBot=100
        (108, 109, 105, 106),  # bear inside prev body
    )
    return check("Z9 — bear inside prev bull body (harami-style)", _sig(df), Z9)


# ---------------------------------------------------------------------------
# NONE — no signal possible
#
# When the previous bar has zero range (o=h=l=c), isDoji[1]=False and
# c[1]==o[1] so p1Bull=False and p1Bear=False.
# Every T/Z pattern requires p1Bull or p1Bear, so nothing can fire.
# ---------------------------------------------------------------------------
def test_NONE():
    df = _df(
        (100, 105, 99, 103),
        (103, 103, 103, 103),  # prev: zero-range → p1Bull=False p1Bear=False
        (103, 107, 102, 106),
    )
    return check("NONE — no pattern (zero-range prev bar)", _sig(df), NONE)


# ---------------------------------------------------------------------------
# ok3 helper
# ---------------------------------------------------------------------------
def test_ok3():
    sig = pd.Series([0, 0, 0, T4, 0, 0, 0])
    r = ok3(sig)
    passed = (r.iloc[3] is True or r.iloc[3] == True) and \
             (r.iloc[4] is True or r.iloc[4] == True) and \
             (r.iloc[5] is True or r.iloc[5] == True) and \
             not (r.iloc[6] is True or r.iloc[6] == True)
    print(f"  [{'PASS' if passed else 'FAIL'}] ok3 helper")
    return passed


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        test_T4, test_Z4, test_T1G, test_Z7, test_Z7_suppressed,
        test_T2, test_Z2, test_Z1G, test_Z9, test_NONE, test_ok3,
    ]
    print("\n=== TZ Signal Engine Tests ===\n")
    results = [t() for t in tests]
    passed = sum(results)
    total  = len(results)
    print(f"\n{'='*35}")
    print(f"Results: {passed}/{total} passed")
    print(f"{'='*35}\n")
    sys.exit(0 if passed == total else 1)
