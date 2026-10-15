import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { createQueryClient } from '@/api/queries'
import type { components } from '@/api/schema'
import { AppProviders } from '@/components/providers'
import { routes } from '@/routes'
import { server } from '@/test/handlers'
import { AuthGate } from './AuthGate'

// ── the Firebase SDK, in memory ─────────────────────────────────────────────

interface FakeUser {
  uid: string
  email: string
  getIdToken: (force?: boolean) => Promise<string>
}

const fb = vi.hoisted(() => {
  const state = {
    initialized: 0,
    user: null as FakeUser | null,
    nextEmail: 'owner@example.com',
    popup: 'ok' as 'ok' | 'blocked' | 'closed',
    redirects: 0,
    refreshes: 0,
    listeners: new Set<(u: FakeUser | null) => void>(),
  }
  const userFor = (email: string): FakeUser => ({
    uid: `uid-${email}`,
    email,
    getIdToken: async (force?: boolean) => {
      if (force) state.refreshes++
      return `id-token-${email}`
    },
  })
  const setUser = (u: FakeUser | null) => {
    state.user = u
    for (const l of state.listeners) l(u)
  }
  const reset = () => {
    Object.assign(state, { initialized: 0, user: null, nextEmail: 'owner@example.com', popup: 'ok', redirects: 0, refreshes: 0 })
    state.listeners.clear()
  }
  const failure = (code: string) => Object.assign(new Error(code), { code })
  return { state, userFor, setUser, reset, failure }
})

vi.mock('firebase/app', () => ({
  getApps: () => [],
  initializeApp: (options: unknown, name: string) => {
    fb.state.initialized++
    return { name, options }
  },
}))

vi.mock('firebase/auth', () => ({
  getAuth: () => ({
    get currentUser() {
      return fb.state.user
    },
    useDeviceLanguage() {},
  }),
  onAuthStateChanged: (_auth: unknown, callback: (u: FakeUser | null) => void) => {
    fb.state.listeners.add(callback)
    queueMicrotask(() => callback(fb.state.user))
    return () => fb.state.listeners.delete(callback)
  },
  getRedirectResult: async () => null,
  signInWithPopup: async () => {
    if (fb.state.popup === 'blocked') throw fb.failure('auth/popup-blocked')
    if (fb.state.popup === 'closed') throw fb.failure('auth/popup-closed-by-user')
    fb.setUser(fb.userFor(fb.state.nextEmail))
  },
  signInWithRedirect: async () => {
    fb.state.redirects++
  },
  signOut: async () => fb.setUser(null),
  GoogleAuthProvider: class {
    setCustomParameters() {}
  },
}))

// ── the API's sign-in routes ────────────────────────────────────────────────

type AuthConfig = components['schemas']['AuthConfig']

const ON: AuthConfig = {
  enabled: true,
  provider: 'firebase',
  firebase: { apiKey: 'public-web-key', authDomain: 'demo.firebaseapp.com', projectId: 'demo' },
}
const OFF: AuthConfig = { enabled: false, provider: null, firebase: null }

/** Who /api/auth/me says the bearer of each token is. */
const ALLOWED = new Set(['owner@example.com'])

function meFor(authorization: string | null) {
  const email = authorization?.match(/^Bearer id-token-(.+)$/)?.[1]
  if (!email) return HttpResponse.json({ detail: 'Sign in required' }, { status: 401, headers: { 'WWW-Authenticate': 'Bearer' } })
  const allowed = ALLOWED.has(email)
  return HttpResponse.json({
    email,
    email_verified: true,
    allowed,
    reason: allowed ? null : `Awaiting approval: ${email} is signed in but not yet on this dashboard's access list.`,
  })
}

let seen: { path: string; authorization: string | null }[] = []

beforeEach(() => {
  fb.reset()
  seen = []
  server.events.on('request:start', ({ request }) => {
    seen.push({ path: new URL(request.url).pathname, authorization: request.headers.get('authorization') })
  })
})
afterEach(() => server.events.removeAllListeners())

function serveAuth(config: AuthConfig) {
  server.use(
    http.get('*/api/auth/config', () => HttpResponse.json(config)),
    http.get('*/api/auth/me', ({ request }) => meFor(request.headers.get('authorization'))),
  )
}

function renderGated(path = '/live') {
  const client = createQueryClient({ retry: false })
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(
    <AppProviders client={client} market="US">
      <AuthGate>
        <RouterProvider router={router} />
      </AuthGate>
    </AppProviders>,
  )
  return { client, router }
}

const liveHeading = () => screen.findByRole('heading', { level: 1, name: /Live trading/ })
const dataRequests = () => seen.filter((r) => !r.path.startsWith('/api/auth/'))

// ── tests ───────────────────────────────────────────────────────────────────

describe('sign-in off', () => {
  it('renders the app as before, without loading Firebase or sending a token', async () => {
    serveAuth(OFF)
    renderGated()
    expect(await liveHeading()).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Sign in with Google/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument()
    expect(fb.state.initialized).toBe(0)
    await waitFor(() => expect(dataRequests().length).toBeGreaterThan(0))
    expect(seen.every((r) => r.authorization === null)).toBe(true)
  })

  it('renders the app when the API cannot say (down, or older than sign-in)', async () => {
    server.use(http.get('*/api/auth/config', () => HttpResponse.json({ detail: 'Not Found' }, { status: 404 })))
    renderGated()
    expect(await liveHeading()).toBeInTheDocument()
  })
})

describe('sign-in on', () => {
  it('asks for Google sign-in before anything, and reads no data', async () => {
    serveAuth(ON)
    renderGated()
    expect(await screen.findByRole('button', { name: /Sign in with Google/ })).toBeInTheDocument()
    expect(screen.getByText(/Access is by invitation/)).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
    expect(fb.state.initialized).toBe(1)
    expect(dataRequests()).toEqual([])
  })

  it('after signing in, attaches the ID token to every request and shows the account', async () => {
    serveAuth(ON)
    renderGated()
    await userEvent.click(await screen.findByRole('button', { name: /Sign in with Google/ }))
    expect(await liveHeading()).toBeInTheDocument()
    await waitFor(() => expect(dataRequests().length).toBeGreaterThan(2))
    const afterSignIn = seen.filter((r) => r.path !== '/api/auth/config')
    expect(afterSignIn.every((r) => r.authorization === 'Bearer id-token-owner@example.com')).toBe(true)
    expect(screen.getByTitle('owner@example.com')).toHaveTextContent('owner@example.com')
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('goes to Google by redirect when the browser blocks the popup', async () => {
    serveAuth(ON)
    fb.state.popup = 'blocked'
    renderGated()
    await userEvent.click(await screen.findByRole('button', { name: /Sign in with Google/ }))
    await waitFor(() => expect(fb.state.redirects).toBe(1))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says nothing when the visitor closes the popup', async () => {
    serveAuth(ON)
    fb.state.popup = 'closed'
    renderGated()
    await userEvent.click(await screen.findByRole('button', { name: /Sign in with Google/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: /Sign in with Google/ })).toBeEnabled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows "Awaiting approval" with the email to an account not on the list, and reads no data', async () => {
    serveAuth(ON)
    fb.state.nextEmail = 'stranger@example.org'
    renderGated()
    await userEvent.click(await screen.findByRole('button', { name: /Sign in with Google/ }))
    expect(await screen.findByRole('heading', { name: 'Awaiting approval' })).toBeInTheDocument()
    expect(screen.getByText('stranger@example.org')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
    expect(dataRequests()).toEqual([])

    // approved meanwhile: checking again lets them in
    ALLOWED.add('stranger@example.org')
    try {
      await userEvent.click(screen.getByRole('button', { name: 'Check again' }))
      expect(await liveHeading()).toBeInTheDocument()
    } finally {
      ALLOWED.delete('stranger@example.org')
    }
  })

  it('shows "Awaiting approval" when a data route refuses the account later (403)', async () => {
    serveAuth(ON)
    fb.setUser(fb.userFor('owner@example.com'))
    renderGated()
    expect(await liveHeading()).toBeInTheDocument()
    // taken off the list: the API now refuses with 403 + WWW-Authenticate, and /me says so
    ALLOWED.delete('owner@example.com')
    try {
      server.use(
        http.get('*/api/accounts', () =>
          HttpResponse.json(
            { detail: 'Awaiting approval: owner@example.com ...' },
            { status: 403, headers: { 'WWW-Authenticate': 'Bearer error="insufficient_scope"' } },
          ),
        ),
      )
      await userEvent.click(screen.getByRole('button', { name: 'Refresh now' }))
      expect(await screen.findByRole('heading', { name: 'Awaiting approval' })).toBeInTheDocument()
      expect(screen.getByText('owner@example.com')).toBeInTheDocument()
    } finally {
      ALLOWED.add('owner@example.com')
    }
  })

  it('does not treat a 403 without the sign-in header (a run kind not allowed) as a sign-in problem', async () => {
    serveAuth(ON)
    fb.setUser(fb.userFor('owner@example.com'))
    renderGated()
    expect(await liveHeading()).toBeInTheDocument()
    const meCalls = () => seen.filter((r) => r.path === '/api/auth/me').length
    const before = meCalls()
    server.use(http.get('*/api/accounts', () => HttpResponse.json({ detail: 'not allowed here' }, { status: 403 })))
    await userEvent.click(screen.getByRole('button', { name: 'Refresh now' }))
    await waitFor(() => expect(seen.filter((r) => r.path === '/api/accounts').length).toBeGreaterThan(1))
    expect(meCalls()).toBe(before)
    expect(screen.getByRole('heading', { level: 1, name: /Live trading/ })).toBeInTheDocument()
  })

  it('signs out from the top bar, back to the sign-in screen, with nothing cached', async () => {
    serveAuth(ON)
    fb.setUser(fb.userFor('owner@example.com'))
    const { client } = renderGated()
    expect(await liveHeading()).toBeInTheDocument()
    await waitFor(() => expect(client.getQueryCache().getAll().length).toBeGreaterThan(0))
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('button', { name: /Sign in with Google/ })).toBeInTheDocument()
    expect(fb.state.user).toBeNull()
    expect(client.getQueryCache().getAll()).toEqual([])
  })

  it('asks to sign in again when the server rejects the token', async () => {
    server.use(
      http.get('*/api/auth/config', () => HttpResponse.json(ON)),
      http.get('*/api/auth/me', () =>
        HttpResponse.json({ detail: 'Sign-in expired' }, { status: 401, headers: { 'WWW-Authenticate': 'Bearer' } }),
      ),
    )
    fb.setUser(fb.userFor('owner@example.com'))
    renderGated()
    expect(await screen.findByRole('heading', { name: 'The server did not accept this sign-in' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sign in again' }))
    expect(await screen.findByRole('button', { name: /Sign in with Google/ })).toBeInTheDocument()
  })

  it('explains a server that requires sign-in but has no web configuration', async () => {
    serveAuth({ ...ON, firebase: { ...ON.firebase!, apiKey: '' } })
    renderGated()
    expect(await screen.findByRole('heading', { name: 'Sign-in is not configured' })).toBeInTheDocument()
    expect(fb.state.initialized).toBe(0)
  })
})
