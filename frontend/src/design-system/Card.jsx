/**
 * MD3 Card — 3 variants
 * Usage:
 *   <Card>content</Card>
 *   <Card variant="filled" padding="sm">compact</Card>
 *   <Card variant="outlined" className="col-span-2">wide</Card>
 */
const VARIANTS = {
  elevated: 'bg-md-surface-con shadow-md-1',
  filled:   'bg-md-surface-high',
  outlined: 'bg-md-surface-con border border-md-outline-var',
}

const PADDINGS = {
  none: '',
  sm:   'p-3',
  md:   'p-4',
  lg:   'p-6',
}

export function Card({
  children,
  variant = 'elevated',
  padding = 'md',
  className = '',
  ...props
}) {
  return (
    <div
      className={[
        'rounded-md-md',
        VARIANTS[variant] ?? VARIANTS.elevated,
        PADDINGS[padding] ?? PADDINGS.md,
        className,
      ].join(' ')}
      {...props}
    >
      {children}
    </div>
  )
}

export function CardHeader({ title, subtitle, action, className = '' }) {
  return (
    <div className={`flex items-start justify-between gap-3 mb-3 ${className}`}>
      <div>
        <h3 className="text-md-on-surface font-medium text-base leading-6">{title}</h3>
        {subtitle && <p className="text-md-on-surface-var text-xs mt-0.5">{subtitle}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  )
}
