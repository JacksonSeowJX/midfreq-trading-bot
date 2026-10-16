"""
Keeping the C++ ports and the Python strategies in step, without having to.

The Python strategies are the definition; a C++ port is an accelerator that is used only while it
still matches. Each port is recorded with a fingerprint of the exact Python it was verified
against (packages/midfreq-engine/src/midfreq/engine/verified.json):

- per strategy: the class, its bases up to BaseStrategy, and the classes whose methods it calls
  (the ensemble uses the RSI, MACD and Bollinger strategies' indicators);
- shared by all: the backtester loop, the portfolio, the risk manager and its sizers, the candle.

A fingerprint is a hash of the code with docstrings, comments and formatting taken out, so
editing documentation changes nothing. When the Python changes, the port no longer matches and
midfreq.backtest.native runs that strategy on Python instead, saying why: a port that was not
updated can cost speed, never change a result.

    python -m midfreq.backtest.parity status [--github]   # which ports are current or stale
    python -m midfreq.backtest.parity verify [--slow]     # after porting: run the equivalence
                                                          # tests on every port, record if they pass

`verify` runs the tests with MIDFREQ_ENGINE_VERIFYING=1, which uses every port whatever its
fingerprint, so a port is recorded only once the C++ is shown to match the Python as it is now.
"""
from __future__ import annotations

import argparse
import ast
import functools
import hashlib
import inspect
import json
import os
import platform
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from midfreq.backtest.backtester import Backtester
from midfreq.core.candle import Candle
from midfreq.core.portfolio import Portfolio
from midfreq.core.risk_manager import (EqualDollarSizer, FixedFractionalSizer, FixedQuantitySizer,
                                       KellyCriterionSizer, RiskManager)
from midfreq.strategies import candle_strategies as cs

VERIFYING = "MIDFREQ_ENGINE_VERIFYING"

# what every port depends on: a change here makes every port stale
SHARED = (Backtester, Portfolio, RiskManager, FixedQuantitySizer, FixedFractionalSizer,
          KellyCriterionSizer, EqualDollarSizer, Candle)

# classes a strategy calls into beyond its own bases
DEPENDS = {cs.EnsembleStrategy: (cs.RSIStrategy, cs.MACDStrategy, cs.BollingerBandsStrategy)}


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return tree


def normalized_source(obj) -> str:
    """The code of a class or function with docstrings, comments and formatting removed."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    return ast.unparse(_strip_docstrings(tree))


def sources(cls: type) -> List[type]:
    """What a strategy's behaviour is read from: the class, its bases up to BaseStrategy, and its
    dependencies, each once."""
    out: List[type] = []
    for c in cls.__mro__:
        if isinstance(c, type) and issubclass(c, cs.BaseStrategy):
            out.append(c)
    for dep in DEPENDS.get(cls, ()):
        for c in sources(dep):
            if c not in out:
                out.append(c)
    return out


def _digest(objs: Iterable) -> str:
    h = hashlib.sha256()
    for obj in objs:
        h.update(f"{obj.__module__}.{obj.__qualname__}\n".encode())
        h.update(normalized_source(obj).encode())
        h.update(b"\n\0")
    return h.hexdigest()


@functools.lru_cache(maxsize=None)
def fingerprint(cls: type) -> str:
    return _digest(sources(cls))


@functools.lru_cache(maxsize=None)
def shared_fingerprint() -> str:
    return _digest(SHARED)


def ported_classes() -> List[type]:
    from midfreq.backtest.native import native_strategy_classes
    return native_strategy_classes()


# ─── The record ──────────────────────────────────────────────────────────────────────────────

def record_path() -> Optional[Path]:
    """verified.json beside the engine's Python package, or None when the engine is not installed."""
    try:
        from midfreq import engine
    except ImportError:
        return None
    return Path(engine.__file__).resolve().parent / "verified.json"


def _load(path: Optional[Path]) -> Optional[dict]:
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def verified() -> Optional[dict]:
    return _load(record_path())


def port_reason(cls: type, record: Optional[dict] = None) -> Optional[str]:
    """None when the C++ port of `cls` matches its Python; otherwise why it does not."""
    if os.environ.get(VERIFYING) == "1":
        return None
    record = verified() if record is None else record
    if not record:
        return "there is no record of the Python the C++ ports were verified against"
    if record.get("shared") != shared_fingerprint():
        return ("the backtester, portfolio or risk rules changed since the C++ ports were verified "
                "(python -m midfreq.backtest.parity verify)")
    want = (record.get("strategies") or {}).get(cls.__name__)
    if want is None:
        return f"{cls.__name__} has no verified C++ port"
    if want != fingerprint(cls):
        return (f"{cls.__name__} (or a class it builds on) changed since its C++ port was verified: port "
                f"the change to packages/midfreq-engine/src/strategies, then python -m midfreq.backtest.parity verify")
    return None


def status(record: Optional[dict] = None) -> List[Tuple[str, str, Optional[str]]]:
    """(strategy, "current" | "stale", reason) for every port."""
    record = verified() if record is None else record
    rows = []
    for cls in ported_classes():
        reason = port_reason(cls, record)
        rows.append((cls.__name__, "current" if reason is None else "stale", reason))
    return rows


def current_record() -> dict:
    return {
        "about": ("Fingerprints of the Python each C++ port was last verified to reproduce exactly "
                  "(midfreq.backtest.parity). Written by `python -m midfreq.backtest.parity verify` "
                  "after the equivalence tests pass; do not edit by hand."),
        "verified_on": date.today().isoformat(),
        "python": platform.python_version(),
        "shared": shared_fingerprint(),
        "strategies": {cls.__name__: fingerprint(cls) for cls in ported_classes()},
    }


# ─── Command line ────────────────────────────────────────────────────────────────────────────

def _repo_root() -> Path:
    from midfreq.core.paths import repo_root
    return repo_root()


def _equivalence_tests(slow: bool) -> List[str]:
    tests = ["packages/midfreq-engine/tests", "packages/midfreq-backtest/tests/test_native.py"]
    if slow:
        tests.append("tests/slow/test_native_real_data.py")
    return tests


def cmd_status(github: bool) -> int:
    rows = status()
    width = max(len(n) for n, _, _ in rows)
    for name, state, reason in rows:
        print(f"{name:<{width}}  {state}" + (f"  ({reason})" if reason else ""))
        if github and reason:
            print(f"::warning title=C++ port stale::{name} runs on the Python backtester: {reason}")
    stale = sum(state == "stale" for _, state, _ in rows)
    print(f"{len(rows) - stale} of {len(rows)} C++ ports current" + (", the rest run on Python" if stale else ""))
    return 0


def cmd_verify(slow: bool, record_to: Optional[Path] = None) -> int:
    root = _repo_root()
    path = record_to or record_path()
    if path is None:
        print("the C++ engine is not installed (uv sync)", file=sys.stderr)
        return 2
    if record_to is None and root not in path.parents:
        print(f"refusing to write {path}: it is not inside the checkout at {root}", file=sys.stderr)
        return 2
    env = dict(os.environ, **{VERIFYING: "1"})
    if slow:
        env["MIDFREQ_SLOW"] = "1"
    print("running the equivalence tests on every C++ port (rebuild first with uv sync if the C++ changed)")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           *_equivalence_tests(slow)], cwd=root, env=env)
    if done.returncode != 0:
        print("the C++ does not match the Python: nothing recorded", file=sys.stderr)
        return 1
    old = _load(path) or {}
    new = current_record()
    changed = [n for n, h in new["strategies"].items() if (old.get("strategies") or {}).get(n) != h]
    path.write_text(json.dumps(new, indent=2) + "\n")
    shared = " and the shared code" if old.get("shared") != new["shared"] else ""
    print(f"recorded {path}: " + (", ".join(changed) + shared if changed or shared else "no change"))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m midfreq.backtest.parity", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="which C++ ports are current or stale")
    st.add_argument("--github", action="store_true", help="also print GitHub Actions warnings for stale ports")
    ve = sub.add_parser("verify", help="run the equivalence tests on every port and record them if they pass")
    ve.add_argument("--slow", action="store_true", help="include the real-data test (needs the data/ cache)")
    ve.add_argument("--record-to", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        return cmd_status(args.github)
    return cmd_verify(args.slow, args.record_to)


if __name__ == "__main__":
    sys.exit(main())
