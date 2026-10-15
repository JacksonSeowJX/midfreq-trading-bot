/**
 * TanStack Query hooks, one per API route the dashboard reads.
 *
 * Live data (accounts, the bot's record, health) refetches every LIVE_REFRESH_MS and when the
 * tab regains focus; research results change only when a study is re-run, so they are cached.
 */
import { keepPreviousData, QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Market } from '@/lib/preferences'
import { api, ApiError, isApiError, unwrap, unwrapEmpty } from './client'
import type { BacktestRequest, CompareRequest, Job, OptimizeRequest, SavedRun, SavedRunTable, TableFormat } from './types'

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
  savedRuns: ['saved-runs'] as const,
  savedRun: (id: string) => ['saved-run', id] as const,
  savedRunTable: (id: string, name: string) => ['saved-run', id, 'table', name] as const,
  runsDeletable: ['saved-runs', 'deletable'] as const,
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
      // the recent-runs list shows its status too, and a finished run is now in Run history
      if (!isJobActive(job)) {
        void qc.invalidateQueries({ queryKey: queryKeys.jobs })
        if (job.run_id) void qc.invalidateQueries({ queryKey: queryKeys.savedRuns, exact: true })
      }
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

// ── saved runs: the run store (Run history) ─────────────────────────────────

/** How many saved runs the history asks for, newest first; the rest are filtered in the page. */
export const SAVED_RUNS_LIMIT = 500

/**
 * Saved runs, newest first. From a bucket (the hosted site) the first listing reads every run's
 * manifest, so it can take a few seconds; it is kept for 30 s and refreshed on focus after that.
 */
export function useSavedRuns() {
  return useQuery({
    queryKey: queryKeys.savedRuns,
    queryFn: ({ signal }) => unwrap(api.GET('/api/runs', { params: { query: { limit: SAVED_RUNS_LIMIT } }, signal })),
    staleTime: 30_000,
  })
}

/** One saved run (summary, how it was run, its table names). A saved run never changes. */
export function useSavedRun(id: string | null) {
  return useQuery({
    queryKey: queryKeys.savedRun(id ?? ''),
    queryFn: ({ signal }) => unwrap(api.GET('/api/runs/{run_id}', { params: { path: { run_id: id ?? '' } }, signal })),
    enabled: !!id,
    staleTime: Infinity,
  })
}

/** One of a saved run's tables as JSON rows (up to the API's 100,000). Never refetched. */
export function useSavedRunTable(id: string, name: string, { enabled = true } = {}) {
  return useQuery({
    queryKey: queryKeys.savedRunTable(id, name),
    queryFn: async ({ signal }) =>
      (await unwrap(
        api.GET('/api/runs/{run_id}/tables/{name}', { params: { path: { run_id: id, name }, query: { format: 'json' } }, signal }),
      )) as SavedRunTable,
    enabled: enabled && !!id && !!name,
    staleTime: Infinity,
  })
}

/** Fetch a table as a file (through the API client, so any sign-in header goes with it) and save it. */
export async function downloadSavedRunTable(id: string, name: string, format: TableFormat): Promise<void> {
  const blob = await unwrap(
    api.GET('/api/runs/{run_id}/tables/{name}', { params: { path: { run_id: id, name }, query: { format } }, parseAs: 'blob' }),
  )
  saveFile(blob, `${id}_${name}.${format}`)
}

function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = Object.assign(document.createElement('a'), { href: url, download: filename })
  document.body.append(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/**
 * Whether this server lets runs be deleted. Assumed so until a delete is refused (403: the
 * hosted site), then false for the rest of the visit, so the Delete buttons go.
 */
export function useRunsDeletable(): boolean {
  const q = useQuery({
    queryKey: queryKeys.runsDeletable,
    queryFn: () => true,
    initialData: true,
    staleTime: Infinity,
    gcTime: Infinity,
  })
  return q.data
}

/**
 * Delete a saved run; on success it leaves the cached list at once. (The page showing it drops
 * its cached detail once it has navigated away: forgetting it while it is on screen refetches it.)
 */
export function useDeleteSavedRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => unwrapEmpty(api.DELETE('/api/runs/{run_id}', { params: { path: { run_id: id } } })),
    onSuccess: (_, id) => {
      qc.setQueryData<SavedRun[]>(queryKeys.savedRuns, (old) => old?.filter((r) => r.run_id !== id))
      void qc.invalidateQueries({ queryKey: queryKeys.savedRuns, exact: true })
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 403) qc.setQueryData(queryKeys.runsDeletable, false)
    },
  })
}
