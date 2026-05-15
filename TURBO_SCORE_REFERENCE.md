# Turbo Score — Backend Mapping Reference

**Source:** `backend/turbo_engine.py` · `_calc_turbo_score()` (line 274)  
**Version:** v3 (SP500 pooled stats, 500 tickers, 2yr window)  
**Score range:** 0 – 100

---

## 1. Family Caps & Score Summing

Score is built by summing 8 capped families + uncapped context.

```
s = 0
s += min(backbone,  18)   # conso_2809 + tz_bull + chain bonus
s += min(vol,       22)   # VABS atomic, Wyckoff, L88/260308, svs/um
s += min(breakout,  18)   # ULTRA v2, BE, BO/BX, RS
s += min(combo,     14)   # Combo signals, CD/CA/CW, HILO, ATR/BB
s += min(trend,     17)   # T/Z, WLNBB, RL, Blue, CCI, Fuchsia, W
s += min(delta,     12)   # Order-flow / delta signals
s += min(ema_x,     10)   # PREUP EMA cross series
s += min(g_sig,     10)   # G signals
s += min(conf,      18)   # Confluence bonuses (same-bar + sequence)
s -= penalties             # kill conditions (negative adjustments)
s = max(0, s)
s += context               # Wick + PARA + FLY + Vol×10 — UNCAPPED (~18 max)
s = min(100, s)
```

**Theoretical max (no caps):** backbone 18+vol 22+brk 18+combo 14+trend 17+delta 12+ema 10+g 10+conf 18+context 18 = **157**  
**Practical max (with signals):** ~100 (clamped)

---

## 2. Family Details — Every Signal & Its Weight

### 2a. Backbone / Setup Chain (cap 18)

| Signal key | Weight | Notes |
|---|---|---|
| `conso_2809` | +4 | freq 79% → low weight |
| `tz_bull` | +6 | freq 65% |
| `conso_2809` + `tz_bull` + `bf_buy` | +8 bonus | **Full chain** — most predictive combo |
| `conso_2809` + `tz_bull` (no `bf_buy`) | +3 bonus | Partial chain |

**Key:** `has_conso`, `has_tz_bull`, `has_bf_buy` — referenced by multiple families below.

---

### 2b. Volume / Accumulation (cap 22)

| Signal key | Weight | Notes |
|---|---|---|
| `abs_sig` | +5 | VABS absorption |
| `climb_sig` | +5 | CLB Avg3=2.80% (raised 4→5) |
| `load_sig` | +4 | |
| `vbo_up` | +5 | VBO Avg3=2.37% (lowered 6→5) |
| `ns` | +4 | Night Star Avg3=2.35% (lowered 5→4) |
| `sq` | +5 | SQ Win%=57.5%, Avg3=2.63 (raised 4→5) |
| `sc` | +2 | Selling Climax (buying side) |
| `svs_2809` | +3 | Volume expansion within conso_2809 setup |
| `um_2809` | +3 | NASDAQ: 67% A with tz_bull |
| `sig_l88` | +5 | L88 pattern (checked first) |
| `sig_260308` | +3 | elif — only if no L88 |
| `va` | +3 | ATR Volume Confirm crossover |

---

### 2c. Breakout / Expansion (cap 18)

| Signal key | Weight | Notes |
|---|---|---|
| `bf_buy` | +6 | freq 43%, raised 4→6 |
| `fbo_bull` | +5 | Failed breakout bull |
| `eb_bull` | +3 | Avg3=2.39% (lowered 4→3) |
| `be_up` | +10 | BE full-body engulf — highest single weight |
| `ultra_3up` | +2 | Avg3=1.65% WORST signal (lowered 4→2) |
| `bo_up` OR `bx_up` | +5 | freq 14% → rare, high info |
| `rs_strong` | +5 | |
| `rs` | +3 | elif — only if no rs_strong |

---

### 2d. Combo / Momentum (cap 14)

| Signal key | Weight | Notes |
|---|---|---|
| `rocket` | +12 | top-tier combo (checked first) |
| `buy_2809` | +8 | elif — only if no rocket |
| `sig3g` | +4 | |
| `rtv` | +3 | |
| `hilo_buy` | +4 | NASDAQ: 93% A with conso_2809 (raised from +2) |
| `atr_brk` OR `bb_brk` | +2 | |
| `cd` | +5 | elif chain: cd > ca > cw |
| `ca` | +3 | elif |
| `cw` | +2 | elif |
| `seq_bcont` | +3 | Continuation sequence |

---

### 2e. L-structure / Trend (cap 17)

| Signal key | Weight | Notes |
|---|---|---|
| `tz_sig` | varies | See T/Z weight table below |
| `tz_bull_flip` | +3 (with bf_buy) / +4 | 100%A triple |
| `tz_attempt` | +2 | elif — only if no flip |
| `fri34` | +6 | WLNBB setup |
| `fri43` | +4 | elif |
| `l34` (no fri34) | +5 | |
| `blue` | +3 | Avg3=2.76% (raised 2→3) |
| `cci_ready` | +2 | |
| `l43` (no fri43/fri34) | +4 | Avg3=2.60% (raised 2→4) |
| `fuchsia_rl` | +3 | Avg3=2.80%, Win%=53.3% |
| `tz_weak_bull` | +2 | W — early bearish→bull turn |

**T/Z Signal Weights** (`_TZ_W` dict, `turbo_engine.py:92`):

| tz_sig | Weight |
|---|---|
| T4, T6 | 9 |
| T2G | 8 |
| T1 | 7 |
| T1G | 6 |
| T2 | 5 |
| T9, T10 | 4 |
| T3, T11 | 2 |
| T5 | 1 |
| (anything else) | 0 |

---

### 2f. Delta / Order-Flow (cap 12)

| Signal key | Weight | Notes |
|---|---|---|
| `d_blast_bull` | +5 | ΔΔ↑ Avg3=2.46% (lowered 6→5) |
| `d_surge_bull` | +4 | elif — only if no blast |
| `d_strong_bull` | +2 | B/S↑ Win%=48.9% (major lower 5→2) |
| `d_absorb_bull` | +6 | Ab↑ Avg3=2.99% #3 overall (raised 4→6) |
| `d_spring` | +6 | dSPR Avg3=3.36% #1 overall (checked first) |
| `d_div_bull` | +4 | elif — only if no spring; Avg3=2.54% (raised 3→4) |
| `d_vd_div_bull` | +3 | |
| `d_cd_bull` | +2 | elif — only if no vd_div |

---

### 2g. EMA Cross (cap 10)

Mutually exclusive chain — highest fires, rest ignored:

| Signal key | Weight |
|---|---|
| `preup66` | +8 |
| `preup55` | +6 |
| `preup89` | +5 |
| `preup3` | +5 |
| `preup2` | +4 |

---

### 2h. G Signals (cap 10)

Additive (all can stack):

| Signal key | Weight | Notes |
|---|---|---|
| `g2` | +4 | Avg3=2.64%, Win%=54.9% — best G |
| `g4` | +3 | |
| `g1` | +3 | |
| `g6` | +2 | |
| `g11` | +2 | |

---

## 3. Confluence Bonuses (cap 18)

Source: Run 25 (n=2254, Jan–Apr 2026).

**Aliases used:**
- `_d4` = `d_absorb_bull` OR `d_spring` (institutional absorption)
- `_d6` = `d_surge_bull` OR `d_blast_bull` (delta surge/blast)
- `_l34` = `l34` OR `fri34` (WLNBB setup bar, same bar)
- `_be` = `be_up` (full-body engulf)
- `_l34_r3` = `_l34_recent_3b` OR `_fri34_recent_3b` (L34 fired 1-3 bars ago)
- `_dabs_r5` = `_dabsorb_recent_5b` (D4 fired 1-5 bars ago)

### Tier 1 — Same-bar confluences

| Condition | Bonus | Stats |
|---|---|---|
| `_d6` + `_be` | +12 | +6.26% avg 5d, 71% win, 15.6% FP (n=32) |
| `_d4` + `_l34` | +8 | +2.53% avg 5d, 70.8% win, 4.2% FP (n=24) |
| `_d4` + `_be` | +6 | +2.89% avg 5d, alpha +3.47% (n=52) |

### Tier 2 — Sequence bonuses

| Condition | Bonus | Stats |
|---|---|---|
| `_l34_r3` + `_d4` (no current `_l34`) | +10 | L34→D4 sequence: +7.87% (n=31) |
| `_dabs_r5` + `_be` (no current `_d4`) | +8 | D4→BE_UP sequence: +5.33% (n=54) |
| `_l34_r3` + `_be` (no current `_l34`) | +5 | L34→BE_UP sequence: +1.77% (n=55) |

### Tier 3 — State bonuses

| Condition | Bonus | Notes |
|---|---|---|
| `ns` + `cons_atr` + `_l34` | +4 | Accumulation ready: +1.17% avg, 66.1% win |

---

## 4. Kill / Penalty Conditions (applied before context)

| Condition | Penalty | Reason |
|---|---|---|
| (`g4` OR `g6`) AND no `_l34`/`_be`/`_d4` | -4 | Isolated G trigger: -1.80% avg, 34.7% FPs |
| `d_strong_bull` alone (no structure signals) | -3 | Impulse-only path: -1.66% avg 5d |
| `_d6` + `_l34` + no `_be` | -5 | D6+L34 without BE: -2.52% avg (opposite of D6+BE) |
| RSI > 80, no `_d4`/`_d6` | -6 | Overheated expansion |
| RSI > 75, no `_d4`/`_d6`/`_be` | -3 | Overheated expansion (mild) |
| `bc` (buying climax) + no `_be` | -3 | Distribution risk |

---

## 5. Context / Confirmation — UNCAPPED (max ~18)

Applied after `max(0, s)` — cannot be killed by penalties:

| Signal key | Weight | Notes |
|---|---|---|
| `x2g_wick` | +5 | Wick X2G (strongest) |
| `x2_wick` | +4 | elif |
| `x1g_wick` | +4 | elif |
| `x1_wick` | +3 | elif |
| `x3_wick` | +2 | elif |
| `wick_bull` | +5 | 94% C-anchor in WK↑+4BF+T/Z↑ triple (raised 3→5) |
| `para_retest` | +3 | PARA retest: False%=6.3% lowest |
| `para_plus` OR `para_start` | +2 | elif |
| `fly_abcd` | +4 | FLY strongest (checked first) |
| `fly_cd` OR `fly_bd` OR `fly_ad` | +3 | elif |
| `vol_spike_10x` | +3 | Win%=61.8% (vol×5 too noisy at 49.6%) |

---

## 6. N=1 / N=3 / N=5 / N=10 — How Client Switching Works

**N=1** = default `turbo_score` — signal must fire on the **last (most recent) bar only**.

**N=3 / N=5 / N=10** = `turbo_score_n3`, `turbo_score_n5`, `turbo_score_n10`  
→ signal fires if it appeared on **any of the last N bars**.

All four scores are computed at scan time (`turbo_engine.py:1105`) and stored in DB.  
Frontend switches between them **without a rescan** — just reads the stored column.

**Helper `_sn(df, col, n)`** = `any(df[col].iloc[-n:])` — True if signal in last N rows.

Signals NOT re-checked per-N (use current-bar value always):
- `tz_sig` / `tz_name` (T/Z state — structural, not per-bar)
- `rs`, `rs_strong` (relative strength — slow-changing)
- `cd`, `ca`, `cw` (TZ state transitions)

---

## 7. Badge → Score Key Mapping

Badges shown in the UI map to these DB/score keys:

| UI Badge | DB Column(s) | Score family |
|---|---|---|
| `Z` / `T` (T4, T6 …) | `tz_sig`, `tz_bull` | Backbone + Trend |
| `L` (L34/FRI34) | `l34`, `fri34`, `l43`, `fri43` | Trend (cap 17) |
| `F` (F1–F11) | `f1`–`f11`, `any_f` | — (display only, not scored) |
| `FLY` | `fly_abcd`, `fly_cd`, `fly_bd`, `fly_ad` | Context uncapped |
| `G` (G1/G2/G4/G6/G11) | `g1`, `g2`, `g4`, `g6`, `g11` | G family (cap 10) |
| `B` (B1–B11) | `b1`–`b11` | — (display only, not scored) |
| `Combo` | `rocket`, `buy_2809`, `sig3g`, `rtv`, `hilo_buy` | Combo family (cap 14) |
| `ULT` | `bf_buy`, `fbo_bull`, `eb_bull`, `ultra_3up`, `bo_up`, `bx_up` | Breakout (cap 18) |
| `VOL` | `abs_sig`, `climb_sig`, `load_sig`, `vbo_up`, `ns`, `sq`, `sc` | Vol/accum (cap 22) |
| `VABS` | `abs_sig`, `climb_sig`, `load_sig`, `vbo_up`, `sq`, `svs_2809` | Vol/accum (cap 22) |
| `WICK` | `wick_bull`, `x1_wick`–`x2g_wick`, `x3_wick` | Context uncapped |
| `turbo_score` | `turbo_score` (N=1) / `turbo_score_n3/5/10` | Final score 0-100 |
| Polygon badge | `data_source = "polygon"` | — (display only) |
| yf badge | `data_source = "yfinance"` | — (display only) |

---

## 8. Score Tier Labels

```python
# turbo_engine.py:8
# turbo_score tiers:
#   ≥ 80  — Tier 1 ULTRA
#   ≥ 65  — Tier 2 STRONG
#   ≥ 50  — Tier 3 GOOD
#   ≥ 35  — Tier 4 WEAK
#   <  35 — No signal
```

---

## 9. Quick Formula Summary

```
turbo_score =
  min(backbone_score, 18)    # conso+tz_bull+chain_bonus
+ min(vol_score,      22)    # VABS+Wyckoff+L88+svs
+ min(breakout_score, 18)    # bf_buy+be_up+BO+RS
+ min(combo_score,    14)    # rocket>buy_2809+CD+HILO
+ min(trend_score,    17)    # T/Z_weight+WLNBB+blue+fuchsia
+ min(delta_score,    12)    # d_spring+d_absorb+d_blast
+ min(ema_score,      10)    # preup66>preup55>preup89>...
+ min(g_score,        10)    # g2+g4+g1+g6+g11
+ min(confluence,     18)    # same-bar+sequence bonuses
- penalties                   # kill conditions
→ max(0, s)
+ context_uncapped            # wick+para+fly+vol10x
→ min(100, s)
```
