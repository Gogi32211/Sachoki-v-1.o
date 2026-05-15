# Sachoki — Target Monorepo Architecture

> This document describes the **target state** after the migration is complete.
> The current working app lives in `backend/` and `frontend/` and is unchanged.
> See `docs/MIGRATION_PLAN.md` for the phased path from current state to target.

---

## Repository Layout

```
sachoki/                          ← monorepo root
│
├── apps/                         ← independently deployable services
│   ├── dashboard/                ← React SPA + BFF
│   ├── scanner-api/              ← signal computation + scan persistence
│   └── research-api/             ← replay, analytics, exports
│
├── packages/                     ← shared code (no circular deps)
│   ├── shared/                   ← DB helpers, Pydantic models, design system
│   └── signal-engine/            ← pure signal computation (Phase 5+)
│
├── infra/railway/                ← env examples per service
│
├── docs/                         ← architecture, contracts, migration plan
│
├── backend/                      ← CURRENT production code (do not remove)
├── frontend/                     ← CURRENT production code (do not remove)
└── Dockerfile                    ← CURRENT unified build (do not remove)
```

---

## Railway Production Project: `sachoki-prod`

```
┌─────────────────────────────────────────────────────────────┐
│  Railway Project: sachoki-prod                              │
│                                                             │
│  ┌──────────────┐   ┌────────────────┐   ┌─────────────┐  │
│  │  dashboard   │   │  scanner-api   │   │ research-api│  │
│  │              │──▶│                │◀──│             │  │
│  │  React SPA   │   │  FastAPI       │   │  FastAPI    │  │
│  │  BFF         │   │  APScheduler   │   │  APScheduler│  │
│  └──────────────┘   └────────────────┘   └─────────────┘  │
│          │                  │                    │          │
│          └──────────────────┴────────────────────┘         │
│                             │                              │
│                    ┌────────┴────────┐                     │
│                    │   PostgreSQL    │                     │
│                    │   (shared)      │                     │
│                    └────────┬────────┘                     │
│                             │                              │
│                    ┌────────┴────────┐                     │
│                    │     Redis       │                     │
│                    │   (shared)      │                     │
│                    └─────────────────┘                     │
└─────────────────────────────────────────────────────────────┘
```

### Service Definitions

| Service | Root Dir | Port | Healthcheck |
|---------|----------|------|-------------|
| dashboard | `apps/dashboard` | 8080 | `GET /api/health` |
| scanner-api | `apps/scanner-api` | 8080 | `GET /api/health` |
| research-api | `apps/research-api` | 8080 | `GET /api/health` |

All three services share one PostgreSQL instance and one Redis instance provisioned by Railway plugins.

---

## Service Boundaries

### scanner-api — Signal Source of Truth

Owns all candle and signal computation. Other services consume its API; they do not recompute signals.

```
Inputs:  yfinance / Polygon.io OHLCV
Outputs: T/Z signals, WLNBB, VABS, TURBO_SCORE, ULTRA_SCORE, scan results
Storage: shared PostgreSQL (scan results, stock_stat, ultra_scan tables)
```

### dashboard — User Interface

Thin BFF that aggregates scanner-api and research-api responses into the views the React app needs.

```
Inputs:  scanner-api (SCANNER_API_URL), research-api (RESEARCH_API_URL)
Outputs: React SPA served to browser, BFF JSON for UI panels
Storage: shared PostgreSQL (watchlist, portfolio, chart_observations)
```

### research-api — Analytics

Hosts long-running background jobs. Reads raw signal data from scanner-api or the shared DB; never re-runs scanning.

```
Inputs:  shared PostgreSQL (stock_stat, signal tables), scanner-api on demand
Outputs: replay reports, sequence stats, CSV / ZIP exports
Storage: shared PostgreSQL (replay results, sequence cache)
```

---

## Data Flow

### Live Scan

```
APScheduler (scanner-api)
  → fetch OHLCV (yfinance / Polygon)
  → compute T/Z + WLNBB + VABS + TURBO + ULTRA
  → persist to PostgreSQL ultra_scan table
  → cache summary in Redis

dashboard BFF (on user request)
  → GET scanner-api /api/scans/ultra/latest
  → aggregate + format for UI
  → return to React
```

### Research Job

```
User triggers replay from dashboard
  → POST research-api /api/replay/run
  → research-api reads stock_stat from PostgreSQL
  → runs backtest in background (APScheduler / thread)
  → writes results to PostgreSQL replay_results table
  → dashboard polls research-api /api/replay/status
```

---

## Shared Database Schema Ownership

One PostgreSQL instance. Table ownership by service:

| Table | Owner service |
|-------|--------------|
| `ultra_scan` | scanner-api |
| `stock_stat` | scanner-api |
| `signal_replay` | scanner-api |
| `watchlist` | dashboard |
| `paper_portfolio` | dashboard |
| `chart_observations` | dashboard |
| `replay_results` | research-api |
| `sequence_results` | research-api |

Services may read each other's tables but must not write outside their owned tables.

---

## Key Design Rules

1. **One PostgreSQL, no per-service databases.** All services use `DATABASE_URL` pointing to the same instance.
2. **scanner-api is the only signal producer.** No other service recomputes T/Z or ULTRA scores.
3. **research-api is async by default.** All heavy jobs run in background; the API returns a job ID immediately.
4. **dashboard BFF is thin.** It aggregates and formats; it contains no domain logic.
5. **packages/shared has no domain logic.** Utilities only — Pydantic models, DB helpers, design system primitives.
6. **packages/signal-engine is Phase 5+.** Do not extract signal logic until scanner-api extraction is complete.
