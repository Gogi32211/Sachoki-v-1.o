# packages/signal-engine

Future home for the reusable T/Z / WLNBB / VABS signal calculation core, extracted from scanner-api so that research-api can import it without a network call.

## Status

PHASE 1 — Empty placeholder. Do not move logic here yet.

## Planned contents (Phase 5+)

- Pure signal computation functions (no FastAPI, no DB, no I/O)
- `signal_engine.py` core (bar classification)
- `wlnbb_engine.py` L-signal core
- `vabs_engine.py` volume absorption core
- `indicators.py` (RSI, CCI, ATR)
- Pydantic schemas for signal output

## Rules

- No FastAPI imports.
- No database reads.
- No yfinance / Polygon calls.
- Fully unit-testable with synthetic OHLCV DataFrames.

Extraction is blocked on:
1. Mapping every import in `signal_engine.py` and `wlnbb_engine.py`.
2. Ensuring no hidden circular dependency with `turbo_engine.py`.
3. Writing regression tests against current output before moving.
