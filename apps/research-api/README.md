# research-api

The analytical backend service for long-running research jobs, backtesting, statistics, and exports.

## Responsibilities

- Replay / backtest engine (`replay_engine.py`)
- Signal statistics computation
- Sequence scan (`sequence_engine.py`)
- TZ × WLNBB analytics (`analyzers/tz_wlnbb/`)
- Rare reversal miner (`analyzers/rare_reversal/`)
- Pullback miner (`analyzers/pullback_miner/`)
- TZ Intelligence / ABR classifier (`tz_intelligence/`)
- Stock Stat CSV generation
- Research bundle exports (CSV / ZIP)
- Long-running background jobs (APScheduler)
- Consumes scanner-api for live signal data

## What this service does NOT own

- Live scanning / scoring → scanner-api
- React UI → dashboard
- Watchlist / portfolio / chart observations → dashboard

## API surface (Phase 4 targets, current paths)

```
POST /api/replay/run
GET  /api/replay/reports
GET  /api/replay/report/{name}
GET  /api/replay/export/{name}
GET  /api/sequence-scan/trigger
GET  /api/sequence-scan/results
POST /api/stock-stat/trigger
GET  /api/stock-stat/download
GET  /api/rare-reversal/scan
GET  /api/pullback-miner/scan
GET  /api/tz-intelligence/scan
```

## Local development

```bash
# Run from the service root (apps/research-api), not the repo root.

cd apps/research-api
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8102
```

Endpoints available after start:

| Method | Path | Response |
|--------|------|---------|
| GET | `/health` | `{ status, service }` |
| GET | `/version` | `{ service, version, phase }` |
| GET | `/api/debug/status` | env-var presence booleans |

Railway start command (set in `railway.toml` later):
```
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

## Railway config

Root Directory: `apps/research-api`
Healthcheck: `GET /api/health`

See `infra/railway/research-api.env.example` for required environment variables.
