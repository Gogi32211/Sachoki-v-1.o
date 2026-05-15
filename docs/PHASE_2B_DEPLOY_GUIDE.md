# Phase 2B — Railway Deployment Guide

Step-by-step instructions for deploying the three skeleton services into a NEW Railway project.
Follow in order. Do not touch the existing production Railway project.

---

## Prerequisites

- GitHub repo: `Gogi32211/Sachoki-v-1.o`
- Branch: `architecture/monorepo-services-split` (must be pushed to GitHub first)
- Railway account with access to create a new project

Push the branch before starting:
```bash
cd "Sachoki v 1.o"
git push -u origin architecture/monorepo-services-split
```

---

## Step 1 — Create a new Railway project

1. Go to [railway.app](https://railway.app) → **New Project**
2. Name: `sachoki-staging`
3. Do **not** use the existing production project

---

## Step 2 — Add PostgreSQL

Inside `sachoki-staging`:

1. Click **New** → **Database** → **Add PostgreSQL**
2. Railway provisions it and auto-creates `DATABASE_URL`
3. Note the `DATABASE_URL` value — you will paste it into each service's env vars

---

## Step 3 — Add Redis (optional but recommended)

Inside `sachoki-staging`:

1. Click **New** → **Database** → **Add Redis**
2. Railway provisions it and auto-creates `REDIS_URL`
3. Note the `REDIS_URL` value

If Redis is unavailable, skip this step. All three services degrade gracefully — `redis_configured` will be `false` in debug status.

---

## Step 4 — Deploy scanner-api  ← deploy this FIRST

### 4a. Create the service

1. Click **New** → **GitHub Repo**
2. Select repo: `Gogi32211/Sachoki-v-1.o`
3. Branch: `architecture/monorepo-services-split`
4. **Root Directory**: `apps/scanner-api`
5. Railway auto-detects Python via `requirements.txt` and Nixpacks
6. Name the service: `scanner-api`

### 4b. Set start command (if not auto-detected)

In service **Settings → Deploy**:
```
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

### 4c. Set environment variables

In service **Variables**:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | paste from PostgreSQL service (or use Railway reference `${{Postgres.DATABASE_URL}}`) |
| `REDIS_URL` | paste from Redis service (or use Railway reference `${{Redis.REDIS_URL}}`) |
| `MASSIVE_API_KEY` | your key |
| `SCHEDULER_ENABLED` | `false` |
| `SCANNING_ENABLED` | `false` |
| `SCANNER_API_SERVICE_NAME` | `scanner-api` |
| `ENVIRONMENT` | `staging` |
| `LOG_LEVEL` | `info` |

### 4d. Deploy and verify

1. Click **Deploy**
2. Watch build logs — Nixpacks installs fastapi + uvicorn
3. When deploy completes, find the public URL in **Settings → Networking**
4. Hit the healthcheck:
   ```
   GET https://<scanner-api-public-url>/health
   → { "status": "ok", "service": "scanner-api" }
   ```
5. Get the **private domain** from **Settings → Networking → Private Domain**
   - Pattern: `scanner-api.railway.internal`
   - Full internal URL: `http://scanner-api.railway.internal:<PORT>`
   - PORT will be shown in Railway service settings — typically the same `$PORT` Railway assigns
   - If Railway shows a specific internal port, use it. Otherwise use the default (commonly 8080 for internal)

---

## Step 5 — Deploy research-api

### 5a. Create the service

1. Click **New** → **GitHub Repo**
2. Select repo: `Gogi32211/Sachoki-v-1.o`
3. Branch: `architecture/monorepo-services-split`
4. **Root Directory**: `apps/research-api`
5. Name the service: `research-api`

### 5b. Set start command (if not auto-detected)

```
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

### 5c. Set environment variables

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | Railway reference `${{Postgres.DATABASE_URL}}` |
| `REDIS_URL` | Railway reference `${{Redis.REDIS_URL}}` |
| `SCANNER_API_URL` | `http://scanner-api.railway.internal:<port>` (from Step 4d) |
| `RESEARCH_JOBS_ENABLED` | `false` |
| `RESEARCH_API_SERVICE_NAME` | `research-api` |
| `ENVIRONMENT` | `staging` |
| `LOG_LEVEL` | `info` |

### 5d. Deploy and verify

```
GET https://<research-api-public-url>/health
→ { "status": "ok", "service": "research-api" }

GET https://<research-api-public-url>/api/debug/status
→ { "scanner_api_url_configured": true, ... }
```

---

## Step 6 — Deploy dashboard

### 6a. Create the service

1. Click **New** → **GitHub Repo**
2. Select repo: `Gogi32211/Sachoki-v-1.o`
3. Branch: `architecture/monorepo-services-split`
4. **Root Directory**: `apps/dashboard`
5. Name the service: `dashboard`

### 6b. Set start command (if not auto-detected)

```
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

### 6c. Set environment variables

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | Railway reference `${{Postgres.DATABASE_URL}}` |
| `REDIS_URL` | Railway reference `${{Redis.REDIS_URL}}` |
| `SCANNER_API_URL` | `http://scanner-api.railway.internal:<port>` |
| `RESEARCH_API_URL` | `http://research-api.railway.internal:<port>` |
| `MASSIVE_API_KEY` | your key |
| `ANTHROPIC_API_KEY` | your key |
| `CLAUDE_MODEL_HAIKU` | e.g. `claude-haiku-4-5-20251001` |
| `DASHBOARD_SERVICE_NAME` | `dashboard` |
| `ENVIRONMENT` | `staging` |
| `LOG_LEVEL` | `info` |

### 6d. Enable public domain

In **Settings → Networking → Generate Domain** for dashboard.
(scanner-api and research-api can stay private-only for now.)

### 6e. Deploy and verify

```
GET https://<dashboard-public-url>/health
→ { "status": "ok", "service": "dashboard" }

GET https://<dashboard-public-url>/api/debug/status
→ { "scanner_api_url_configured": true, "research_api_url_configured": true, ... }
```

---

## Step 7 — Final verification matrix

Run all of these after all three services are deployed:

| Service | Endpoint | Expected |
|---------|----------|----------|
| scanner-api | `GET /health` | `{ status: "ok", service: "scanner-api" }` |
| scanner-api | `GET /version` | `{ phase: "2A-skeleton" }` |
| scanner-api | `GET /api/debug/status` | `scanning_enabled: false, scheduler_enabled: false` |
| research-api | `GET /health` | `{ status: "ok", service: "research-api" }` |
| research-api | `GET /version` | `{ phase: "2A-skeleton" }` |
| research-api | `GET /api/debug/status` | `research_jobs_enabled: false` |
| dashboard | `GET /health` | `{ status: "ok", service: "dashboard" }` |
| dashboard | `GET /version` | `{ phase: "2A-skeleton" }` |
| dashboard | `GET /api/debug/status` | all booleans present, no secrets |

---

## Networking diagram (staging)

```
                    ┌─────────────────────────────────────┐
                    │  Railway project: sachoki-staging    │
                    │                                      │
  public traffic    │  ┌──────────────┐                   │
  ───────────────►  │  │  dashboard   │  :8080 public     │
                    │  │  BFF skeleton│                   │
                    │  └──────┬───┬──┘                    │
                    │         │   │  private network       │
                    │  ┌──────┘   └──────────┐            │
                    │  ▼                     ▼            │
                    │  ┌──────────────┐  ┌────────────┐   │
                    │  │ scanner-api  │  │research-api│   │
                    │  │  :PORT priv  │  │ :PORT priv │   │
                    │  └──────┬───┬──┘  └─────┬──────┘   │
                    │         │   │            │           │
                    │  ┌──────┴───┘            │           │
                    │  ▼                       ▼           │
                    │  ┌───────────────────────────────┐   │
                    │  │  PostgreSQL (shared)          │   │
                    │  ├───────────────────────────────┤   │
                    │  │  Redis (shared)               │   │
                    │  └───────────────────────────────┘   │
                    └─────────────────────────────────────┘
```

---

## Railway reference variables (shortcut)

Instead of copy-pasting DATABASE_URL into every service, use Railway reference syntax in the Variables tab:

```
DATABASE_URL = ${{Postgres.DATABASE_URL}}
REDIS_URL    = ${{Redis.REDIS_URL}}
```

This auto-updates if the plugin URL rotates.

---

## Troubleshooting

**Build fails — Python not detected**
- Verify `apps/<service>/requirements.txt` exists (it does — added in Phase 2B prep)
- Nixpacks detects Python by the presence of `requirements.txt` at service root

**Start fails — module not found**
- Confirm Root Directory is set to `apps/scanner-api` (not the repo root)
- Start command must be `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
- Running from the service root, `backend/` is a package importable as `backend.main`

**Health check times out**
- Check logs: service may be crashing on startup
- Verify `$PORT` is being used (Railway injects it; hardcoding 8080 will fail healthcheck)

**`database_configured: false` in debug/status**
- DATABASE_URL env var not set on that service
- Use Railway reference `${{Postgres.DATABASE_URL}}` and redeploy
