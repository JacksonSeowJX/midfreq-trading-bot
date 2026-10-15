"""
Generates the Python API reference (mkdocs-gen-files script, mkdocs.yml `plugins: gen-files`).

One page per public module of the packages in packages/*/src/midfreq/, written under
api/python/ when the site is built, plus api/python/SUMMARY.md, the navigation that
mkdocs-literate-nav reads. Nothing is written to site_docs/: the pages exist only in the build, so
the reference cannot drift from the code.

- A module page is the module's docstring and members, rendered by mkdocstrings from the source
  (griffe's static analysis; nothing is imported).
- A package page (`<package>/index.md`, the section's index through mkdocs-section-index) is the
  distribution's README.md for a top-level package (midfreq.core, midfreq.data, ...), or the
  package docstring for a subpackage, followed by the names the package re-exports and a table
  of its modules with the first sentence of each module's docstring. A README section that a
  hand-written page includes on its own (`<!-- include: packages/x/README.md#section -->`) is
  left out of the package page, which links to that page instead, so no text appears twice.
- The packages are listed in layer order: core, engine, data, strategies, backtest, live, research.
- Private modules (a name starting with "_", including __main__) are left out, and so is any
  folder without an __init__.py below midfreq/ (an implicit namespace subpackage, which griffe
  does not traverse): midfreq.data.providers.
- "Edit this page" on a module page opens the module's source file.
"""
from __future__ import annotations

import ast
import posixpath
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mkdocs_gen_files

ROOT = Path(__file__).resolve().parents[2]          # the repository
PACKAGES = ROOT / "packages"
DOCS = ROOT / "site_docs"
OUT = Path("api/python")                             # under docs_dir, in the build only

SECTION_INCLUDE = re.compile(r"<!--[ \t]*include:[ \t]*(?P<path>[^\s#]+)#(?P<section>[\w-]+)")
FENCED = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$", re.M | re.S)
HEADING = re.compile(r"^#{2,6}[ \t]+(?P<text>.+?)[ \t]*$", re.M)

nav = mkdocs_gen_files.Nav()


def _shown_elsewhere() -> Dict[str, List[Tuple[str, str]]]:
    """README path -> [(section anchor, page)] for each README section a hand-written page
    includes on its own (`<!-- include: packages/x/README.md#section -->`). Those sections are left
    out of the package page, which links to the page that shows them instead."""
    out: Dict[str, List[Tuple[str, str]]] = {}
    for md in sorted(DOCS.rglob("*.md")):
        text = FENCED.sub("", md.read_text(encoding="utf-8"))
        text = re.sub(r"`[^`\n]*`", "", text)
        for m in SECTION_INCLUDE.finditer(text):
            out.setdefault(m["path"], []).append((m["section"], md.relative_to(DOCS).as_posix()))
    return out


def _section_titles(readme: Path) -> Dict[str, str]:
    """anchor -> heading text, for the README's level-2 to level-6 headings."""
    from pymdownx.slugs import slugify
    slug = slugify(case="lower")
    text = FENCED.sub("", readme.read_text(encoding="utf-8"))
    return {slug(m["text"].replace("`", ""), "-"): m["text"] for m in HEADING.finditer(text)}


ELSEWHERE = _shown_elsewhere()


# ── reading modules without importing them ───────────────────────────────────

def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _summary(path: Path) -> str:
    """The first sentence of a module's docstring, on one line."""
    doc = ast.get_docstring(_tree(path)) or ""
    lines = [line.strip() for line in doc.strip().splitlines()]
    # skip a title underlined with ===/---, as several older modules have
    if len(lines) > 1 and lines[1] and set(lines[1]) <= {"=", "-"}:
        lines = lines[2:]
    paragraph = " ".join(_take_paragraph(lines))
    m = re.match(r"(.+?[.!?])(\s|$)", paragraph)
    text = m.group(1) if m else paragraph
    return text.replace("|", "\\|")


def _take_paragraph(lines: List[str]) -> List[str]:
    out: List[str] = []
    for line in lines:
        if not line:
            if out:
                break
            continue
        out.append(line)
    return out


def _reexports(init: Path, package: str) -> List[Tuple[str, str]]:
    """(name, defining module) for each name in `__all__` that the package imports from one of
    its own modules."""
    tree = _tree(init)
    exported: Optional[List[str]] = None
    sources: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__"
                                                for t in node.targets):
            exported = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(package + "."):
            for alias in node.names:
                sources[alias.asname or alias.name] = node.module
    if not exported:
        return []
    return [(name, sources[name]) for name in exported if name in sources]


# ── pages ────────────────────────────────────────────────────────────────────

def _module_page(identifier: str, source: Path, doc_path: Path) -> None:
    with mkdocs_gen_files.open(doc_path, "w") as f:
        f.write(f"::: {identifier}\n")
    mkdocs_gen_files.set_edit_path(doc_path, Path("..") / source.relative_to(ROOT))


def _package_page(identifier: str, init: Path, doc_path: Path, readme: Optional[Path],
                  modules: List[Tuple[str, Path, str]], subpackages: List[Tuple[str, Path, str]]) -> None:
    out: List[str] = []
    if readme is not None:
        distribution = readme.parent.name
        readme_path = readme.relative_to(ROOT).as_posix()
        elsewhere = ELSEWHERE.get(readme_path, [])
        skip = f" skip={','.join(a for a, _ in elsewhere)}" if elsewhere else ""
        out += [f"# {identifier}", "",
                f"The `{identifier}` package, from the `{distribution}` distribution "
                f"([`packages/{distribution}`](repo:packages/{distribution})). The overview "
                "below is the distribution's README.", "",
                f"<!-- include: {readme_path} strip-title{skip} -->", ""]
        if elsewhere:
            titles = _section_titles(readme)
            here = doc_path.parent.as_posix()
            out += ['!!! info "More of this README"',
                    "    These sections of the README are shown with the concepts they belong to:", ""]
            out += [f"    - [{titles.get(anchor, anchor)}]({posixpath.relpath(page, here)})"
                    for anchor, page in elsewhere]
            out.append("")
    else:
        out += [f"::: {identifier}", "    options:", "      members: false", ""]

    names = _reexports(init, identifier)
    if names:
        out += ["## Importable names", "",
                f"`{identifier}` re-exports these names, so `from {identifier} import "
                f"{names[0][0]}` works; each is documented in the module that defines it.", "",
                "| Name | Defined in |", "|---|---|"]
        out += [f"| [`{name}`][{module}.{name}] | [`{module}`][{module}] |" for name, module in names]
        out.append("")

    rows = subpackages + modules
    if rows:
        out += ["## Modules", "", "| Module | Summary |", "|---|---|"]
        out += [f"| [`{ident}`]({link}) | {summary} |" for ident, link, summary in rows]
        out.append("")

    with mkdocs_gen_files.open(doc_path, "w") as f:
        f.write("\n".join(out))
    mkdocs_gen_files.set_edit_path(doc_path, Path("..") / (readme or init).relative_to(ROOT))


def _walk(package_dir: Path, identifier: str, doc_dir: Path, readme: Optional[Path]) -> None:
    """Pages for one package and, recursively, its subpackages."""
    init = package_dir / "__init__.py"
    # navigation titles: the top-level package in full, then short names
    # (('midfreq.core',), ('midfreq.core', 'model'), ('midfreq.core', 'model', 'data'), ...)
    parts = identifier.split(".")
    nav_parts = (".".join(parts[:2]),) + tuple(parts[2:])
    nav[nav_parts] = (doc_dir / "index.md").relative_to(OUT).as_posix()

    modules: List[Tuple[str, Path, str]] = []
    subpackages: List[Tuple[str, Path, str]] = []
    for child in sorted(package_dir.iterdir()):
        if child.name.startswith(("_", ".")):
            continue
        if child.is_dir() and (child / "__init__.py").is_file():
            sub = f"{identifier}.{child.name}"
            subpackages.append((sub, Path(child.name) / "index.md", _summary(child / "__init__.py")))
            _walk(child, sub, doc_dir / child.name, None)
        elif child.is_file() and child.suffix == ".py":
            mod = f"{identifier}.{child.stem}"
            page = doc_dir / f"{child.stem}.md"
            modules.append((mod, Path(page.name), _summary(child)))
            nav[nav_parts + (child.stem,)] = page.relative_to(OUT).as_posix()
            _module_page(mod, child, page)

    _package_page(identifier, init, doc_dir / "index.md", readme,
                  [(m, p.as_posix(), s) for m, p, s in modules],
                  [(m, p.as_posix(), s) for m, p, s in subpackages])


# the packages in layer order, foundation first; any package not named here follows, by name
LAYER_ORDER = ["core", "engine", "data", "strategies", "backtest", "live", "research"]


def _layer(init: Path):
    name = init.parent.name
    return (LAYER_ORDER.index(name) if name in LAYER_ORDER else len(LAYER_ORDER), name)


for src in sorted(PACKAGES.glob("*/src/midfreq/*/__init__.py"), key=_layer):
    package_dir = src.parent
    distribution = package_dir.parents[2]
    readme = distribution / "README.md"
    _walk(package_dir, f"midfreq.{package_dir.name}", OUT / "midfreq" / package_dir.name,
          readme if readme.is_file() else None)

with mkdocs_gen_files.open(OUT / "SUMMARY.md", "w") as f:
    f.write("* [Overview](index.md)\n")
    f.writelines(nav.build_literate_nav())
