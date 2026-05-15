# Profile Playbook Implementation Audit

Generated: 2026-05-03

---

## 1. Files Changed

| Path | What Changed | Why |
|------|-------------|-----|
| `backend/main.py` | Added profile enrichment in `api_turbo_scan` endpoint | Adds profile fields to every turbo-scan API response without touching DB schema |
| `frontend/src/components/TurboScanPanel.jsx` | Added Sweet Spot filter row, `sweetSpotFilter` state, profile columns (Pf Score, Category) in table header and rows | Exposes profile context in UI; filter activates profile-sort override |

## 2. New Files Added

| Path | Purpose |
|------|---------|
| `backend/profile_playbook.py` | Core profile/playbook module: PROFILES dict, signal normalization, extraction, score computation, row enrichment |
| `tests/test_profile_playbook.py` | 28 unit tests covering price buckets, signal parsing, score computation, canonical field safety |
| `backend/profile_playbook_implementation_audit.md` | This file |

## 3. Functions Added

| Function | File | Summary |
|----------|------|---------|
| `normalize_signal_token(token)` | profile_playbook.py | Maps arrow symbols / aliases to canonical signal name |
| `parse_signal_cell(value)` | profile_playbook.py | Parses string cell (CSV) → Set of signal names; handles whitespace/comma/pipe |
| `extract_signals_from_turbo_row(row)` | profile_playbook.py | Maps turbo_scan_results boolean columns → Set of canonical signal names; also parses tz_sig string |
| `get_signals_5bar(rows_for_ticker)` | profile_playbook.py | Union of signals over last 6 rows (for replay/historical context) |
| `get_profile(row, universe)` | profile_playbook.py | Assigns profile key based on universe + current close (or last_price) |
| `compute_profile_score(signals, profile_name)` | profile_playbook.py | Computes profile_score, category, sweet_spot_active, late_warning, matched pairs |
| `enrich_row_with_profile(row, universe, signals=None)` | profile_playbook.py | Adds all profile fields to row dict; never overwrites canonical score columns |

## 4. API / Frontend Changes

### New response fields (all turbo-scan results)
```
profile_name              str    — e.g. "SP500_50_150"
profile_score             int    — additive playbook score (0+)
profile_category          str    — SWEET_SPOT / BUILDING / WATCH / LATE
sweet_spot_active         bool
late_warning              bool
profile_role              str|null
profile_description       str|null
profile_experimental      bool   — True for NASDAQ profiles
profile_preferred_preset  str|null
profile_suggested_tp      float|null
profile_suggested_sl      float|null
profile_max_hold          int|null
matched_profile_signals   list[str]
matched_profile_pairs     list[str]
```

### New frontend columns
- **Pf Score** — sortable, shows profile_score integer; tooltip shows matched pairs
- **Category** — color-coded badge: ⭐ SWEET_SPOT (green), ↑ BUILDING (yellow), WATCH (gray), ⚠ LATE (amber)

### New filter
- **⭐ Sweet Spot** button — filters to `sweet_spot_active=true AND late_warning=false`
- When active: sorts by `profile_score DESC, turbo_score DESC`
- When inactive: default sort unchanged

### Default sorting
- Unchanged when Sweet Spot filter is off
- Overridden to profile_score → turbo_score when Sweet Spot filter is active

## 5. Validation Status

### Unit tests
28/28 PASS. Covers: price bucket assignment, boundary conditions, signal normalization,
cell parsing, score computation, pair bonus detection, category assignment, canonical
field safety, experimental flag correctness.

### Profile calibration status
These are **starting weights** derived from the statistical background in the task spec.
They have NOT been validated against live TP/SL replay data.

| Profile | Status | Notes |
|---------|--------|-------|
| SP500_LT20 | NEEDS_RECALIBRATION | High volatility — weights need replay validation |
| SP500_20_50 | NEEDS_RECALIBRATION | Starting weights; pair bonuses need combo-perf validation |
| SP500_50_150 | NEEDS_RECALIBRATION | Best candidate profile — validate first with replay |
| SP500_150_300 | NEEDS_RECALIBRATION | Quality-setup-only; validate SHAKEOUT_ABSORB signal mapping |
| SP500_300_PLUS | NEEDS_RECALIBRATION | Weak bucket — currently combo-only; validate pair set |
| NASDAQ_PENNY | EXPERIMENTAL / NEEDS_RECALIBRATION | Do not use as trading filter |
| NASDAQ_REAL | EXPERIMENTAL / NEEDS_RECALIBRATION | Do not use as trading filter |

### Validation reports
Validation reports (profile_score_distribution.csv, profile_sweet_spot_validation.csv,
profile_tpsl_validation.csv, profile_examples.csv, profile_signal_token_coverage.csv,
profile_playbook_validation.md) require running replay analytics first.

To generate: run replay on sp500/1d → profile enrichment produces per-row profile fields →
reports can be derived from replay output CSVs by grouping on profile_name/profile_category.

## 6. No-Go Confirmation

| Field | Status |
|-------|--------|
| FINAL_BULL_SCORE | **UNCHANGED** — not touched by profile enrichment |
| TURBO_SCORE | **UNCHANGED** |
| SIGNAL_SCORE | **UNCHANGED** |
| RTB_SCORE / rtb_total | **UNCHANGED** |
| Raw signal logic | **UNCHANGED** — no signal engine modifications |
| Visual labels (badges, tier labels) | **UNCHANGED** |
| Split analytics | **UNCHANGED / DISABLED** — not referenced |
| Default scanner sort | **UNCHANGED** — sort override only when Sweet Spot filter is explicitly enabled |
| profile_score | **ADDITIVE ONLY** — context/playbook layer, never used as primary ranking unless filter active |
| Canonical scoring source | **UNCHANGED** — profile_playbook.py has zero imports from canonical_scoring_engine |

## 7. Known Limitations

1. **Profile weights are starting weights** — derived from statistical background, not TP/SL replay validation. Sweet spot thresholds (55–75 for SP500_50_150, etc.) are initial estimates.
2. **SWEET_SPOT thresholds need replay calibration** — currently set conservatively. Real distribution may show too few or too many rows in SWEET_SPOT.
3. **NASDAQ profiles are experimental** — marked as such. Single signals are weak on NASDAQ; combo-first logic is partially encoded but needs separate validation.
4. **Current close is used for row-level profile assignment** — median_price is fallback only.
5. **`extract_signals_from_turbo_row` maps boolean columns** — compound signals requiring multiple bars are not detected (e.g., true 5-bar windows). Only current-bar boolean flags are used.
6. **SHAKEOUT_ABSORB** signal is defined in SP500_150_300 weights but has no direct turbo column; will not contribute to score until mapped.
7. **Profile enrichment is computed on API response**, not stored in DB — rescanning updates scores automatically on next fetch.

## 8. Next Recommended Steps

1. Run replay analytics on sp500/1d with current stock_stat data
2. Derive `profile_score_distribution.csv` from replay output (group by profile_name, profile_category)
3. Cross-validate SWEET_SPOT rows against `big_win_10d_rate` and `avg_max_high_10d`
4. If SWEET_SPOT outperforms WATCH in TP/SL (conservative EV positive), mark profile as PASS
5. If SWEET_SPOT does not outperform, set sweet_spot thresholds as NEEDS_RECALIBRATION and keep filter as research-only
6. Do NOT enable Sweet Spot as a default workflow until at least SP500_50_150 shows PASS in TP/SL validation
7. NASDAQ profiles should remain experimental/context-only until a separate combo-first replay pass is done
