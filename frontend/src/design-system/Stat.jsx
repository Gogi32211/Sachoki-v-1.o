/**
 * MD3 Stat card — numeric metric display.
 * Usage:
 *   <Stat label="Events Found" value={1243} />
 *   <Stat label="Median Return" value="+4.2%" positive />
 *   <Stat label="Fail Rate" value="18%" negative />
 *   <Stat label="Days" value="3/20" sub="completed" />
 */
export function Stat({ label, value, sub, positive, negative, className = '' }) {
  const valueColor = positive
    ? 'text-md-positive'
    : negative
    ? 'text-md-negative'
    : 'text-md-on-surface'

  return (
    <div className={`bg-md-surface-con rounded-md-md px-3 py-2.5 ${className}`}>
      <p className="text-xs text-md-on-surface-var leading-4 mb-1">{label}</p>
      <p className={`text-base font-medium leading-6 tabular-nums ${valueColor}`}>
        {value ?? '—'}
      </p>
      {sub && <p className="text-xs text-md-on-surface-var leading-4 mt-0.5">{sub}</p>}
    </div>
  )
}

/** Compact inline stat (e.g. in a table row or tooltip) */
export function InlineStat({ label, value, className = '' }) {
  return (
    <span className={`inline-flex flex-col ${className}`}>
      <span className="text-xs text-md-on-surface-var leading-4">{label}</span>
      <span className="text-xs font-medium text-md-on-surface leading-4">{value ?? '—'}</span>
    </span>
  )
}
