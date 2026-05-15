/**
 * MD3 Typography components.
 * All sizes are ≥ 14px per design spec.
 *
 * Usage:
 *   <Display>Sachoki</Display>
 *   <Headline>Signal Replay</Headline>
 *   <Title>Run Configuration</Title>
 *   <Body>Description text</Body>
 *   <Label>ema50_state</Label>
 *   <Caption positive>+4.2%</Caption>
 */

export function Display({ children, className = '' }) {
  return <h1 className={`text-md-display font-normal text-md-on-surface ${className}`}>{children}</h1>
}

export function Headline({ children, as: Tag = 'h2', className = '' }) {
  return <Tag className={`text-md-headline font-normal text-md-on-surface ${className}`}>{children}</Tag>
}

export function Title({ children, as: Tag = 'h3', className = '' }) {
  return <Tag className={`text-md-title font-medium text-md-on-surface ${className}`}>{children}</Tag>
}

export function Body({ children, variant = 'md', muted = false, className = '' }) {
  const size = variant === 'lg' ? 'text-base' : 'text-sm'
  const color = muted ? 'text-md-on-surface-var' : 'text-md-on-surface'
  return <p className={`${size} ${color} leading-6 ${className}`}>{children}</p>
}

export function Label({ children, muted = false, className = '' }) {
  const color = muted ? 'text-md-on-surface-var' : 'text-md-on-surface'
  return <span className={`text-xs font-medium leading-4 ${color} ${className}`}>{children}</span>
}

/** Numeric value with optional color coding */
export function Caption({ children, positive, negative, mono = true, className = '' }) {
  const color = positive
    ? 'text-md-positive'
    : negative
    ? 'text-md-negative'
    : 'text-md-on-surface-var'
  return (
    <span className={`text-xs leading-4 ${mono ? 'font-mono tabular-nums' : ''} ${color} ${className}`}>
      {children}
    </span>
  )
}
