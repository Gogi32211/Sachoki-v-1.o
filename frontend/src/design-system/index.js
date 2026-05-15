/**
 * Design System — Sachoki MD3
 *
 * All components follow Material Design 3 dark-theme spec.
 * Minimum font size: 14px enforced globally.
 *
 * Quick reference:
 *   import { Button, Card, Badge, Stat, ... } from '../design-system'
 *
 * Color tokens (use as Tailwind classes):
 *   bg-md-surface-con      → default card background
 *   bg-md-surface-high     → elevated card
 *   text-md-on-surface     → primary text
 *   text-md-on-surface-var → secondary/muted text
 *   text-md-primary        → accent/interactive
 *   text-md-positive       → gains / green
 *   text-md-negative       → losses / red
 *   text-md-warning        → caution / amber
 *   border-md-outline-var  → subtle border
 *   border-md-outline      → visible border
 *   shadow-md-1 … shadow-md-4 → elevation shadows
 */

export { Button }                             from './Button'
export { Card, CardHeader }                   from './Card'
export { Badge, VerdictBadge, ConfidenceBadge, FamilyBadge } from './Badge'
export { Stat, InlineStat }                   from './Stat'
export { TextField, NumberField, SelectField, ToggleGroup } from './Input'
export { DataTable, Thead, Th, SortTh, Tr, Td, EmptyRow } from './Table'
export { LinearProgress, Spinner }            from './Progress'
export { Alert }                              from './Alert'
export { FilterChip, AssistChip, ChipGroup }  from './Chip'
export { Display, Headline, Title, Body, Label, Caption } from './Typography'
export { Divider }                            from './Divider'
export { EmptyState }                         from './EmptyState'
export { Skeleton }                           from './Skeleton'
export { PageHeader }                         from './PageHeader'
export { StatusChip }                         from './StatusChip'

// ── Utility formatters (shared across panels) ─────────────────────────────────
export function fmtPct(v, digits = 2) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`
}

export function fmtNum(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString() : '—'
}

export function fmtDate(s) {
  if (!s) return '—'
  return String(s).slice(0, 10)
}
