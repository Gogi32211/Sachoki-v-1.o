# Sachoki — Target Architecture (6-service split)

**Status:** Direction approved. Phase A (this doc + checkpoint tag) is committed.
Phase C step 1 (market_bars cache layer inside scanner-api as a module, not
yet a separate service) lands alongside this doc. Further phases below are
the runway.

---

## The picture

```
┌─────────────┐    ┌──────────┐    ┌────────────┐    ┌────────────┐    ┌────────────┐
│ market-data │ →  │ scanner  │ →  │  engine    │ →  │ generator  │ →  │ dashboard  │
│    -api     │    │  -api    │    │   -api     │    │   -api     │    │  (UI+BFF)  │
└─────────────┘    └──────────┘    └────────────┘    └────────────┘    └────────────┘
   Massive →           run            18 engines       top_movers /         pages /
   market_bars       lifecycle         scoring         best_setups /        admin /
                     orchestration                     sector_heat          BFF proxy

                                                    ┌────────────┐
                                                    │ research   │
                                                    │   -api     │
                                                    │  replay /  │
                                                    │  history   │
                                                    └────────────┘
```

## One-line per service (the law)

| Service        | Job                                  | Forbidden                              |
|----------------|--------------------------------------|----------------------------------------|
| market-data-api| sync Massive → `market_bars`         | scoring, dashboard views               |
| engine-api     | pure compute (18 engines + scoring)  | hit Massive, write DB                  |
| scanner-api    | scan lifecycle, run + candidate IO   | compute engines, fetch Massive directly|
| generator-api  | dashboard-ready views                | raw scoring                            |
| dashboard      | UI + BFF proxy + admin               | compute anything                       |
| research-api   | replay, historical stats             | touch live scan path                   |

> **The hard rule:** no service does another service's job.

## Data flow

### A. Sync Market Data (admin button)
```
admin click → market-data-api → Massive (only missing candles) → market_bars
```
Idempotent. Pulls only what we don't have. Adjusted=true. Completed bars only.
No yfinance ever.

### B. Run Scan (admin button)
```
admin click → scanner-api → market_bars (read) → engine-api (compute)
                                                ↓
                                ultra_scan_runs + ultra_scan_candidates
```
Never re-fetches Massive if the candles already exist for the requested window.
Scoring iteration is free: re-run scan = re-call engine-api on the same bars.

### C. Generate Views (admin button)
```
admin click → generator-api → latest scan candidates → scan_generated_views
```
Pure derivation. Re-runnable. No upstream data fetch.

### D. Full Pipeline (admin button)
```
admin click → A → B → C
```

### E. Display
```
dashboard frontend → dashboard BFF → generator-api / scanner-api → render
```
Dashboard never computes anything visual-bearing. If a page needs a number,
that number comes precomputed from generator-api.

## Tables (Postgres, shared)

### Existing (do not change)
- `ultra_scan_runs`
- `ultra_scan_candidates`

### New
- **`market_bars`** ← Phase C step 1 lands today (as module inside scanner-api)
  ```
  symbol      VARCHAR
  tf          VARCHAR
  ts          TIMESTAMPTZ
  open        NUMERIC
  high        NUMERIC
  low         NUMERIC
  close       NUMERIC
  volume      NUMERIC
  adjusted    BOOLEAN
  provider    VARCHAR
  created_at  TIMESTAMPTZ
  updated_at  TIMESTAMPTZ
  PRIMARY KEY (symbol, tf, ts, provider, adjusted)
  ```

- **`scan_generated_views`** ← Phase E
  ```
  scan_run_id INTEGER REFERENCES ultra_scan_runs(id) ON DELETE CASCADE
  view_type   VARCHAR  (top_movers / best_setups / sector_heat / dashboard_summary)
  payload_json JSONB
  generator_version VARCHAR
  created_at  TIMESTAMPTZ
  updated_at  TIMESTAMPTZ
  ```

## Roadmap (incremental, no big-bang)

The principle: **prove every service boundary as a module first, extract to its
own Railway service only after the API surface stabilizes.** Premature service
extraction = more deployment infra (env vars, service discovery, retries,
auth) before we even know if the boundary is right.

| Phase | What                          | Where it lives                | Risk |
|-------|-------------------------------|-------------------------------|------|
| **A** | This doc + checkpoint tag     | repo                          | 0    |
| **C-1** ← today | `market_bars` + cache module | `apps/scanner-api/backend/market_data.py` | low  |
| C-2   | Admin "Sync Market Data" button | dashboard                  | low  |
| B-1   | Extract `engine-api` module   | `apps/scanner-api/backend/engine_registry.py` is already the API surface | low |
| B-2   | Promote engine-api to Railway service | `apps/engine-api/`     | medium (network + auth) |
| C-3   | Promote market-data to Railway service | `apps/market-data-api/` | medium |
| E     | `generator-api` module → service | inline first             | medium |
| F     | Admin Control Center (4 buttons) | dashboard                | low |
| G     | Dashboard reads only views    | dashboard                     | low |

## Why module-first, service-second

Old monolith mistake we're undoing: too much logic in one process. New
monolith mistake we want to avoid: pre-emptive microservices without stable
contracts.

**Module-first wins:**
- Refactor the API surface 10× faster (in-process imports, no HTTP overhead, no auth shimming).
- Once the surface is stable for 1–2 weeks, extraction is mechanical: same Python module + thin HTTP layer + env-var deploy.
- Single-process testing of the full pipeline; service extraction adds the network as a new failure surface only when needed.

**Service-second wins (when to actually extract):**
- engine-api: extract when scoring iterations get expensive enough that we want
  to scale compute horizontally (multiple engine workers).
- market-data-api: extract when sync becomes long-running enough that we want
  it on its own scheduled worker / cron service.
- generator-api: extract when dashboard views become expensive enough to
  cache independently of scans.
- research-api: already a separate Railway service (empty), extract content
  when replay/statistics features actually land.

## What lands today (Phase A + C-1)

1. This document (`docs/ARCHITECTURE_TARGET.md`).
2. Git tag `phase-8j-pre-split-architecture` on current HEAD (safe rollback point).
3. `market_bars` table schema added to scanner-api init SQL.
4. `apps/scanner-api/backend/market_data.py` module with:
   - `sync_bars(symbols, tf, days)` → fetches missing candles from Massive, writes to `market_bars`.
   - `get_bars(symbol, tf, days)` → reads from `market_bars`, falls back to Massive on cache miss + writes back.
5. `scan_engine.fetch_bars` rewired to use `market_data.get_bars` (read-through cache).
6. New admin endpoint `POST /api/admin/sync-market-data` with `x-admin-token` auth (same pattern as `/api/admin/seed`).
7. BFF proxy + dashboard admin section (separate small commit).

**Result:** running the same scan twice no longer re-fetches Massive on the
second run. Scoring iteration becomes nearly free.

## Railway / ops requirements (for later phases)

Phase C-1 (today) requires **only** that the existing scanner-api Postgres
have permission to create one new table (`market_bars`). The schema-init
block in scanner-api/main.py runs `CREATE TABLE IF NOT EXISTS` on startup —
no manual migration step needed.

When we eventually extract market-data-api / engine-api / generator-api into
their own Railway services (phases B-2, C-3, E), the per-service Railway
config will need:

```
SCANNER_API_URL          = https://<scanner-api>.up.railway.app   (already set)
RESEARCH_API_URL         = https://<research-api>.up.railway.app  (already set)
MARKET_DATA_API_URL      = https://<market-data-api>.up.railway.app   ← new
ENGINE_API_URL           = https://<engine-api>.up.railway.app        ← new
GENERATOR_API_URL        = https://<generator-api>.up.railway.app     ← new
DATABASE_URL             = (same Postgres, shared)
MASSIVE_API_KEY          = (only market-data-api needs this once extracted)
REDIS_URL                = (shared progress / cache)
SCANNER_MAX_SYMBOLS      = 2000   (already env-driven)
ADMIN_TOKEN              = (used by /api/admin/* endpoints)
```

A concrete Railway-creation prompt for the operator is provided in the commit
message that lands these phases.
