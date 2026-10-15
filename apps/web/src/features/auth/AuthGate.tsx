/**
 * The sign-in gate, above the router. It asks the API whether sign-in is on
 * (GET /api/auth/config, public):
 *
 * - off (a laptop, the VM), or the question cannot be answered: the app, exactly as before;
 * - on (the hosted dashboard): the Firebase SDK loads, and the app shows only once a Google
 *   account is signed in and GET /api/auth/me says it is allowed. A signed-in account that is
 *   not allowed sees "Awaiting approval" with the email it signed in with.
 *
 * This is the experience only: the API checks the ID token on every data route itself. Every
 * request carries the token (api/auth.ts); when a data route refuses it later (a token that no
 * longer verifies, an account taken off the list), access is checked again.
 */
import { ShieldAlertIcon } from 'lucide-react'
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import { onAuthRejected } from '@/api/auth'
import { api, unwrap } from '@/api/client'
import type { components } from '@/api/schema'
import { GateScreen } from './GateScreen'

type AuthConfig = components['schemas']['AuthConfig']

// Sign-in itself (its screens, then the Firebase SDK) loads only when the API asks for it.
const FirebaseGate = lazy(() => import('./FirebaseGate'))

export function AuthGate({ children }: { children: ReactNode }) {
  // undefined: still asking; null: no answer (an API that is down or predates sign-in), as off
  const [config, setConfig] = useState<AuthConfig | null | undefined>(undefined)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    unwrap(api.GET('/api/auth/config', { signal: controller.signal })).then(setConfig, (e: unknown) => {
      if (!(e instanceof DOMException && e.name === 'AbortError')) setConfig(null)
    })
    return () => controller.abort()
  }, [attempt])

  // Open, but a data route asks for sign-in: the configuration was missed (the API was starting
  // up, say), so ask again.
  const open = config !== undefined && !config?.enabled
  useEffect(() => (open ? onAuthRejected(() => setAttempt((n) => n + 1)) : undefined), [open])

  if (config === undefined) return <GateScreen busy title="Loading…" />
  if (!config?.enabled) return <>{children}</>
  if (!config.firebase?.apiKey) {
    return (
      <GateScreen icon={ShieldAlertIcon} title="Sign-in is not configured">
        This server requires sign-in, but has no Firebase web configuration (MIDFREQ_AUTH_WEB_API_KEY).
      </GateScreen>
    )
  }
  return (
    <Suspense fallback={<GateScreen busy title="Checking sign-in…" />}>
      <FirebaseGate config={config.firebase}>{children}</FirebaseGate>
    </Suspense>
  )
}
