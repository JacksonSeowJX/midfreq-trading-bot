"""Runs (backtest, compare, optimize) as background jobs: start one, poll it, read its result.

The runs are the Streamlit dashboard's (runs.py), done in worker processes (runner.py). A run kind
outside Settings.job_kinds is 403; a run that cannot be done as asked (an unknown strategy or
parameter, a symbol with no cached candles) is 422 before it starts; more than
Settings.max_concurrent_jobs at once is 429. A run that fails once started (no candles between the
dates) is a job with status "failed" and the reason in `error`.
"""
import threading
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException

from midfreq.api import runs
from midfreq.api import schemas as s
from midfreq.api.deps import get_settings
from midfreq.api.runner import Busy, JobRunner
from midfreq.api.settings import Settings

_runners: Dict[int, JobRunner] = {}         # one per process (per max_concurrent_jobs, in practice one)
_runners_lock = threading.Lock()


def get_job_runner(settings: Settings = Depends(get_settings)) -> JobRunner:
    """The process's job runner; tests replace it with app.dependency_overrides[get_job_runner]."""
    with _runners_lock:
        runner = _runners.get(settings.max_concurrent_jobs)
        if runner is None:
            runner = _runners[settings.max_concurrent_jobs] = JobRunner(max_concurrent=settings.max_concurrent_jobs)
        return runner


def _stop_runners() -> None:
    # On server shutdown: terminate a run in progress rather than wait for it to finish.
    with _runners_lock:
        stopping = list(_runners.values())
        _runners.clear()
    for runner in stopping:
        runner.shutdown()


router = APIRouter(tags=["runs"], on_shutdown=[_stop_runners])


def _start(kind: str, req: s.RunSettings, settings: Settings, runner: JobRunner) -> s.Job:
    if kind not in settings.job_kinds:
        raise HTTPException(status_code=403, detail=f"{kind} runs are not allowed on this server "
                                                    f"(allowed: {', '.join(settings.job_kinds) or 'none'})")
    try:
        plan = runs.plan(kind, req, settings)
    except runs.RunError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        return runner.submit(kind, req.model_dump(mode="json"), plan)
    except Busy as e:
        raise HTTPException(status_code=429, detail=str(e))


@router.get("/api/jobs/capabilities", response_model=s.JobCapabilities)
def capabilities(settings: Settings = Depends(get_settings)):
    return runs.capabilities(settings)


@router.post("/api/jobs/backtest", response_model=s.Job, status_code=202,
             responses={403: {"description": "this run kind is not allowed here"},
                        429: {"description": "too many runs in progress"}})
def start_backtest(req: s.BacktestRequest, settings: Settings = Depends(get_settings),
                   runner: JobRunner = Depends(get_job_runner)):
    return _start("backtest", req, settings, runner)


@router.post("/api/jobs/compare", response_model=s.Job, status_code=202,
             responses={403: {"description": "this run kind is not allowed here"},
                        429: {"description": "too many runs in progress"}})
def start_compare(req: s.CompareRequest, settings: Settings = Depends(get_settings),
                  runner: JobRunner = Depends(get_job_runner)):
    return _start("compare", req, settings, runner)


@router.post("/api/jobs/optimize", response_model=s.Job, status_code=202,
             responses={403: {"description": "this run kind is not allowed here"},
                        429: {"description": "too many runs in progress"}})
def start_optimize(req: s.OptimizeRequest, settings: Settings = Depends(get_settings),
                   runner: JobRunner = Depends(get_job_runner)):
    return _start("optimize", req, settings, runner)


@router.get("/api/jobs", response_model=List[s.Job])
def jobs(runner: JobRunner = Depends(get_job_runner)):
    """Recent jobs, newest first, without their results."""
    return runner.jobs()


@router.get("/api/jobs/{job_id}", response_model=s.Job, responses={404: {"description": "no such job"}})
def job(job_id: str, runner: JobRunner = Depends(get_job_runner)):
    found = runner.get(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r} (jobs are kept in memory: the "
                                                    f"most recent {runner.keep}, until the server restarts)")
    return found
