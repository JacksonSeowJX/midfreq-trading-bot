/**
 * TanStack Query hooks, one per API route the dashboard reads.
 *
 * Live data (accounts, the bot's record, health) refetches every LIVE_REFRESH_MS and when the
 * tab regains focus; research results change only when a study is re-run, so they are cached.
 */
import { keepPreviousData, QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Market } from '@/lib/preferences'
import { api, ApiError, unwrap } from './client'
import type { BacktestRequest, CompareRequest, Job, OptimizeRequest } from './types'

export const LIVE_REFRESH_MS = 60_000
const RESEARCH_STALE_MS = 5 * 60_000

/** How often a queued or running job is polled. An object, so tests can shorten it. */
export const JOB_POLL = { ms: 1000 }
/** How often the recent-runs list refreshes while one of them is still going. */
const JOBS_LIST_POLL_MS = 3000

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
  capabilities: ['job-capabilities'] as const,
  strategies: ['strategies'] as const,
  universes: ['universes'] as const,
  jobs: ['jobs'] as const,
  job: (id: string) => ['job', id] as const,
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

// ── runs: backtest, compare, optimize as background jobs ────────────────────

/** What this deployment lets you run, and the per-market choices (symbols, universes, fees). */
export function useCapabilities() {
  return useQuery({
    queryKey: queryKeys.capabilities,
    queryFn: ({ signal }) => unwrap(api.GET('/api/jobs/capabilities', { signal })),
    staleTime: RESEARCH_STALE_MS,
  })
}

/** The strategy registry, with each parameter's default and range. */
export function useStrategies() {
  return useQuery({
    queryKey: queryKeys.strategies,
    queryFn: ({ signal }) => unwrap(api.GET('/api/strategies', { signal })),
    staleTime: RESEARCH_STALE_MS,
  })
}

export function useUniverses() {
  return useQuery({
    queryKey: queryKeys.universes,
    queryFn: ({ signal }) => unwrap(api.GET('/api/universes', { signal })),
    staleTime: RESEARCH_STALE_MS,
  })
}

export function isJobActive(job: Pick<Job, 'status'>): boolean {
  return job.status === 'queued' || job.status === 'running'
}

/** Recent runs, newest first, without their results. Polled while one of them is going. */
export function useJobs() {
  return useQuery({
    queryKey: queryKeys.jobs,
    queryFn: ({ signal }) => unwrap(api.GET('/api/jobs', { signal })),
    refetchInterval: (query) => (query.state.data?.some(isJobActive) ? JOBS_LIST_POLL_MS : false),
  })
}

/**
 * One run, polled every JOB_POLL.ms while it is queued or running. A finished run never
 * changes, so it is not fetched again. Leaving the page stops the polling (the run itself
 * carries on in the API).
 */
export function useJob(id: string | null) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: queryKeys.job(id ?? ''),
    queryFn: async ({ signal }) => {
      const job = await unwrap(api.GET('/api/jobs/{job_id}', { params: { path: { job_id: id ?? '' } }, signal }))
      // the recent-runs list shows its status too
      if (!isJobActive(job)) void qc.invalidateQueries({ queryKey: queryKeys.jobs })
      return job
    },
    enabled: !!id,
    refetchInterval: (query) => {
      if (query.state.status === 'error') return false
      const job = query.state.data
      return !job || isJobActive(job) ? JOB_POLL.ms : false
    },
    staleTime: (query) => (query.state.data && !isJobActive(query.state.data) ? Infinity : 0),
    refetchOnWindowFocus: (query) => !query.state.data || isJobActive(query.state.data),
  })
}

export type StartJob =
  | { kind: 'backtest'; body: BacktestRequest }
  | { kind: 'compare'; body: CompareRequest }
  | { kind: 'optimize'; body: OptimizeRequest }

function postJob(req: StartJob): Promise<Job> {
  switch (req.kind) {
    case 'backtest':
      return unwrap(api.POST('/api/jobs/backtest', { body: req.body }))
    case 'compare':
      return unwrap(api.POST('/api/jobs/compare', { body: req.body }))
    case 'optimize':
      return unwrap(api.POST('/api/jobs/optimize', { body: req.body }))
  }
}

/** Start a run. The answer (202) is the queued job, which then goes into the cache to be polled. */
export function useStartJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: postJob,
    onSuccess: (job) => {
      qc.setQueryData(queryKeys.job(job.id), job)
      void qc.invalidateQueries({ queryKey: queryKeys.jobs })
    },
  })
}
