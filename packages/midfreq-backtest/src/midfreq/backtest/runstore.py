"""
The run store: every top-level run (a backtest, a comparison, a grid search, a walk-forward)
saved with everything needed to read it back and to reproduce it.

    runs/<run_id>/
      run.json            how it was run: kind, strategy, params, symbols, dates, costs, risk
                          settings, objective, git commit, data cut-off, source, created (UTC)
      metrics.parquet     one row of headline numbers
      equity.parquet      the equity curve (t, equity)
      trades.parquet      every trade
      windows.parquet     walk-forward: per window, the chosen params and the in-sample AND
                          out-of-sample metrics
      grid.parquet        grid search: every combination's params and metrics
      ...                 any other table a run kind produces

There is no shared index file: the catalogue is built from the run.json manifests, so two
processes saving at once cannot corrupt it. Sub-backtests inside an optimization are not saved
one by one; the optimization is saved once, with its grid or windows table.

`runs/` is not in git (it grows with every run). A run the report cites is promoted into
results/ (`python -m midfreq.backtest.runstore promote <run_id>`), as committed CSV and JSON.

Query across runs with pandas (RunStore.list) or DuckDB, e.g.
    SELECT * FROM 'runs/*/windows.parquet'
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

RUN_KINDS = ("backtest", "compare", "grid_search", "walk_forward")


@dataclass
class RunRecord:
    kind: str                                         # one of RUN_KINDS
    meta: Dict[str, Any]                              # how it was run (JSON-serialisable)
    metrics: Dict[str, Any]                           # headline numbers
    tables: Dict[str, pd.DataFrame] = field(default_factory=dict)   # equity, trades, windows, grid, ...
    run_id: Optional[str] = None                      # set by the store on save
    created: Optional[datetime] = None                # UTC, set by the store on save


class RunStore(ABC):
    """Where runs are saved and read back."""

    @abstractmethod
    def save(self, record: RunRecord) -> str:
        """Write the record (atomically: a half-written run is never listed) and return its id,
        `<UTC yyyymmddThhmmss>_<kind>_<short hash>`."""

    @abstractmethod
    def list(self, kind: Optional[str] = None, strategy: Optional[str] = None,
             limit: int = 200) -> pd.DataFrame:
        """One row per run, newest first: run_id, created, kind, source, strategy, universe,
        n_symbols, start, end, git_commit, and the headline metrics as columns."""

    @abstractmethod
    def load(self, run_id: str) -> RunRecord:
        """The whole run. KeyError if there is no such run."""

    @abstractmethod
    def table(self, run_id: str, name: str) -> pd.DataFrame:
        """One table of a run. KeyError if the run or the table does not exist."""

    @abstractmethod
    def delete(self, run_id: str) -> None:
        """Remove a run. KeyError if there is no such run."""


def default_run_store() -> RunStore:
    """The store this process saves to: MIDFREQ_RUNS (a folder, or gs://bucket/prefix), else
    <repo>/runs."""
    raise NotImplementedError


def record_from_backtest(*args, **kwargs) -> RunRecord:   # filled in by the implementation
    raise NotImplementedError
