"""
Runs as background jobs: an in-memory registry of the recent ones, and a pool of worker processes
that do the CPU-bound work, so the API keeps answering while a walk-forward grinds through its
grid.

    runner.submit(kind, request, plan) -> Job    # Busy when max_concurrent runs are already going
    runner.get(job_id) -> Job, or None
    runner.jobs() -> [Job]                       # newest first, without their results

Workers send their progress back through a queue, which a thread here drains into the registry;
the result comes back with the finished task. The registry is this process's own: restarting the
API forgets every job, and each API process has its own (run uvicorn with one worker).
"""
import logging
import multiprocessing
import os
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import Any, Callable, Dict, List, Optional

from midfreq.api import runs
from midfreq.api import schemas as s

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")

Target = Callable[[runs.Plan, runs.Progress], Any]


class Busy(RuntimeError):
    """As many runs as the runner allows are already queued or running."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── in a worker process ─────────────────────────────────────────────────────

_events = None          # the queue progress goes back through, set when the worker starts


def _init_worker(events) -> None:
    global _events
    _events = events
    try:
        os.nice(5)      # below the API's own process, so requests stay quick while a run computes
    except (AttributeError, OSError):
        pass


def _work(job_id: str, plan: runs.Plan, target: Target):
    """A job, in a worker process: (when it started, its result, its last progress message)."""
    return _run(job_id, plan, target, lambda *event: _events.put(event))


def _run(job_id: str, plan: runs.Plan, target: Target, send: Callable):
    started, last = _now(), [""]

    def progress(fraction: float, message: str = "") -> None:
        last[0] = message or last[0]
        send(job_id, float(fraction), message)

    send(job_id, 0.0, "")                         # the first event: the job has started
    return started, target(plan, progress), last[0]


# ── in the API process ──────────────────────────────────────────────────────

@dataclass
class _Record:
    id: str
    kind: str
    request: Dict[str, Any]
    created: datetime
    status: str = "queued"
    progress: float = 0.0
    message: str = "queued"
    started: Optional[datetime] = None
    finished: Optional[datetime] = None
    error: Optional[str] = None
    result: Any = field(default=None, repr=False)

    def job(self, with_result: bool = True) -> s.Job:
        result = {self.kind: self.result} if with_result and self.result is not None else {}
        return s.Job(id=self.id, kind=self.kind, status=self.status, progress=self.progress, message=self.message,
                     created=self.created, started=self.started, finished=self.finished, error=self.error,
                     request=self.request, **result)


class JobRunner:
    """At most `max_concurrent` runs at once, in worker processes ("process") or, where processes
    cannot be used, threads ("thread"); the most recent `keep` jobs are remembered with their
    results. `target` does the work (runs.execute; tests give their own, with threads)."""

    def __init__(self, max_concurrent: int = 1, keep: int = 50, workers: str = "process",
                 target: Target = runs.execute):
        if workers not in ("process", "thread"):
            raise ValueError(f"workers: 'process' or 'thread', not {workers!r}")
        self.max_concurrent = max(1, int(max_concurrent))
        self.keep = keep
        self.workers = workers
        self._target = target
        self._lock = threading.RLock()
        self._jobs: "OrderedDict[str, _Record]" = OrderedDict()
        self._pool: Optional[Executor] = None
        self._events = None                      # the workers' progress queue (process workers)

    # ── the registry ────────────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[s.Job]:
        with self._lock:
            rec = self._jobs.get(job_id)
            return rec.job() if rec is not None else None

    def jobs(self) -> List[s.Job]:
        with self._lock:
            return [rec.job(with_result=False) for rec in reversed(self._jobs.values())]

    def active(self) -> int:
        with self._lock:
            return sum(1 for rec in self._jobs.values() if rec.status in ACTIVE)

    def submit(self, kind: str, request: Dict[str, Any], plan: runs.Plan) -> s.Job:
        with self._lock:
            active = self.active()
            if active >= self.max_concurrent:
                raise Busy(f"{active} run(s) in progress, and this server runs at most {self.max_concurrent} "
                           f"at a time; try again when one has finished")
            rec = _Record(id=uuid.uuid4().hex[:12], kind=kind, request=request, created=_now())
            self._jobs[rec.id] = rec
            self._evict()
            job = rec.job()
            try:
                pool = self._executor()
                try:
                    future = self._start(pool, rec.id, plan)
                except BrokenProcessPool:        # a worker died since the last run: start afresh
                    self._forget(pool)
                    pool = self._executor()
                    future = self._start(pool, rec.id, plan)
            except BaseException:
                del self._jobs[rec.id]           # never started: it must not hold a place
                raise
        future.add_done_callback(partial(self._finish, rec.id, pool))
        return job

    def _evict(self) -> None:
        """Forget the oldest finished jobs beyond `keep` (never one still going)."""
        for job_id in [k for k, rec in self._jobs.items() if rec.status not in ACTIVE]:
            if len(self._jobs) <= self.keep:
                break
            del self._jobs[job_id]

    def _event(self, job_id: str, fraction: float, message: str) -> None:
        """Progress from a worker; the first event is the job starting."""
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None or rec.status not in ACTIVE:
                return                           # late news of a job that has finished
            if rec.status == "queued":
                rec.status, rec.started, rec.message = "running", _now(), "running"
            rec.progress = min(max(fraction, rec.progress), 1.0)      # never backwards
            if message:
                rec.message = message

    def _finish(self, job_id: str, pool: Executor, future: Future) -> None:
        result, error, started, message = None, None, None, ""
        if future.cancelled():
            error = "cancelled: the server stopped before it ran"
        elif future.exception() is not None:
            exc = future.exception()
            if isinstance(exc, runs.RunError):
                error = str(exc)
            elif isinstance(exc, BrokenProcessPool):
                self._forget(pool)
                error = "the worker process running this job stopped unexpectedly (out of memory?)"
            else:
                log.error("job %s failed", job_id, exc_info=(type(exc), exc, exc.__traceback__))
                error = f"{type(exc).__name__}: {exc}"
        else:
            started, result, message = future.result()
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return
            rec.finished = _now()
            rec.started = rec.started or started or rec.finished
            if error is None:
                rec.status, rec.progress, rec.result = "done", 1.0, result
                rec.message = message or "done"
            else:
                rec.status, rec.error = "failed", error
                rec.message = "failed"

    # ── the workers ─────────────────────────────────────────────────────

    def _executor(self) -> Executor:
        with self._lock:
            if self._pool is None:
                if self.workers == "thread":
                    self._pool = ThreadPoolExecutor(max_workers=self.max_concurrent, thread_name_prefix="midfreq-job")
                else:
                    # spawn, not fork: the API process has threads, and a forked copy of them can deadlock
                    ctx = multiprocessing.get_context("spawn")
                    if self._events is None:
                        self._events = ctx.Queue()
                        threading.Thread(target=self._listen, args=(self._events,), name="midfreq-job-progress",
                                         daemon=True).start()
                    self._pool = ProcessPoolExecutor(max_workers=self.max_concurrent, mp_context=ctx,
                                                     initializer=_init_worker, initargs=(self._events,))
            return self._pool

    def _start(self, pool: Executor, job_id: str, plan: runs.Plan) -> Future:
        if self.workers == "thread":
            return pool.submit(_run, job_id, plan, self._target, self._event)
        return pool.submit(_work, job_id, plan, self._target)

    def _forget(self, pool: Executor) -> None:
        """Drop a broken pool (it has stopped its processes itself); the next run starts a new one."""
        with self._lock:
            if self._pool is pool:
                self._pool = None

    def _listen(self, events) -> None:
        while True:
            try:
                event = events.get()
            except (EOFError, OSError, ValueError):
                return
            if event is None:
                return
            self._event(*event)

    def shutdown(self) -> None:
        """Stop the workers and the progress thread. A run in progress is abandoned: its worker
        process is terminated, since otherwise the interpreter waits for it on exit (a walk-forward
        can take a quarter of an hour). A thread cannot be stopped; it finishes on its own."""
        with self._lock:
            pool, self._pool = self._pool, None
            events, self._events = self._events, None
        if pool is not None:
            for process in list((getattr(pool, "_processes", None) or {}).values()):
                process.terminate()
            pool.shutdown(wait=False, cancel_futures=True)
        if events is not None:
            events.put(None)
