/**
 * MD3 Button — 5 variants
 * Usage:
 *   <Button>Save</Button>
 *   <Button variant="tonal" size="sm">Cancel</Button>
 *   <Button variant="outlined" loading>Scanning…</Button>
 *   <Button variant="text" icon="⬇">Export</Button>
 */
const VARIANTS = {
  filled:   'bg-md-primary text-md-on-primary hover:shadow-md-1 active:shadow-none disabled:bg-md-outline-var disabled:text-md-on-surface-var disabled:shadow-none',
  tonal:    'bg-md-primary-container text-md-on-primary-container hover:shadow-md-1 active:shadow-none disabled:bg-md-outline-var disabled:text-md-on-surface-var',
  elevated: 'bg-md-surface-con shadow-md-1 text-md-primary hover:shadow-md-2 active:shadow-md-1 disabled:shadow-none disabled:bg-md-outline-var disabled:text-md-on-surface-var',
  outlined: 'border border-md-outline text-md-primary hover:bg-md-primary/10 active:bg-md-primary/12 disabled:border-md-outline-var disabled:text-md-on-surface-var',
  text:     'text-md-primary hover:bg-md-primary/10 active:bg-md-primary/12 disabled:text-md-on-surface-var',
  danger:   'bg-md-error-container text-md-error hover:shadow-md-1 disabled:opacity-50',
}

const SIZES = {
  sm: 'h-8  px-4  text-xs  gap-1.5 rounded-md-full',
  md: 'h-10 px-6  text-sm  gap-2   rounded-md-full',
  lg: 'h-12 px-8  text-base gap-2  rounded-md-full',
}

export function Button({
  children,
  variant = 'filled',
  size = 'md',
  icon,
  loading = false,
  disabled = false,
  className = '',
  ...props
}) {
  const isDisabled = disabled || loading
  return (
    <button
      disabled={isDisabled}
      className={[
        'inline-flex items-center justify-center font-medium transition-all duration-100',
        'select-none cursor-pointer disabled:cursor-not-allowed',
        VARIANTS[variant] ?? VARIANTS.filled,
        SIZES[size] ?? SIZES.md,
        className,
      ].join(' ')}
      {...props}
    >
      {loading ? (
        <span className="inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
      ) : icon ? (
        <span className="text-base leading-none">{icon}</span>
      ) : null}
      {children}
    </button>
  )
}
