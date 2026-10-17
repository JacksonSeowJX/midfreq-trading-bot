import { InfoIcon } from 'lucide-react'
import { useMemo, type ReactNode } from 'react'
import type { BacktestResult, TradeOut } from '@/api/types'
import { EquityChart } from '@/components/charts/EquityChart'
import { MultiLineChart } from '@/components/charts/MultiLineChart'
import { useChartPalette, useSeriesColors } from '@/components/charts/palette'
import { PriceChart } from '@/components/charts/PriceChart'
import { DataTable, type Column } from '@/components/DataTable'
import { KpiCard, KpiGrid } from '@/components/KpiCard'
import { Section } from '@/components/PageHeader'
import { EmptyState } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { fmtMoney, fmtNum, fmtPct, fmtShortDateTime, tone, toneClass } from '@/lib/format'
import { isBasket } from '../form'
import { benchmarkLabel, headline, paramsText, tradeMarkers, tradeSide, type RunSummary } from '../logic'

function actionBadge(action: string) {
  const side = tradeSide(action)
  return <Badge variant={side === 'buy' ? 'gain' : side === 'sell' ? 'loss' : 'default'}>{action.toUpperCase()}</Badge>
}

function tradeColumns(ccy: string): Column<TradeOut>[] {
  return [
    { id: 'time', header: 'Time', cell: (t) => fmtShortDateTime(t.timestamp), sortValue: (t) => t.timestamp },
    { id: 'symbol', header: 'Symbol', cell: (t) => <span className="font-medium">{t.symbol}</span>, sortValue: (t) => t.symbol },
    { id: 'action', header: 'Action', cell: (t) => actionBadge(t.action), sortValue: (t) => t.action },
    { id: 'qty', header: 'Qty', align: 'right', cell: (t) => fmtNum(t.qty, Number.isInteger(t.qty) ? 0 : 2), sortValue: (t) => t.qty },
    { id: 'price', header: `Price (${ccy})`, align: 'right', cell: (t) => fmtNum(t.price, 2), sortValue: (t) => t.price },
    { id: 'commission', header: 'Commission', align: 'right', cell: (t) => fmtNum(t.commission, 2), sortValue: (t) => t.commission },
    {
      id: 'pnl',
      header: `P&L (${ccy})`,
      align: 'right',
      cell: (t) => <span className={toneClass[tone(t.pnl, 2)]}>{fmtNum(t.pnl, 2, true)}</span>,
      sortValue: (t) => t.pnl,
    },
  ]
}

/**
 * One backtest: headline figures, the price with indicators and trades, the equity curve, the
 * trades. A cross-sectional strategy trades a whole universe, so in place of one stock's price
 * its equity is charted against holding every stock of the universe in equal amounts.
 * `noChart` says why a chart is missing (a saved run keeps neither the price nor buy and hold).
 */
export function BacktestResultView({
  result,
  summary,
  noChart,
}: {
  result: BacktestResult
  summary: RunSummary
  noChart?: ReactNode
}) {
  const ccy = summary.currency
  const h = headline(result.metrics, result.equity)
  const capital = summary.initialCapital ?? result.equity[0]?.v
  const basket = isBasket(result.strategy)
  const chart = basket ? null : result.chart
  const markers = useMemo(() => (chart ? tradeMarkers(result.trades, chart.symbol) : []), [chart, result.trades])
  const equity = useMemo(() => result.equity.map((p) => ({ timestamp: p.t, value: p.v })), [result.equity])
  const trades = useMemo(() => [...result.trades].sort((a, b) => b.timestamp.localeCompare(a.timestamp)), [result.trades])
  const vsCapital = h.finalValue !== null && capital ? h.finalValue - capital : null

  return (
    <div className="space-y-4">
      <KpiGrid>
        <KpiCard label="Return" value={fmtPct(h.returnPct)} tone={tone(h.returnPct, 2)} sub={`${result.strategy} · ${paramsText(result.params)}`} />
        <KpiCard
          label="Final value"
          value={fmtMoney(h.finalValue, ccy)}
          sub={vsCapital !== null ? `${fmtMoney(vsCapital, ccy, 0, true)} on ${fmtMoney(capital, ccy)}` : undefined}
          subTone={tone(vsCapital)}
        />
        <KpiCard label="Sharpe ratio" value={fmtNum(h.sharpe, 2)} hint="Annualized from daily returns (end-of-day equity, 252 trading days), with no risk-free rate" />
        <KpiCard label="Max drawdown" value={fmtPct(h.maxDrawdownPct, 2, false)} hint="Largest fall from a peak of equity" />
        <KpiCard label="Win rate" value={fmtPct(h.winRatePct, 1, false)} hint="Share of closed trades that made money" />
        <KpiCard label="Trades" value={fmtNum(h.trades)} />
        <KpiCard
          label="Profit factor"
          value={h.profitFactorInfinite ? '∞' : fmtNum(h.profitFactor, 2)}
          hint="Gross profit over gross loss; ∞ when no trade lost"
        />
        <KpiCard
          label="Buy and hold"
          value={fmtPct(h.benchmarkPct)}
          tone={tone(h.benchmarkPct, 2)}
          sub={h.alphaPct !== null ? `alpha ${fmtPct(h.alphaPct)}` : undefined}
          subTone={tone(h.alphaPct, 2)}
          hint={
            basket
              ? 'Every stock of the universe bought in equal amounts at the start and held; alpha is the return minus this'
              : 'The same symbols bought at the start and held; alpha is the return minus this'
          }
        />
      </KpiGrid>

      {basket ? (
        <p className="flex items-start gap-1.5 rounded-md border-l-2 border-brand bg-card px-3 py-2 text-xs text-muted-foreground">
          <InfoIcon aria-hidden className="mt-px size-3.5 shrink-0 text-brand" />
          Ranked the {fmtNum(result.symbols.length)} stocks of the universe against each other and traded the basket, so the
          chart is of the equity against holding them all, not of one stock’s price.
        </p>
      ) : result.symbols.length > 1 ? (
        <p className="flex items-start gap-1.5 rounded-md border-l-2 border-brand bg-card px-3 py-2 text-xs text-muted-foreground">
          <InfoIcon aria-hidden className="mt-px size-3.5 shrink-0 text-brand" />
          Traded {result.symbols.join(' and ')}{chart ? `; the price chart shows ${chart.symbol}` : ''}.
        </p>
      ) : null}

      {basket ? (
        <BasketEquity result={result} currency={ccy} capital={capital} noChart={noChart} />
      ) : (
        <Card>
          <CardContent className="space-y-5">
            <Section
              title={chart ? `${chart.symbol}: price, indicators and trades` : 'Price'}
              aside={chart ? `${fmtNum(chart.candles.length)} candles · ${fmtNum(markers.length)} trades marked` : undefined}
            >
              {chart && chart.candles.length ? (
                <PriceChart
                  candles={chart.candles}
                  indicators={chart.indicators}
                  trades={markers}
                  label={`${chart.symbol} candles with ${result.strategy} indicators and trades`}
                />
              ) : (
                <EmptyState title="No price chart for this run" className="py-6">
                  {noChart}
                </EmptyState>
              )}
            </Section>
            <Section title="Equity curve" aside={capital ? `against the initial capital of ${fmtMoney(capital, ccy)}` : undefined}>
              {equity.length ? (
                <EquityChart
                  points={equity}
                  currency={ccy}
                  baseline={capital}
                  baselineLabel="initial capital"
                  pointMarkers={equity.length <= 60}
                  label="Equity curve of the backtest"
                />
              ) : (
                <EmptyState title="No equity curve" className="py-6" />
              )}
            </Section>
          </CardContent>
        </Card>
      )}

      <Section title="Trades" aside={`${fmtNum(trades.length)} trades, newest first`}>
        <DataTable
          label="Backtest trades"
          columns={tradeColumns(ccy)}
          rows={trades}
          rowKey={(t, i) => `${t.timestamp}-${t.symbol}-${i}`}
          maxHeight={360}
          empty={<EmptyState title="The strategy placed no trades in this period" className="py-6" />}
        />
      </Section>
    </div>
  )
}

/**
 * A basket's equity against holding every stock of its universe in equal amounts (the
 * backtester's benchmark, so the gap between the two lines is the alpha). A saved run keeps no
 * benchmark curve: then the equity alone, against the starting capital.
 */
function BasketEquity({
  result,
  currency,
  capital,
  noChart,
}: {
  result: BacktestResult
  currency: string
  capital: number | undefined
  noChart?: ReactNode
}) {
  const colors = useSeriesColors()
  const palette = useChartPalette()
  const held = result.benchmark?.equity.length ? result.benchmark : null
  const equity = useMemo(() => result.equity.map((p) => ({ timestamp: p.t, value: p.v })), [result.equity])
  return (
    <Card>
      <CardContent>
        <Section
          title="Equity against holding the universe"
          aside={capital ? `starting from ${fmtMoney(capital, currency)}` : undefined}
          description={held ? `Dashed: ${benchmarkLabel(held)}, the same capital invested at the first candle and held.` : undefined}
        >
          {held && equity.length ? (
            <MultiLineChart
              lines={[
                { name: result.strategy, color: colors[0]!, points: result.equity },
                { name: benchmarkLabel(held), color: palette.muted, points: held.equity, dashed: true },
              ]}
              currency={currency}
              baseline={capital}
              label={`Equity of ${result.strategy} against holding the ${fmtNum(held.symbols.length)} stocks in equal amounts`}
            />
          ) : equity.length ? (
            <div className="space-y-2">
              <EquityChart
                points={equity}
                currency={currency}
                baseline={capital}
                baselineLabel="initial capital"
                pointMarkers={equity.length <= 60}
                label="Equity curve of the backtest"
              />
              {noChart ? <p className="text-xs text-muted-foreground">{noChart}</p> : null}
            </div>
          ) : (
            <EmptyState title="No equity curve" className="py-6" />
          )}
        </Section>
      </CardContent>
    </Card>
  )
}
