import { CircleCheckIcon, CircleDotIcon, CircleXIcon } from 'lucide-react'
import type { SessionSummary } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { sessionStatus } from './logic'

export function StatusBadge({ session }: { session: SessionSummary }) {
  const status = sessionStatus(session)
  if (status === 'crashed')
    return (
      <Badge variant="loss">
        <CircleXIcon aria-hidden /> Crashed
      </Badge>
    )
  if (status === 'open')
    return (
      <Badge variant="warning">
        <CircleDotIcon aria-hidden /> No end yet
      </Badge>
    )
  return (
    <Badge variant="outline">
      <CircleCheckIcon aria-hidden /> Ended
    </Badge>
  )
}
