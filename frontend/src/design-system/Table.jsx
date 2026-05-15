/**
 * MD3 Table primitives — sortable headers, striped rows, responsive wrapper.
 *
 * Usage:
 *   <DataTable>
 *     <Thead>
 *       <tr>
 *         <SortTh col="name" sortBy={sb} sortDir={sd} onSort={handleSort}>Name</SortTh>
 *         <Th>Status</Th>
 *       </tr>
 *     </Thead>
 *     <tbody>
 *       <Tr>
 *         <Td mono>AAPL</Td>
 *         <Td><Badge>OK</Badge></Td>
 *       </Tr>
 *     </tbody>
 *   </DataTable>
 */

/** Responsive scroll wrapper */
export function DataTable({ children, className = '' }) {
  return (
    <div className={`overflow-x-auto rounded-md-md border border-md-outline-var ${className}`}>
      <table className="min-w-full text-sm border-collapse">
        {children}
      </table>
    </div>
  )
}

export function Thead({ children }) {
  return (
    <thead className="bg-md-surface-high text-md-on-surface-var">
      {children}
    </thead>
  )
}

/** Plain header cell */
export function Th({ children, className = '', ...props }) {
  return (
    <th
      className={`px-3 py-2.5 text-left text-xs font-medium whitespace-nowrap select-none ${className}`}
      {...props}
    >
      {children}
    </th>
  )
}

/** Sortable header cell */
export function SortTh({ col, sortBy, sortDir, onSort, children, className = '' }) {
  const active = sortBy === col
  return (
    <th
      onClick={() => onSort(col)}
      className={[
        'px-3 py-2.5 text-left text-xs font-medium whitespace-nowrap',
        'cursor-pointer select-none hover:bg-white/5 transition-colors',
        active ? 'text-md-primary' : 'text-md-on-surface-var',
        className,
      ].join(' ')}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {active
          ? <span className="text-md-primary">{sortDir === 'desc' ? '▼' : '▲'}</span>
          : <span className="text-md-outline opacity-0 group-hover:opacity-100">▼</span>
        }
      </span>
    </th>
  )
}

/** Table body row */
export function Tr({ children, onClick, className = '' }) {
  return (
    <tr
      onClick={onClick}
      className={[
        'border-t border-md-outline-var',
        'hover:bg-white/[0.03] transition-colors',
        onClick ? 'cursor-pointer' : '',
        className,
      ].join(' ')}
    >
      {children}
    </tr>
  )
}

/** Table data cell */
export function Td({ children, mono = false, right = false, positive, negative, className = '' }) {
  const colorCls = positive
    ? 'text-md-positive'
    : negative
    ? 'text-md-negative'
    : 'text-md-on-surface'

  return (
    <td className={[
      'px-3 py-2 text-sm',
      mono    ? 'font-mono' : '',
      right   ? 'text-right tabular-nums' : '',
      colorCls,
      className,
    ].join(' ')}>
      {children}
    </td>
  )
}

/** Colgroup for consistent widths */
export function EmptyRow({ cols, message = 'No data' }) {
  return (
    <tr>
      <td colSpan={cols} className="px-3 py-8 text-center text-md-on-surface-var text-sm">
        {message}
      </td>
    </tr>
  )
}
