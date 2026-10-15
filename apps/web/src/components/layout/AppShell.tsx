import { Suspense } from 'react'
import { Outlet } from 'react-router'
import { KpiGridSkeleton } from '@/components/KpiCard'
import { TableSkeleton } from '@/components/states'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

function PageFallback() {
  return (
    <div className="space-y-4">
      <KpiGridSkeleton />
      <TableSkeleton rows={8} />
    </div>
  )
}

export function AppShell() {
  return (
    <div className="flex h-full min-h-0">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1600px] p-4 lg:p-5">
            <Suspense fallback={<PageFallback />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>
    </div>
  )
}
