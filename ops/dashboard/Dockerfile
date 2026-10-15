# The hosted dashboard: the web app (apps/web) built with Node, served with the API (apps/api)
# by one Python process. Built by ops/dashboard/deploy_dashboard.sh through Cloud Build from
# committed code only (plus the price cache, which is not in git), and run on Cloud Run, where
# the bot's record and the broker snapshots come from the Cloud Storage bucket the VM uploads to,
# and visitors may run backtests and comparisons but not optimizations.

FROM node:22-slim AS web
WORKDIR /src
COPY apps/api/openapi.json apps/api/openapi.json
COPY apps/web/package.json apps/web/package-lock.json apps/web/
RUN cd apps/web && npm ci --no-audit --no-fund
COPY apps/web apps/web
# the user guide, shown on the dashboard's Guide page (imported by the web build)
COPY docs/DASHBOARD_GUIDE.md docs/DASHBOARD_GUIDE.md
RUN cd apps/web && npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY apps/api apps/api
# only the API and what it imports (not Streamlit, the broker SDKs or the dev tools)
RUN uv sync --frozen --no-dev --package midfreq-api --extra gcs --no-editable
COPY config config
COPY results results
# the price cache for backtests (copied into the build by deploy_dashboard.sh; may be empty)
COPY data data
COPY --from=web /src/apps/web/dist apps/web/dist
# the commit this image was built from, recorded in every run it saves (the image has no .git).
# Sign-in is on by default here and fails closed: without a project or an access list nobody gets in.
ARG GIT_COMMIT=unknown
ENV PATH=/app/.venv/bin:$PATH \
    MIDFREQ_ROOT=/app \
    MIDFREQ_GIT_COMMIT=$GIT_COMMIT \
    MIDFREQ_DASHBOARD_RUN_DELETE=off \
    MIDFREQ_AUTH=firebase \
    MIDFREQ_DASHBOARD_ACCOUNTS=snapshots \
    MIDFREQ_DASHBOARD_JOBS=backtest,compare \
    MIDFREQ_DASHBOARD_MAX_JOBS=1 \
    PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 app && chown -R app /app
USER app
CMD exec uvicorn midfreq.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers
