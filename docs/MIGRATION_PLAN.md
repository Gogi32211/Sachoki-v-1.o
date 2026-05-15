# Migration Plan

Safe, phased migration from the current monolith to the target monorepo service architecture.

**Golden rule for every phase: the current working app (`backend/` + `frontend/`) must remain deployable and functionally unchanged.**

---

## Current state

Single-service monolith:
- `backend/main.py` — all FastAPI routes (85+), APScheduler, all engines imported
- `frontend/src/` — React SPA, calls `backend/main.py` via relative `/api/*` paths
- `Dockerfile` — two-stage build: Node → React dist → Python + StaticFiles
- Deployed as one Railway service

---

## Phase 1 — Folder structure only (CURRENT PHASE)

**Status: In progress**

Goal: Establish the monorepo skeleton without moving any code.

Tasks:
- [x] Create branch `architecture/monorepo-services-split`
- [x] Create `apps/dashboard/`, `apps/scanner-api/`, `apps/research-api/` scaffolds
- [x] Create `packages/shared/`, `packages/signal-engine/` placeholders
- [x] Create `infra/railway/*.env.example` files
- [x] Write `docs/ARCHITECTURE.md` (target state)
- [x] Write `docs/SERVICE_CONTRACTS.md` (inter-service API contracts)
- [x] Write `docs/MIGRATION_PLAN.md` (this file)
- [x] Write README.md per service
- [x] Verify existing app still builds (`npm run build`, `py_compile backend/main.py`)

Definition of done: PR merged to main with only additive files. Zero changes to `backend/` or `frontend/`.

---

## Phase 2 — scanner-api skeleton

**Status: Not started**

Goal: Create a minimal standalone FastAPI service inside `apps/scanner-api/backend/` that can run alongside the monolith without conflict.

Tasks:
- [ ] Create `apps/scanner-api/backend/main.py` — fresh FastAPI app, no imports from `backend/`
- [ ] Implement:
  - `GET /api/health` → `{ status: "ok", version: "0.1.0" }`
  - `GET /api/version` → version info
  - `GET /api/scans/ultra/latest` → reads `ultra_scan` table from shared PostgreSQL
  - `GET /api/scans/ultra/latest/candidates` → filtered subset of above
  - `GET /api/debug/status` → scheduler + DB + Redis status
- [ ] Create `apps/scanner-api/backend/requirements.txt` — minimal deps only
- [ ] Create `apps/scanner-api/Dockerfile` — Python only (no Node build stage)
- [ ] Create `apps/scanner-api/railway.toml`
- [ ] Write tests for the new routes (no signal logic to test yet)

Constraints:
- No signal computation in this skeleton — reads DB only
- Does not replace `backend/main.py` — runs as a separate process on a different port locally
- No changes to `backend/` or `frontend/`

Local test:
```bash
cd apps/scanner-api/backend
uvicorn main:app --port 8081
```

---

## Phase 3 — Dashboard BFF calls scanner-api

**Status: Not started**

Goal: The dashboard's BFF routes for scan results delegate to scanner-api instead of running locally. The React SPA is unchanged.

Tasks:
- [ ] Extract dashboard-specific routes from `backend/main.py` into `apps/dashboard/backend/main.py`:
  - Watchlist routes
  - Paper portfolio routes
  - Chart observation routes
  - Health / settings routes
- [ ] Add `httpx` client in dashboard BFF: `GET {SCANNER_API_URL}/api/scans/ultra/latest`
- [ ] Route `/api/ultra-scan/results` in dashboard BFF → proxy to scanner-api
- [ ] Route `/api/turbo-scan` in dashboard BFF → proxy to scanner-api
- [ ] Add `SCANNER_API_URL` env var handling with fallback to `http://localhost:8081`
- [ ] Update `apps/dashboard/Dockerfile` to build React SPA + Python BFF
- [ ] Update `apps/dashboard/railway.toml`

Constraints:
- React `api.js` is unchanged — still calls relative `/api/*` paths
- `backend/main.py` remains fully functional as fallback
- Validate that both monolith and split-service modes return identical JSON for the same request

---

## Phase 4 — Research API extraction

**Status: Not started**

Goal: Move all long-running research routes to `apps/research-api/backend/`.

Routes to move:
- `POST /api/replay/run` + status/report/export
- `POST /api/sequence-scan/trigger` + status/results
- `POST /api/stock-stat/trigger` + status/download
- `GET /api/rare-reversal/scan`
- `GET /api/pullback-miner/scan` + report
- `GET /api/tz-wlnbb/scan` + replay
- `GET /api/tz-intelligence/scan`

Tasks:
- [ ] Create `apps/research-api/backend/main.py` with the above routes
- [ ] Copy required engine files:
  - `replay_engine.py`, `sequence_engine.py`, `stats_engine.py`
  - `analyzers/` subtree
  - `tz_intelligence/` subtree
- [ ] Wire `SCANNER_API_URL` for data fetches that previously read from the monolith
- [ ] Create `apps/research-api/Dockerfile` and `railway.toml`
- [ ] Update dashboard BFF to proxy research routes to `RESEARCH_API_URL`
- [ ] Remove extracted routes from `apps/dashboard/backend/main.py`

Constraints:
- Do not change signal logic or scoring formulas during extraction
- Write integration tests that compare output against the monolith before removing routes
- `backend/main.py` stays in place until Phase 6

---

## Phase 5 — Redis cache layer

**Status: Not started**

Goal: Add Redis-backed caching to scanner-api so repeated UI polling does not re-query PostgreSQL.

Tasks:
- [ ] Add `redis[asyncio]` to scanner-api requirements
- [ ] Cache `/api/scans/ultra/latest` per `(universe, tf)` key with 60 s TTL
- [ ] Cache `/api/scans/ultra/latest/candidates` with 60 s TTL
- [ ] Expose cache hit/miss counters in `GET /api/debug/status`
- [ ] Add `REDIS_URL` env var (optional — graceful degradation if absent)

Constraints:
- All cache misses must fall back to PostgreSQL transparently
- No changes to signal logic or scoring

---

## Phase 6 — Railway multi-service deployment

**Status: Not started**

Goal: Deploy all three services to Railway project `sachoki-prod`.

Tasks:
- [ ] Create Railway project `sachoki-prod`
- [ ] Provision shared PostgreSQL plugin
- [ ] Provision shared Redis plugin
- [ ] Create `dashboard` service → Root Directory: `apps/dashboard`
- [ ] Create `scanner-api` service → Root Directory: `apps/scanner-api`
- [ ] Create `research-api` service → Root Directory: `apps/research-api`
- [ ] Configure env vars per service (see `infra/railway/*.env.example`)
- [ ] Set `SCANNER_API_URL` in dashboard to internal Railway service URL
- [ ] Set `RESEARCH_API_URL` in dashboard to internal Railway service URL
- [ ] Run smoke tests against production URLs
- [ ] Decommission the old single-service Railway deployment
- [ ] Remove `backend/main.py` routes that have been fully migrated (only after smoke tests pass)

Constraints:
- Old Railway service stays live until all smoke tests pass on the new deployment
- Rollback plan: re-enable old service, point DNS back

---

## Risk areas

| Risk | Mitigation |
|------|-----------|
| Import graph in `backend/main.py` has 30+ files with cross-imports | Map all imports before moving any file; use `py_compile` after each move |
| APScheduler jobs run in both monolith and scanner-api during transition | Gate scheduler startup with `SCHEDULER_ENABLED=true` env var; only enable in scanner-api |
| Shared PostgreSQL write conflicts during parallel operation | Each service owns distinct tables (see `docs/ARCHITECTURE.md`); no shared write paths |
| React SPA hardcodes `/api/*` relative paths | BFF proxies all existing paths unchanged — no frontend changes required until Phase 6 |
| Replay jobs are memory-intensive | research-api gets its own Railway service with isolated memory limit |

---

## What is never moved

The following stay in `backend/` permanently until explicitly decided otherwise:

- `signal_engine.py` — T/Z computation core
- `wlnbb_engine.py` — L-signal computation core
- `ultra_score.py` — ULTRA Score formula
- `turbo_engine.py` — TURBO_SCORE formula
- All scoring constants and weights

Any proposal to change scoring or signal logic is a separate workstream with its own branch and test suite.
