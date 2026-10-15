import { createContext, useContext } from 'react'

/** The signed-in account, inside the sign-in gate; null when sign-in is off. */
export interface AuthSession {
  email: string | null
  signOut: () => void
}

export const AuthSessionContext = createContext<AuthSession | null>(null)

export function useAuthSession(): AuthSession | null {
  return useContext(AuthSessionContext)
}
