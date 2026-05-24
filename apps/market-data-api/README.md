# market-data-api

The data layer for Sachoki — sole owner of Massive HTTP fetches and the
`market_bars` cache. Extracted from `apps/scanner-api/backend/market_data.py`
in Phase C-3.

## Architectural law

- **OWNS** Massive HTTP fetches. After C-3 verification, `MASSIVE_API_KEY`
  should be removed from `scanner-api` env vars — only this service needs it.
- **OWNS** `market_bars` table writes. Other services may read directly from
  Postgres for performance, but only this service writes.
- **OWNS** NASDAQ splits calendar fetch + lifecycle classification.
- **NO** engine compute (that's `engine-api`).
- **NO** scan orchestration (that's `scanner-api`).
- **NO** dashboard views (that's `generator-api`, eventually).

## Endpoints

```
GET  /health
GET  /version
GET  /api/debug/status

GET  /api/market-data/bars/{symbol}?tf=1d&days=180   — read cached + fetch on miss
POST /api/market-data/sync                           — bulk pre-warm  (x-admin-token)
GET  /api/market-data/split-universe                 — NASDAQ split lifecycle list
GET  /api/market-data/split-flags/{symbol}           — per-ticker split flags
```

## Required env

```
DATABASE_URL    — shared Postgres (market_bars table)
MASSIVE_API_KEY — the ONLY service that needs this after Phase C-3
ADMIN_TOKEN     — protects POST /api/market-data/sync
```

## Optional env

```
MASSIVE_BASE    — override Massive API host (default: https://api.massive.com)
SEED_TOKEN      — back-compat fallback for ADMIN_TOKEN
LOG_LEVEL       — default: info
```

## Local dev

```bash
cd apps/market-data-api
pip install -r requirements.txt
export DATABASE_URL=...
export MASSIVE_API_KEY=...
export ADMIN_TOKEN=$(openssl rand -hex 16)
uvicorn backend.main:app --reload --port 8003
```

## Railway deploy

Root Directory: `apps/market-data-api`. Health check: `/health`.

After deploy, on `scanner-api` service:
```
MARKET_DATA_API_URL = https://<market-data-api>.up.railway.app
```

scanner-api auto-switches to the HTTP path. If `MARKET_DATA_API_URL` is
unset, scanner-api falls back to its in-process `market_data.py` module
(legacy compatible mode).

Once verified in production: remove `MASSIVE_API_KEY` from `scanner-api`
env vars — only `market-data-api` needs it.
