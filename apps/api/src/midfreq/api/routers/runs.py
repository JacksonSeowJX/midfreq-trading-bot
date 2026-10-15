"""Saved runs (the run store): list them, open one, download its tables, delete it locally."""
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from midfreq.api import schemas as s

router = APIRouter(tags=["run history"])


def _todo():
    raise HTTPException(status_code=501, detail="not implemented yet")


@router.get("/api/runs", response_model=List[s.RunSummary])
def runs(kind: Optional[str] = None, strategy: Optional[str] = None, limit: int = Query(200, ge=1, le=2000)):
    """Saved runs, newest first."""
    _todo()


@router.get("/api/runs/{run_id}", response_model=s.RunDetail, responses={404: {"description": "no such run"}})
def run(run_id: str):
    _todo()


@router.get("/api/runs/{run_id}/tables/{name}", responses={404: {"description": "no such run or table"},
                                                           200: {"description": "JSON rows, or a CSV or Parquet file"}})
def run_table(run_id: str, name: str, format: Literal["json", "csv", "parquet"] = "json",
              limit: int = Query(100000, ge=1, le=1000000)):
    _todo()


@router.delete("/api/runs/{run_id}", status_code=204, responses={403: {"description": "deleting is not allowed here"},
                                                                  404: {"description": "no such run"}})
def delete_run(run_id: str):
    _todo()
