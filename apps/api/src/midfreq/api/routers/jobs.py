"""Runs (backtest, compare, optimize) as background jobs: start one, poll it, read its result."""
from typing import List

from fastapi import APIRouter, HTTPException

from midfreq.api import schemas as s

router = APIRouter(tags=["runs"])


def _todo():
    raise HTTPException(status_code=501, detail="not implemented yet")


@router.get("/api/jobs/capabilities", response_model=s.JobCapabilities)
def capabilities():
    _todo()


@router.post("/api/jobs/backtest", response_model=s.Job, status_code=202,
             responses={403: {"description": "this run kind is not allowed here"},
                        429: {"description": "too many runs in progress"}})
def start_backtest(req: s.BacktestRequest):
    _todo()


@router.post("/api/jobs/compare", response_model=s.Job, status_code=202,
             responses={403: {"description": "this run kind is not allowed here"},
                        429: {"description": "too many runs in progress"}})
def start_compare(req: s.CompareRequest):
    _todo()


@router.post("/api/jobs/optimize", response_model=s.Job, status_code=202,
             responses={403: {"description": "this run kind is not allowed here"},
                        429: {"description": "too many runs in progress"}})
def start_optimize(req: s.OptimizeRequest):
    _todo()


@router.get("/api/jobs", response_model=List[s.Job])
def jobs():
    """Recent jobs, newest first, without their results."""
    _todo()


@router.get("/api/jobs/{job_id}", response_model=s.Job, responses={404: {"description": "no such job"}})
def job(job_id: str):
    _todo()
