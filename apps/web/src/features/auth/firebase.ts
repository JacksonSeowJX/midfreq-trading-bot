/**
 * The Firebase JS SDK (modular, auth only) behind AuthBackend. Imported only through
 * loadAuthBackend, so it is its own chunk and loads only when sign-in is on.
 *
 * Google is the only provider: the account chooser opens in a popup, and where the browser
 * blocks popups the whole page goes to Google and comes back (getRedirectResult then reports a
 * failure, if there was one). The session persists in the browser (IndexedDB) until sign-out.
 */
import { getApps, initializeApp } from 'firebase/app'
import {
  getAuth,
  getRedirectResult,
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithPopup,
  signInWithRedirect,
  signOut,
} from 'firebase/auth'
import { errorCode, type AuthBackend, type FirebaseWebConfig } from './backend'

const APP_NAME = 'midfreq-dashboard'
const POPUP_UNAVAILABLE = new Set(['auth/popup-blocked', 'auth/operation-not-supported-in-this-environment'])

export function createFirebaseBackend(config: FirebaseWebConfig): AuthBackend {
  const app = getApps().find((a) => a.name === APP_NAME) ?? initializeApp(config, APP_NAME)
  const auth = getAuth(app)
  auth.useDeviceLanguage()
  const google = new GoogleAuthProvider()
  // always show the chooser, so a visitor signed in to the wrong account can pick another
  google.setCustomParameters({ prompt: 'select_account' })

  return {
    onUserChanged: (callback) =>
      onAuthStateChanged(auth, (user) => callback(user ? { uid: user.uid, email: user.email } : null)),
    redirectError: () =>
      getRedirectResult(auth).then(
        () => undefined,
        (e: unknown) => e,
      ),
    async signInWithGoogle() {
      try {
        await signInWithPopup(auth, google)
      } catch (e) {
        const code = errorCode(e)
        if (code && POPUP_UNAVAILABLE.has(code)) {
          await signInWithRedirect(auth, google)
          return
        }
        throw e
      }
    },
    signOut: () => signOut(auth),
    idToken: async (forceRefresh = false) => (auth.currentUser ? auth.currentUser.getIdToken(forceRefresh) : null),
  }
}
