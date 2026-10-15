"""
MkDocs hooks for the documentation site (mkdocs.yml, `hooks:`).

The site reuses the repository's own documents instead of copying them, so a page cannot drift
from the file it shows. These markers are expanded when a page is built:

    <!-- include: docs/DASHBOARD_GUIDE.md -->
    <!-- include: docs/ARCHITECTURE.md#fill-models-and-slippage-models -->
        the file's text in place of the marker (a path from the repository root). With a
        fragment, only that section: its heading and everything up to the next heading of the
        same or a higher level. The fragment is the heading's anchor, as GitHub and this site
        write it. Options, after the path:
          strip-title   drop the first level-1 heading of the file (the page gives its own), or,
                        for a section, the section's own heading
          intro-only    keep only the text before the first subheading
          skip=a,b      leave out the sections with these anchors (each with its subsections)
          level=N       move every heading by the same amount, so that the first is at level N
          app-links     links starting with "/" are dashboard routes: they go to
                        extra.dashboard_url instead of the repository
          edit          "Edit this page" opens this file rather than the page's own source

    <!-- openapi-routes: tag; tag; ... -->
        a table of the API's routes, grouped by tag in the order given (tags not named follow),
        generated from apps/api/openapi.json

    <!-- venues-table -->
    <!-- venues-table: yfinance -->
        a table of the venues in config/venues.json; with a connector's name, only the venues
        that connector serves, with its settings for each

    [text](repo:path/in/the/repository#anchor)
        in a hand-written page, a link to a repository file

Links in an included file are written relative to that file in the repository, as GitHub
renders them. Here they are rewritten for the page that includes the file: a file (or section)
the site itself includes becomes a link to that page, an image is published with the site (under
assets/repo/), and anything else becomes a link to the file on GitHub. A link to an anchor of the
file itself (`#time`) goes to whichever page shows that section. A link to a repository path that
does not exist, or to a section that does not exist, is a warning, so `mkdocs build --strict`
fails on it.

The API's contract, apps/api/openapi.json, is published as api/http/openapi.json on every build,
so the HTTP API reference always renders the contract of the commit the site was built from. The
navigation file of the generated Python API reference (api/python/SUMMARY.md, written by
gen_api_pages.py and read by mkdocs-literate-nav) is kept out of the built site and its search.

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
from mkdocs.structure.files import File, InclusionLevel
from pymdownx.slugs import slugify

log = logging.getLogger("mkdocs.hooks.repo_docs")

OPENAPI = "apps/api/openapi.json"           # the contract in the repository
VENUES = "config/venues.json"               # the venue specifications
OPENAPI_URI = "api/http/openapi.json"       # where the site publishes it
NAV_FILE = "api/python/SUMMARY.md"          # the generated reference's navigation (gen_api_pages.py)
ASSETS = "assets/repo"                      # images shown by included files, at their repository path
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
FLAGS = {"strip-title", "intro-only", "app-links", "edit"}   # and skip=..., level=N

INCLUDE = re.compile(r"^[ \t]*<!--[ \t]*include:[ \t]*(?P<path>[^\s#]+)(?:#(?P<section>[\w-]+))?"
                     r"(?P<opts>(?:[ \t]+[\w-]+(?:=[\w,.-]+)?)*)[ \t]*-->[ \t]*$", re.M)
ROUTES = re.compile(r"^[ \t]*<!--[ \t]*openapi-routes(?:[ \t]*:(?P<order>[^>]*?))?[ \t]*-->[ \t]*$", re.M)
VENUES_TABLE = re.compile(r"^[ \t]*<!--[ \t]*venues-table(?:[ \t]*:[ \t]*(?P<connector>[\w-]+))?[ \t]*-->[ \t]*$",
                          re.M)
LINK = re.compile(r"(?P<bang>!?)\[(?P<text>(?:[^\[\]]|\[[^\[\]]*\])*)\]"
                  r"\((?P<target><[^>\n]*>|[^)\s]+)(?P<title>[ \t]+\"[^\"\n]*\")?\)")
FENCE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")
CODE_SPAN = re.compile(r"(`+)[^\n]+?\1")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
TITLE = re.compile(r"\A\s*# [^\n]*\n")
HEADING = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<text>.+?)[ \t]*#*[ \t]*$")

_slug = slugify(case="lower")


@dataclass
class _Site:
    root: Path
    repo_url: str
    branch: str
    dashboard_url: str
    included: Dict[str, str] = field(default_factory=dict)              # path -> page (whole file)
    sections: Dict[Tuple[str, str], str] = field(default_factory=dict)  # (path, anchor) -> page
    images: Set[str] = field(default_factory=set)                       # paths published under ASSETS


_site: Optional[_Site] = None


@dataclass
class _Include:
    path: str
    section: Optional[str]
    flags: Set[str]
    skip: List[str]
    level: Optional[int]


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


def _anchor(heading_text: str) -> str:
    """A heading's anchor, as the site's toc (pymdownx.slugs, lower case) and GitHub make it."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading_text)     # links: their text
    text = re.sub(r"<[^>]+>", "", text).replace("`", "")
    return _slug(text, "-")


def _headings(text: str) -> List[Tuple[int, int, str, int]]:
    """(line index, level, anchor, character offset) of each ATX heading outside code."""
    out, pos, fence = [], 0, None
    for i, line in enumerate(text.splitlines(keepends=True)):
        m = FENCE.match(line)
        stripped = line.strip()
        if fence is None and m:
            fence = m.group("fence")
        elif fence is not None:
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = None
        else:
            h = HEADING.match(line.rstrip("\n"))
            if h:
                out.append((i, len(h.group("hashes")), _anchor(h.group("text")), pos))
        pos += len(line)
    return out


def _cut(text: str, anchor: str) -> Optional[Tuple[int, int]]:
    """Line range [first, end) of the section with this anchor, heading included."""
    heads = _headings(text)
    n_lines = len(text.splitlines())
    for k, (line, level, a, _) in enumerate(heads):
        if a == anchor:
            end = next((ln for ln, lv, _, _ in heads[k + 1:] if lv <= level), n_lines)
            return line, end
    return None


def _lines(text: str) -> List[str]:
    return text.splitlines(keepends=True)


def _select(text: str, inc: _Include, where: str) -> Optional[str]:
    """The part of a file an include marker asks for."""
    if inc.section:
        span = _cut(text, inc.section)
        if span is None:
            log.warning("%s: %s has no section #%s", where, inc.path, inc.section)
            return None
        lines = _lines(text)[span[0]:span[1]]
        if "strip-title" in inc.flags:
            lines = lines[1:]
        text = "".join(lines)
    elif "strip-title" in inc.flags:
        text = TITLE.sub("", text, count=1)
    for anchor in inc.skip:
        span = _cut(text, anchor)
        if span is None:
            log.warning("%s: skip=%s: %s has no such section here", where, anchor, inc.path)
            continue
        lines = _lines(text)
        text = "".join(lines[:span[0]] + lines[span[1]:])
    if "intro-only" in inc.flags:
        heads = _headings(text)
        first = heads[0][3] if heads and heads[0][3] > 0 else None
        if heads and heads[0][3] == 0:            # the selection starts with its own heading
            first = heads[1][3] if len(heads) > 1 else None
        if first is not None:
            text = text[:first]
    if inc.level is not None:
        heads = _headings(text)
        if heads:
            shift = inc.level - heads[0][1]
            lines = _lines(text)
            for line, level, _, _ in heads:
                new = max(1, min(6, level + shift))
                lines[line] = "#" * new + lines[line].lstrip("#")
            text = "".join(lines)
    return text.rstrip("\n") + "\n"


def _parse(m: re.Match, where: str) -> _Include:
    flags, skip, level = set(), [], None
    for token in m.group("opts").split():
        key, eq, value = token.partition("=")
        if not eq and key in FLAGS:
            flags.add(key)
        elif eq and key == "skip":
            skip += [s for s in value.split(",") if s]
        elif eq and key == "level" and value.isdigit() and 1 <= int(value) <= 6:
            level = int(value)
        else:
            log.warning("%s: unknown include option %r (known: %s, skip=..., level=N)",
                        where, token, ", ".join(sorted(FLAGS)))
    section = m.group("section")
    return _Include(posixpath.normpath(m.group("path")), section, flags, skip, level)


def _split(target: str) -> Tuple[str, str]:
    """('path', '?query#fragment') of a link target."""
    cut = min((i for i in (target.find("?"), target.find("#")) if i >= 0), default=len(target))
    return target[:cut], target[cut:]


def _fragment(suffix: str) -> str:
    return suffix.partition("#")[2]


def _resolve(raw: str, source: Optional[str], app_links: bool) -> Tuple[str, str, str]:
    """What a link points at: ("keep", "", ""), ("url", url, ""), or ("repo", path, suffix).

    `source` is the repository path of the included file the link is in, or None for a
    hand-written page, where only repo: links are rewritten."""
    target = raw[1:-1] if raw.startswith("<") else raw
    if target.startswith("repo:"):
        path, suffix = _split(target[len("repo:"):])
        return "repo", posixpath.normpath(path.lstrip("/") or "."), suffix
    if source is None or SCHEME.match(target):
        return "keep", "", ""
    if target.startswith("#"):                     # an anchor of the included file itself
        return "repo", source, target
    if target.startswith("/"):
        if app_links:
            return "url", _site.dashboard_url.rstrip("/") + target, ""
        path, suffix = _split(target.lstrip("/"))
        return "repo", posixpath.normpath(path or "."), suffix
    path, suffix = _split(target)
    if not path:                                   # "?query" alone: nothing to resolve
        return "keep", "", ""
    return "repo", posixpath.normpath(posixpath.join(posixpath.dirname(source), path)), suffix


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
    anchor = _fragment(suffix)
    page = _site.sections.get((path, anchor)) if anchor else _site.included.get(path)
    if page is not None:                           # a file or section this site shows
        if page == page_uri:
            return suffix if anchor else "#"
        return posixpath.relpath(page, page_dir) + suffix
    if anchor and absolute.is_file() and absolute.suffix == ".md":
        if _cut(absolute.read_text(encoding="utf-8"), anchor) is None:
            log.warning("%s: links to %s#%s, which has no such section", where, path, anchor)
    if path in _site.images:
        return posixpath.relpath(f"{ASSETS}/{path}", page_dir) + suffix
    kind = "tree" if absolute.is_dir() else "blob"
    return f"{_site.repo_url}/{kind}/{_site.branch}/{'' if path == '.' else path}{suffix}"


def _rewrite(text: str, source: Optional[str], page_uri: str, app_links: bool, where: str) -> str:
    code = _code_ranges(text)

    def sub(m: re.Match) -> str:
        if _in_code(code, m.start()):
            return m.group(0)
        kind, value, suffix = _resolve(m.group("target"), source, app_links)
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


def _includes(text: str, where: str) -> List[Tuple[re.Match, _Include]]:
    code = _code_ranges(text)
    return [(m, _parse(m, where)) for m in INCLUDE.finditer(text) if not _in_code(code, m.start())]


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


# ── the venues, from config/venues.json ──────────────────────────────────────

def _percent(rate: float) -> str:
    return f"{rate * 100:.4f}".rstrip("0").rstrip(".") + "%"


def _fees(fees: dict) -> str:
    if fees.get("model") == "maker_taker":
        text = f"maker {_percent(fees['maker'])}, taker {_percent(fees['taker'])}"
    else:
        text = _percent(fees.get("rate", 0.0))
    return text + ("" if fees.get("calibrated") else " (not calibrated)")


def _sessions(sessions) -> str:
    if sessions == "24/7":
        return "24/7"
    return ", ".join(f"{a}–{b}" for a, b in sessions)


def _lot(lot: dict) -> str:
    default = lot.get("default", 1)
    shown = f"{default:g}" if isinstance(default, (int, float)) else str(default)
    return f"per instrument (default {shown})" if lot.get("rule") == "per_instrument" else shown


def _options(opts: dict) -> str:
    if not opts:
        return "none"
    return ", ".join(f"`{k}`: `{json.dumps(v)}`" for k, v in opts.items())


def _venues_table(connector: Optional[str], where: str) -> str:
    spec = json.loads((_site.root / VENUES).read_text(encoding="utf-8"))
    venues = spec.get("venues", {})
    if connector is None:
        out = ["| Venue | Name | Timezone | Currency | Sessions (local time) | Lot size | Fees per side "
               "| Connectors (default first) |", "|---|---|---|---|---|---|---|---|"]
        for mic, v in venues.items():
            default = v.get("default_connector", "")
            names = [default] + [c for c in v.get("connectors", {}) if c != default]
            out.append(f"| `{mic}` | {_cell(v.get('name', mic))} | {v['timezone']} "
                       f"| {v['currency']} | {_sessions(v.get('sessions', []))} | {_lot(v.get('lot_size', {}))} "
                       f"| {_fees(v.get('fees', {}))} | {', '.join(f'`{c}`' for c in names if c)} |")
    else:
        served = {mic: v for mic, v in venues.items() if connector in v.get("connectors", {})}
        if not served:
            log.warning("%s: no venue in %s is served by connector %r", where, VENUES, connector)
        out = [f"| Venue | Currency | Default connector for the venue | `{connector}` settings |",
               "|---|---|---|---|"]
        for mic, v in served.items():
            default = "yes" if v.get("default_connector") == connector else f"no (`{v.get('default_connector')}`)"
            out.append(f"| `{mic}` | {v['currency']} | {default} | {_options(v['connectors'][connector])} |")
    out += ["", f"Generated from [`{VENUES}`](repo:{VENUES}) when this site was built."]
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
    """Find what the pages include (and on which page each section ends up), publish the images
    those files show, and publish the API contract. Runs after the gen-files plugin, so the
    generated API reference pages are seen too."""
    _site.included.clear()
    _site.sections.clear()
    _site.images.clear()
    # (markdown text, the repository file its links are relative to, or None for a page's own
    # text, where only repo: links count)
    texts: List[Tuple[str, Optional[str]]] = []
    for page in files.documentation_pages():
        text = Path(page.abs_src_path).read_text(encoding="utf-8")
        texts.append((text, None))
        for _, inc in _includes(text, page.src_uri):
            raw = _read(inc.path, page.src_uri)
            if raw is None:
                continue
            part = _select(raw, inc, page.src_uri)
            if part is None:
                continue
            if not inc.section:
                _site.included.setdefault(inc.path, page.src_uri)
            for _, _, anchor, _ in _headings(part):
                _site.sections.setdefault((inc.path, anchor), page.src_uri)
            if inc.section:
                _site.sections.setdefault((inc.path, inc.section), page.src_uri)
            texts.append((part, inc.path))

    for text, source in texts:
        code = _code_ranges(text)
        for m in LINK.finditer(text):
            if _in_code(code, m.start()):
                continue
            kind, target, _ = _resolve(m.group("target"), source, app_links=False)
            if (kind == "repo" and _is_image(target, m.group("bang")) and target not in _site.images
                    and (_site.root / target).is_file()):
                _site.images.add(target)
                files.append(File.generated(config, f"{ASSETS}/{target}", abs_src_path=str(_site.root / target)))

    contract = _site.root / OPENAPI
    if contract.is_file():
        files.append(File.generated(config, OPENAPI_URI, abs_src_path=str(contract)))
    else:
        log.warning("repo_docs: %s is missing, so the HTTP API reference has no contract to show", OPENAPI)
    return files


def on_nav(nav, config, files):
    """The generated API reference's navigation file has been read by mkdocs-literate-nav by now;
    keep it out of the built site and the search index."""
    for file in files:
        if file.src_uri == NAV_FILE:
            file.inclusion = InclusionLevel.EXCLUDED
    return nav


def on_page_markdown(markdown, page, config, files):
    where = page.file.src_uri
    code = _code_ranges(markdown)

    def venues(m: re.Match) -> str:
        if _in_code(code, m.start()):
            return m.group(0)
        return _venues_table(m.group("connector"), where)

    markdown = VENUES_TABLE.sub(venues, markdown)            # before links: it links with repo:
    markdown = _rewrite(markdown, None, where, app_links=False, where=where)

    code = _code_ranges(markdown)

    def routes(m: re.Match) -> str:
        if _in_code(code, m.start()):
            return m.group(0)
        return _routes(m.group("order"), where)

    markdown = ROUTES.sub(routes, markdown)
    code = _code_ranges(markdown)
    edit: List[str] = []

    def include(m: re.Match) -> str:
        if _in_code(code, m.start()):
            return m.group(0)
        inc = _parse(m, where)
        if "app-links" in inc.flags and not _site.dashboard_url:
            log.warning("%s: app-links needs extra.dashboard_url in mkdocs.yml", where)
        raw = _read(inc.path, where)
        if raw is None:
            return ""
        part = _select(raw, inc, where)
        if part is None:
            return ""
        if "edit" in inc.flags:
            edit.append(inc.path)
        label = inc.path + (f"#{inc.section}" if inc.section else "")
        return _rewrite(part, inc.path, where, "app-links" in inc.flags, f"{label} (in {where})")

    markdown = INCLUDE.sub(include, markdown)
    if edit:
        # The page shows that file, so "edit this page" opens the file itself.
        page.edit_url = f"{_site.repo_url}/edit/{_site.branch}/{edit[0]}"
    return markdown
