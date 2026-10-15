# CI workflows

Four GitHub Actions workflows, in [`.github/workflows/`](repo:.github/workflows):

| Workflow | Runs on | What it does |
|---|---|---|
| [`ci.yml`](repo:.github/workflows/ci.yml) | every push and pull request, except commits that touch only `live_sessions/` | the `research`, `vm` and `web` jobs below |
| [`release.yml`](repo:.github/workflows/release.yml) | a `vX.Y.Z` tag, or *Run workflow* with a version | builds, installs, tests and publishes a release |
| [`deploy.yml`](repo:.github/workflows/deploy.yml) | *Run workflow* only | deploys, stages, switches or rolls back a release on the trading VM, after approval |
| [`pages.yml`](repo:.github/workflows/pages.yml) | a push to `main` that changes the documentation's sources, or *Run workflow* | builds this site with `mkdocs build --strict` and publishes it to GitHub Pages |

## CI

A newer push to the same branch cancels a run still in progress.

| Job | Environment | Steps |
|---|---|---|
| `research` | Python 3.12, the workspace from `uv.lock` (`uv sync --locked`) | the whole test suite; the package-layer check (`lint-imports`) |
| `vm` | Python 3.10, the trading VM's pinned versions | builds the release bundle with `ops/build_release.sh`, installs it with the VM's installer (with its smoke test and a dry run of the session launcher), removes the packages' source folders, and runs the packages' and the cross-package tests against the installed wheels |
| `web` | Node 22 | checks that the web app's API types match `apps/api/openapi.json`, then typecheck, lint, tests and a production build |

The `vm` job is the reason a release can be trusted on the VM: it tests what the bundle ships,
on the VM's interpreter and versions, rather than the source tree.

## Release

The Release workflow checks that the version is `X.Y.Z`, that every package's `pyproject.toml`
carries it and that it is not released yet; then it builds the bundle, installs it as the VM
would on Python 3.10, runs the tests against the installed wheels, and publishes a GitHub
Release with the bundle and its SHA-256 checksum. Started from the button, it also creates the
tag. See [Releases and deployment](releases_and_deployment.md#releases).

## Deploy

The Deploy workflow has two jobs. `plan` checks the inputs (an action: `status`, `deploy`,
`stage`, `switch` or `rollback`; a version; and a `force` box for deploying while a session runs)
and that the release is published, and shows the exact command. `apply` runs in the protected
`production` environment, so it waits for the owner's approval and runs only from `main`; it then
sends the command to the VM. See [Releases and deployment](releases_and_deployment.md#deploys).

## Pages

The Pages workflow installs the documentation toolchain pinned in `site_docs/requirements.txt`
on Python 3.12, runs `mkdocs build --strict`, in which any warning (a broken link or anchor, a
page missing from the navigation, a docstring the API reference cannot parse) fails the build,
and publishes the result with GitHub's Pages actions. It is triggered by changes to the site's
own sources and to everything the site includes or generates from: `docs/`, the packages (their
READMEs and docstrings), `apps/api/openapi.json` and `config/venues.json`. See
[Documentation](documentation.md).
