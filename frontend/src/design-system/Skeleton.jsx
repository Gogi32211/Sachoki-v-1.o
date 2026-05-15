/**
 * MD3 Skeleton — shimmer loading placeholders.
 * Usage:
 *   <Skeleton width="w-32" height="h-4" />
 *   <Skeleton.Text lines={3} />
 *   <Skeleton.Card />
 *   <Skeleton.TableRow cols={5} />
 */

function SkeletonBase({ width = 'w-full', height = 'h-4', className = '', rounded = 'rounded-md-sm' }) {
  return (
    <div
      className={[
        'animate-pulse bg-md-surface-high',
        width,
        height,
        rounded,
        className,
      ].join(' ')}
    />
  )
}

function SkeletonText({ lines = 3, className = '' }) {
  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      {Array.from({ length: lines }, (_, i) => (
        <SkeletonBase
          key={i}
          width={i === lines - 1 && lines > 1 ? 'w-3/4' : 'w-full'}
          height="h-3.5"
        />
      ))}
    </div>
  )
}

function SkeletonCard({ className = '' }) {
  return (
    <div className={`rounded-md-md bg-md-surface-con p-4 space-y-3 ${className}`}>
      <div className="flex items-center gap-3">
        <SkeletonBase width="w-8" height="h-8" rounded="rounded-full" />
        <div className="flex-1 space-y-1.5">
          <SkeletonBase width="w-1/3" height="h-3.5" />
          <SkeletonBase width="w-1/2" height="h-3" />
        </div>
      </div>
      <SkeletonText lines={2} />
    </div>
  )
}

function SkeletonTableRow({ cols = 4, className = '' }) {
  return (
    <tr className={className}>
      {Array.from({ length: cols }, (_, i) => (
        <td key={i} className="px-3 py-2.5">
          <SkeletonBase width={i === 0 ? 'w-20' : 'w-12'} height="h-3.5" />
        </td>
      ))}
    </tr>
  )
}

export const Skeleton = Object.assign(SkeletonBase, {
  Text: SkeletonText,
  Card: SkeletonCard,
  TableRow: SkeletonTableRow,
})
