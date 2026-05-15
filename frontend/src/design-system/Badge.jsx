/**
 * MD3-styled Badge / Chip for verdicts, signals, labels.
 * Usage:
 *   <Badge>Default</Badge>
 *   <Badge variant="positive">+12.3%</Badge>
 *   <Badge variant="negative">FAILED</Badge>
 *   <VerdictBadge verdict="STRONG_EDGE" />
 *   <ConfidenceBadge label="HIGH" />
 */

const COLOR_MAP = {
  default:  'bg-md-surface-high  text-md-on-surface',
  primary:  'bg-md-primary-container text-md-on-primary-container',
  positive: 'bg-md-positive-con   text-md-positive',
  negative: 'bg-md-negative-con   text-md-negative',
  warning:  'bg-md-warning-con    text-md-warning',
  error:    'bg-md-error-container text-md-error',
  muted:    'bg-md-outline-var    text-md-on-surface-var',
}

const SIZES = {
  sm: 'text-xs px-2    py-0.5 rounded-md-sm',
  md: 'text-xs px-2.5  py-1   rounded-md-sm',
}

export function Badge({ children, variant = 'default', size = 'sm', className = '' }) {
  return (
    <span className={[
      'inline-flex items-center font-medium leading-none whitespace-nowrap',
      COLOR_MAP[variant] ?? COLOR_MAP.default,
      SIZES[size] ?? SIZES.sm,
      className,
    ].join(' ')}>
      {children}
    </span>
  )
}

// ── Verdict Badge (semantic mapping from signal_statistics verdict) ───────────

const VERDICT_MAP = {
  STRONG_EDGE:       'positive',
  GOOD_WITH_CONTEXT: 'primary',
  WATCH_ONLY:        'warning',
  NO_EDGE:           'muted',
  NEGATIVE_EDGE:     'negative',
  TOO_FEW_SAMPLES:   'muted',
}

export function VerdictBadge({ verdict }) {
  return (
    <Badge variant={VERDICT_MAP[verdict] ?? 'default'}>
      {verdict ?? '—'}
    </Badge>
  )
}

// ── Confidence Badge ──────────────────────────────────────────────────────────

const CONFIDENCE_MAP = {
  HIGH:            'primary',
  MEDIUM:          'default',
  LOW:             'muted',
  TOO_FEW_SAMPLES: 'muted',
}

export function ConfidenceBadge({ label }) {
  return (
    <Badge variant={CONFIDENCE_MAP[label] ?? 'muted'}>
      {label ?? '—'}
    </Badge>
  )
}

// ── Signal family color chip ──────────────────────────────────────────────────
const FAMILY_COLORS = {
  T: 'bg-blue-900/60 text-blue-300',
  Z: 'bg-violet-900/60 text-violet-300',
  L: 'bg-teal-900/60 text-teal-300',
  F: 'bg-orange-900/60 text-orange-300',
  G: 'bg-green-900/60 text-green-300',
  B: 'bg-pink-900/60 text-pink-300',
  COMBO: 'bg-yellow-900/60 text-yellow-300',
  ROLE:  'bg-gray-800 text-gray-300',
}

export function FamilyBadge({ family }) {
  const cls = FAMILY_COLORS[family] ?? 'bg-md-surface-high text-md-on-surface-var'
  return (
    <span className={`inline-flex items-center text-xs px-2 py-0.5 rounded-md-sm font-medium ${cls}`}>
      {family}
    </span>
  )
}
