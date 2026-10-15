import { CircleAlertIcon, HourglassIcon, LaptopIcon } from 'lucide-react'
import { isApiError } from '@/api/client'
import type { JobKind } from '@/api/types'
import { describeError, type ErrorCopy } from '@/components/errorCopy'
import { KIND_LABEL } from './logic'

export const LOCAL_API_CMD = 'uv run uvicorn midfreq.api.main:app --port 8000'
export const LOCAL_WEB_CMD = 'cd apps/web && npm run dev'

/** Why a kind is not available here. */
function notAllowedCopy(kind: JobKind): string {
  return kind === 'optimize'
    ? 'Optimize runs only on a local install: a grid search or walk-forward runs hundreds of backtests, more than this hosted dashboard allows.'
    : `This deployment does not run ${KIND_LABEL[kind].toLowerCase()} jobs; a local install does.`
}

/** Why a run could not be started: not allowed here (403), bad settings (422), busy (429), or the usual. */
export function describeStartError(error: unknown, kind: JobKind, maxConcurrent?: number): ErrorCopy {
  if (isApiError(error)) {
    if (error.status === 403) {
      return {
        icon: LaptopIcon,
        title: kind === 'optimize' ? 'Optimize runs only on a local install' : `${KIND_LABEL[kind]} is not allowed here`,
        tone: 'warning',
        body: localInstallBody(kind),
      }
    }
    if (error.status === 422) {
      return {
        icon: CircleAlertIcon,
        title: 'Check the settings',
        tone: 'warning',
        body: 'The API rejected some of the settings. Each problem is marked in the form.',
      }
    }
    if (error.status === 429) {
      const n = maxConcurrent ?? 1
      return {
        icon: HourglassIcon,
        title: 'A run is already in progress',
        tone: 'warning',
        body: `This deployment runs ${n === 1 ? 'one at a time' : `${n} at a time`}. Wait for the current run to finish and try again.`,
      }
    }
  }
  return describeError(error)
}

/**
 * Why this kind is not available here, and how to run the dashboard on one's own machine,
 * where every run kind is allowed.
 */
export function localInstallBody(kind: JobKind) {
  return (
    <>
      <p>{notAllowedCopy(kind)}</p>
      <div className="mt-2 text-left">
        <p>From the repository root, start the API and the dashboard, then open http://localhost:5173:</p>
        <pre className="mt-2 overflow-x-auto rounded-md border bg-muted px-3 py-2 font-mono text-xs text-foreground">
          {LOCAL_API_CMD}
          {'\n'}
          {LOCAL_WEB_CMD}
        </pre>
      </div>
    </>
  )
}
