import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { Tooltip } from 'radix-ui'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  isMarket,
  isThemePreference,
  MARKET_KEY,
  MarketContext,
  systemTheme,
  THEME_KEY,
  ThemeContext,
  type Market,
  type ResolvedTheme,
  type ThemePreference,
} from '@/lib/preferences'
import { safeStorage } from '@/lib/utils'

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(() => {
    const saved = safeStorage.get(THEME_KEY)
    return isThemePreference(saved) ? saved : 'system'
  })
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme)

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setSystem(mq.matches ? 'dark' : 'light')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const resolved: ResolvedTheme = preference === 'system' ? system : preference

  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolved === 'dark')
  }, [resolved])

  const value = useMemo(
    () => ({
      preference,
      resolved,
      setPreference: (p: ThemePreference) => {
        safeStorage.set(THEME_KEY, p)
        setPreferenceState(p)
      },
    }),
    [preference, resolved],
  )
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function MarketProvider({ children, initial }: { children: ReactNode; initial?: Market }) {
  const [market, setMarketState] = useState<Market>(() => {
    if (initial) return initial
    const saved = safeStorage.get(MARKET_KEY)
    return isMarket(saved) ? saved : 'US'
  })
  const value = useMemo(
    () => ({
      market,
      setMarket: (m: Market) => {
        safeStorage.set(MARKET_KEY, m)
        setMarketState(m)
      },
    }),
    [market],
  )
  return <MarketContext.Provider value={value}>{children}</MarketContext.Provider>
}

/** Everything the app needs above the router: data cache, theme, market, tooltips. */
export function AppProviders({
  client,
  market,
  children,
}: {
  client: QueryClient
  market?: Market
  children: ReactNode
}) {
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MarketProvider initial={market}>
          <Tooltip.Provider delayDuration={250}>{children}</Tooltip.Provider>
        </MarketProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
}
