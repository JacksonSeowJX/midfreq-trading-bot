/**
 * Sign-in on the wire: every API call carries the signed-in user's Firebase ID token, and a
 * refusal by the sign-in check is reported to whoever listens (the sign-in gate).
 *
 * With sign-in off there is no token source, and requests go out exactly as before.
 */
import type { Middleware } from 'openapi-fetch'

/** The current ID token (the Firebase SDK refreshes it when it is about to expire), or null. */
export type TokenSource = () => Promise<string | null>

let tokenSource: TokenSource | null = null
const rejectionListeners = new Set<(status: 401 | 403) => void>()

/** Set by the sign-in gate once Firebase is ready; null when signed out or sign-in is off. */
export function setTokenSource(source: TokenSource | null): void {
  tokenSource = source
}

/**
 * Called when a data route refuses the request for sign-in reasons (401, or 403 with the
 * server's `WWW-Authenticate: Bearer` header, which a run-kind 403 does not carry). The sign-in
 * routes themselves are not reported: the gate reads their answers directly.
 */
export function onAuthRejected(listener: (status: 401 | 403) => void): () => void {
  rejectionListeners.add(listener)
  return () => {
    rejectionListeners.delete(listener)
  }
}

export const authMiddleware: Middleware = {
  async onRequest({ request }) {
    if (!tokenSource) return undefined
    // without one (the SDK could not refresh it), the server answers 401 and the gate asks to sign in again
    const token = await tokenSource().catch(() => null)
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
  onResponse({ request, response }) {
    if (response.status !== 401 && response.status !== 403) return undefined
    if (!/^bearer\b/i.test(response.headers.get('www-authenticate') ?? '')) return undefined
    if (new URL(request.url).pathname.startsWith('/api/auth/')) return undefined
    const status = response.status
    for (const listener of rejectionListeners) listener(status)
    return undefined
  },
}
