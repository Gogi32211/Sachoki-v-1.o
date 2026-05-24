# Sachoki Design System

A single-source-of-truth visual layer for the dashboard. Verbatim color parity
with old Ultra (`frontend/src/utils/signalBadges.js`), but expressed as design
tokens + a badge resolver + thin CSS classes — so changes happen in **one place**.

---

## Files

| File | Purpose | Edit when |
|---|---|---|
| `apps/dashboard/frontend/design-tokens.css` | All colors, spacing, typography, radii, motion as CSS variables. | Rebranding; tweaking any color; changing typography scale. |
| `apps/dashboard/frontend/signal-badges.js` | `getSignalBadgeClass(label)` — label → CSS class. Verbatim port of old `signalBadges.js`. | Adding a new signal label, renaming a family, changing routing for a label. |
| `apps/dashboard/frontend/signal-badges.css` | Visual rules for `.sig-*` classes. Consumes tokens; **no hex codes here**. | Adding a new sub-variant CSS class (always alongside a new token). |
| `apps/dashboard/frontend/styles.css` | Layout, tables, filter rows, RTB pills, cat chips. Consumes tokens. | Changing component layout or composition. |
| `apps/dashboard/frontend/index.html` | Loads tokens → badges → app.js in that order. | Never under normal work. |

Load order (defined in `index.html`):

```
design-tokens.css      ← variables only
signal-badges.css      ← reads variables
styles.css             ← reads variables
signal-badges.js       ← global window.SignalBadges
app.js                 ← uses SignalBadges.renderSignalBadge(label)
```

---

## Architecture rule (single source of truth)

```
designer / brand change
        │
        ▼
design-tokens.css      ← one file, one variable per color decision
        │
        ▼
signal-badges.css      ← consumes variables (no hex)
styles.css             ← consumes variables (no hex)
        │
        ▼
signal-badges.js       ← returns class names (no inline colors)
        │
        ▼
app.js renders SignalBadges.renderSignalBadge(label)
```

**Hard rules:**
- Component CSS / app.js MUST NOT contain hex codes for signal colors. They live in `design-tokens.css` only.
- A label's color is decided **once**, in `signal-badges.js`. Both Super Chart history and Ultra latest table call the same resolver.
- Adding a new color = **one new token** in `design-tokens.css` + **one new class** in `signal-badges.css` + **one new branch** in `signal-badges.js`. Nothing else touched.

---

## Token taxonomy

`design-tokens.css` is organized into 5 layers, top-down:

1. **Raw palette** — Tailwind-derived hex (e.g. `--c-green-900: #14532d`). Internal to this file. Do not reference outside.
2. **Semantic roles** — `--surface-base`, `--text-primary`, `--text-muted`, `--border-primary`, `--c-pos`, `--c-neg`, `--c-accent`. This is what `styles.css` reads for layout.
3. **Signal-family tokens** — `--sig-t-bg`, `--sig-t-fg`, `--sig-z-bg`, `--sig-i-rocket-bg`, `--sig-gog-p-ring`, etc. One per (family × sub-variant × role). 80+ tokens total.
4. **Typography** — `--font-sans`, `--font-mono`, `--fz-*`, `--fw-*`, `--lh-*`.
5. **Spacing / radii / motion** — `--sp-*` (4px grid), `--radius-*`, `--ease-*`, `--dur-*`.

---

## Signal family color decisions (verbatim from old Ultra)

| Family | Decision logic | Examples |
|---|---|---|
| **T** | single class `sig-t` (green-900 / green-300) | T2, T4, T6 |
| **Z** | `sig-z` (red-900 / red-300); PREUP (P2/P3/P50/P55/P66/P89) → `sig-preup` (gray-700 / white) | Z10, Z1G, P89 |
| **L** | conditional 12 sub-variants: FRI→cyan, BL→sky, CCI*→violet, RL/RH→fuchsia, PP→yellow, L555/L22→rose, L2L4→sky-400, L88→**bold violet**, BE*→emerald, `↑`-arrow→lime, `↓`-arrow→red, default→blue | L34, FRI64, L88, BE↑ |
| **F** | `sig-f` (orange-900 / orange-300) | F1–F11, 4BF, FBO |
| **FLY** | `sig-fly` (purple-900 / purple-200) | ABCD, CD, BD, AD |
| **G** | `sig-g` (violet-900 / violet-200) | G1, G2, G4, G6, G11 |
| **B** | `sig-b` (amber-900 / amber-300) | B1–B11 |
| **I (Combo)** | conditional 8 sub-variants: ROCKET/BUY→**bold green**, BEST★/BEST↑/4BF→**bold yellow**, STRONG→emerald, MATCH→teal, V×N→**bold pink**, `↑`/3G/NS/ABS/CLM/LOAD→lime, `↓`/CONS/↓BIAS→red, default→teal | ROCKET, V×10, 3G |
| **WICK** | `↑` → sig-wick-up (sky); else → sig-wick-down (red-900/50) | WC↑, WP↓ |
| **SETUP** | ringed bold pills: A→orange, SM→lime, N→cyan, MX→pink, ABCD→amber | A, SM, N, MX, ABCD |
| **GOG** | ringed bold tier pills: G*P→green, G*L→emerald, G*C→teal, base GOG/GOG1/2/3→fuchsia | G1P, G2L, G3C, GOG1 |
| **CTX** | LDP/LRP→green semibold, LDC/LRC→teal, LDS/LD→cyan, SQB/BCT→blue, WRC/F8C→slate | LDP, LRC, F8C |
| **WLNBB** | volume-bucket pills: W→slate, L→sky, N→yellow, B→orange, VB→rose | W |
| **ACTION** | bold ringed: BUY_READY→emerald, WATCH_CLOSELY→yellow, WAIT_CONFIRMATION→blue, TOO_LATE→amber, AVOID→rose | (decision tokens) |
| **ABR** | A→emerald/50, B+→cyan/50, B→blue/50, C→surface-high, R→red/50 | ABR A, ABR B+ |
| **EMA** | `sig-ema` (emerald/50) | EMA OK, EMA50 RECLAIM |
| **UNKNOWN** | `sig-neutral` (white/5 over border white/10) | anything unmatched |

---

## How to add a new signal family

**Example: hypothetical new family `WAVE` with two sub-variants `WAVE↑` and `WAVE↓`.**

### 1. Add tokens (`design-tokens.css`)

```css
--sig-wave-up-bg:   var(--c-cyan-900);
--sig-wave-up-fg:   var(--c-cyan-300);
--sig-wave-down-bg: var(--c-rose-900);
--sig-wave-down-fg: var(--c-rose-300);
```

### 2. Add classes (`signal-badges.css`)

```css
.sig-wave-up   { background: var(--sig-wave-up-bg);   color: var(--sig-wave-up-fg); }
.sig-wave-down { background: var(--sig-wave-down-bg); color: var(--sig-wave-down-fg); }
```

### 3. Add branch (`signal-badges.js` → `getSignalFamily` + `getSignalBadgeClass`)

```js
// in getSignalFamily:
if (u === 'WAVE↑' || u === 'WAVE↓') return 'WAVE';

// in getSignalBadgeClass switch:
case 'WAVE':
  return s.includes('↑') ? 'sig sig-wave-up' : 'sig sig-wave-down';
```

### 4. That's it.
The label `"WAVE↑"` now renders with the correct color in **every** consumer — Super Chart history, Ultra table Signals column, debug panels, anything new added later. No edits to `app.js`, `styles.css`, the BFF, or the scanner-api.

---

## How to rebrand (e.g. switch from dark to light theme)

Override the semantic role tokens in a `:root[data-theme="light"]` block in `design-tokens.css`:

```css
:root[data-theme="light"] {
  --surface-base:     #ffffff;
  --surface-raised:   #f5f5f5;
  --text-primary:     #1f1f1f;
  --border-primary:   #e5e7eb;
  /* signal-family backgrounds typically need higher contrast in light mode;
     override only the ones that need it: */
  --sig-z-bg: var(--c-red-200);
  --sig-z-fg: var(--c-red-900);
  /* … */
}
```

Then toggle `<html data-theme="light">`. No other file changes.

---

## Consumers (today)

- **Super Chart History timeline** (`app.js _buildHistoryTimeline`) — every signal-row badge calls `SignalBadges.renderSignalBadge(label)`. Row-level CSS color hardcoding has been removed.
- **Ultra latest table** (`app.js renderCandidateTable`) — the "Signals" column flattens `c.signals.{setup,vabs,i,ult,l,gog,ctx,f,fly,g,b,wick}` and calls the same resolver. T/Z column shows the first T- or Z-badge through the same resolver.
- **CSV export** — exports raw labels, not classes. Consumers reading the CSV can re-resolve colors offline if needed.

Anything future (mobile, watchlist cards, alert toasts, scan-progress per-ticker rows) should import the same resolver and inherit colors automatically.

---

## What this design system does *not* solve

It is purely visual. The deferred old-Ultra parity items live elsewhere:

- **Engine ports** (`PHASE_8G_FINAL_REPORT.md` §7) — turbo, rtb-v4, profile_playbook, sector_engine, tz_intelligence. Affects which labels appear, not how they look.
- **Real-ticker verification** (`PHASE_8G_VERIFICATION_GATE.md`) — needs Massive credentials + reference old Ultra output; orthogonal to UI.
- **Engine manifest / declarative engine registration** — the architectural extension that makes adding a new *engine* a one-file change (analogous to how this design system makes adding a new *badge color* a one-file change). Tracked as Phase 8H-engines.
