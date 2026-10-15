import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import * as f from './fixtures'

const notFound = (detail: string) => HttpResponse.json({ detail }, { status: 404 })

/** A working API with the fixtures' data. Tests override single routes with server.use(). */
export const handlers = [
  http.get('*/api/health', () => HttpResponse.json(f.health)),
  http.get('*/api/accounts', () => HttpResponse.json(f.accounts)),
  http.get('*/api/accounts/:id', ({ params }) => {
    if (params.id === 'moomoo_us') return HttpResponse.json(f.accountView)
    if (params.id === 'binance_spot')
      return HttpResponse.json({ detail: 'Binance API timed out after 5 s' }, { status: 503 })
    return notFound('no such account')
  }),
  http.get('*/api/forward-test', ({ request }) => {
    const market = new URL(request.url).searchParams.get('market') ?? 'US'
    return market === 'US' ? HttpResponse.json(f.forwardTest) : notFound('no forward-test session for this market')
  }),
  http.get('*/api/sessions', ({ request }) => {
    const market = new URL(request.url).searchParams.get('market')
    return HttpResponse.json(f.sessions.filter((s) => !market || s.market === market))
  }),
  http.get('*/api/sessions/:id', ({ params }) =>
    params.id === f.sessionDetail.summary.id ? HttpResponse.json(f.sessionDetail) : notFound('no such session'),
  ),
  http.get('*/api/results/overview', () => HttpResponse.json(f.overview)),
  http.get('*/api/research/files', () => HttpResponse.json(f.researchFiles)),
  http.get('*/api/jobs/capabilities', () => HttpResponse.json(f.capabilities)),
  http.get('*/api/strategies', () => HttpResponse.json(f.strategies)),
  http.get('*/api/universes', () => HttpResponse.json(f.universes)),
  http.get('*/api/jobs', () => HttpResponse.json(f.jobList)),
  http.get('*/api/jobs/:id', ({ params }) => {
    const job = f.jobsById[String(params.id)]
    return job ? HttpResponse.json(job) : notFound('no such job')
  }),
  http.post('*/api/jobs/:kind', async ({ params, request }) => {
    const kind = String(params.kind)
    if (kind !== 'backtest' && kind !== 'compare' && kind !== 'optimize') return notFound('Not Found')
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json(
      { id: 'job-new', kind, status: 'queued', progress: 0, message: '', created: new Date().toISOString(), request: body },
      { status: 202 },
    )
  }),
  http.get('*/api/research/files/:name', ({ params }) => {
    if (params.name === f.researchCsv.name) return HttpResponse.json(f.researchCsv)
    if (params.name === f.researchJson.name) return HttpResponse.json(f.researchJson)
    return notFound('no such file')
  }),
]

export const server = setupServer(...handlers)

/** The API process is down: every call fails at the network level. */
export const apiDown = http.all('*/api/*', () => HttpResponse.error())
