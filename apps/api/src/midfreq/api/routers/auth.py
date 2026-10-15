"""
Sign-in: what the web app needs to start Firebase Authentication, and who is signed in.

/api/auth/config is public (the web app reads it before anyone has signed in); the Firebase web
API key in it is public by design (it names the project; it grants nothing). /api/auth/me needs
a valid ID token but not access, so a signed-in account that is not allowed yet can be told so.
See midfreq.api.auth.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request

from midfreq.api import schemas as s
from midfreq.api.auth import TokenVerifier, access_denied, get_token_verifier, identify
from midfreq.api.settings import Settings, get_settings

router = APIRouter(tags=["auth"])


@router.get("/api/auth/config", response_model=s.AuthConfig)
def auth_config(settings: Settings = Depends(get_settings)):
    """Whether this server requires sign-in, and the Firebase web configuration to sign in with."""
    if not settings.auth_enabled:
        return s.AuthConfig(enabled=False)
    return s.AuthConfig(enabled=True, provider="firebase", firebase=s.FirebaseWebConfig(
        apiKey=settings.auth_web_api_key, authDomain=settings.auth_web_domain,
        projectId=settings.auth_project))


@router.get("/api/auth/me", response_model=s.AuthMe,
            responses={401: {"description": "no valid ID token"}})
def auth_me(request: Request, settings: Settings = Depends(get_settings),
            verifier: Optional[TokenVerifier] = Depends(get_token_verifier)):
    """The signed-in account and whether it may read the dashboard (with sign-in off: anyone may)."""
    if not settings.auth_enabled:
        return s.AuthMe(email=None, email_verified=False, allowed=True)
    identity = identify(request, verifier)
    reason = access_denied(identity, settings)
    return s.AuthMe(email=identity.email, email_verified=identity.email_verified,
                    allowed=reason is None, reason=reason)
