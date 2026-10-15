/**
 * The typed API client. Types come from ../api/openapi.json (npm run gen:api), so a route or
 * field the API does not declare is a compile error here.
 *
 * Every call is relative to /api on the page's own origin: the Vite dev server proxies it to
 * FastAPI, and a hosted build is served by the same origin as the API.
 */
import createClient from 'openapi-fetch'
import type { paths } from './schema'

function origin(): string {
  return typeof window !== 'undefined' && window.location?.origin
    ? window.location.origin
    : 'http://localhost'
}

export const api = createClient<paths>({
  baseUrl: origin(),
  // look fetch up on every call (not once at import), so test interceptors and polyfills apply
  fetch: (request) => globalThis.fetch(request),
})

/**
 * A failed call. `status` is the HTTP status, or 0 when the API could not be reached at all.
 * `detail` is FastAPI's `{"detail": ...}` message when the response carried one.
 */
export class ApiError extends Error {
  readonly status: number
  readonly detail?: string

  constructor(status: number, detail?: string) {
    super(detail ? `${status}: ${detail}` : `HTTP ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }

  /** The API process is down or the proxy could not reach it. */
  get unreachable(): boolean {
    return this.status === 0 || this.status === 502 || this.status === 504 || (this.status === 500 && !this.detail)
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError
}

function detailOf(body: unknown): string | undefined {
  if (typeof body === 'string') return body.trim() ? body.trim().slice(0, 300) : undefined
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === 'string') return d
    if (Array.isArray(d)) {
      // 422 validation errors: [{loc, msg, type}, ...]
      return d
        .map((x) => (x && typeof x === 'object' && 'msg' in x ? String((x as { msg: unknown }).msg) : String(x)))
        .join('; ')
    }
  }
  return undefined
}

type Result<T> = { data?: T; error?: unknown; response: Response }

/** Resolve an openapi-fetch call to its data, or throw ApiError. */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  let result: Result<T>
  try {
    result = await call
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0)
  }
  const { data, error, response } = result
  if (!response.ok || data === undefined) {
    throw new ApiError(response.status, detailOf(error))
  }
  return data
}
