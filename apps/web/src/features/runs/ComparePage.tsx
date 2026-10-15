import { RunPage } from './RunPage'
import { requestedStrategies, runSummary } from './logic'
import { CompareResultView } from './results/CompareResultView'

export default function ComparePage() {
  return (
    <RunPage
      kind="compare"
      title="Compare"
      description="Several strategies on the same stock, period and costs, side by side."
      empty="Pick two to eight strategies on the left and press Compare strategies. Earlier comparisons are listed below."
      renderResult={(job) =>
        job.compare ? (
          <CompareResultView result={job.compare} summary={runSummary(job)} requested={requestedStrategies(job.request)} />
        ) : (
          <p className="text-xs text-muted-foreground">The run finished without a result.</p>
        )
      }
    />
  )
}
