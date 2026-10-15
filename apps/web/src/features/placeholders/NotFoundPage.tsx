import { Link } from 'react-router'
import { EmptyState } from '@/components/states'

export function NotFoundPage() {
  return (
    <EmptyState title="No such page" className="py-12">
      <Link className="text-primary hover:underline" to="/live">
        Go to the Live view
      </Link>
    </EmptyState>
  )
}
