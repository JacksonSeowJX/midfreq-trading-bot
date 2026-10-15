"""
Saved runs: a finished job made into a RunRecord for the run store (midfreq.backtest.runstore),
and saved runs read back as the Run history shows them (routers/runs.py).

    record_for_job(kind, request, plan, result, job_id) -> RunRecord
    save_job(store, kind, request, plan, job_id, result) -> run_id     # what a job calls when it is done
    summaries(store.list(...)) -> [RunSummary];  summary_of(record) -> RunSummary

What a job's run holds, in the shapes runstore's record_from_* helpers give runs saved by
scripts, so the Run history reads both alike (the store writes `meta` as run.json, adding the
provenance: git_commit, host, ...; and each table as Parquet):

  kind      backtest | compare | grid_search | walk_forward (an optimize job is one of the last two)
  meta      source "api", job_id, job_kind, strategy, params, symbols, n_symbols, universe, symbol,
            market, currency, timeframe, start, end, initial_capital, commission_rate (as charged),
            slippage_bps, risk (the request's settings), data_cutoff (the last candle the run saw),
            request (as posted) and, per kind: strategies (the labels) and runs (compare); method,
            objective, objective_label and best_params, max_combinations (grid) or n_splits,
            train_pct (walk-forward)
  metrics   backtest: the backtester's single values with n_open_positions; compare: the best
            strategy's (by return) with best_strategy and n_strategies; grid_search: the best
            combination's (in sample) with objective_value, combinations, grid_size; walk_forward:
            the optimizer's summary (avg_oos_return, avg_train_return, consistency_pct,
            wf_efficiency, ...) with n_skipped
  tables    backtest: equity (t, equity), trades, positions (open at the end, if any); compare:
            strategies (label, strategy, params as JSON, n_symbols, the metrics; best return
            first) and equity (label, t, equity); grid_search: grid (the parameters, the metrics,
            _objective: the value ranked on; best first); walk_forward: windows (window, train
            and test dates, the chosen parameters, is_<metric> in sample: the objective it was
            chosen on and the train return; oos_<metric> out of sample: every test metric)

`universe` is the universe's name for a basket (cross-sectional) strategy and otherwise the
symbol traded (both of a pair), so the Run history can say what every run traded. Only single
values are kept as metrics (the backtester's open_positions becomes the positions table).
"""
import json
import logging
import math
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
from pydantic import ValidationError

from midfreq.api import runs
from midfreq.api import schemas as s
from midfreq.api.convert import plain, records, utc
from midfreq.backtest.runstore import RunRecord, RunStore

log = logging.getLogger(__name__)

SOURCE = "api"
_SCALAR = (bool, int, float, str, type(None))
# RunStore.list's columns that are not metrics
LISTED = ("run_id", "created", "kind", "source", "strategy", "universe", "n_symbols", "timeframe", "start", "end",
          "objective", "method", "git_commit")


# ── a store that cannot be opened ───────────────────────────────────────────

class RunStoreUnavailable(RuntimeError):
    """The run store cannot be used here; the message says why."""


class UnavailableRunStore(RunStore):
    """Stands in for a run store that could not be made: every call raises RunStoreUnavailable."""

    def __init__(self, reason: str):
        self.reason = reason

    def _fail(self):
        raise RunStoreUnavailable(self.reason)

    def save(self, record):
        self._fail()

    def list(self, kind=None, strategy=None, limit=200):
        self._fail()

    def load(self, run_id):
        self._fail()

    def table(self, run_id, name):
        self._fail()

    def delete(self, run_id):
        self._fail()


# ── a job's run, as the store keeps it ──────────────────────────────────────

def _scalars(d: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {str(k): v for k, v in (d or {}).items() if isinstance(v, _SCALAR)}


def _unique(items: Iterable[Any]) -> List[Any]:
    return list(dict.fromkeys(items))


def _times(values: List[datetime]) -> pd.Series:
    return pd.Series(pd.to_datetime(values, utc=True), dtype="datetime64[ns, UTC]")


def _iso(value: Optional[datetime]) -> Optional[str]:
    t = utc(value)
    return t.isoformat() if t is not None else None


def _meta(kind: str, request: Dict[str, Any], plan: runs.Plan, job_id: str) -> Dict[str, Any]:
    symbols = _unique(sym for run in plan.runs for sym in run.symbols)
    traded = _unique(request.get("universe") if runs.is_basket(run.strategy) else " / ".join(run.symbols)
                     for run in plan.runs)
    meta = {
        "source": SOURCE, "job_id": job_id, "job_kind": kind,
        "strategy": ", ".join(_unique(run.strategy for run in plan.runs)),
        "symbols": symbols, "n_symbols": len(symbols), "universe": ", ".join(str(t) for t in traded if t),
        "symbol": plan.symbol, "market": plan.market, "currency": runs.MARKETS[plan.market]["currency"],
        "timeframe": plan.timeframe, "start": plan.start.isoformat(), "end": plan.end.isoformat(),
        "initial_capital": plan.initial_capital, "commission_rate": plan.commission_rate,
        "slippage_bps": plan.slippage_bps, "risk": dict(plan.risk),
    }
    if any(run.strategy == "Pairs Trading" for run in plan.runs):
        meta["pair_symbol"] = request.get("pair_symbol")
    return meta


def _equity_frame(points: List[s.SeriesPoint]) -> pd.DataFrame:
    return pd.DataFrame({"t": _times([p.t for p in points]), "equity": [float(p.v) for p in points]})


def _backtest(result: s.BacktestResult, meta: Dict[str, Any]) -> RunRecord:
    trades = pd.DataFrame([t.model_dump() for t in result.trades], columns=list(s.TradeOut.model_fields))
    trades["timestamp"] = _times(list(trades["timestamp"]))
    tables = {"equity": _equity_frame(result.equity), "trades": trades}
    positions = result.metrics.get("open_positions")
    if isinstance(positions, dict) and positions:
        tables["positions"] = pd.DataFrame([{"symbol": sym, **(pos if isinstance(pos, dict) else {"qty": pos})}
                                            for sym, pos in positions.items()])
    metrics = {**_scalars(result.metrics), "n_open_positions": len(positions) if isinstance(positions, dict) else 0}
    meta.update(params=dict(result.params), data_cutoff=_iso(result.equity[-1].t) if result.equity else None)
    return RunRecord(kind="backtest", meta=meta, metrics=metrics, tables=tables)


def _compare(result: s.CompareResult, meta: Dict[str, Any], plan: runs.Plan) -> RunRecord:
    by_label = {run.label: run for run in plan.runs}
    rows = [{"label": r.strategy, "strategy": by_label[r.strategy].strategy if r.strategy in by_label else r.strategy,
             "params": json.dumps(r.params, sort_keys=True),
             "n_symbols": len(by_label[r.strategy].symbols) if r.strategy in by_label else None,
             **_scalars(r.metrics)} for r in result.rows]                 # best return first, as the job ranks them
    curves = [(label, p) for label, points in result.equity.items() for p in points]
    equity = pd.DataFrame({"label": [label for label, _ in curves], "t": _times([p.t for _, p in curves]),
                           "equity": [float(p.v) for _, p in curves]})
    ends = [points[-1].t for points in result.equity.values() if points]
    meta.update(strategies=[r.strategy for r in result.rows], params={r.strategy: dict(r.params) for r in result.rows},
                runs=[{"label": run.label, "strategy": run.strategy, "params": dict(run.params),
                       "symbols": list(run.symbols)} for run in plan.runs],
                skipped=[run.label for run in plan.runs if run.label not in result.equity],
                data_cutoff=_iso(max(ends)) if ends else None)
    best = result.rows[0]
    metrics = {**_scalars(best.metrics), "best_strategy": best.strategy, "n_strategies": len(result.rows)}
    return RunRecord(kind="compare", meta=meta, metrics=metrics,
                     tables={"strategies": pd.DataFrame(rows), "equity": equity})


def _grid(result: s.OptimizeResult, meta: Dict[str, Any], plan: runs.Plan) -> RunRecord:
    grid = pd.DataFrame([{**_scalars(r.params), **_scalars(r.metrics)} for r in result.rows])     # best first
    best = result.rows[0]
    metrics = {**{k: v for k, v in _scalars(best.metrics).items() if k != "_objective"},
               "objective_value": best.metrics.get("_objective"), "combinations": len(result.rows),
               "grid_size": result.summary.get("grid_size")}
    meta.update(method="grid", objective=result.objective, objective_label=result.summary.get("objective_label"),
                max_combinations=plan.max_combinations, grid_size=result.summary.get("grid_size"),
                best_params=dict(best.params), data_cutoff=None)
    return RunRecord(kind="grid_search", meta=meta, metrics=metrics, tables={"grid": grid})


def _walk_forward(result: s.OptimizeResult, meta: Dict[str, Any], plan: runs.Plan) -> RunRecord:
    """One row per window: its dates, the parameters chosen on its train span (as columns; param_<name>
    where a name is taken), the in-sample figures the job has (is_<objective>: the value they were
    chosen on; is_return_pct: the train return) and every out-of-sample figure (oos_<metric>)."""
    train_returns = result.summary.get("train_returns") or []
    rows = []
    for i, w in enumerate(result.windows):
        row: Dict[str, Any] = {"window": w.index, "train_start": w.train_start, "train_end": w.train_end,
                               "test_start": w.test_start, "test_end": w.test_end}
        for key, value in _scalars(w.params).items():
            row[key if key not in row else f"param_{key}"] = value
        row[f"is_{result.objective}"] = w.train_objective
        if i < len(train_returns) and result.objective != "return_pct":
            row["is_return_pct"] = train_returns[i]
        row.update({f"oos_{k}": v for k, v in _scalars(w.test_metrics).items()})
        rows.append(row)
    windows = pd.DataFrame(rows)
    for col in ("train_start", "train_end", "test_start", "test_end"):
        windows[col] = _times(list(windows[col]))
    metrics = {k: v for k, v in _scalars(result.summary).items() if k not in ("objective_label", "error")}
    metrics["n_skipped"] = len(result.summary.get("skipped_windows") or [])
    meta.update(method="walk_forward", objective=result.objective,
                objective_label=result.summary.get("objective_label"), n_splits=plan.n_splits,
                train_pct=plan.train_pct, data_cutoff=_iso(max(w.test_end for w in result.windows)))
    return RunRecord(kind="walk_forward", meta=meta, metrics=metrics, tables={"windows": windows})


def record_for_job(kind: str, request: Dict[str, Any], plan: runs.Plan, result: Any, job_id: str = "") -> RunRecord:
    """The run a finished job made, as the store keeps it (the module docstring lists what is kept)."""
    meta = _meta(kind, request, plan, job_id)
    if kind == "backtest":
        record = _backtest(result, meta)
    elif kind == "compare":
        record = _compare(result, meta, plan)
    elif kind == "optimize" and result.method == "grid":
        record = _grid(result, meta, plan)
    elif kind == "optimize":
        record = _walk_forward(result, meta, plan)
    else:
        raise ValueError(f"no run kind {kind!r}")
    record.meta["request"] = request
    return record


def save_job(store: RunStore, kind: str, request: Dict[str, Any], plan: runs.Plan, job_id: str, result: Any) -> str:
    """Save a finished job's run; its run id."""
    return store.save(record_for_job(kind, request, plan, result, job_id=job_id))


# ── saved runs, read back ───────────────────────────────────────────────────

def _text(value: Any) -> Optional[str]:
    value = plain(value)
    return None if value is None or value == "" else str(value)


def _int(value: Any) -> Optional[int]:
    value = plain(value)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _day(value: Any) -> Optional[str]:
    """A run's start or end: the date (2026-01-05) when it has no time of day, else ISO 8601."""
    value = plain(value)
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return str(value)
    if ts is pd.NaT:
        return None
    if ts.hour == ts.minute == ts.second == ts.microsecond == ts.nanosecond == 0:
        return ts.date().isoformat()
    return ts.isoformat()


def _created_from_id(run_id: str) -> Optional[datetime]:
    # run ids begin with the UTC time they were saved: 20261016T043105_backtest_ab12cd
    try:
        return datetime.strptime(run_id[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def summary_from_row(row: Dict[str, Any]) -> Optional[s.RunSummary]:
    """One row of RunStore.list as a RunSummary: its number columns are the metrics (a column
    other runs have and this one does not is NaN there, and is left out). None if the row cannot
    be read (logged)."""
    metrics = {k: v for k, v in row.items() if k not in LISTED and isinstance(v, (int, float))
               and not isinstance(v, bool) and math.isfinite(v)}
    run_id = _text(row.get("run_id")) or ""
    try:
        return s.RunSummary(run_id=run_id, created=utc(row.get("created")) or _created_from_id(run_id),
                            kind=row.get("kind"), source=_text(row.get("source")) or "",
                            strategy=_text(row.get("strategy")), universe=_text(row.get("universe")),
                            n_symbols=_int(row.get("n_symbols")), start=_day(row.get("start")),
                            end=_day(row.get("end")), git_commit=_text(row.get("git_commit")), metrics=metrics)
    except ValidationError as e:
        log.warning("run %r is left out of the list: %s", run_id, e)
        return None


def summaries(df: pd.DataFrame) -> List[s.RunSummary]:
    if df is None or df.empty:
        return []
    out = (summary_from_row(row) for row in records(df.reset_index(drop=True)))
    return [r for r in out if r is not None]


def summary_of(record: RunRecord) -> s.RunSummary:
    """A loaded run's summary. Its metrics are kept as saved: a metric present and null is one
    JSON cannot carry (an infinite profit factor), not a missing one."""
    meta = record.meta or {}
    run_id = record.run_id or ""
    n_symbols = meta.get("n_symbols")
    if n_symbols is None and isinstance(meta.get("symbols"), list):
        n_symbols = len(meta["symbols"])
    created = utc(record.created) or utc(meta.get("created")) or _created_from_id(run_id)
    return s.RunSummary(run_id=run_id, created=created or datetime.fromtimestamp(0, timezone.utc),
                        kind=record.kind, source=_text(meta.get("source")) or "",
                        strategy=_text(meta.get("strategy")), universe=_text(meta.get("universe")),
                        n_symbols=_int(n_symbols), start=_day(meta.get("start")), end=_day(meta.get("end")),
                        git_commit=_text(meta.get("git_commit")),
                        metrics=plain({k: v for k, v in (record.metrics or {}).items()}))


def table_frame(df: pd.DataFrame) -> pd.DataFrame:
    """A stored table as it is served: string column names, and a meaningful index as columns."""
    if not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    return df
