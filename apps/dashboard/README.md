# dashboard

The user-facing service for Sachoki. Owns the React SPA, the dashboard BFF (Backend For Frontend), and all UI-oriented API routes.

## Responsibilities

- React frontend (Vite + Tailwind)
- Dashboard BFF — thin FastAPI layer that aggregates scanner-api + research-api responses
- Best Setups display
- Top Candidates panel
- Top Movers panel
- News and AI summaries
- Watchlist management
- Live price display
- Paper Portfolio UI

## What this service does NOT own

- T/Z / WLNBB / VABS signal computation → scanner-api
- Replay / backtest / statistics → research-api
- ULTRA or Turbo scoring logic → scanner-api

## Service calls

```
dashboard BFF  →  scanner-api   (SCANNER_API_URL)
dashboard BFF  →  research-api  (RESEARCH_API_URL)
```

## Local development

```bash
# BFF skeleton — run from the service root (apps/dashboard), not the repo root.

cd apps/dashboard
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8100
```

Endpoints available after start:

| Method | Path | Response |
|--------|------|---------|
| GET | `/health` | `{ status, service }` |
| GET | `/version` | `{ service, version, phase }` |
| GET | `/api/debug/status` | env-var presence booleans |

Frontend (current production React app — not yet moved to this service):
```bash
# Still runs from repo root frontend/ until Phase 3
cd frontend
npm install
npm run dev      # Vite dev server on :5173, proxies /api → root backend :8080
```

Railway start command (set in `railway.toml` later):
```
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

## Railway config

Root Directory: `apps/dashboard`
Healthcheck: `GET /api/health`

See `infra/railway/dashboard.env.example` for required environment variables.
