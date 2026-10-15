import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { createQueryClient } from '@/api/queries'
import { AppProviders } from '@/components/providers'
import { AuthGate } from '@/features/auth/AuthGate'
import { routes } from '@/routes'
import './index.css'

const router = createBrowserRouter(routes)
const queryClient = createQueryClient()

const root = document.getElementById('root')
if (!root) throw new Error('index.html has no #root element')

createRoot(root).render(
  <StrictMode>
    <AppProviders client={queryClient}>
      {/* sign-in first when the API requires it (the hosted dashboard); otherwise the app as is */}
      <AuthGate>
        <RouterProvider router={router} />
      </AuthGate>
    </AppProviders>
  </StrictMode>,
)
