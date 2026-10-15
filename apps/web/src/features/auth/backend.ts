/**
 * What the sign-in gate needs from an identity provider, and the one it uses: Firebase
 * Authentication with Google accounts only. The Firebase SDK is a separate chunk, loaded only
 * when the API says sign-in is on (GET /api/auth/config), so a laptop's dashboard never fetches it.
 */
import type { components } from '@/api/schema'

export type FirebaseWebConfig = components['schemas']['FirebaseWebConfig']

export interface AuthUser {
  uid: string
  email: string | null
}

export interface AuthBackend {
  /** Calls back with the signed-in user (null when signed out) once known, then on every change. */
  onUserChanged(callback: (user: AuthUser | null) => void): () => void
  /** The error of a redirect sign-in that returned to this page load, if it failed; else undefined. */
  redirectError(): Promise<unknown>
  /** A Google account chooser in a popup, or a full-page redirect where popups are blocked. */
  signInWithGoogle(): Promise<void>
  signOut(): Promise<void>
  /** The signed-in user's ID token (refreshed when about to expire, or now with `forceRefresh`). */
  idToken(forceRefresh?: boolean): Promise<string | null>
}

export async function loadAuthBackend(config: FirebaseWebConfig): Promise<AuthBackend> {
  const { createFirebaseBackend } = await import('./firebase')
  return createFirebaseBackend(config)
}

/** The Firebase error code (`auth/popup-blocked`), when there is one. */
export function errorCode(e: unknown): string | undefined {
  if (e && typeof e === 'object' && 'code' in e && typeof e.code === 'string') return e.code
  return undefined
}

/** What to tell the visitor when sign-in fails; null when they closed the window themselves. */
export function signInErrorMessage(e: unknown): string | null {
  switch (errorCode(e)) {
    case 'auth/popup-closed-by-user':
    case 'auth/cancelled-popup-request':
    case 'auth/user-cancelled':
      return null
    case 'auth/network-request-failed':
      return 'Google could not be reached. Check the connection and try again.'
    case 'auth/too-many-requests':
      return 'Too many attempts. Wait a minute, then try again.'
    case 'auth/unauthorized-domain':
      return 'This address is not authorised for sign-in yet (Identity Platform, authorised domains).'
    case 'auth/operation-not-allowed':
      return 'Google sign-in is not enabled for this project yet.'
    case 'auth/user-disabled':
      return 'This account has been disabled.'
    default: {
      const code = errorCode(e)
      return code ? `Sign-in failed (${code}). Try again.` : 'Sign-in failed. Try again.'
    }
  }
}
