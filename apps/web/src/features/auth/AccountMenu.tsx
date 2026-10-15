import { LogOutIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { useAuthSession } from './session'

/** The signed-in account and Sign out, in the top bar; nothing when sign-in is off. */
export function AccountMenu() {
  const session = useAuthSession()
  if (!session) return null
  const who = session.email ?? 'this account'
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <span className="hidden max-w-48 truncate text-xs text-muted-foreground lg:inline" title={who}>
        {who}
      </span>
      <Tooltip content={`Signed in as ${who}`}>
        <Button variant="ghost" size="sm" onClick={session.signOut}>
          <LogOutIcon aria-hidden />
          <span className="sr-only md:not-sr-only">Sign out</span>
        </Button>
      </Tooltip>
    </div>
  )
}
