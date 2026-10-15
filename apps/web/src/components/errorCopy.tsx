import {
  CircleAlertIcon,
  ConstructionIcon,
  InboxIcon,
  ServerOffIcon,
  TriangleAlertIcon,
  type LucideIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { isApiError } from '@/api/client'

export const API_START_HINT = 'uv run uvicorn midfreq.api.main:app --reload'

export interface ErrorCopy {
  icon: LucideIcon
  title: string
  body: ReactNode
  tone: 'warning' | 'loss'
}

/** What to tell the reader about a failed call. */
export function describeError(error: unknown): ErrorCopy {
  if (isApiError(error)) {
    if (error.unreachable) {
      return {
        icon: ServerOffIcon,
        title: 'API unreachable',
        tone: 'loss',
        body: (
          <>
            The dashboard could not reach the API at <code className="font-mono">/api</code>. Start it with{' '}
            <code className="font-mono">{API_START_HINT}</code> (port 8000).
          </>
        ),
      }
    }
    if (error.status === 501) {
      return {
        icon: ConstructionIcon,
        title: 'Not available yet',
        tone: 'warning',
        body: 'The API does not implement this endpoint yet (501).',
      }
    }
    if (error.status === 503) {
      return {
        icon: TriangleAlertIcon,
        title: 'Unavailable right now',
        tone: 'warning',
        body: error.detail ?? 'The source cannot be read at the moment (503).',
      }
    }
    if (error.status === 404) {
      return { icon: InboxIcon, title: 'Not found', tone: 'warning', body: error.detail ?? 'Nothing at this address (404).' }
    }
    return {
      icon: CircleAlertIcon,
      title: `API error ${error.status}`,
      tone: 'loss',
      body: error.detail ?? 'The API returned an error.',
    }
  }
  return {
    icon: CircleAlertIcon,
    title: 'Something went wrong',
    tone: 'loss',
    body: error instanceof Error ? error.message : String(error),
  }
}
