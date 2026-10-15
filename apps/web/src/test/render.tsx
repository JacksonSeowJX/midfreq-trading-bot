import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router'
import { createQueryClient } from '@/api/queries'
import { AppProviders } from '@/components/providers'
import type { Market } from '@/lib/preferences'
import { routes } from '@/routes'

/** One component with the app's providers, a fresh cache and no retries. */
export function renderWithProviders(ui: ReactElement, { market = 'US' as Market, path = '/' } = {}) {
  const client = createQueryClient({ retry: false })
  return render(
    <AppProviders client={client} market={market}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </AppProviders>,
  )
}

/** The whole app at a path, as the browser would load it. */
export function renderApp(path: string, { market = 'US' as Market } = {}) {
  const client = createQueryClient({ retry: false })
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const utils = render(
    <AppProviders client={client} market={market}>
      <RouterProvider router={router} />
    </AppProviders>,
  )
  return { ...utils, router }
}
