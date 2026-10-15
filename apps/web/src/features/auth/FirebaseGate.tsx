/**
 * The sign-in gate with sign-in on (loaded only then, with the Firebase SDK after it): Google
 * sign-in, then GET /api/auth/me, then the app, "Awaiting approval", or what went wrong. See
 * AuthGate.
 */
import { useQueryClient } from '@tanstack/react-query'
import { LoaderCircleIcon, LogOutIcon, RefreshCwIcon, ShieldAlertIcon, UserRoundCheckIcon } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { onAuthRejected, setTokenSource } from '@/api/auth'
import { api, isApiError, unwrap } from '@/api/client'
import type { components } from '@/api/schema'
import { describeError } from '@/components/errorCopy'
import { Button } from '@/components/ui/button'
import {
  loadAuthBackend,
  signInErrorMessage,
  type AuthBackend,
  type AuthUser,
  type FirebaseWebConfig,
} from './backend'
import { GateScreen } from './GateScreen'
import { AuthSessionContext, type AuthSession } from './session'

type AuthMe = components['schemas']['AuthMe']

/** What /api/auth/me said about one signed-in account. */
type Access =
  | { uid: string; kind: 'allowed'; me: AuthMe }
  | { uid: string; kind: 'refused'; me: AuthMe }
  | { uid: string; kind: 'error'; error: unknown }

export default function FirebaseGate({ config, children }: { config: FirebaseWebConfig; children: ReactNode }) {
  const queryClient = useQueryClient()
  const [backend, setBackend] = useState<AuthBackend | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined) // undefined: not known yet
  const [access, setAccess] = useState<Access | null>(null)
  const [signInError, setSignInError] = useState<string | null>(null)
  const [signingIn, setSigningIn] = useState(false)
  const latestCheck = useRef(0)

  // the SDK, and the signed-in user from then on
  useEffect(() => {
    let cancelled = false
    let unsubscribe = () => {}
    loadAuthBackend(config).then(
      (b) => {
        if (cancelled) return
        setTokenSource(() => b.idToken())
        setBackend(b)
        void b.redirectError().then((e) => {
          if (!cancelled && e) setSignInError(signInErrorMessage(e))
        })
        unsubscribe = b.onUserChanged((u) => {
          if (cancelled) return
          setUser(u)
          if (!u) setAccess(null)
        })
      },
      (e: unknown) => {
        if (!cancelled) setLoadError(e)
      },
    )
    return () => {
      cancelled = true
      unsubscribe()
      setTokenSource(null)
    }
  }, [config])

  const check = useCallback(
    async (uid: string, { refresh = false } = {}) => {
      if (!backend) return
      const id = ++latestCheck.current
      let next: Access
      try {
        if (refresh) await backend.idToken(true)
        const me = await unwrap(api.GET('/api/auth/me'))
        next = { uid, kind: me.allowed ? 'allowed' : 'refused', me }
      } catch (error) {
        next = { uid, kind: 'error', error }
      }
      if (id === latestCheck.current) setAccess(next)
    },
    [backend],
  )

  // a different account (or none): nothing cached for the previous one survives
  const uid = user?.uid
  useEffect(() => {
    queryClient.clear()
    if (uid) void check(uid)
  }, [uid, check, queryClient])

  // a data route refused the token: check again (with a fresh token after a 401)
  useEffect(() => {
    if (!uid) return undefined
    return onAuthRejected((status) => void check(uid, { refresh: status === 401 }))
  }, [uid, check])

  const signIn = async () => {
    if (!backend) return
    setSignInError(null)
    setSigningIn(true)
    try {
      await backend.signInWithGoogle()
    } catch (e) {
      setSignInError(signInErrorMessage(e))
    } finally {
      setSigningIn(false)
    }
  }

  const signOut = useCallback(() => {
    setAccess(null)
    queryClient.clear()
    void backend?.signOut()
  }, [backend, queryClient])

  const checkAgain = () => {
    if (!uid) return
    setAccess(null)
    void check(uid, { refresh: true })
  }

  const current = user && access?.uid === user.uid ? access : null
  const email = current?.kind === 'allowed' ? (current.me.email ?? user?.email ?? null) : null
  const session = useMemo<AuthSession>(() => ({ email, signOut }), [email, signOut])

  if (loadError) {
    return (
      <GateScreen icon={ShieldAlertIcon} title="Sign-in could not start" actions={<ReloadButton />}>
        The sign-in library did not load. Check the connection, then reload the page.
      </GateScreen>
    )
  }
  if (!backend || user === undefined) return <GateScreen busy title="Checking sign-in…" />
  if (user === null) return <SignInScreen onSignIn={signIn} busy={signingIn} error={signInError} />
  if (!current) return <GateScreen busy title="Checking access…" />
  if (current.kind === 'allowed') {
    return <AuthSessionContext.Provider value={session}>{children}</AuthSessionContext.Provider>
  }
  if (current.kind === 'refused') {
    return <AwaitingApproval me={current.me} fallbackEmail={user.email} onCheckAgain={checkAgain} onSignOut={signOut} />
  }
  const e = current.error
  const rejected = isApiError(e) && e.status === 401
  const copy = describeError(e)
  return (
    <GateScreen
      icon={ShieldAlertIcon}
      title={rejected ? 'The server did not accept this sign-in' : copy.title}
      actions={
        <>
          {rejected ? null : (
            <Button variant="outline" onClick={checkAgain}>
              <RefreshCwIcon aria-hidden /> Try again
            </Button>
          )}
          <Button variant={rejected ? 'default' : 'ghost'} onClick={signOut}>
            <LogOutIcon aria-hidden /> {rejected ? 'Sign in again' : 'Sign out'}
          </Button>
        </>
      }
    >
      {rejected ? 'Sign out, then sign in with Google again.' : copy.body}
    </GateScreen>
  )
}

// ── the screens ─────────────────────────────────────────────────────────────

function SignInScreen({ onSignIn, busy, error }: { onSignIn: () => void; busy: boolean; error: string | null }) {
  return (
    <GateScreen
      title="Sign in to the midfreq dashboard"
      actions={
        <Button variant="outline" className="h-10 w-full gap-2.5 text-sm" onClick={onSignIn} disabled={busy}>
          {busy ? <LoaderCircleIcon aria-hidden className="animate-spin" /> : <GoogleMark />}
          Sign in with Google
        </Button>
      }
    >
      <p>Access is by invitation: sign in with the Google account the owner approved.</p>
      {error ? (
        <p role="alert" className="mt-3 rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
          {error}
        </p>
      ) : null}
    </GateScreen>
  )
}

function AwaitingApproval({
  me,
  fallbackEmail,
  onCheckAgain,
  onSignOut,
}: {
  me: AuthMe
  fallbackEmail: string | null
  onCheckAgain: () => void
  onSignOut: () => void
}) {
  const email = me.email ?? fallbackEmail ?? 'this account'
  const unverified = !me.email_verified
  return (
    <GateScreen
      icon={UserRoundCheckIcon}
      title={unverified ? 'Email address not verified' : 'Awaiting approval'}
      actions={
        <>
          <Button variant="outline" onClick={onCheckAgain}>
            <RefreshCwIcon aria-hidden /> Check again
          </Button>
          <Button variant="ghost" onClick={onSignOut}>
            <LogOutIcon aria-hidden /> Sign out
          </Button>
        </>
      }
    >
      <p>
        You are signed in as <strong className="font-semibold text-foreground">{email}</strong>.
      </p>
      <p className="mt-2">
        {unverified
          ? (me.reason ?? 'Verify this address with Google, then check again.')
          : 'This account is not on the dashboard’s access list yet. Ask the owner to add it, then check again, or sign out to use another Google account.'}
      </p>
    </GateScreen>
  )
}

function ReloadButton() {
  return (
    <Button variant="outline" onClick={() => window.location.reload()}>
      <RefreshCwIcon aria-hidden /> Reload
    </Button>
  )
}

/** Google's "G", as its sign-in branding asks for on a Sign in with Google button. */
function GoogleMark() {
  return (
    <svg aria-hidden viewBox="0 0 48 48" className="size-[18px]">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  )
}
