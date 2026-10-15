import { RunPage } from './RunPage'
import { runSummary } from './logic'
import { BacktestResultView } from './results/BacktestResultView'

export default function BacktestPage() {
  return (
    <RunPage
      kind="backtest"
      title="Backtest"
      description="One strategy over one period: the price with its indicators and trades, the equity curve against the starting capital, and every trade."
      empty="Set up a run on the left and press Run backtest. It runs in the API in the background; earlier runs are listed below."
      renderResult={(job) =>
        job.backtest ? (
          <BacktestResultView result={job.backtest} summary={runSummary(job)} />
        ) : (
          <p className="text-xs text-muted-foreground">The run finished without a result.</p>
        )
      }
    />
  )
}
