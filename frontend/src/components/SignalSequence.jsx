import { useState } from 'react'
import SignalChip from './SignalChip'
import { parseSignalSequence } from '../utils/signalBadges'

/**
 * SignalSequence — renders an ordered sequence of signal chips.
 * Accepts string ("Z5 -> T3", "Z5→T3"), array, or null.
 *
 * - mode='table' (default): show all chips, wrap if needed
 * - mode='card' | 'compact': show `max` chips with clickable "+N" overflow
 */
export default function SignalSequence({
  value,
  max,
  mode = 'table',
  separator = '→',
  size = 'sm',
  className = '',
}) {
  const items = parseSignalSequence(value)
  if (items.length === 0) return null

  const defaultMax = mode === 'card' ? 5 : mode === 'compact' ? 3 : Infinity
  const limit = max ?? defaultMax

  const [expanded, setExpanded] = useState(false)
  const showAll = mode === 'table' || expanded
  const shown = showAll ? items : items.slice(0, limit)
  const extra = items.length - shown.length

  return (
    <span className={`inline-flex items-center flex-wrap gap-0.5 ${className}`}>
      {shown.map((s, i) => (
        <span key={`${s}-${i}`} className="inline-flex items-center gap-0.5">
          {i > 0 && (
            <span className="text-[9px] text-md-on-surface-var/70">{separator}</span>
          )}
          <SignalChip signal={s} size={size} />
        </span>
      ))}
      {extra > 0 && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setExpanded(true) }}
          className="text-[10px] px-1 py-px rounded border border-white/10 bg-white/[0.04] text-md-on-surface-var hover:bg-white/[0.08] hover:text-md-on-surface transition-colors ml-0.5"
          title={`Show ${extra} more`}
        >
          +{extra}
        </button>
      )}
    </span>
  )
}
