import { RefreshCwIcon } from 'lucide-react'
import { LIVE_REFRESH_MS } from '@/api/queries'
import { PageHeader } from '@/components/PageHeader'
import { SourceTag } from '@/components/SourcePanel'
import { MARKET_LABEL, useMarket } from '@/lib/preferences'
import { AccountPanel } from './AccountPanel'
import { ForwardTestPanel } from './ForwardTestPanel'
import { SessionsPanel } from './SessionsPanel'
import { WelcomeCard } from './WelcomeCard'

export default function LivePage() {
  const { market } = useMarket()
  return (
    <>
      <WelcomeCard />
      <PageHeader
        title={`Live trading · ${MARKET_LABEL[market]}`}
        description={
          <span className="inline-flex flex-wrap items-center gap-1.5">
            Two independent sources, never merged, so they can be reconciled:
            <SourceTag source="broker" /> what the broker reports, and <SourceTag source="bot" /> what the bot recorded.
          </span>
        }
        actions={
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <RefreshCwIcon className="size-3.5" aria-hidden />
            Auto-refresh every {LIVE_REFRESH_MS / 1000} s
          </span>
        }
      />
      <div className="grid gap-4 2xl:grid-cols-2">
        <AccountPanel />
        <ForwardTestPanel market={market} />
      </div>
      <div className="mt-4">
        <SessionsPanel market={market} />
      </div>
    </>
  )
}
