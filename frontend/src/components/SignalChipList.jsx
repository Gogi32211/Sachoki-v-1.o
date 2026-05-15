import { useState } from 'react'
import SignalChip from './SignalChip'
import { normalizeSignal } from '../utils/signalBadges'

/**
 * SignalChipList — renders a list of independent signal chips.
 * - mode='table'   : show all chips, wrap to next line if needed (default)
 * - mode='card'    : show maxVisible, expand on click
 * - mode='compact' : show maxVisible, clickable "+N more"
 *
 * `signals` can be an array of strings, objects with .label, or a single
 * comma/space-separated string. Falsy / empty entries are filtered out.
 */
export default function SignalChipList({
  signals,
  mode = 'table',
  maxVisible,
  size = 'sm',
  className = '',
  wrap = true,
}) {
  const items = normalizeList(signals)
  if (items.length === 0) {
    return <span className="text-md-on-surface-var/50">—</span>
  }

  const defaultMax = mode === 'card' ? 5 : mode === 'compact' ? 3 : Infinity
  const limit = maxVisible ?? defaultMax

  const [expanded, setExpanded] = useState(false)
  const showAll = mode === 'table' || expanded
  const shown = showAll ? items : items.slice(0, limit)
  const extra = items.length - shown.length

  const wrapCls = wrap ? 'flex-wrap' : ''

  return (
    <div className={`inline-flex items-center gap-0.5 ${wrapCls} ${className}`}>
      {shown.map((s, i) => (
        <SignalChip key={`${s}-${i}`} signal={s} size={size} />
      ))}
      {extra > 0 && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setExpanded(true) }}
          className="text-[10px] px-1 py-px rounded border border-white/10 bg-white/[0.04] text-md-on-surface-var hover:bg-white/[0.08] hover:text-md-on-surface transition-colors"
          title={`Show ${extra} more signal${extra > 1 ? 's' : ''}`}
        >
          +{extra}
        </button>
      )}
    </div>
  )
}

function normalizeList(signals) {
  if (!signals) return []
  if (typeof signals === 'string') {
    return signals
      .split(/[,\s|]+/)
      .map((s) => normalizeSignal(s))
      .filter(Boolean)
  }
  if (!Array.isArray(signals)) return []
  return signals
    .map((s) => {
      if (!s) return null
      if (typeof s === 'string') return normalizeSignal(s)
      if (typeof s === 'object') return normalizeSignal(s.label || s.signal || s.name || '')
      return null
    })
    .filter(Boolean)
}
