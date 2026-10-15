import { CircleCheckIcon, CircleXIcon } from 'lucide-react'
import type { SearchCorrection } from '@/api/types'
import { KpiCard, KpiGrid } from '@/components/KpiCard'
import { fmtNum, fmtPct, fmtShare } from '@/lib/format'
import { pboOf } from './logic'
import { RealityCheckBars } from './OverviewCharts'

const LUCK_HINT =
  'How often the best of the same number of combinations with no edge scored as well as the real winner.'

/**
 * "Corrected for the search": White's Reality Check per configuration and the Probability of
 * Backtest Overfitting, the tests applied after the two-configuration standard.
 */
export function SearchCorrectionView({ sc }: { sc: SearchCorrection }) {
  const passed = sc.configs.length > 0 && sc.configs.every((c) => c.p_value < 0.05)
  const pbo = pboOf(sc)
  const nCandidates = sc.configs[0]?.n_candidates
  return (
    <div className="space-y-4">
      <p className="max-w-4xl text-[13px] leading-relaxed text-muted-foreground">
        Many combinations were tried, and the best of many looks good by luck alone.{' '}
        <strong className="text-foreground">White&apos;s Reality Check</strong> asks how often the best of the same
        number of combinations with <em>no edge at all</em> would score as well as the real winner.
      </p>
      <KpiGrid className="lg:grid-cols-5">
        {sc.configs.map((c) => (
          <KpiCard
            key={c.name}
            label={`By luck · ${c.n_windows ?? '?'} windows`}
            value={fmtShare(c.p_value)}
            sub={c.name}
            hint={LUCK_HINT}
          />
        ))}
        <KpiCard label="Needed to pass" value="under 5%" />
        <KpiCard
          label="Verdict"
          value={
            <span className="inline-flex items-center gap-1.5">
              {passed ? <CircleCheckIcon className="size-5" aria-hidden /> : <CircleXIcon className="size-5" aria-hidden />}
              {passed ? 'Passes' : 'Does not pass'}
            </span>
          }
          tone={passed ? 'gain' : 'loss'}
        />
        {pbo ? (
          <KpiCard
            label="PBO · S&P 100"
            value={fmtNum(pbo.pbo, 3)}
            sub={pbo.nConfigs ? `${pbo.nConfigs} configurations` : undefined}
            hint="Probability of Backtest Overfitting: how often the settings the search picks land in the bottom half on unseen data. Pure noise gives 0.5."
          />
        ) : null}
      </KpiGrid>
      <div className="grid gap-4 xl:grid-cols-2">
        {sc.configs.map((c) => (
          <figure key={c.name} className="min-w-0 rounded-lg border bg-card p-3">
            <figcaption className="mb-1 text-[13px] font-semibold">{c.name}</figcaption>
            <RealityCheckBars config={c} />
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              Winner: <strong className="text-foreground">{c.best}</strong> at {fmtPct(c.best_mean)} per window. Luck
              matched or beat it in {fmtShare(c.p_value)} of {sc.n_boot ? fmtNum(sc.n_boot) : 'the'} redraws.
              {c.naive_p_value !== null && c.naive_p_value !== undefined
                ? ` Tested alone, as if nothing else had been tried, that figure would be ${fmtShare(c.naive_p_value)}.`
                : ''}
            </p>
          </figure>
        ))}
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        The green bar is the winner. The dotted red line is where luck alone reaches 5% of the time (the 95th
        percentile of the best no-edge candidate); to pass, the winner would have to sit to the right of it.
        {nCandidates ? ` ${nCandidates} combinations` : ' The combinations'}: cross-sectional reversal and momentum on
        each of 4 stock universes, all over the same 3 years ending {sc.end}. Source:{' '}
        <code className="font-mono">results/{sc.file}</code>.
      </p>
    </div>
  )
}
