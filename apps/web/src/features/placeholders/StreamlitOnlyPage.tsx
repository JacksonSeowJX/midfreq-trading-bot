import { MonitorPlayIcon } from 'lucide-react'
import { Link } from 'react-router'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/states'

const STREAMLIT_CMD = 'uv run streamlit run apps/dashboard/app.py'

/** A view that still runs in the Streamlit app, with how to open it there. */
export function StreamlitOnlyPage({ title, what }: { title: string; what: string }) {
  return (
    <>
      <PageHeader title={title} description={what} />
      <EmptyState icon={MonitorPlayIcon} title={`${title} still runs in the Streamlit app`} className="py-12">
        <p>
          It runs the backtest engine in-process on the local price cache, so it stays in the Streamlit dashboard for
          now and will move here later. Start it from the repository root:
        </p>
        <pre className="mt-3 rounded-md border bg-muted px-3 py-2 text-left font-mono text-xs text-foreground">
          {STREAMLIT_CMD}
        </pre>
        <p className="mt-3">
          The <Link className="text-primary hover:underline" to="/live">Live</Link>,{' '}
          <Link className="text-primary hover:underline" to="/overview">Overview</Link> and{' '}
          <Link className="text-primary hover:underline" to="/research">Research</Link> views already work here.
        </p>
      </EmptyState>
    </>
  )
}

export function NotFoundPage() {
  return (
    <EmptyState title="No such page" className="py-12">
      <Link className="text-primary hover:underline" to="/live">
        Go to the Live view
      </Link>
    </EmptyState>
  )
}
