"""Saved runs (the run store): list them, open one, download its tables, delete one locally.

Every finished dashboard job is saved (routers/jobs.py), and so are the runs scripts and the CLI
make, all through midfreq.backtest.runstore; the store comes from the `get_run_store` dependency.
An id or table name that could not be one (anything but letters, digits, _ and -) is 404 without
asking the store; a store that cannot be read (no credentials for the bucket) is 503 with the
reason. Deleting is refused (403) where Settings.runs_deletable is off: the hosted dashboard.
"""
import io
import logging
import re
from typing import Any, Callable, Iterator, List, Literal, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from midfreq.api import schemas as s
from midfreq.api.convert import plain, records
from midfreq.api.deps import get_run_store, get_settings
from midfreq.api.saved_runs import RunStoreUnavailable, summaries, summary_of, table_frame
from midfreq.api.settings import Settings
from midfreq.backtest.runstore import RUN_KINDS, RunStore

log = logging.getLogger(__name__)
router = APIRouter(tags=["run history"])

RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
TABLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
CSV_ROWS = 10_000                 # rows per streamed CSV chunk
CHUNK = 1 << 20                   # bytes per streamed Parquet chunk
MEDIA = {"csv": "text/csv; charset=utf-8", "parquet": "application/vnd.apache.parquet"}


def _no_run(run_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no run {run_id!r}")


def _checked(run_id: str) -> str:
    if not RUN_ID.match(run_id):
        raise _no_run(run_id)
    return run_id


def _ask(call: Callable[[], Any], missing: str) -> Any:
    """A store call: KeyError is 404 (`missing`), a store that cannot be read 503 with why."""
    try:
        return call()
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(status_code=404, detail=missing)
    except RunStoreUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:                                       # noqa: BLE001 (a bucket's errors are many)
        log.exception("the run store failed")
        raise HTTPException(status_code=503, detail=f"the run store cannot be read: {type(e).__name__}: {e}")


@router.get("/api/runs", response_model=List[s.RunSummary])
def runs(kind: Optional[str] = None, strategy: Optional[str] = None, limit: int = Query(200, ge=1, le=2000),
         store: RunStore = Depends(get_run_store)):
    """Saved runs, newest first."""
    if kind is not None and kind not in RUN_KINDS:
        raise HTTPException(status_code=422, detail=f"kind: one of {', '.join(RUN_KINDS)}, not {kind!r}")
    df = _ask(lambda: store.list(kind=kind, strategy=strategy, limit=limit), "no runs")
    return summaries(df)[:limit]


@router.get("/api/runs/{run_id}", response_model=s.RunDetail, responses={404: {"description": "no such run"}})
def run(run_id: str, store: RunStore = Depends(get_run_store)):
    _checked(run_id)
    record = _ask(lambda: store.load(run_id), f"no run {run_id!r}")
    record.run_id = record.run_id or run_id
    return s.RunDetail(summary=summary_of(record), meta=plain(record.meta or {}),
                       tables=[str(name) for name in record.tables])


def _csv(df: pd.DataFrame) -> Iterator[bytes]:
    for i in range(0, max(len(df), 1), CSV_ROWS):
        yield df.iloc[i:i + CSV_ROWS].to_csv(index=False, header=(i == 0)).encode()


def _bytes(data: bytes) -> Iterator[bytes]:
    for i in range(0, len(data), CHUNK):
        yield data[i:i + CHUNK]


@router.get("/api/runs/{run_id}/tables/{name}", responses={404: {"description": "no such run or table"},
                                                           200: {"description": "JSON rows, or a CSV or Parquet file"}})
def run_table(run_id: str, name: str, format: Literal["json", "csv", "parquet"] = "json",
              limit: int = Query(100000, ge=1, le=1000000), store: RunStore = Depends(get_run_store)):
    # json: {run_id, name, columns, rows (at most `limit`, NaN as null), total}; csv and parquet:
    # the whole table as a file to download, <run_id>_<name>.<format>. (A comment, not a
    # docstring: the docstring would become part of the OpenAPI contract.)
    _checked(run_id)
    if not TABLE.match(name):
        raise HTTPException(status_code=404, detail=f"no table {name!r} in run {run_id!r}")
    df = table_frame(_ask(lambda: store.table(run_id, name), f"no table {name!r} in run {run_id!r}"))
    if format == "json":
        return {"run_id": run_id, "name": name, "columns": list(df.columns), "rows": records(df.head(limit)),
                "total": len(df)}
    if format == "csv":
        body = _csv(df)
    else:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        body = _bytes(buf.getvalue())
    return StreamingResponse(body, media_type=MEDIA[format],
                             headers={"Content-Disposition": f'attachment; filename="{run_id}_{name}.{format}"'})


@router.delete("/api/runs/{run_id}", status_code=204, responses={403: {"description": "deleting is not allowed here"},
                                                                  404: {"description": "no such run"}})
def delete_run(run_id: str, settings: Settings = Depends(get_settings), store: RunStore = Depends(get_run_store)):
    if not settings.runs_deletable:
        raise HTTPException(status_code=403, detail="deleting runs is not allowed on this server")
    _checked(run_id)
    _ask(lambda: store.delete(run_id), f"no run {run_id!r}")
    return Response(status_code=204)
