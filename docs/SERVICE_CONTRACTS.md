# Service Contracts

Inter-service API contracts for Sachoki. These are the agreed interfaces between services.
All routes are prefixed with the service base URL from the calling service's env var.

---

## scanner-api contracts

Consumed by: dashboard, research-api

### Health

```
GET /api/health
→ 200 { status: "ok", version: "x.y.z", scan_count: N }
```

### Version

```
GET /api/version
→ 200 { version: "x.y.z", scoring_engine: "...", signal_engine: "..." }
```

### Latest ULTRA scan results

```
GET /api/scans/ultra/latest
  ?universe=sp500|nasdaq|russell2k|all_us
  &tf=1d|4h
  &limit=N          (default 100)
  &offset=N         (default 0)

→ 200 {
    scan_id: str,
    scanned_at: ISO8601,
    universe: str,
    tf: str,
    total: int,
    results: [
      {
        ticker: str,
        ultra_score: int,
        ultra_score_band_v2: "A+"|"A"|"B"|"C"|"D",
        ultra_score_priority: str,
        turbo_score: int,
        t_signal: str,
        z_signal: str,
        combo: str[],
        profile: str,
        sector: str,
        change_pct: float,
        volume: int
      }
    ]
  }
```

### Top ULTRA candidates

```
GET /api/scans/ultra/latest/candidates
  ?universe=sp500
  &tf=1d
  &min_score=80
  &limit=20

→ 200 {
    candidates: [
      { ticker, ultra_score, ultra_score_band_v2, ultra_score_priority, turbo_score, combo: [] }
    ]
  }
```

### Debug / status

```
GET /api/debug/status
→ 200 {
    scheduler_running: bool,
    last_scan_times: { "sp500:1d": ISO8601, ... },
    db_connected: bool,
    redis_connected: bool,
    cache_hits: int,
    cache_misses: int
  }
```

---

## research-api contracts

Consumed by: dashboard

### Replay job

```
POST /api/replay/run
Body: { universe: str, tf: str, lookback_days: int }
→ 202 { job_id: str, status: "QUEUED" }

GET /api/replay/status?job_id=...
→ 200 { job_id, status: "QUEUED"|"RUNNING"|"DONE"|"ERROR", progress_pct: int, error?: str }

GET /api/replay/report/{job_id}
  ?page=1&page_size=100
→ 200 { total: int, rows: [...] }

GET /api/replay/export/{job_id}
→ 200 text/csv attachment
```

### Sequence scan

```
POST /api/sequence-scan/trigger
Body: { universe, tf, length: int, type: "BULL"|"BEAR"|"ALL" }
→ 202 { job_id: str }

GET /api/sequence-scan/status?job_id=...
→ 200 { job_id, status, progress_pct }

GET /api/sequence-scan/results?job_id=...&sort=score&limit=50
→ 200 { sequences: [{ sequence, type_seq, count, win_rate, score, ... }] }
```

### Stock stat

```
POST /api/stock-stat/trigger
Body: { universe, tf }
→ 202 { job_id: str }

GET /api/stock-stat/status?job_id=...
→ 200 { job_id, status, progress_pct, rows_processed }

GET /api/stock-stat/download?job_id=...
→ 200 text/csv attachment
```

---

## dashboard BFF contracts

Consumed by: React SPA (browser)

The BFF is a thin aggregation layer — its routes are not consumed by other services.
Full API surface documented in `ARCHITECTURE.md` (root) under "API Endpoints".

---

## Versioning rules

1. All inter-service routes are versioned by the service version, not a URL prefix.
2. Breaking changes require a version bump and a deprecation period of at least one phase.
3. Additive fields (new response keys) are non-breaking.
4. Removing or renaming fields is a breaking change — coordinate across services before merging.

---

## Error response shape (all services)

```json
{
  "error": "human-readable message",
  "code": "SNAKE_CASE_ERROR_CODE",
  "detail": {}
}
```

HTTP status codes:
- 400 — bad request / validation error
- 404 — resource not found
- 503 — upstream service unreachable (dashboard/research-api only)
- 500 — internal error
