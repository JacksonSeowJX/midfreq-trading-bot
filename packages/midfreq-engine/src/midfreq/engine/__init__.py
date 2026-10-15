"""
midfreq.engine: the native backtest core, in C++17 behind a pybind11 module (`_core`).

The C++ library (packages/midfreq-engine/include, src) holds the hot path of a backtest: the merged
event stream, the event loop, portfolio accounting, the risk rules, the metrics and the ported
candle strategies. It is built to reproduce midfreq.backtest.backtester.Backtester exactly: the
same trades, the same equity curve, bit for bit, because every operation rounds as CPython's does
(see include/midfreq/engine/pymath.hpp).

This package imports nothing else from midfreq. midfreq.backtest.native adapts it to the
Backtester interface (DataStorage, Portfolio, RiskManager, the strategy classes) and to the
optimiser; use that rather than this module directly.

    from midfreq import engine
    engine.available()          # False when the extension was not built (e.g. on the VM)
    engine.strategies()         # the strategies implemented natively
"""
from __future__ import annotations

import sys

try:
    from midfreq.engine import _core
except ImportError as exc:  # not built: everything falls back to the Python backtester
    _core = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

__all__ = ["available", "import_error", "strategies", "sum_mode", "core"]


def available() -> bool:
    """True when the compiled extension imported."""
    return _core is not None


def import_error():
    """Why the extension is unavailable (an ImportError), or None."""
    return _IMPORT_ERROR


def core():
    """The compiled module; raises ImportError when it is not built."""
    if _core is None:
        raise ImportError(f"midfreq.engine._core is not built: {_IMPORT_ERROR}")
    return _core


def strategies() -> list:
    return list(_core.strategies()) if _core is not None else []


def sum_mode() -> str:
    """How this interpreter's sum() adds floats: compensated since 3.12, left to right before."""
    return "neumaier" if sys.version_info >= (3, 12) else "naive"
