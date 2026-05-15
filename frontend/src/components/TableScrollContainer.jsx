/**
 * TableScrollContainer — wraps wide tables so they scroll horizontally
 * inside their panel rather than overflowing the page.
 *
 * Vertical sticky thead continues to work because we only manage horizontal
 * scroll here.
 */
export default function TableScrollContainer({
  children,
  className = '',
  minWidth = 'max-content',
}) {
  return (
    <div className={`w-full overflow-x-auto overscroll-x-contain ${className}`}>
      <div style={{ minWidth }}>
        {children}
      </div>
    </div>
  )
}
