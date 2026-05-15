import { getSignalBadgeClass, getSignalLabel, normalizeSignal } from '../utils/signalBadges'

/**
 * SignalChip — unified signal badge.
 * Uses Superchart's color palette via getSignalBadgeClass.
 */
export default function SignalChip({ signal, size = 'sm', compact = true, title, className = '' }) {
  const s = normalizeSignal(signal)
  if (!s) return null

  const sizeCls = size === 'md'
    ? 'text-xs px-1.5 py-0.5'
    : 'text-[10px] px-1 py-px'

  const colorCls = getSignalBadgeClass(s)
  const label = getSignalLabel(s)

  return (
    <span
      title={title || label}
      className={`inline-block rounded border border-white/10 font-mono leading-none ${sizeCls} ${colorCls} ${className}`}
    >
      {label}
    </span>
  )
}
