# generator-api

Pre-aggregation layer for the dashboard. Reads `ultra_scan_candidates`,
runs the 4 view builders (`top_movers` / `best_setups` / `sector_heat` /
`dashboard_summary`), persists payloads to `scan_generated_views`.

Extracted from `apps/scanner-api/backend/generator.py` in Phase E.

## Architectural law

- **OWNS** `scan_generated_views` table.
- **READS** `ultra_scan_candidates` + `ultra_scan_runs` (shared Postgres).
- **NO** Massive HTTP. **NO** engine compute. **NO** scan orchestration.
  **NO** dashboard frontend concerns.

## Endpoints

```
GET  /health
GET  /version
GET  /api/debug/status

POST /api/generator/run                  — generate + save all 4 views  (x-admin-token)
GET  /api/generator/views                — read all 4 for a run_id
GET  /api/generator/views/{view_type}    — read one
```

## Required env

```
DATABASE_URL  — shared Postgres
ADMIN_TOKEN   — protects POST /api/generator/run
```

## Optional env

```
SEED_TOKEN    — back-compat fallback for ADMIN_TOKEN
LOG_LEVEL     — default: info
```

## Local dev

```bash
cd apps/generator-api
pip install -r requirements.txt
export DATABASE_URL=...
export ADMIN_TOKEN=$(openssl rand -hex 16)
uvicorn backend.main:app --reload --port 8004
```

## Railway deploy

Root Directory: `apps/generator-api`. Health check: `/health`.

After deploy, on `scanner-api` service:
```
GENERATOR_API_URL = https://<generator-api>.up.railway.app
```

scanner-api auto-switches to the HTTP path. If `GENERATOR_API_URL` is
unset, scanner-api falls back to its in-process `generator.py` module
(legacy compatible mode).
