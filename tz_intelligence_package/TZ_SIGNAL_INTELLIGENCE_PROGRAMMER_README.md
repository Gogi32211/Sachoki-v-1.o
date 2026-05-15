# TZ Signal Intelligence — Programmer Package

## Files

- `TZ_SIGNAL_INTELLIGENCE_master_matrix_seed.csv` — machine-readable rule seed extracted from uploaded T/Z analytics files.
- `TZ_SIGNAL_INTELLIGENCE_master_matrix_seed.json` — same matrix in JSON form.
- `TZ_ALL_SIGNAL_ANALYTICS_CONSOLIDATED.docx` — all T/Z analytics reports consolidated into one DOCX.
- Original individual `T*_analytics.docx` and `Z*_analytics.docx` files are included in the ZIP for verification.

## Intended app tab

Add a new Sachoki Screener app tab named **TZ Signal Intelligence**.

The tab should classify each ticker/day into:

- BULL_A
- BULL_B
- PULLBACK_READY
- PULLBACK_GO
- SHORT_WATCH
- SHORT_GO
- REJECT
- NO_EDGE

## Core implementation principle

Do not classify by candle color only. A T/Z signal is geometry. Trade role is determined by:

1. final signal
2. final composite
3. unrestricted 4-bar sequence
4. WLNBB/L bucket
5. volume bucket
6. wick/suffix
7. price bucket
8. EMA20/50/89 relation and reclaim
9. final close position inside local 4-bar range
10. known good/reject matrix rules

## Backend suggested function

```python
def classify_tz_event(row, history_rows, rules_matrix) -> dict:
    return {
        "ticker": row["ticker"],
        "date": row["date"],
        "final_signal": row.get("signal"),
        "final_composite": row.get("composite"),
        "seq4_base": "prev3|prev2|prev1|final",
        "role": "BULL_A / PULLBACK_READY / SHORT_WATCH / ...",
        "score": 0,
        "quality": "A/B/Watch/Reject",
        "action": "BUY_TRIGGER / WAIT_FOR_T_CONFIRMATION / WAIT_FOR_BREAKDOWN / IGNORE",
        "reason_codes": [],
        "explanation": "human readable reason"
    }
```

## Scoring seed

- STRONG composite/sequence: +70 base
- GOOD: +55 base
- AVERAGE: +25 to +35 context only
- REJECT: -40 and potential SHORT_WATCH
- final close top 75% of 4-bar range: +10
- EMA50 reclaim: +10
- reject composite or exact reject sequence: -40
- SHORT_GO requires breakdown confirmation after SHORT_WATCH.

## Required inputs from existing codebase

- T/Z signal detection logic including priority order.
- WLNBB/L bucket logic.
- Suffix/wick logic.
- Volume bucket logic.
- Daily OHLCV + EMA20/50/89/200 data.

Generated rows in matrix: 1523.
