"""
Sign-in for the hosted dashboard: Firebase Authentication (Google Identity Platform) ID tokens,
checked on the server for every route.

Off by default (Settings.auth == "off", a laptop or the VM): nothing changes. On
(MIDFREQ_AUTH=firebase, the Cloud Run service), every route needs
`Authorization: Bearer <Firebase ID token>` except PUBLIC_ROUTES: the health check, the sign-in
configuration the web app starts from, and the web app's own files (which hold no data).

    no token, or a token that does not verify      401  (WWW-Authenticate: Bearer)
    a verified token whose email is not verified   403
    a verified email not on the access list        403  "awaiting approval"
    Google's signing keys cannot be fetched        503

The access list is Settings.auth_allowed_emails and auth_allowed_domains (environment variables
on the service, never committed): an email is allowed when it is listed, or when the part after
its @ is exactly one of the domains (case-insensitive; a subdomain must be listed itself).

Passwords never reach this service: Firebase holds the accounts, the web app signs in with the
Firebase JS SDK and sends the ID token it gets; here the token's signature (Google's public
keys, cached for as long as Google's Cache-Control allows), audience and issuer (the project),
expiry and subject are checked with google-auth, which the gcs extra already installs.
"""
import logging
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Tuple

from fastapi import Depends, HTTPException, Request

from midfreq.api.settings import Settings, get_settings
from midfreq.api.web import WEB_ROUTE

log = logging.getLogger(__name__)

# Route paths (as declared) anyone may call. Everything else is checked, so a route added later
# is private unless it is listed here.
PUBLIC_ROUTES = frozenset({"/api/health", "/api/auth/config", WEB_ROUTE})
# Routes that need a valid token but not access: the web app asks who is signed in, and whether
# they are allowed, before showing anything.
IDENTITY_ROUTES = frozenset({"/api/auth/me"})

CLOCK_SKEW_SECONDS = 10       # tolerance for iat and exp between Google's clock and this one
CERTS_TIMEOUT_SECONDS = 10    # fetching Google's public keys
CERTS_TTL_DEFAULT = 3600      # when Google's response carries no max-age
CERTS_TTL_MAX = 24 * 3600


@dataclass(frozen=True)
class Identity:
    """Who a verified token says the caller is."""
    uid: str
    email: Optional[str]
    email_verified: bool


class InvalidToken(Exception):
    """The token is malformed, expired, signed by someone else or meant for another project."""


class VerifierUnavailable(Exception):
    """The token cannot be checked now (Google's keys cannot be fetched, google-auth missing)."""


class TokenVerifier(Protocol):
    def __call__(self, token: str) -> Identity: ...


# ── the access list ─────────────────────────────────────────────────────────

def is_allowed(email: Optional[str], settings: Settings) -> bool:
    """Whether `email` is on the access list: listed itself, or its domain listed exactly."""
    if not email:
        return False
    e = email.strip().lower()
    if e in {a.strip().lower() for a in settings.auth_allowed_emails}:
        return True
    local, at, domain = e.rpartition("@")
    if not at or not local or not domain:
        return False
    return domain in {d.strip().lower().lstrip("@") for d in settings.auth_allowed_domains}


def access_denied(identity: Identity, settings: Settings) -> Optional[str]:
    """Why a verified caller may not read the dashboard, or None when they may."""
    if not identity.email:
        return "This sign-in has no email address; sign in with an email account."
    if not identity.email_verified:
        return (f"Email address not verified: open the link sent to {identity.email}, "
                "then sign in again.")
    if not is_allowed(identity.email, settings):
        return (f"Awaiting approval: {identity.email} is signed in but not yet on this "
                "dashboard's access list.")
    return None


# ── verifying Firebase ID tokens ────────────────────────────────────────────

_MAX_AGE = re.compile(r"max-age=(\d+)")


class CachedGetRequest:
    """A google-auth transport request that keeps successful GET responses for their
    Cache-Control max-age (bounded), so Google's public keys are fetched about once an hour
    instead of on every request. Anything else goes straight to `inner`."""

    def __init__(self, inner: Callable[..., Any], clock: Callable[[], float] = time.monotonic,
                 timeout: float = CERTS_TIMEOUT_SECONDS):
        self._inner, self._clock, self._timeout = inner, clock, timeout
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        timeout = timeout or self._timeout
        if method.upper() != "GET" or body is not None:
            return self._inner(url, method=method, body=body, headers=headers, timeout=timeout, **kwargs)
        now = self._clock()
        with self._lock:
            hit = self._cache.get(url)
        if hit is not None and hit[0] > now:
            return hit[1]
        response = self._inner(url, method="GET", headers=headers, timeout=timeout, **kwargs)
        if response.status == 200:
            ttl = self._ttl(response.headers)
            if ttl > 0:
                with self._lock:
                    self._cache[url] = (now + ttl, response)
        return response

    @staticmethod
    def _ttl(headers: Mapping[str, str]) -> int:
        cache_control = ""
        for k, v in (headers or {}).items():
            if k.lower() == "cache-control":
                cache_control = v.lower()
        if "no-store" in cache_control or "no-cache" in cache_control:
            return 0
        m = _MAX_AGE.search(cache_control)
        return min(int(m.group(1)), CERTS_TTL_MAX) if m else CERTS_TTL_DEFAULT


def identity_from_claims(claims: Mapping[str, Any], project_id: str) -> Identity:
    """The caller, from a token google-auth has verified (signature, audience, expiry), after
    the checks it leaves to us: the issuer is this project and there is a subject."""
    if claims.get("aud") != project_id:
        raise InvalidToken("token is for another project")
    if claims.get("iss") != f"https://securetoken.google.com/{project_id}":
        raise InvalidToken("token was not issued by this project's sign-in")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 128:
        raise InvalidToken("token has no subject")
    email = claims.get("email")
    return Identity(uid=sub, email=email if isinstance(email, str) and email else None,
                    email_verified=claims.get("email_verified") is True)


class FirebaseVerifier:
    """Checks Firebase ID tokens for one project with google-auth
    (google.oauth2.id_token.verify_firebase_token). google-auth is imported on first use, so a
    deployment with sign-in off never needs it."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._request: Optional[CachedGetRequest] = None
        self._lock = threading.Lock()

    def _transport(self) -> CachedGetRequest:
        with self._lock:
            if self._request is None:
                from google.auth.transport.requests import Request as GoogleRequest
                self._request = CachedGetRequest(GoogleRequest())
            return self._request

    def __call__(self, token: str) -> Identity:
        try:
            from google.auth import exceptions as google_errors
            from google.oauth2 import id_token
            transport = self._transport()
        except ImportError as e:
            raise VerifierUnavailable("google-auth is not installed (install midfreq-api[gcs])") from e
        try:
            claims = id_token.verify_firebase_token(token, transport, audience=self.project_id,
                                                    clock_skew_in_seconds=CLOCK_SKEW_SECONDS)
        except google_errors.TransportError as e:
            raise VerifierUnavailable(f"cannot fetch Google's signing keys: {e}") from e
        except (ValueError, google_errors.GoogleAuthError) as e:
            raise InvalidToken(str(e)) from e
        if not isinstance(claims, Mapping):
            raise InvalidToken("token has no claims")
        return identity_from_claims(claims, self.project_id)


@lru_cache(maxsize=None)
def _firebase_verifier(project_id: str) -> FirebaseVerifier:
    # one per project and process, so the cached keys are shared by every request
    return FirebaseVerifier(project_id)


# ── the dependencies ────────────────────────────────────────────────────────

def get_token_verifier(settings: Settings = Depends(get_settings)) -> Optional[TokenVerifier]:
    """How ID tokens are checked; None when sign-in is off. Tests replace it with
    app.dependency_overrides[get_token_verifier]."""
    if not settings.auth_enabled:
        return None
    return _firebase_verifier(settings.auth_project)


def _bearer(request: Request) -> Optional[str]:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


def identify(request: Request, verifier: Optional[TokenVerifier]) -> Identity:
    """The caller's verified identity (once per request); 401 without a valid token."""
    known = getattr(request.state, "identity", None)
    if isinstance(known, Identity):
        return known
    token = _bearer(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Sign in required: no Firebase ID token was sent.",
                            headers={"WWW-Authenticate": "Bearer"})
    if verifier is None:
        raise HTTPException(status_code=503, detail="Sign-in is not configured on this server.")
    try:
        identity = verifier(token)
    except InvalidToken as e:
        log.info("rejected an ID token: %.200s", e)   # a malformed token may be quoted in the message
        raise HTTPException(status_code=401, detail="Sign-in expired or invalid; sign in again.",
                            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    except VerifierUnavailable as e:
        log.warning("cannot verify ID tokens: %s", e)
        raise HTTPException(status_code=503, detail="Sign-in cannot be checked right now; try again shortly.")
    request.state.identity = identity
    return identity


def require_access(request: Request, settings: Settings = Depends(get_settings),
                   verifier: Optional[TokenVerifier] = Depends(get_token_verifier)) -> None:
    """The app-wide dependency (create_app): with sign-in on, every route but PUBLIC_ROUTES
    needs a signed-in, verified, allowed caller. It runs before the route's own dependencies,
    so a refused request reads nothing."""
    if not settings.auth_enabled:
        return
    route = getattr(request.scope.get("route"), "path", None)
    if route in PUBLIC_ROUTES:
        return
    identity = identify(request, verifier)
    if route in IDENTITY_ROUTES:
        return
    reason = access_denied(identity, settings)
    if reason is not None:
        raise HTTPException(status_code=403, detail=reason,
                            headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'})
