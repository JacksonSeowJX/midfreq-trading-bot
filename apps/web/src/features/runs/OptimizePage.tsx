import { RunPage } from './RunPage'
import { OptimizeResultView } from './results/OptimizeResultView'

export default function OptimizePage() {
  return (
    <RunPage
      kind="optimize"
      title="Optimize"
      description="Grid search over a strategy's parameters, or walk-forward: the validation method used throughout the project."
      empty="Pick a strategy and a method on the left and press Run. A walk-forward on a large universe takes several minutes; you can leave and come back."
      renderResult={(job, caps) =>
        job.optimize ? (
          <OptimizeResultView result={job.optimize} objectives={caps?.objectives ?? {}} />
        ) : (
          <p className="text-xs text-muted-foreground">The run finished without a result.</p>
        )
      }
    />
  )
}
