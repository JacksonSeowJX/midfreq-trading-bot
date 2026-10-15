import { createContext, useContext } from 'react'

// ── theme ───────────────────────────────────────────────────────────────────

export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_KEY = 'midfreq.theme'

export interface ThemeState {
  preference: ThemePreference
  resolved: ResolvedTheme
  setPreference: (p: ThemePreference) => void
}

export const ThemeContext = createContext<ThemeState | null>(null)

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme outside ThemeProvider')
  return ctx
}

export function systemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function isThemePreference(v: unknown): v is ThemePreference {
  return v === 'light' || v === 'dark' || v === 'system'
}

// ── market ──────────────────────────────────────────────────────────────────

export const MARKETS = ['US', 'HK'] as const
export type Market = (typeof MARKETS)[number]

export const MARKET_KEY = 'midfreq.market'

export const MARKET_LABEL: Record<Market, string> = {
  US: 'United States (USD)',
  HK: 'Hong Kong (HKD)',
}

export interface MarketState {
  market: Market
  setMarket: (m: Market) => void
}

export const MarketContext = createContext<MarketState | null>(null)

export function useMarket(): MarketState {
  const ctx = useContext(MarketContext)
  if (!ctx) throw new Error('useMarket outside MarketProvider')
  return ctx
}

export function isMarket(v: unknown): v is Market {
  return v === 'US' || v === 'HK'
}
