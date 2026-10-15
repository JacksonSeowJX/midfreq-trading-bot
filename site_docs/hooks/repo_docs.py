"""
MkDocs hooks for the documentation site (mkdocs.yml, `hooks:`).

The site reuses the repository's own documents instead of copying them, so a page cannot drift
from the file it shows. Three markers are expanded when a page is built:

    <!-- include: docs/DASHBOARD_GUIDE.md -->
        the file's text in place of the marker (a path from the repository root). Options,
        after the path:
          strip-title   drop the file's first level-1 heading (the page gives its own)
          app-links     links starting with "/" are dashboard routes: they go to
                        extra.dashboard_url instead of the repository

    <!-- openapi-routes: tag; tag; ... -->
        a table of the API's routes, grouped by tag in the order given (tags not named follow),
        generated from apps/api/openapi.json

    [text](repo:path/in/the/repository)
        in a hand-written page, a link to a repository file

Links in an included file are written relative to that file in the repository, as GitHub
renders them. Here they are rewritten for the page that includes the file: a file the site
itself includes becomes a link to that page, an image is published with the site (under
assets/repo/), and anything else becomes a link to the file on GitHub. A link to a repository
path that does not exist is a warning, so `mkdocs build --strict` fails on it.

The API's contract, apps/api/openapi.json, is published as api/openapi.json on every build, so
the API reference always renders the contract of the commit the site was built from.

Markers and links inside fenced code blocks and inline code are left alone.
"""
from __future__ import annotations

import json
import logging
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File

log = logging.getLogger("mkdocs.hooks.repo_docs")

OPENAPI = "apps/api/openapi.json"           # the contract in the repository
OPENAPI_URI = "api/openapi.json"            # where the site publishes it
ASSETS = "assets/repo"                      # images shown by included files, at their repository path
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
OPTIONS = {"strip-title", "app-links"}

INCLUDE = re.compile(r"^[ \t]*<!--[ \t]*include:[ \t]*(?P<path>\S+)(?P<opts>(?:[ \t]+[\w-]+)*)[ \t]*-->[ \t]*$",
                     re.M)
ROUTES = re.compile(r"^[ \t]*<!--[ \t]*openapi-routes(?:[ \t]*:(?P<order>[^>]*?))?[ \t]*-->[ \t]*$", re.M)
LINK = re.compile(r"(?P<bang>!?)\[(?P<text>(?:[^\[\]]|\[[^\[\]]*\])*)\]"
                  r"\((?P<target><[^>\n]*>|[^)\s]+)(?P<title>[ \t]+\"[^\"\n]*\")?\)")
FENCE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")
CODE_SPAN = re.compile(r"(`+)[^\n]+?\1")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
TITLE = re.compile(r"\A\s*# [^\n]*\n")


@dataclass
class _Site:
    root: Path
    repo_url: str
    branch: str
    dashboard_url: str
    included: Dict[str, str] = field(default_factory=dict)   # repository path -> src_uri of its page
    images: Set[str] = field(default_factory=set)            # repository paths published under ASSETS


_site: Optional[_Site] = None


# ── markdown helpers ─────────────────────────────────────────────────────────

def _code_ranges(text: str) -> List[Tuple[int, int]]:
    """Character ranges of fenced code blocks and inline code spans."""
    ranges, pos, fence, start = [], 0, None, 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        m = FENCE.match(line)
        if fence is None and m:
            fence, start = m.group("fence"), pos
        elif fence is not None and stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            ranges.append((start, pos + len(line)))
            fence = None
        pos += len(line)
    if fence is not None:
        ranges.append((start, pos))
    ranges.extend(m.span() for m in CODE_SPAN.finditer(text))
    return ranges


def _in_code(ranges: List[Tuple[int, int]], pos: int) -> bool:
    return any(a <= pos < b for a, b in ranges)


def _split(target: str) -> Tuple[str, str]:
    """('path', '?query#fragment') of a link target."""
    cut = min((i for i in (target.find("?"), target.find("#")) if i >= 0), default=len(target))
    return target[:cut], target[cut:]


def _resolve(raw: str, base: Optional[str], app_links: bool) -> Tuple[str, str, str]:
    """What a link points at: ("keep", "", ""), ("url", url, ""), or ("repo", path, suffix).

    `base` is the directory of the included file the link is in (repository-relative), or None
    for a hand-written page, where only repo: links are rewritten."""
    target = raw[1:-1] if raw.startswith("<") else raw
    if target.startswith("repo:"):
        path, suffix = _split(target[len("repo:"):])
        return "repo", posixpath.normpath(path.lstrip("/") or "."), suffix
    if base is None or target.startswith("#") or SCHEME.match(target):
        return "keep", "", ""
    if target.startswith("/"):
        if app_links:
            return "url", _site.dashboard_url.rstrip("/") + target, ""
        path, suffix = _split(target.lstrip("/"))
        return "repo", posixpath.normpath(path or "."), suffix
    path, suffix = _split(target)
    if not path:                                   # "?query" alone: nothing to resolve
        return "keep", "", ""
    return "repo", posixpath.normpath(posixpath.join(base, path)), suffix


def _is_image(path: str, bang: str) -> bool:
    return bool(bang) or Path(path).suffix.lower() in IMAGE_SUFFIXES


def _href(path: str, suffix: str, page_uri: str, where: str) -> str:
    """The URL of a repository path, as seen from the page `page_uri`."""
    if path == ".." or path.startswith("../"):
        log.warning("%s: the link to %s leaves the repository", where, path)
        return _site.repo_url
    absolute = _site.root / path
    if not absolute.exists():
        log.warning("%s: links to %s, which is not in the repository", where, path)
    page_dir = posixpath.dirname(page_uri) or "."
    page = _site.included.get(path)
    if page is not None and page != page_uri:      # a file this site shows: link to its page
        return posixpath.relpath(page, page_dir) + suffix
    if path in _site.images:
        return posixpath.relpath(f"{ASSETS}/{path}", page_dir) + suffix
    kind = "tree" if absolute.is_dir() else "blob"
    return f"{_site.repo_url}/{kind}/{_site.branch}/{'' if path == '.' else path}{suffix}"


def _rewrite(text: str, base: Optional[str], page_uri: str, app_links: bool, where: str) -> str:
    code = _code_ranges(text)

    def sub(m: re.Match) -> str:
        if _in_code(code, m.start()):
            return m.group(0)
        kind, value, suffix = _resolve(m.group("target"), base, app_links)
        if kind == "keep":
            return m.group(0)
        href = value if kind == "url" else _href(value, suffix, page_uri, where)
        if " " in href:
            href = f"<{href}>"
        return f"{m.group('bang')}[{m.group('text')}]({href}{m.group('title') or ''})"

    return LINK.sub(sub, text)


def _read(path: str, where: str) -> Optional[str]:
    absolute = _site.root / path
    if not absolute.is_file():
        log.warning("%s: includes %s, which is not a file in the repository", where, path)
        return None
    return absolute.read_text(encoding="utf-8")


# ── the API's routes, from its contract ──────────────────────────────────────

def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _first_sentence(text: Optional[str]) -> str:
    if not text:
        return ""
    paragraph = " ".join(text.strip().split("\n\n")[0].split())
    m = re.match(r"(.+?[.!?])(\s|$)", paragraph)
    return m.group(1) if m else paragraph


def _routes(order_spec: Optional[str], where: str) -> str:
    spec = json.loads((_site.root / OPENAPI).read_text(encoding="utf-8"))
    groups: Dict[str, List[Tuple[str, str, dict]]] = {}
    for path, item in spec.get("paths", {}).items():
        for method in HTTP_METHODS:
            op = item.get(method)
            if op is not None:
                groups.setdefault((op.get("tags") or ["other"])[0], []).append((method.upper(), path, op))
    order = [t.strip() for t in (order_spec or "").split(";") if t.strip()]
    for tag in order:
        if tag not in groups:
            log.info("%s: openapi-routes names the tag %r, which the API does not use", where, tag)
    tags = [t for t in order if t in groups] + [t for t in groups if t not in order]
    n_ops = sum(len(ops) for ops in groups.values())
    info = spec.get("info", {})
    out = [f"Generated from `{OPENAPI}` when this site was built: *{info.get('title', 'the API')}*, "
           f"version {info.get('version', '?')}, OpenAPI {spec.get('openapi', '?')}, "
           f"{n_ops} operations on {len(spec.get('paths', {}))} paths.", ""]
    for tag in tags:
        out += [f"**{tag[:1].upper()}{tag[1:]}**", "",
                "| Method | Path | Summary | Responses |", "|---|---|---|---|"]
        for method, path, op in groups[tag]:
            summary = _first_sentence(op.get("description")) or op.get("summary", "")
            codes = ", ".join(str(c) for c in op.get("responses", {}))
            out.append(f"| `{method}` | `{path}` | {_cell(summary)} | {codes} |")
        out.append("")
    return "\n".join(out)


# ── MkDocs events ────────────────────────────────────────────────────────────

def on_config(config):
    global _site
    if not config.repo_url:
        raise PluginError("repo_docs: mkdocs.yml must set repo_url")
    _site = _Site(root=Path(config.config_file_path).resolve().parent,
                  repo_url=config.repo_url.rstrip("/"),
                  branch=config.extra.get("repo_branch", "main"),
                  dashboard_url=config.extra.get("dashboard_url", ""))
    return config


def on_files(files, config):
    """Find what the pages include, publish the images those files show and the API contract."""
    _site.included.clear()
    _site.images.clear()
    # (markdown text, the repository directory its links are relative to, or None for a page's
    # own text, where only repo: links count)
    texts: List[Tuple[str, Optional[str]]] = []
    for page in files.documentation_pages():
        text = Path(page.abs_src_path).read_text(encoding="utf-8")
        texts.append((text, None))
        code = _code_ranges(text)
        for m in INCLUDE.finditer(text):
            if not _in_code(code, m.start()):
                path = posixpath.normpath(m.group("path"))
                _site.included.setdefault(path, page.src_uri)
                included = _read(path, page.src_uri)
                if included is not None:
                    texts.append((included, posixpath.dirname(path)))

    for text, base in texts:
        code = _code_ranges(text)
        for m in LINK.finditer(text):
            if _in_code(code, m.start()):
                continue
            kind, target, _ = _resolve(m.group("target"), base, app_links=False)
            if (kind == "repo" and _is_image(target, m.group("bang")) and target not in _site.images
                    and (_site.root / target).is_file()):
                _site.images.add(target)
                files.append(File.generated(config, f"{ASSETS}/{target}", abs_src_path=str(_site.root / target)))

    contract = _site.root / OPENAPI
    if contract.is_file():
        files.append(File.generated(config, OPENAPI_URI, abs_src_path=str(contract)))
    else:
        log.warning("repo_docs: %s is missing, so the API reference has no contract to show", OPENAPI)
    return files


def on_page_markdown(markdown, page, config, files):
    where = page.file.src_uri
    markdown = _rewrite(markdown, None, where, app_links=False, where=where)

    code = _code_ranges(markdown)

    def routes(m: re.Match) -> str:
        if _in_code(code, m.start()):
            return m.group(0)
        return _routes(m.group("order"), where)

    markdown = ROUTES.sub(routes, markdown)
    code = _code_ranges(markdown)
    first: List[str] = []

    def include(m: re.Match) -> str:
        if _in_code(code, m.start()):
            return m.group(0)
        path = posixpath.normpath(m.group("path"))
        opts = set(m.group("opts").split())
        for unknown in sorted(opts - OPTIONS):
            log.warning("%s: unknown include option %r (known: %s)", where, unknown, ", ".join(sorted(OPTIONS)))
        if "app-links" in opts and not _site.dashboard_url:
            log.warning("%s: app-links needs extra.dashboard_url in mkdocs.yml", where)
        text = _read(path, where)
        if text is None:
            return ""
        if "strip-title" in opts:
            text = TITLE.sub("", text, count=1)
        first.append(path)
        return _rewrite(text, posixpath.dirname(path), where, "app-links" in opts, f"{path} (in {where})")

    markdown = INCLUDE.sub(include, markdown)
    if first:
        # The page shows that file, so "edit this page" opens the file itself.
        page.edit_url = f"{_site.repo_url}/blob/{_site.branch}/{first[0]}"
    return markdown
