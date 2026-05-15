/**
 * MD3 Progress — linear + circular variants.
 * Usage:
 *   <LinearProgress value={65} />              — determinate
 *   <LinearProgress indeterminate />            — indeterminate
 *   <LinearProgress value={30} color="positive" label="30%" />
 */

const COLOR_MAP = {
  primary:  'bg-md-primary',
  positive: 'bg-md-positive',
  negative: 'bg-md-negative',
  warning:  'bg-md-warning',
  error:    'bg-md-error',
}

export function LinearProgress({
  value,
  indeterminate = false,
  color = 'primary',
  label,
  showLabel = false,
  className = '',
}) {
  const pct = Math.max(0, Math.min(100, value ?? 0))
  const trackCls = COLOR_MAP[color] ?? COLOR_MAP.primary

  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      {(showLabel || label) && (
        <div className="flex justify-between text-xs text-md-on-surface-var">
          <span>{label}</span>
          {showLabel && !label && <span>{pct}%</span>}
        </div>
      )}
      <div className="w-full h-1 bg-md-surface-high rounded-md-full overflow-hidden">
        {indeterminate ? (
          <div className={`h-full w-1/3 ${trackCls} rounded-md-full animate-bounce`}
               style={{ animationName: 'md-indeterminate' }} />
        ) : (
          <div
            className={`h-full ${trackCls} rounded-md-full transition-all duration-300`}
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
    </div>
  )
}

/** Inline spinner */
export function Spinner({ size = 16, className = '' }) {
  return (
    <span
      className={`inline-block rounded-full border-2 border-md-primary border-t-transparent animate-spin ${className}`}
      style={{ width: size, height: size }}
    />
  )
}
