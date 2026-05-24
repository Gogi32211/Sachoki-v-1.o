# Phase 8I — Real Ultra ↔ Turbo ↔ RTB Audit

**Status:** Findings doc. **No implementation yet — Phase 8G `turbo_score = ultra_score` aliasing and the RTB tier-approximation are now formally proven wrong and must be reverted.**

## TL;DR

The Phase 8G assumption that **Turbo was a secondary score replaceable by Ultra**, and that **RTB was a display row only**, was wrong on both counts. Old code clearly shows:

1. **Turbo is the input to Ultra**, not the other way around. `run_turbo_scan` produces full rows with all signal flags + a `turbo_score`. `ultra_orchestrator` wraps each Turbo row, then calls `ultra_score.compute_ultra_score(row)` which reads those flags.
2. **`turbo_score` is THE primary score in the old UltraScanPanel UI** — primary sort key, primary band filter (0-20/.../81-100), tier emoji (🔥/★/▲), table "Score" column.
3. **`ultra_score` is a SECOND, separate score** banded A+/A/B/C/D, shown in its own "ULTRA Score" column.
4. **`rtb_phase` is real input data** — `calc_rtb_v4` runs inside `_scan_turbo_ticker` for every bar; the result **feeds back into `turbo_score`** (NASDAQ profile combo bonuses + penalty conditions), and is also a filter + display field in the UI, and downstream input to `beta_engine`.
5. **The N=1/3/5/10 lookback** uses `turbo_score_n3`/`turbo_score_n5`/`turbo_score_n10` columns — these are computed by re-running `_calc_turbo_score` on an N-bar-old snapshot. They are **not derivable from `ultra_score`**.

So aliasing `turbo_score = ultra_score` and approximating `rtb_phase` from GOG tier are both formula-changing shortcuts that violate the spec rule "no formula changes."

## Code references

### 1. Old dependency order (verbatim from code)

```
fetch_htf(ticker, interval) → OHLCV df
   ↓
turbo_engine._scan_turbo_ticker(ticker, interval, …)        [backend/turbo_engine.py:782]
   ├─ compute_signals(df)               [signal_engine.py]
   ├─ compute_b_signals(df)             [signal_engine.py]
   ├─ compute_g_signals(df)             [signal_engine.py]
   ├─ compute_f_signals(df)             [f_engine.py]
   ├─ compute_wlnbb(df)                 [wlnbb_engine.py]
   ├─ compute_combo(df)                 [combo_engine.py]
   ├─ compute_tz_state(df)              [combo_engine.py]
   ├─ compute_wick(df), compute_wick_x  [wick_engine.py]
   ├─ compute_vabs(df)                  [vabs_engine.py]
   ├─ compute_260308_l88(df)            [ultra_engine.py]
   ├─ compute_ultra_v2(df)              [ultra_engine.py]
   ├─ compute_delta(df)                 [delta_engine.py]
   │
   ├─ for each bar (b0…bN):
   │     calc_rtb_v4(row, history, prev_phase, prev_age, soft_streak,
   │                 pending_phase, pending_phase_count)        [rtb_engine.py]
   │     → row["rtb_build"], row["rtb_turn"], row["rtb_ready"],
   │       row["rtb_bonus3"], row["rtb_late"], row["rtb_total"],
   │       row["rtb_phase"], row["rtb_transition"], row["rtb_phase_age"]
   │                                                  [turbo_engine.py:1304-1386]
   │
   ├─ _calc_turbo_score(row, profile)                  [turbo_engine.py:307-...]
   │     ← reads ALL signal flags + rtb_phase + rtb_transition
   │     → row["turbo_score"]
   │
   ├─ for n in (3, 5, 10):
   │     _calc_turbo_score(row_n_bars_old, profile)
   │     → row["turbo_score_n3"], row["turbo_score_n5"], row["turbo_score_n10"]
   │                                                  [turbo_engine.py:1554-1655]
   │
   └─ get_turbo_results(...)                           [turbo_engine.py:2049]
         returns Turbo rows from SQLite, ordered by turbo_score DESC

   ↓
ultra_orchestrator.run_ultra_scan_job(universe, tf, …)  [Stage 1, turbo only]
   ↓
ultra_orchestrator._empty_unenriched_row(turbo_row)     [wraps Turbo row as Ultra row]
   ↓
ultra_orchestrator._attach_ultra_score(row)             [calls compute_ultra_score(row)]
   ↓
ultra_score.compute_ultra_score(row)                    [reads signals + profile fields]
   → row["ultra_score"]            (0-100)
   → row["ultra_score_band"]       (legacy A/B/C/D)
   → row["ultra_score_band_v2"]    (A+/A/B/C/D)
   → row["ultra_score_priority"]   (HIGH_PRIORITY / WATCH_A / …)
   → row["ultra_score_reasons"]    (active signal tokens)
   → row["ultra_score_flags"]      (risk flags)

   ↓
[ Stage 2 — enrich(tickers): adds tz_wlnbb, tz_intel, pullback, rare_reversal
  to selected subset of rows, then re-runs _attach_ultra_score on each ]

   ↓
UltraScanPanel.jsx
   ├─ sort:   default sortBy = "turbo_score"          [line 832]
   ├─ filter: score band (0-20/…/81-100) on
   │          effectiveScoreCol = turbo_score / turbo_score_nN  [line 935-944]
   ├─ filter: rtb_phase                                [line 954]
   ├─ filter: direction (bull/bear) on tz_bull          [line 952-953]
   ├─ table:  "Score" column = r.turbo_score            [line 555-569]
   ├─ table:  "ULTRA Score" column = r.ultra_score banded A+/A/B/C/D
   ├─ table:  "RTB" column = `${r.rtb_phase} ${r.rtb_total}`  [line 1045-1046]
   └─ export: both turbo_* and ultra_* columns in CSV   [lines 735, 1218-1228, 1282]
```

### 2. Answer matrix (with code citations)

| # | Question | Verdict | Evidence |
|---|---|---|---|
| **A** | Did old Ultra candidate selection depend on `turbo_score`? | **YES** | `min_store_score >= 5.0` in `run_turbo_scan` ([turbo_engine.py:1920](backend/turbo_engine.py:1920)). Only rows with `turbo_score >= min_store_score` are stored, which become the input to Ultra. |
| **B** | Did old Ultra ranking/sorting use `turbo_score` or `ultra_score`? | **`turbo_score`** | `useState('turbo_score')` is the default sort ([UltraScanPanel.jsx:832](frontend/src/components/UltraScanPanel.jsx:832)). The `effectiveScoreCol` chosen by N-lookback is always `turbo_score`-family ([line 935-938](frontend/src/components/UltraScanPanel.jsx:935)). |
| **C** | Did band A+/A/B/C/D come from `ultra_score`, `turbo_score`, or both? | **Both, but separately** | Score band buttons (0-20…81-100) filter on `turbo_score` ([line 944](frontend/src/components/UltraScanPanel.jsx:944)). A+/A/B/C/D label is a property of `ultra_score` ([ultra_score.py:573-585](backend/ultra_score.py:573)). The two labels coexist in different columns. |
| **D** | Did old Ultra filters use `turbo_score` as the primary score? | **YES** | Score band, direction (`tz_bull`), N-lookback, RTB phase filter — all read from the Turbo row. ([UltraScanPanel.jsx:935-993](frontend/src/components/UltraScanPanel.jsx:935)) |
| **E** | Did old `ultra_score.compute_ultra_score()` call `turbo_engine` internally? | **No (but it reads Turbo's row)** | `ultra_score.py` has no import of `turbo_engine`. It expects a flat dict with all signal booleans + `profile_score` + `tz_intel` etc. ([ultra_score.py:304](backend/ultra_score.py:304)) — that dict is the **Turbo row** in production, attached by `ultra_orchestrator._attach_ultra_score`. |
| **F** | Did `turbo_engine` consume Ultra signals, or did Ultra consume Turbo output? | **Ultra consumes Turbo output** | `ultra_orchestrator.run_ultra_scan_job` calls `run_turbo_scan` first, then `get_turbo_results`, then `_empty_unenriched_row(turbo_row)` wraps each ([ultra_orchestrator.py:467-525](backend/ultra_orchestrator.py:467)). |
| **G** | Was RTB part of candidate selection, score boosting, or display only? | **All three** | (1) **Score boosting**: `_calc_turbo_score` reads `rtb_phase=="C"` and `rtb_transition=="B_TO_C"` as NASDAQ combo bonuses, and demotes `T4/T6` without `rtb_phase=="C"` context ([turbo_engine.py:518-594](backend/turbo_engine.py:518)). (2) **Filter**: `rtbPhase` selector in UI ([UltraScanPanel.jsx:830, 954](frontend/src/components/UltraScanPanel.jsx:830)). (3) **Display**: RTB column shows `${r.rtb_phase} ${r.rtb_total}` ([line 1046](frontend/src/components/UltraScanPanel.jsx:1046)). (4) **Downstream**: `beta_engine.calc_beta_score` adds phase_bonus `{C:7, B:4, A:2, D:0}` ([beta_engine.py:200-202](backend/beta_engine.py:200)), and `beta_zone` flips to `SHORT_WATCH` when `display<40 and rtb_phase=='D'` ([beta_engine.py:303-311](backend/beta_engine.py:303)). |
| **H** | Which fields were stored in old row output? | **All of them** | DB schema in `turbo_engine.py:189-209` includes: `turbo_score`, `turbo_score_n3`, `turbo_score_n5`, `turbo_score_n10`, `rtb_build`, `rtb_turn`, `rtb_ready`, `rtb_bonus3`, `rtb_late`, `rtb_total`, `rtb_phase`, `rtb_transition`, `rtb_phase_age`. `ultra_score`, `ultra_score_band_v2`, `ultra_score_priority`, `ultra_score_reasons`, `ultra_score_flags`, `signal_score`, `real_ultra_score`, `final_bull_score`, `final_bear_score`, `pf`, `cat` are attached by `_attach_ultra_score` ([ultra_orchestrator.py:356-370](backend/ultra_orchestrator.py:356)) on top of the Turbo row. **`turbo_score` ≠ `ultra_score`** anywhere in the code. |

### 3. What Phase 8G got wrong

| Phase 8G assertion | Reality | Severity |
|---|---|---|
| "turbo_score is superseded by ultra_score; alias them" ([chart_rtb_engine.py docstring](apps/scanner-api/backend/chart_rtb_engine.py)) | Turbo and Ultra are TWO distinct scores. UI sorts by `turbo_score`, filters score bands on `turbo_score`, shows BOTH columns. Aliasing destroys the primary sort key. | **HIGH** |
| "turbo_engine is too tightly coupled to old yfinance + SQLite scan orchestration" | True for the *scan orchestration* parts (universe fetch, SQLite store), but the **`_calc_turbo_score(row, profile)` function itself is pure** — takes a row dict, returns a float. That's the part that needed to be ported, ~1100 lines of pure-Python scoring rules. | **HIGH** |
| "rtb_phase derived from GOG tier via approximation" ([chart_rtb_engine.py compute_rtb_from_bar](apps/scanner-api/backend/chart_rtb_engine.py)) | RTB is computed by `calc_rtb_v4(row, history, prev_phase, …)` — a 690-line stateful per-bar engine with hard/soft resets and hysteresis. It is **not** a derivation of GOG; if anything, GOG depends on RTB context, not vice versa. | **HIGH** |
| "turbo_score_n3/n5/n10 not ported (N= lookback unsupported)" | True, but these are not optional. The score band filter operates on whichever `turbo_score_n*` matches the current N selector. Without them, the N=3/5/10 buttons in the UI have nothing to read. | **MEDIUM** |
| "signal_source = inferred_proxy vs engine_registry" debug flag | Still useful and correct, but the deeper issue is that `ultra_score` should be computed **after** the Turbo row is fully populated (including rtb_phase, profile_score, etc.) — not on a synthetic row. Phase 8G commit 5 fixed signals; it did NOT fix the rest of the row context. | **MEDIUM** |

### 4. Concrete dependency diagram (corrected)

```
                    OHLCV (fetch_bars)
                         │
                         ▼
                 indicator_builder.build_indicators(df)         [Phase 8G ✓]
                         │
                         ▼
              engine_registry.run_engines() runs:
              tz, wlnbb, vabs, wick, combo, f, fly, b, g,
              ult260, ult_v2, gog                                [Phase 8G ✓]
                         │
                         ▼
         build per-bar Turbo row dict with all signal flags
         (mirroring backend/turbo_engine.py:1300 input shape)
                         │
                         ▼
         FOR EACH BAR (or just latest):
            calc_rtb_v4(row, history, prev_phase, prev_age,
                        soft_streak, pending_phase, pending_phase_count)
            → row["rtb_phase"], row["rtb_total"], row["rtb_build"],
              row["rtb_turn"], row["rtb_ready"], row["rtb_bonus3"],
              row["rtb_late"], row["rtb_transition"], row["rtb_phase_age"]
                                                          ← Phase 8H NEEDS
                         │
                         ▼
         _calc_turbo_score(row, profile)
            → row["turbo_score"]                          ← Phase 8H NEEDS
                         │
                         ▼
         [If lookback enabled] _calc_turbo_score on N-bar-old snapshots
            → row["turbo_score_n3"], _n5, _n10            ← Phase 8H NEEDS
                         │
                         ▼
         profile_playbook.enrich_row_with_profile(row, universe)
            → row["profile_score"], row["profile_category"],
              row["sweet_spot_active"], row["late_warning"]  ← Phase 8H NEEDS
                         │
                         ▼
         compute_ultra_score(row)                          [Phase 8G done; correctness
                                                            depends on row above]
            → row["ultra_score"], row["ultra_score_band_v2"],
              row["ultra_score_priority"], row["ultra_score_reasons"],
              row["ultra_score_flags"]
                         │
                         ▼
         normalized scanner output (single source of truth)
                         │
            ┌────────────┼────────────────────┐
            ▼            ▼                    ▼
        Ultra latest   Super Chart        Filters / Export
```

### 5. What this means for current Phase 8G state

Files that contain the wrong abstractions and must change:

- `apps/scanner-api/backend/chart_rtb_engine.py` — **delete or rewrite**. The "tier_approximation_v1" path produces fictitious values that don't match the old engine's hysteresis-confirmed phases.
- `apps/scanner-api/backend/engine_registry.py` — the `fill_scores_from_bar()` call that aliases `turbo_score = ultra_score` must be removed; turbo_score must come from the real `_calc_turbo_score`.
- `apps/scanner-api/backend/scoring_adapter.py` — the `final_bull_score / final_bear_score / signal_score` placeholders are fine in shape, but the **ultra_score computation must happen AFTER turbo+rtb populate the row**. Order today: engines → ultra_score. Correct order: engines → rtb → turbo → profile_playbook → ultra_score.
- `apps/dashboard/frontend/app.js` — the UI table column "Score" should read `c.turbo_score` (primary), with `ultra_score` in a separate column. Current Phase 8H-UI labels "Score" as `c.ultra_score` — close enough visually but semantically wrong once real turbo_score lands.

### 6. Restoration plan (proposed Phase 8I)

Sized by remaining engine ports + new wiring. **Awaits user approval before any code edit.**

**Step 1 — Port `rtb_engine.py` verbatim** (~690 lines, pure-Python, no external deps).
File: `apps/scanner-api/backend/chart_rtb_engine.py` (replaces the tier-approximation stub).
Exports `calc_rtb_v4(row, history, prev_phase, prev_phase_age, soft_streak, pending_phase, pending_phase_count) -> dict`. Pure function; identical to old.

**Step 2 — Port `_calc_turbo_score` verbatim** from `backend/turbo_engine.py` lines 307–~1200.
File: `apps/scanner-api/backend/chart_turbo_engine.py`.
Exports `compute_turbo_score(row, profile='sp500') -> float`. The function is pure — it reads a row dict and returns a number. The surrounding SQLite/yfinance plumbing is NOT ported.

Includes the per-bar-snapshot N=3/5/10 variants (lines 1554-1655) — exported as `compute_turbo_score_nN(rows_window, n, profile) -> float`.

**Step 3 — Build the row that turbo expects.**
The Turbo row is a flat dict with every signal column the upstream engines produce. `engine_registry` already produces all those columns on its `bar` shape (`bar.signals.*`). Add a thin `_flatten_bar_to_turbo_row(bar, history)` helper that:
- Lifts each signal label into a boolean column (e.g. `BUY` in `signals.i` → `buy_2809=True`)
- Surfaces `tz_sig` (first T/Z label), `vol_bucket`, `tz_bull`, `tz_attempt`, `rsi`, `cci`, `avg_vol`
- Adds 5-bar / 3-bar history fields (`_l34_recent_5b`, `_l64_recent_5b`, `_blue_recent_5b`, `_ztrap_recent_5b`) used by NASDAQ combo bonuses

This is where the verbatim mapping table from `turbo_engine.py:189-209` (DB schema) becomes the spec.

**Step 4 — Wire the corrected pipeline in `engine_registry.run_engines`.**

```
for each bar:
  bar = build_bar(...)
  apply_engine_outputs(bar, tz_df, wl_df, vabs_df, ...)  # already done
  row = _flatten_bar_to_turbo_row(bar, history)          # NEW
  rtb = calc_rtb_v4(row, history, prev_phase, …)         # NEW; carries state across bars
  row.update(rtb)
  bar["scores"]["rtb_phase"]      = rtb["rtb_phase"]
  bar["scores"]["rtb_total"]      = rtb["rtb_total"]
  bar["scores"]["rtb_build"]      = rtb["rtb_build"]
  bar["scores"]["rtb_turn"]       = rtb["rtb_turn"]
  bar["scores"]["rtb_ready"]      = rtb["rtb_ready"]
  bar["scores"]["rtb_bonus3"]     = rtb["rtb_bonus3"]
  bar["scores"]["rtb_late"]       = rtb["rtb_late"]
  bar["scores"]["rtb_transition"] = rtb["rtb_transition"]
  bar["scores"]["rtb_phase_age"]  = rtb["rtb_phase_age"]
  prev_phase, prev_phase_age, soft_streak, pending_phase, pending_phase_count = \
    rtb["rtb_phase"], rtb["rtb_phase_age"], rtb["_soft_streak"], …

  bar["scores"]["turbo_score"]    = compute_turbo_score(row, profile=universe_to_profile(universe))
  # N-lookback variants only on latest bar
```

**Step 5 — `scoring_adapter` reads the now-fully-populated row.**
`compute_ultra_score(row)` runs AFTER step 4, so it sees the same row Old Ultra saw (including `rtb_phase`, `turbo_score`, eventually `profile_score`). No further change to `ultra_score.py`.

**Step 6 — Frontend table column corrections.**
- "Score" column → `c.turbo_score` (primary).
- "ULTRA" column → `c.ultra_score` + band chip (secondary).
- "RTB" column → real `c.scores.rtb_phase + c.scores.rtb_total`.
- Score band filter (0-20/…/81-100) → `c.turbo_score`.
- N=1/3/5/10 segmented buttons → switch which column the score band filters against (`turbo_score_n*`).

**Step 7 — Delete or relabel `chart_rtb_engine.py` approximation.**
Keep the file as a thin shim around the real `calc_rtb_v4` port, OR delete it entirely and import the real one. No more `rtb_source="tier_approximation_v1"` debug flag — that flag goes away because no approximation exists.

**Step 8 — Update Phase 8G reports.**
`PHASE_8G_FINAL_REPORT.md` §15 currently lists turbo/rtb as "INTENTIONAL_GAP — documented" — must be re-marked `DEPRECATED_BUG — replaced in Phase 8I`. `PHASE_8G_VERIFICATION_GATE.md` §6 same treatment.

### 7. What is NOT in this phase

- `profile_playbook.py` (Pf, Cat, sweet_spot_active, late_warning) — would be a separate Phase 8J. Without it `ultra_score`'s `profile_score` input stays −1 (= neutral, contributes 0 to scoring per ultra_score.py docs). That's a known quality gap, but it does NOT block Turbo/RTB restoration.
- `sector_engine.py` (dynamic `sector_band`) — separate workstream.
- `tz_intelligence/` ABR classifier — Phase 8J+.

### 8. Acceptance criteria recap (from your spec, with checkboxes)

- [x] No more `turbo_score` alias to `ultra_score` unless old code proves equivalence. **Old code disproves equivalence.** Aliasing will be removed in Phase 8I.
- [ ] Old Ultra score/filter/sort behavior restored. **Needs Phase 8I.**
- [ ] New scanner output contains real turbo and real RTB fields. **Needs Phase 8I.**
- [ ] Ultra latest, Super Chart, filters, and export all read the same real turbo/RTB fields from normalized scanner output. **Needs Phase 8I + frontend column rebind.**
- [ ] Real-data test includes at least 5 tickers where old turbo_score existed. **Same staging gate as Phase 8G — requires Massive key + old Ultra reference.**

## Conclusion

The audit confirms the user's suspicion. Phase 8G's treatment of Turbo as "superseded" and RTB as "display-only" was wrong. Both must be ported as real engines, and the engine registry pipeline must be re-ordered to: signals → flatten-to-Turbo-row → RTB v4 → Turbo score → (profile_playbook) → Ultra score.

The good news: the foundation Phase 8G built (engine_registry, normalized bar schema, design system, scanner_client) is **the right substrate** for Phase 8I — no architectural rework is needed. Phase 8I is two engine ports (~800 lines of pure-Python from the old code) plus rewiring the call order in `engine_registry.run_engines()`.

**Awaiting user approval to proceed with Phase 8I steps 1–8.**
