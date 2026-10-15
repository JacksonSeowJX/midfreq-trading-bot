import { useForwardTest } from '@/api/queries'
import type { ForwardTest } from '@/api/types'
import { EquityChart } from '@/components/charts/EquityChart'
import { KpiCard, KpiGrid, KpiGridSkeleton } from '@/components/KpiCard'
import { SourcePanel } from '@/components/SourcePanel'
import { ChartSkeleton, EmptyState, QueryState } from '@/components/states'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  currencyFor,
  fmtCalendarDate,
  fmtDateTime,
  fmtMoney,
  fmtNum,
  fmtPct,
  tone,
} from '@/lib/format'
import type { Market } from '@/lib/preferences'
import { HoldingsTable, TradesTable } from './ForwardTestTables'

/** The seven headline numbers of the forward test. */
export function ForwardTestKpis({ data, currency }: { data: ForwardTest; currency: string }) {
  return (
    <KpiGrid>
      <KpiCard
        label="Value now"
        value={fmtMoney(data.value, currency)}
        sub={`of ${fmtMoney(data.capital, currency)} allocated`}
      />
      <KpiCard
        label="Profit / loss"
        value={fmtMoney(data.pnl, currency, 0, true)}
        tone={tone(data.pnl)}
        hint="The change in the broker's reported account total since the test began: after fees, and including any dividends the broker credits."
      />
      <KpiCard label="P&L %" value={fmtPct(data.pnl_pct)} tone={tone(data.pnl_pct, 2)} sub="of the capital allocated" />
      <KpiCard
        label="Banked from sales"
        value={fmtMoney(data.realised, currency, 0, true)}
        tone={tone(data.realised)}
        hint="Profit on positions already sold, before fees. The rest of the profit or loss is still in the stocks held."
      />
      <KpiCard label="Sessions" value={fmtNum(data.sessions)} sub={`since ${fmtCalendarDate(data.started)}`} />
      <KpiCard
        label="Rebalances"
        value={fmtNum(data.rebalances)}
        hint="How many times the strategy has re-ranked its stocks and traded."
      />
      <KpiCard
        label="Orders filled"
        value={fmtNum(data.orders)}
        sub={data.first_trade ? `first ${fmtCalendarDate(data.first_trade)}` : 'no trade yet'}
      />
    </KpiGrid>
  )
}

function ForwardTestBody({ data }: { data: ForwardTest }) {
  const ccy = currencyFor(data.market)
  const capital = fmtMoney(data.capital, ccy)
  return (
    <>
      <ForwardTestKpis data={data} currency={ccy} />
      <p className="text-xs leading-relaxed text-muted-foreground">
        Profit / loss is the change in the broker&apos;s reported account total since the test began, so it is after
        fees and includes any dividends the broker credits.
        {data.market === 'US'
          ? ` The paper account holds about US$1,000,000; the strategy is capped at ${capital} to match the backtests.`
          : ` The strategy is capped at ${capital}.`}{' '}
        Deployed {fmtCalendarDate(data.started)}
        {data.first_trade ? `, first trade ${fmtCalendarDate(data.first_trade)}` : ''}. As of{' '}
        {data.as_of ? fmtDateTime(data.as_of) : 'the last upload'}, the last session the VM has uploaded.
      </p>
      {data.equity.length >= 2 ? (
        <EquityChart
          points={data.equity}
          currency={ccy}
          baseline={data.capital}
          baselineLabel={`${capital} allocated`}
          height={260}
          label={`Forward-test value over time, from ${fmtMoney(data.equity[0]?.value, ccy)} to ${fmtMoney(data.value, ccy)}`}
        />
      ) : (
        <EmptyState title="Not enough recorded points for an equity curve yet" className="py-5" />
      )}
      <Tabs defaultValue="trades">
        <TabsList>
          <TabsTrigger value="trades">Trades ({data.trades.length})</TabsTrigger>
          <TabsTrigger value="holdings">Holdings at the last recorded candle ({data.holdings.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="trades">
          <TradesTable trades={data.trades} currency={ccy} />
        </TabsContent>
        <TabsContent value="holdings">
          <HoldingsTable holdings={data.holdings} currency={ccy} />
        </TabsContent>
      </Tabs>
    </>
  )
}

/** The bot's own record of the forward test, from its session logs. */
export function ForwardTestPanel({ market }: { market: Market }) {
  const ft = useForwardTest(market)
  const title = market === 'US' ? 'S&P 100 forward test' : 'Hong Kong forward test'
  return (
    <SourcePanel
      source="bot"
      title={title}
      description={
        ft.data
          ? `The ${fmtMoney(ft.data.capital, currencyFor(market))} the strategy trades: its value, trades and holdings from the session logs.`
          : 'The capital the strategy trades: its value, trades and holdings from the session logs.'
      }
      aside={ft.data?.as_of ? `Last session ${fmtDateTime(ft.data.as_of)}` : undefined}
    >
      <QueryState
        query={ft}
        skeleton={
          <>
            <KpiGridSkeleton count={7} />
            <ChartSkeleton height={260} />
          </>
        }
        notFound={
          <EmptyState title={`No ${market} forward test recorded`}>
            No forward-test session has been recorded for this market.
            {market === 'US' ? (
              <>
                {' '}
                It runs with <code className="font-mono">./ops/run_sp100_forward_test.sh</code>.
              </>
            ) : null}{' '}
            Session logs from the VM reach this machine with <code className="font-mono">./ops/fetch_live_logs.sh</code>.
          </EmptyState>
        }
      >
        {(d) => <ForwardTestBody data={d} />}
      </QueryState>
    </SourcePanel>
  )
}
