import { useIsFetching, useQueryClient } from '@tanstack/react-query'
import { MonitorIcon, MoonIcon, RefreshCwIcon, SunIcon } from 'lucide-react'
import { useCallback, useSyncExternalStore } from 'react'
import { useMatches } from 'react-router'
import { isApiError } from '@/api/client'
import { useHealth } from '@/api/queries'
import { Button } from '@/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip } from '@/components/ui/tooltip'
import { fmtAgo, fmtClock } from '@/lib/format'
import { isMarket, isThemePreference, MARKET_LABEL, MARKETS, useMarket, useTheme } from '@/lib/preferences'
import { useNow } from '@/lib/useNow'
import { cn } from '@/lib/utils'

export interface RouteHandle {
  title: string
  subtitle?: string
}

function usePageHandle(): RouteHandle | undefined {
  const matches = useMatches()
  for (let i = matches.length - 1; i >= 0; i--) {
    const h = matches[i]?.handle as RouteHandle | undefined
    if (h?.title) return h
  }
  return undefined
}

/** Newest successful fetch among the queries on screen, and how many of them are failing. */
function useCacheFreshness() {
  const cache = useQueryClient().getQueryCache()
  const subscribe = useCallback((cb: () => void) => cache.subscribe(cb), [cache])
  const lastUpdated = useSyncExternalStore(subscribe, () => {
    let max = 0
    for (const q of cache.getAll()) {
      if (q.getObserversCount() > 0 && q.state.dataUpdatedAt > max) max = q.state.dataUpdatedAt
    }
    return max
  })
  const failing = useSyncExternalStore(subscribe, () => {
    let n = 0
    for (const q of cache.getAll()) if (q.getObserversCount() > 0 && q.state.status === 'error') n++
    return n
  })
  return { lastUpdated, failing }
}

export function Freshness() {
  const qc = useQueryClient()
  const fetching = useIsFetching() > 0
  const { lastUpdated, failing } = useCacheFreshness()
  const now = useNow()
  return (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground" aria-live="polite">
      <span
        aria-hidden
        className={cn(
          'size-1.5 rounded-full',
          fetching ? 'animate-pulse bg-brand' : failing ? 'bg-warning' : lastUpdated ? 'bg-gain' : 'bg-border',
        )}
      />
      <span className="hidden tabular-nums md:inline" data-testid="freshness">
        {fetching
          ? 'Refreshing…'
          : lastUpdated
            ? `As of ${fmtClock(lastUpdated)} · ${fmtAgo(lastUpdated, now)}`
            : 'No data yet'}
      </span>
      <Tooltip content="Refresh everything on this page now">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Refresh now"
          onClick={() => void qc.refetchQueries({ type: 'active' })}
        >
          <RefreshCwIcon className={cn(fetching && 'animate-spin')} />
        </Button>
      </Tooltip>
    </div>
  )
}

export function ApiStatus() {
  const health = useHealth()
  let dot = 'bg-border'
  let text = 'API …'
  let detail = 'Checking the API'
  if (health.data) {
    dot = 'bg-gain'
    text = `API v${health.data.version}`
    const sources = Object.entries(health.data.sources)
      .map(([k, ok]) => `${k} ${ok ? 'yes' : 'no'}`)
      .join(', ')
    detail = `API online. Sources readable: ${sources || 'none listed'}`
  } else if (health.isError) {
    const e = health.error
    if (isApiError(e) && e.unreachable) {
      dot = 'bg-loss'
      text = 'API offline'
      detail = 'The API cannot be reached at /api'
    } else {
      dot = 'bg-warning'
      text = isApiError(e) ? `API ${e.status}` : 'API error'
      detail = isApiError(e) && e.status === 501 ? 'The API is up; /api/health is not implemented yet' : String(e)
    }
  }
  return (
    <Tooltip content={detail}>
      <span tabIndex={0} className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:inline-flex">
        <span aria-hidden className={cn('size-1.5 rounded-full', dot)} />
        {text}
      </span>
    </Tooltip>
  )
}

export function MarketToggle() {
  const { market, setMarket } = useMarket()
  return (
    <ToggleGroup
      type="single"
      aria-label="Market"
      value={market}
      onValueChange={(v) => {
        if (isMarket(v)) setMarket(v)
      }}
    >
      {MARKETS.map((m) => (
        <ToggleGroupItem key={m} value={m} aria-label={MARKET_LABEL[m]} title={MARKET_LABEL[m]} className="w-9">
          {m}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

export function ThemeToggle() {
  const { preference, setPreference } = useTheme()
  return (
    <ToggleGroup
      type="single"
      aria-label="Theme"
      value={preference}
      onValueChange={(v) => {
        if (isThemePreference(v)) setPreference(v)
      }}
    >
      <ToggleGroupItem value="light" aria-label="Light theme" title="Light">
        <SunIcon />
      </ToggleGroupItem>
      <ToggleGroupItem value="dark" aria-label="Dark theme" title="Dark">
        <MoonIcon />
      </ToggleGroupItem>
      <ToggleGroupItem value="system" aria-label="Follow system theme" title="System">
        <MonitorIcon />
      </ToggleGroupItem>
    </ToggleGroup>
  )
}

export function TopBar() {
  const handle = usePageHandle()
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b bg-card px-4">
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-semibold leading-tight">{handle?.title ?? 'midfreq'}</div>
        {handle?.subtitle ? (
          <div className="hidden truncate text-[11px] leading-tight text-muted-foreground md:block">{handle.subtitle}</div>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <MarketToggle />
        <div aria-hidden className="hidden h-5 w-px bg-border sm:block" />
        <Freshness />
        <ApiStatus />
        <ThemeToggle />
      </div>
    </header>
  )
}
