# engine-api

Pure-compute layer for the Sachoki scanner pipeline. Extracted from
`apps/scanner-api/backend/engine_api/` in Phase B-2.

## What this service does

- Receives OHLCV bars over HTTP.
- Runs the 14-engine pipeline + scoring (turbo, RTB, profile, canonical, beta, ultra).
- Returns normalized per-bar dicts.

## What this service does NOT do

- No `DATABASE_URL` access.
- No Massive HTTP fetch.
- No scan orchestration.
- No dashboard concerns.

All inputs (OHLCV, split flags, profile) come from the caller. Outputs are pure
JSON. No state.

## Endpoints

```
GET  /health
GET  /version
GET  /api/debug/status
GET  /api/engines/list
POST /api/engines/run             — full pipeline
POST /api/engines/single/{name}   — one engine (diagnostic)
```

## Local dev

```bash
cd apps/engine-api
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8002
```

## Railway deploy

Set Root Directory to `apps/engine-api`. No env vars required.

After deploy, set on `scanner-api`:
```
ENGINE_API_URL = https://<engine-api>.up.railway.app
```

scanner-api will route engine compute through HTTP when `ENGINE_API_URL` is set;
otherwise it falls back to its in-process `engine_api/` subpackage.
