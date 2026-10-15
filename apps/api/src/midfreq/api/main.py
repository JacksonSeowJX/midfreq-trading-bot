"""
The dashboard API: read-only JSON over what the Streamlit dashboard reads directly today.

    uv run uvicorn midfreq.api.main:app --reload          # http://127.0.0.1:8000/docs

Two independent sources for live trading, never merged: the bot's own record (session logs,
through a record store) and each broker or exchange account (through a connector's
AccountClient, configured in the `accounts` section of config/connections.yaml). The API never
talks to the trading bot process.

Contract: schemas.py. The routes live in routers/ (record, accounts, research, reference); this
module only assembles them. See apps/api/README.md.
"""
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from midfreq.api import __version__
from midfreq.api.auth import require_access
from midfreq.api.routers import accounts, jobs, record, reference, research, runs
from midfreq.api.routers import auth as sign_in
from midfreq.api.settings import Settings, default_settings, get_settings
from midfreq.api.web import mount_web


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """The app. Without `settings` it reads the repository's folders (midfreq.core.paths); with
    them, every route uses those instead."""
    app = FastAPI(title="midfreq dashboard API", version=__version__,
                  description="Read-only views of research results, the trading bot's record and "
                              "broker accounts.",
                  # sign-in, when Settings.auth is on: every route but the public ones (midfreq.api.auth)
                  dependencies=[Depends(require_access)])
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    else:
        settings = default_settings()

    for r in (reference, record, accounts, research, jobs, runs):
        app.include_router(r.router)
    app.include_router(sign_in.router)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])
    if settings.web_dist is not None:
        mount_web(app, settings.web_dist)      # after the API routes, so /api always matches first
    return app


app = create_app()
