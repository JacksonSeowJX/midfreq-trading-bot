"""
Sign-in (midfreq.api.auth): with Settings.auth on, every route but /api/health, /api/auth/config
and the web app's files needs a verified, allowed Firebase ID token; with it off, nothing changes.

Tokens are checked by a stub verifier here (app.dependency_overrides[get_token_verifier]). The
real verifier's checks run against tokens signed with a throwaway key when google-auth is
installed (the gcs extra); otherwise those tests are skipped.
"""
import dataclasses
import json
import re
import time

import pytest
from fastapi.routing import APIRoute

from midfreq.api import auth
from midfreq.api.auth import (CachedGetRequest, FirebaseVerifier, Identity, InvalidToken, PUBLIC_ROUTES,
                              VerifierUnavailable, get_token_verifier, identity_from_claims, is_allowed)
from midfreq.api.deps import get_record_store
from midfreq.api.main import create_app
from midfreq.api.settings import Settings, default_settings

TestClient = pytest.importorskip("fastapi.testclient").TestClient
pytest.importorskip("httpx")

PROJECT = "demo-midfreq"

# token -> who the stub verifier says it is
TOKENS = {
    "owner": Identity("u1", "owner@example.com", True),
    "owner-shouting": Identity("u2", "OWNER@Example.COM", True),
    "student": Identity("u3", "alice@ntu.edu.sg", True),
    "student-upper": Identity("u4", "Bob@NTU.edu.SG", True),
    "subdomain": Identity("u5", "carol@e.ntu.edu.sg", True),
    "lookalike": Identity("u6", "mallory@evilntu.edu.sg", True),
    "suffix": Identity("u7", "eve@ntu.edu.sg.example.com", True),
    "stranger": Identity("u8", "stranger@example.org", True),
    "unverified-owner": Identity("u9", "owner@example.com", False),
    "no-email": Identity("u10", None, False),
}


class StubVerifier:
    def __init__(self):
        self.calls = []

    def __call__(self, token):
        self.calls.append(token)
        if token == "keys-down":
            raise VerifierUnavailable("cannot fetch keys")
        if token not in TOKENS:
            raise InvalidToken("bad signature")
        return TOKENS[token]


@pytest.fixture
def auth_settings(settings):
    return dataclasses.replace(settings, auth="firebase", auth_project=PROJECT,
                               auth_allowed_emails=("owner@example.com",), auth_allowed_domains=("ntu.edu.sg",),
                               auth_web_api_key="public-web-key")


@pytest.fixture
def verifier():
    return StubVerifier()


def _app(settings, verifier):
    app = create_app(settings)
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    return app


@pytest.fixture
def auth_app(auth_settings, verifier):
    return _app(auth_settings, verifier)


@pytest.fixture
def api(auth_app):
    return TestClient(auth_app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def protected_routes(app):
    """(method, a concrete path) for every API route that is not public."""
    out = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC_ROUTES:
            continue
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        out += [(method, path) for method in sorted(route.methods)]
    return out


# ── every route is checked ──────────────────────────────────────────────────

def test_the_protected_routes_are_every_data_route(auth_app):
    routes = protected_routes(auth_app)
    paths = {p for _, p in routes}
    assert {"/api/sessions", "/api/forward-test", "/api/accounts", "/api/results/overview", "/api/strategies",
            "/api/candles", "/api/research/files", "/api/jobs", "/api/auth/me"} <= paths
    assert ("POST", "/api/jobs/backtest") in routes and ("DELETE", "/api/runs/x") in routes
    assert "/api/health" not in paths and "/api/auth/config" not in paths


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer"}, {"Authorization": "Bearer "},
                                     {"Authorization": "Basic b3duZXI6cHc="}, {"Authorization": "owner"},
                                     bearer("forged")])
def test_every_protected_route_refuses_without_a_valid_token(auth_app, headers):
    client = TestClient(auth_app)
    for method, path in protected_routes(auth_app):
        r = client.request(method, path, headers=headers)
        assert r.status_code == 401, (method, path, r.status_code, r.text)
        assert r.headers["www-authenticate"].startswith("Bearer")


def test_every_protected_route_refuses_an_account_awaiting_approval(auth_app):
    client = TestClient(auth_app)
    for method, path in protected_routes(auth_app):
        if path == "/api/auth/me":
            continue
        r = client.request(method, path, headers=bearer("stranger"))
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        assert "Awaiting approval" in r.json()["detail"]


def test_a_refused_request_reads_nothing(auth_settings, verifier):
    app = _app(auth_settings, verifier)

    def no_reading():
        raise AssertionError("the record was read for a refused request")

    app.dependency_overrides[get_record_store] = no_reading
    client = TestClient(app)
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers=bearer("stranger")).status_code == 403


def test_query_validation_comes_after_sign_in(api):
    # /api/candles requires `symbol`: without a token the answer is 401, not a 422 that describes the route
    assert api.get("/api/candles").status_code == 401
    assert api.get("/api/candles", headers=bearer("owner")).status_code == 422


# ── public routes ───────────────────────────────────────────────────────────

def test_health_and_auth_config_stay_public(api):
    assert api.get("/api/health").status_code == 200
    r = api.get("/api/auth/config")
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "provider": "firebase",
                        "firebase": {"apiKey": "public-web-key", "authDomain": f"{PROJECT}.firebaseapp.com",
                                     "projectId": PROJECT}}


def test_auth_domain_can_be_set(auth_settings, verifier):
    app = _app(dataclasses.replace(auth_settings, auth_domain="login.example.com"), verifier)
    assert TestClient(app).get("/api/auth/config").json()["firebase"]["authDomain"] == "login.example.com"


def test_the_web_app_files_stay_public_but_not_api_paths(auth_settings, verifier, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    client = TestClient(_app(dataclasses.replace(auth_settings, web_dist=dist), verifier))
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/live").text == "<html>app</html>"
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/no-such-route").status_code in (401, 404)
    assert client.get("/api/no-such-route", headers=bearer("owner")).status_code == 404


# ── who may read ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("token", ["owner", "owner-shouting", "student", "student-upper"])
def test_allowed_accounts_read_the_dashboard(api, token):
    r = api.get("/api/sessions", headers=bearer(token))
    assert r.status_code == 200
    assert len(r.json()) == 3


@pytest.mark.parametrize("token", ["subdomain", "lookalike", "suffix", "stranger"])
def test_other_accounts_await_approval(api, token):
    r = api.get("/api/sessions", headers=bearer(token))
    assert r.status_code == 403
    assert r.json()["detail"].startswith("Awaiting approval")
    assert TOKENS[token].email in r.json()["detail"]


def test_an_unverified_email_is_refused_even_when_listed(api):
    r = api.get("/api/sessions", headers=bearer("unverified-owner"))
    assert r.status_code == 403
    assert "not verified" in r.json()["detail"]
    assert api.get("/api/sessions", headers=bearer("no-email")).status_code == 403


def test_the_scheme_is_case_insensitive(api):
    assert api.get("/api/sessions", headers={"Authorization": "bearer owner"}).status_code == 200


def test_when_tokens_cannot_be_checked_it_is_503(api):
    assert api.get("/api/sessions", headers=bearer("keys-down")).status_code == 503


def test_a_token_is_checked_once_per_request(api, verifier):
    api.get("/api/auth/me", headers=bearer("owner"))
    assert verifier.calls == ["owner"]


@pytest.mark.parametrize("email,allowed", [
    ("owner@example.com", True), (" Owner@Example.com ", True), ("alice@ntu.edu.sg", True),
    ("alice@NTU.EDU.SG", True), ("alice@e.ntu.edu.sg", False), ("alice@xntu.edu.sg", False),
    ("alice@ntu.edu.sg.evil.com", False), ("ntu.edu.sg", False), ("@ntu.edu.sg", False),
    ("owner@example.com.evil", False), ("", False), (None, False),
])
def test_is_allowed(auth_settings, email, allowed):
    assert is_allowed(email, auth_settings) is allowed


def test_an_empty_access_list_admits_nobody(auth_settings):
    s = dataclasses.replace(auth_settings, auth_allowed_emails=(), auth_allowed_domains=())
    assert not is_allowed("owner@example.com", s)


# ── /api/auth/me ────────────────────────────────────────────────────────────

def test_me_needs_a_token(api):
    assert api.get("/api/auth/me").status_code == 401
    assert api.get("/api/auth/me", headers=bearer("forged")).status_code == 401


def test_me_says_who_is_signed_in_and_whether_they_may_read(api):
    assert api.get("/api/auth/me", headers=bearer("owner")).json() == {
        "email": "owner@example.com", "email_verified": True, "allowed": True, "reason": None}
    me = api.get("/api/auth/me", headers=bearer("stranger")).json()
    assert (me["email"], me["email_verified"], me["allowed"]) == ("stranger@example.org", True, False)
    assert me["reason"].startswith("Awaiting approval")
    me = api.get("/api/auth/me", headers=bearer("unverified-owner")).json()
    assert (me["email_verified"], me["allowed"]) == (False, False)
    assert "not verified" in me["reason"]


# ── sign-in off: unchanged ──────────────────────────────────────────────────

def test_with_sign_in_off_nothing_needs_a_token(settings):
    assert settings.auth == "off" and not settings.auth_enabled
    client = TestClient(create_app(settings))
    assert client.get("/api/sessions").status_code == 200
    assert client.post("/api/jobs/backtest", json={}).status_code == 422     # reaches the route
    assert client.get("/api/auth/config").json() == {"enabled": False, "provider": None, "firebase": None}
    assert client.get("/api/auth/me").json() == {"email": None, "email_verified": False, "allowed": True,
                                                 "reason": None}


def test_with_sign_in_off_no_verifier_is_made(settings):
    assert get_token_verifier(settings) is None


def test_a_token_is_ignored_with_sign_in_off(settings):
    client = TestClient(create_app(settings))
    assert client.get("/api/sessions", headers=bearer("anything")).status_code == 200


# ── settings ────────────────────────────────────────────────────────────────

AUTH_ENV = ("MIDFREQ_AUTH", "MIDFREQ_AUTH_PROJECT", "MIDFREQ_AUTH_ALLOWED_EMAILS", "MIDFREQ_AUTH_ALLOWED_DOMAINS",
            "MIDFREQ_AUTH_WEB_API_KEY", "MIDFREQ_AUTH_DOMAIN")


@pytest.fixture
def clean_env(monkeypatch):
    for name in AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_sign_in_is_off_by_default(clean_env):
    s = default_settings()
    assert s.auth == "off" and not s.auth_enabled


def test_sign_in_settings_from_the_environment(clean_env):
    clean_env.setenv("MIDFREQ_AUTH", "Firebase")
    clean_env.setenv("MIDFREQ_AUTH_PROJECT", PROJECT)
    clean_env.setenv("MIDFREQ_AUTH_ALLOWED_EMAILS", " Owner@Example.com, ,second@example.com ")
    clean_env.setenv("MIDFREQ_AUTH_ALLOWED_DOMAINS", "ntu.edu.sg,@E.NTU.edu.sg")
    clean_env.setenv("MIDFREQ_AUTH_WEB_API_KEY", "public-web-key")
    s = default_settings()
    assert s.auth_enabled and s.auth_project == PROJECT
    assert s.auth_allowed_emails == ("owner@example.com", "second@example.com")
    assert s.auth_allowed_domains == ("ntu.edu.sg", "e.ntu.edu.sg")
    assert (s.auth_web_api_key, s.auth_web_domain) == ("public-web-key", f"{PROJECT}.firebaseapp.com")


def test_a_misspelt_mode_stops_the_server(clean_env):
    clean_env.setenv("MIDFREQ_AUTH", "firebsae")
    with pytest.raises(ValueError, match="MIDFREQ_AUTH"):
        default_settings()


def test_firebase_needs_a_project(clean_env, settings):
    clean_env.setenv("MIDFREQ_AUTH", "firebase")
    with pytest.raises(ValueError, match="MIDFREQ_AUTH_PROJECT"):
        default_settings()
    with pytest.raises(ValueError):
        dataclasses.replace(settings, auth="firebase")


# ── the real verifier's own checks ──────────────────────────────────────────

def _claims(**over):
    now = int(time.time())
    claims = {"iss": f"https://securetoken.google.com/{PROJECT}", "aud": PROJECT, "sub": "uid-1", "iat": now,
              "exp": now + 3600, "auth_time": now, "email": "owner@example.com", "email_verified": True}
    claims.update(over)
    return {k: v for k, v in claims.items() if v is not None}


def test_identity_from_claims():
    assert identity_from_claims(_claims(), PROJECT) == Identity("uid-1", "owner@example.com", True)
    assert identity_from_claims(_claims(email_verified="true"), PROJECT).email_verified is False
    assert identity_from_claims(_claims(email=None, email_verified=None), PROJECT) == Identity("uid-1", None, False)
    for bad in (_claims(iss="https://securetoken.google.com/other"), _claims(iss="https://accounts.google.com"),
                _claims(aud="other"), _claims(sub=""), _claims(sub=None), _claims(sub="x" * 129)):
        with pytest.raises(InvalidToken):
            identity_from_claims(bad, PROJECT)


class FakeResponse:
    def __init__(self, status=200, data=b"{}", headers=None):
        self.status, self.data, self.headers = status, data, headers or {}


class FakeTransport:
    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        self.calls.append((method, url, timeout))
        return self.response


def test_cached_get_request_keeps_keys_for_their_max_age():
    clock = [1000.0]
    inner = FakeTransport(FakeResponse(headers={"Cache-Control": "public, max-age=600, must-revalidate"}))
    req = CachedGetRequest(inner, clock=lambda: clock[0])
    for _ in range(3):
        assert req("https://keys").status == 200
    assert len(inner.calls) == 1 and inner.calls[0][2] == auth.CERTS_TIMEOUT_SECONDS
    clock[0] += 601
    req("https://keys")
    assert len(inner.calls) == 2


@pytest.mark.parametrize("response", [FakeResponse(status=500), FakeResponse(headers={"cache-control": "no-store"})])
def test_cached_get_request_does_not_keep_failures_or_no_store(response):
    inner = FakeTransport(response)
    req = CachedGetRequest(inner)
    req("https://keys")
    req("https://keys")
    assert len(inner.calls) == 2


def test_cached_get_request_without_max_age_keeps_an_hour():
    assert CachedGetRequest._ttl({}) == auth.CERTS_TTL_DEFAULT
    assert CachedGetRequest._ttl({"cache-control": "max-age=999999"}) == auth.CERTS_TTL_MAX


@pytest.fixture
def signing_key():
    pytest.importorskip("google.auth")
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.auth import crypt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return crypt.RSASigner.from_string(private, key_id="key-1"), {"key-1": public}


def _verifier_with_keys(keys, status=200):
    transport = FakeTransport(FakeResponse(status=status, data=json.dumps(keys).encode(),
                                           headers={"cache-control": "max-age=3600"}))
    v = FirebaseVerifier(PROJECT)
    v._request = CachedGetRequest(transport)
    return v, transport


def test_firebase_verifier_accepts_a_good_token_and_caches_the_keys(signing_key):
    from google.auth import jwt
    signer, keys = signing_key
    v, transport = _verifier_with_keys(keys)
    token = jwt.encode(signer, _claims()).decode()
    assert v(token) == Identity("uid-1", "owner@example.com", True)
    assert v(token).email == "owner@example.com"
    assert len(transport.calls) == 1


@pytest.mark.parametrize("over", [dict(aud="another-project"), dict(iss="https://securetoken.google.com/x"),
                                  dict(exp=int(time.time()) - 3600, iat=int(time.time()) - 7200),
                                  dict(iat=int(time.time()) + 3600)])
def test_firebase_verifier_rejects_bad_claims(signing_key, over):
    from google.auth import jwt
    signer, keys = signing_key
    v, _ = _verifier_with_keys(keys)
    with pytest.raises(InvalidToken):
        v(jwt.encode(signer, _claims(**over)).decode())


def test_firebase_verifier_rejects_another_key_and_garbage(signing_key):
    from google.auth import jwt
    signer, keys = signing_key
    v, _ = _verifier_with_keys({"key-2": keys["key-1"]})          # the signer's kid is not published
    with pytest.raises(InvalidToken):
        v(jwt.encode(signer, _claims()).decode())
    for garbage in ("not-a-jwt", "a.b.c", ""):
        with pytest.raises(InvalidToken):
            v(garbage)


def test_firebase_verifier_without_keys_is_unavailable(signing_key):
    from google.auth import jwt
    signer, keys = signing_key
    v, _ = _verifier_with_keys(keys, status=503)
    with pytest.raises(VerifierUnavailable):
        v(jwt.encode(signer, _claims()).decode())


def test_settings_are_still_constructible_positionally():
    # the fields every deployment passes; the sign-in ones all have defaults
    s = Settings(results_dir=None, live_sessions="x", data_dir=None, config_dir=None)
    assert s.auth == "off"
