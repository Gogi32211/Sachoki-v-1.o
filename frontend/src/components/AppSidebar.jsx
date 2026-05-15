/**
 * AppSidebar — collapsible left navigation.
 *
 * Supports:
 *   - Expanded mode (≈240px, icon + label, group headers)
 *   - Collapsed mode (≈64px, icon only, tooltip on hover)
 *   - Mobile drawer (overlay, closes on tab select)
 *   - localStorage persistence of collapsed state
 *
 * Uses the existing MD3 design tokens — no new color palette.
 */
export default function AppSidebar({
  groups,
  activeTab,
  onSelectTab,
  collapsed,
  onToggleCollapsed,
  mobileOpen,
  onCloseMobile,
}) {
  const handleSelect = (id) => {
    onSelectTab(id)
    if (mobileOpen) onCloseMobile()
  }

  const widthCls = collapsed ? 'w-[64px]' : 'w-[232px]'

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-black/60"
          onClick={onCloseMobile}
          aria-hidden="true"
        />
      )}

      <aside
        className={[
          // Desktop: sticky sidebar that shares flex row with content
          'hidden md:flex',
          widthCls,
          'shrink-0 flex-col h-screen sticky top-0',
          'bg-md-surface-con border-r border-md-outline-var',
          'transition-[width] duration-150',
        ].join(' ')}
      >
        <SidebarBody
          groups={groups}
          activeTab={activeTab}
          collapsed={collapsed}
          onSelectTab={handleSelect}
          onToggleCollapsed={onToggleCollapsed}
        />
      </aside>

      {/* Mobile drawer */}
      <aside
        className={[
          'md:hidden fixed top-0 left-0 z-50 h-screen w-[260px]',
          'bg-md-surface-con border-r border-md-outline-var',
          'transition-transform duration-200 ease-out',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
        ].join(' ')}
      >
        <SidebarBody
          groups={groups}
          activeTab={activeTab}
          collapsed={false}
          onSelectTab={handleSelect}
          onToggleCollapsed={onCloseMobile}
          mobile
        />
      </aside>
    </>
  )
}

function SidebarBody({ groups, activeTab, collapsed, onSelectTab, onToggleCollapsed, mobile }) {
  return (
    <div className="flex flex-col h-full">
      {/* Header: brand + collapse toggle */}
      <div
        className={[
          'flex items-center h-14 shrink-0 border-b border-md-outline-var',
          collapsed ? 'justify-center px-1' : 'justify-between px-3',
        ].join(' ')}
      >
        {!collapsed && (
          <span className="text-base font-semibold tracking-tight text-md-primary">
            Sachoki
          </span>
        )}
        <button
          onClick={onToggleCollapsed}
          title={mobile ? 'Close' : collapsed ? 'Expand sidebar (⌘B)' : 'Collapse sidebar (⌘B)'}
          aria-label="Toggle sidebar"
          className="p-1.5 rounded-md-sm text-md-on-surface-var hover:bg-white/5 hover:text-md-on-surface transition-colors"
        >
          <CollapseIcon mobile={mobile} collapsed={collapsed} />
        </button>
      </div>

      {/* Nav list */}
      <nav className="flex-1 overflow-y-auto overflow-x-hidden py-2">
        {groups.map((group, gi) => (
          <div key={group.label} className={gi > 0 ? 'mt-2' : ''}>
            {!collapsed && (
              <div className="px-3 pt-1.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-md-on-surface-var/60 select-none">
                {group.label}
              </div>
            )}
            {collapsed && gi > 0 && (
              <div className="mx-2 my-1 border-t border-md-outline-var/40" />
            )}
            {group.tabs.map((tab) => (
              <SidebarItem
                key={tab.id}
                tab={tab}
                active={activeTab === tab.id}
                collapsed={collapsed}
                onClick={() => onSelectTab(tab.id)}
              />
            ))}
          </div>
        ))}
      </nav>
    </div>
  )
}

function SidebarItem({ tab, active, collapsed, onClick }) {
  const { icon, text } = parseTabLabel(tab.label)

  if (collapsed) {
    return (
      <button
        onClick={onClick}
        title={text}
        className={[
          'group relative w-full flex items-center justify-center py-2.5 my-0.5',
          'transition-colors duration-100 select-none',
          active
            ? 'bg-md-primary-container/40 text-md-primary'
            : 'text-md-on-surface-var hover:bg-white/5 hover:text-md-on-surface',
        ].join(' ')}
      >
        {/* Active indicator bar */}
        <span
          className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-md-primary"
          style={{ opacity: active ? 1 : 0 }}
        />
        <span className="text-base leading-none">{icon || text.slice(0, 2)}</span>
      </button>
    )
  }

  return (
    <button
      onClick={onClick}
      className={[
        'relative w-full flex items-center gap-2.5 px-3 py-2 mx-0 my-0.5',
        'text-xs font-medium transition-colors duration-100 select-none',
        active
          ? 'bg-md-primary-container/40 text-md-primary'
          : 'text-md-on-surface-var hover:bg-white/5 hover:text-md-on-surface',
      ].join(' ')}
    >
      <span
        className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-md-primary"
        style={{ opacity: active ? 1 : 0 }}
      />
      <span className="text-sm leading-none w-5 text-center shrink-0">
        {icon || '·'}
      </span>
      <span className="truncate">{text}</span>
    </button>
  )
}

function CollapseIcon({ mobile, collapsed }) {
  if (mobile) {
    // X icon for mobile close
    return (
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <path d="M5 5l10 10M15 5L5 15" />
      </svg>
    )
  }
  // Chevron icon for collapse/expand
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {collapsed
        ? <path d="M8 5l5 5-5 5" />
        : <path d="M12 5l-5 5 5 5" />}
    </svg>
  )
}

// Extract emoji icon (if present) and remaining text from a tab label.
// Examples:
//   '🏠 Dashboard'  → { icon: '🏠', text: 'Dashboard' }
//   'Combined'      → { icon: null, text: 'Combined' }
function parseTabLabel(label) {
  if (!label) return { icon: null, text: '' }
  const trimmed = label.trim()
  // Match a leading emoji/pictographic cluster
  const m = trimmed.match(/^([\p{Extended_Pictographic}‍️]+)\s+(.+)$/u)
  if (m) return { icon: m[1], text: m[2] }
  return { icon: null, text: trimmed }
}
