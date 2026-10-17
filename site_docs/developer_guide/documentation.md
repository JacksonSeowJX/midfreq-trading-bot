# Documentation

This site is [MkDocs](https://www.mkdocs.org/) with the
[Material](https://squidfunk.github.io/mkdocs-material/) theme. Its sources are
[`site_docs/`](repo:site_docs) and [`mkdocs.yml`](repo:mkdocs.yml); the Python API reference is
generated with [mkdocstrings](https://mkdocstrings.github.io/). The toolchain is pinned in full in
`site_docs/requirements.txt` and kept out of the uv workspace, so it runs in a throwaway
environment:

```bash
uvx --with-requirements site_docs/requirements.txt mkdocs serve            # http://127.0.0.1:8000/midfreq-trading-bot/
uvx --with-requirements site_docs/requirements.txt mkdocs build --strict   # as the Pages workflow does
```

The build writes `site/`, which git ignores. `--strict` turns every warning into an error: a
broken link or anchor, a page missing from the navigation, a repository path that does not
exist, or a docstring the API reference cannot render.

## Layout

| Path | Holds |
|---|---|
| `mkdocs.yml` | the navigation, the theme and its palettes, the plugins and Markdown extensions |
| `site_docs/<section>/*.md` | the hand-written pages: `getting_started/`, `concepts/`, `tutorials/`, `integrations/`, `api/`, `developer_guide/`, `results/` |
| `site_docs/hooks/repo_docs.py` | the hook that includes repository documents and generates tables (below) |
| `site_docs/hooks/gen_api_pages.py` | the script that generates the Python API reference |
| `site_docs/stylesheets/extra.css` | the two palettes and a few layout adjustments |
| `site_docs/assets/` | the logo and the favicon |

The theme uses Material's navigation tabs and sections, a table of contents per page, search
with suggestions, light and dark palettes (dark by default) with a toggle in the header, Inter
and JetBrains Mono from Google Fonts, copy buttons on code blocks, admonitions, tabbed content,
grid cards and Mermaid diagrams (in fenced `mermaid` code blocks).

## Reusing repository documents

Pages that show a repository document include it when the site is built, through the hook
`site_docs/hooks/repo_docs.py`, rather than copying it, so a page cannot drift from the file:

| In a page | Becomes |
|---|---|
| `<!-- include: docs/DASHBOARD_GUIDE.md -->` | the file's text |
| `<!-- include: docs/ARCHITECTURE.md#time -->` | one section of it: the heading with that anchor and everything up to the next heading of the same or a higher level |
| `[text](repo:path/in/the/repository#anchor)` | a link to the page that shows that file or section, or else to the file on GitHub |
| `<!-- openapi-routes: tag; tag -->` | a table of the API's routes, by tag, from `apps/api/openapi.json` |
| `<!-- venues-table -->`, `<!-- venues-table: yfinance -->` | a table of the venues in `config/venues.json`, or of those one connector serves, with its settings |

An include takes options after the path: `strip-title` drops the file's level-1 heading (or the
section's own heading), `intro-only` keeps only the text before the first subheading,
`skip=a,b` leaves out the sections with those anchors, `level=N` moves every heading so that the
first is at level N, `app-links` sends links to dashboard routes (`/live`, `/backtest?...`) to the
hosted dashboard, and `edit` makes "Edit this page" open the included file.

Links inside an included file are written relative to that file, as GitHub renders them, and
are rewritten for the site: to the page that shows the target file or section, to a copy of an
image published with the site, or to the file on GitHub. A link to a path or a section that does
not exist fails the strict build.

Each section of a file is shown on one page only. `docs/ARCHITECTURE.md`, the venue engine's
design document, is split across the Concepts pages it belongs to. A package README is the
overview of its package in the API reference, except for the sections a Concepts or Developer
guide page includes on its own; the generator leaves those out of the package page and links to
them instead.

## The Python API reference

`site_docs/hooks/gen_api_pages.py` runs at the start of every build, through
[mkdocs-gen-files](https://oprypin.github.io/mkdocs-gen-files/), and writes one page per public
module of every package in `packages/`, under `api/python/`, plus the navigation of that section for
[mkdocs-literate-nav](https://oprypin.github.io/mkdocs-literate-nav/);
[mkdocs-section-index](https://oprypin.github.io/mkdocs-section-index/) makes each package's page
the index of its section. The pages exist only in the build, so the reference cannot drift from
the code.

mkdocstrings renders each page from the source by static analysis
([griffe](https://mkdocstrings.github.io/griffe/)), so nothing is imported and no vendor SDK is
needed. Docstrings are read in the Google style: `Args:`, `Returns:` and `Raises:` sections
become tables, and any other `Title:` line followed by an indented block becomes an admonition.
To show a literal block, such as a command line or a path pattern, leave a blank line after its
introducing line, so that it is read as code. Modules whose name starts with an underscore are
left out, and so is a folder without an `__init__.py` (`midfreq.data.providers`), which griffe
does not traverse. A package page is the distribution's README, or the package's docstring
for a distribution without one (`midfreq-trading`).

griffe finds a package only on the search paths listed under the mkdocstrings handler's `paths`
in `mkdocs.yml`, one `packages/<name>/src` per package. The generator writes pages for every
package it finds in `packages/`, so a new package must be added to that list as well; otherwise
its pages cannot be collected and the build fails (`midfreq.trading could not be found`).

## The HTTP API reference

The hook publishes `apps/api/openapi.json` with the site as `api/http/openapi.json`, and the
[HTTP API reference](../api/http/reference.md) renders it with Redoc, loaded from a CDN with a
pinned version and integrity hash. Each build therefore shows the contract of the commit it was
built from.

## Publishing

[`.github/workflows/pages.yml`](repo:.github/workflows/pages.yml) builds the site on every push to
`main` that changes its sources or anything it includes or generates from, and publishes it to
GitHub Pages ([CI workflows](ci.md#pages)). GitHub Pages must be enabled once for the repository:
Settings, Pages, *Source*: **GitHub Actions**.

## Adding a page

Create it in the section's folder under `site_docs/` and add it to `nav` in `mkdocs.yml`; a page
that is not in the navigation fails the strict build. Write in a formal, factual register, in
British spelling with -ize endings (*organization*, *optimization*). Code shown with output on a
page must have been run as shown, and the output pasted from that run.
