"""Only the connectors (midfreq.data.adapters) may import a venue's SDK, so adding a venue means
adding a connector and nothing else changes (docs/LIVE_ARCHITECTURE.md).

midfreq.live is exempt until its original engine, which calls the Moomoo SDK directly, is retired.
import-linter cannot check external imports in this namespace-package layout, hence this test."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENUE_SDKS = {"moomoo", "futu", "ccxt", "ib_insync", "ibapi", "binance"}
CHECKED = ["midfreq-core", "midfreq-strategies", "midfreq-backtest", "midfreq-trading", "midfreq-research"]


def sdk_imports(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        else:
            continue
        for name in names:
            if name.split(".")[0] in VENUE_SDKS:
                yield node.lineno, name


def test_no_package_outside_the_connectors_imports_a_venue_sdk():
    found = []
    for package in CHECKED:
        for path in sorted((ROOT / "packages" / package / "src").rglob("*.py")):
            found += [f"{path.relative_to(ROOT)}:{line} imports {name}" for line, name in sdk_imports(path)]
    assert not found, "venue SDK imported outside midfreq.data.adapters:\n" + "\n".join(found)
