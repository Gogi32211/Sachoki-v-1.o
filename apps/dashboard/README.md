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
# frontend
cd frontend
npm install
npm run dev      # Vite dev server on :5173

# BFF (once extracted from main.py)
cd backend
uvicorn main:app --reload --port 8090
```

## Railway config

Root Directory: `apps/dashboard`
Healthcheck: `GET /api/health`

See `infra/railway/dashboard.env.example` for required environment variables.
