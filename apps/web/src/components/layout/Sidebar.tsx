import {
  ActivityIcon,
  BookOpenIcon,
  ChartCandlestickIcon,
  FolderOpenIcon,
  GitCompareIcon,
  LayoutDashboardIcon,
  SlidersHorizontalIcon,
  type LucideIcon,
} from 'lucide-react'
import { NavLink } from 'react-router'
import { Tooltip } from '@/components/ui/tooltip'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
}

const NAV: { group: string; items: NavItem[] }[] = [
  { group: 'Monitor', items: [{ to: '/live', label: 'Live', icon: ActivityIcon }] },
  {
    group: 'Research',
    items: [
      { to: '/overview', label: 'Overview', icon: LayoutDashboardIcon },
      { to: '/research', label: 'Research files', icon: FolderOpenIcon },
    ],
  },
  {
    group: 'Run',
    items: [
      { to: '/backtest', label: 'Backtest', icon: ChartCandlestickIcon },
      { to: '/compare', label: 'Compare', icon: GitCompareIcon },
      { to: '/optimize', label: 'Optimize', icon: SlidersHorizontalIcon },
    ],
  },
  { group: 'Help', items: [{ to: '/guide', label: 'Guide', icon: BookOpenIcon }] },
]

/** Icon rail on tablets, labelled list from lg up. */
export function Sidebar() {
  return (
    <nav
      aria-label="Main"
      className="flex w-14 shrink-0 flex-col border-r bg-card lg:w-52"
    >
      <div className="flex h-12 items-center gap-2 border-b px-3.5">
        <span
          aria-hidden
          className="grid size-7 shrink-0 place-items-center rounded-md bg-brand text-[11px] font-bold tracking-tight text-white"
        >
          MF
        </span>
        <span className="hidden min-w-0 lg:block">
          <span className="block truncate text-[13px] font-semibold leading-tight">midfreq</span>
          <span className="block truncate text-[11px] leading-tight text-muted-foreground">trading terminal</span>
        </span>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto px-2 py-3">
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="mb-1 hidden px-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground lg:block">
              {g.group}
            </div>
            <ul className="space-y-0.5">
              {g.items.map((item) => (
                <li key={item.to}>
                  <Tooltip content={item.label} side="right" className="lg:hidden">
                    <NavLink
                      to={item.to}
                      aria-label={item.label}
                      // a plain string: Radix's Slot (the tooltip trigger) cannot merge a className function
                      className="relative flex h-8 items-center justify-center gap-2.5 rounded-md px-2 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground lg:justify-start aria-[current=page]:bg-accent aria-[current=page]:text-foreground aria-[current=page]:before:absolute aria-[current=page]:before:inset-y-1.5 aria-[current=page]:before:left-0 aria-[current=page]:before:w-0.5 aria-[current=page]:before:rounded-full aria-[current=page]:before:bg-brand"
                    >
                      <item.icon className="size-4 shrink-0" aria-hidden />
                      <span className="hidden truncate lg:inline">{item.label}</span>
                    </NavLink>
                  </Tooltip>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  )
}
