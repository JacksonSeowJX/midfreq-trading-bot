import { BookOpenIcon, PlayIcon, XIcon } from 'lucide-react'
import { useId, useState } from 'react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { safeStorage } from '@/lib/utils'
import { WELCOME_DISMISSED_KEY } from './logic'

/**
 * The first-visit introduction: what the dashboard is, and the two places to start. Closing
 * it, or following either link, remembers that it was seen (when storage is available;
 * otherwise it stays closed for this visit).
 */
export function WelcomeCard() {
  const titleId = useId()
  const [dismissed, setDismissed] = useState(() => safeStorage.get(WELCOME_DISMISSED_KEY) === '1')
  if (dismissed) return null
  const dismiss = () => {
    safeStorage.set(WELCOME_DISMISSED_KEY, '1')
    setDismissed(true)
  }
  return (
    <section
      aria-labelledby={titleId}
      className="mb-4 flex gap-3 rounded-lg border border-l-[3px] border-l-brand bg-card px-4 py-3 shadow-[0_1px_2px_rgb(16_24_40/0.04)]"
    >
      <div className="min-w-0 flex-1 space-y-2">
        <h2 id={titleId} className="text-sm font-semibold">
          Welcome to the midfreq trading dashboard
        </h2>
        <p className="max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
          It shows the bot’s live paper trading as both the broker and the bot record it, every research result held
          to the project’s validation standard, and backtests you can run yourself. The guide walks through each page
          step by step and reproduces the project’s main findings.
        </p>
        <div className="flex flex-wrap gap-2 pt-0.5">
          <Button asChild size="sm">
            <Link to="/guide" onClick={dismiss}>
              <BookOpenIcon aria-hidden /> Open the guide
            </Link>
          </Button>
          {/* the live forward test's settings (features/runs/presets.ts), not run until Run is pressed */}
          <Button asChild size="sm" variant="outline">
            <Link to="/backtest?preset=forward-test" onClick={dismiss}>
              <PlayIcon aria-hidden /> Try a backtest
            </Link>
          </Button>
        </div>
      </div>
      <Button variant="ghost" size="icon-sm" aria-label="Dismiss the welcome" onClick={dismiss} className="-mr-1 -mt-0.5">
        <XIcon aria-hidden />
      </Button>
    </section>
  )
}
