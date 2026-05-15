# scanner-api

The signal computation and scan persistence service. Source of truth for all candle data, T/Z signals, WLNBB, scoring, and scan results.

## Responsibilities

- Candle data provider (yfinance / Polygon.io)
- T/Z signal computation (`signal_engine.py`)
- WLNBB / L-signal computation (`wlnbb_engine.py`)
- VABS volume absorption signals (`vabs_engine.py`)
- TURBO_SCORE computation (`turbo_engine.py`)
- ULTRA Score computation (`ultra_score.py`, `ultra_orchestrator.py`)
- GOG / combo / indicator engines
- APScheduler: automated scans at 09:30 / 12:30 / 15:30 ET
- Scan result persistence to shared PostgreSQL
- Debug compare endpoint

## What this service does NOT own

- React frontend → dashboard
- Dashboard BFF aggregation → dashboard
- Replay / backtest / analytics → research-api

## API surface (Phase 2 targets)

```
GET  /api/health
GET  /api/version
GET  /api/scans/ultra/latest
GET  /api/scans/ultra/latest/candidates
GET  /api/debug/status
```

Full existing routes remain in `backend/main.py` until extraction.

## Local development

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

## Railway config

Root Directory: `apps/scanner-api`
Healthcheck: `GET /api/health` (120 s timeout)
Restart policy: `ON_FAILURE`

See `infra/railway/scanner-api.env.example` for required environment variables.
