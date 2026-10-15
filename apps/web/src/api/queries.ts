/**
 * TanStack Query hooks, one per API route the dashboard reads.
 *
 * Live data (accounts, the bot's record, health) refetches every LIVE_REFRESH_MS and when the
 * tab regains focus; research results change only when a study is re-run, so they are cached.
 */
import { keepPreviousData, QueryClient, useQuery } from '@tanstack/react-query'
import type { Market } from '@/lib/preferences'
import { api, ApiError, unwrap } from './client'

export const LIVE_REFRESH_MS = 60_000
const RESEARCH_STALE_MS = 5 * 60_000

export function createQueryClient(overrides?: { retry?: boolean }) {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: true,
        // retry only what can recover by itself: the API restarting, a proxy hiccup
        retry:
          overrides?.retry ??
          ((count, error) =>
            error instanceof ApiError && (error.status === 0 || error.status === 502 || error.status === 504)
              ? count < 2
              : false),
      },
    },
  })
}

export const queryKeys = {
  health: ['health'] as const,
  accounts: ['accounts'] as const,
  account: (id: string, orderDays: number) => ['accounts', id, orderDays] as const,
  forwardTest: (market: Market) => ['forward-test', market] as const,
  sessions: (market: Market, limit: number) => ['sessions', market, limit] as const,
  session: (id: string) => ['session', id] as const,
  overview: (currentOnly: boolean) => ['overview', currentOnly] as const,
  researchFiles: ['research-files'] as const,
  researchFile: (name: string) => ['research-file', name] as const,
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => unwrap(api.GET('/api/health', { signal })),
    refetchInterval: LIVE_REFRESH_MS,
    retry: false,
  })
}

export function useAccounts() {
  return useQuery({
    queryKey: queryKeys.accounts,
    queryFn: ({ signal }) => unwrap(api.GET('/api/accounts', { signal })),
    refetchInterval: LIVE_REFRESH_MS,
  })
}

export function useAccount(id: string | null, { enabled = true, orderDays = 14 } = {}) {
  return useQuery({
    queryKey: queryKeys.account(id ?? '', orderDays),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET('/api/accounts/{account_id}', {
          params: { path: { account_id: id ?? '' }, query: { order_days: orderDays } },
          signal,
        }),
      ),
    enabled: enabled && !!id,
    refetchInterval: LIVE_REFRESH_MS,
  })
}

export function useForwardTest(market: Market) {
  return useQuery({
    queryKey: queryKeys.forwardTest(market),
    queryFn: ({ signal }) => unwrap(api.GET('/api/forward-test', { params: { query: { market } }, signal })),
    refetchInterval: LIVE_REFRESH_MS,
  })
}

export function useSessions(market: Market, limit = 50) {
  return useQuery({
    queryKey: queryKeys.sessions(market, limit),
    queryFn: ({ signal }) => unwrap(api.GET('/api/sessions', { params: { query: { market, limit } }, signal })),
    refetchInterval: LIVE_REFRESH_MS,
    placeholderData: keepPreviousData,
  })
}

export function useSession(id: string | null) {
  return useQuery({
    queryKey: queryKeys.session(id ?? ''),
    queryFn: ({ signal }) =>
      unwrap(api.GET('/api/sessions/{session_id}', { params: { path: { session_id: id ?? '' } }, signal })),
    enabled: !!id,
    refetchInterval: LIVE_REFRESH_MS,
  })
}

export function useOverview(currentOnly: boolean) {
  return useQuery({
    queryKey: queryKeys.overview(currentOnly),
    queryFn: ({ signal }) =>
      unwrap(api.GET('/api/results/overview', { params: { query: { current_only: currentOnly } }, signal })),
    staleTime: RESEARCH_STALE_MS,
    placeholderData: keepPreviousData,
  })
}

export function useResearchFiles() {
  return useQuery({
    queryKey: queryKeys.researchFiles,
    queryFn: ({ signal }) => unwrap(api.GET('/api/research/files', { signal })),
    staleTime: RESEARCH_STALE_MS,
  })
}

export function useResearchFile(name: string | null) {
  return useQuery({
    queryKey: queryKeys.researchFile(name ?? ''),
    queryFn: ({ signal }) =>
      unwrap(api.GET('/api/research/files/{name}', { params: { path: { name: name ?? '' } }, signal })),
    enabled: !!name,
    staleTime: RESEARCH_STALE_MS,
  })
}
