"""
The run store: every top-level run (a backtest, a comparison, a grid search, a walk-forward)
saved with everything needed to read it back and to reproduce it.

    runs/<run_id>/
      run.json            how it was run: kind, strategy, params, symbols, dates, costs, risk
                          settings, objective, git commit, data cut-off, source, created (UTC),
                          and the headline metrics and table sizes, so a listing reads this alone
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

A run id is `<UTC yyyymmddThhmmss>_<kind>_<6 hex digits of the manifest's hash>`, for example
20261016T043512_walk_forward_3fa9c2. A local save writes the run into a hidden temporary folder
in the store's root and renames it into place, so a run that is half written (or whose writer
crashed) is never listed. The Cloud Storage store uploads the manifest last, for the same reason.
Every time in a run is UTC; a time without a zone is read as UTC, as the backtester reads it.
run.json is strict JSON: an infinite or undefined number is written as "inf", "-inf" or "nan".

Usage:
    python -m midfreq.backtest.runstore list [--kind walk_forward] [--strategy NAME]
    python -m midfreq.backtest.runstore show <run_id>
    python -m midfreq.backtest.runstore promote <run_id>      # results/run_<id>.json and .csv
    python -m midfreq.backtest.runstore rm <run_id>
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import warnings
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

RUN_KINDS = ("backtest", "compare", "grid_search", "walk_forward")
SOURCES = ("api", "script", "cli")

MANIFEST = "run.json"
FORMAT = 1                                  # the layout of a run folder; bumped if it changes
RUN_ID = re.compile(r"^\d{8}T\d{6}_(?:" + "|".join(RUN_KINDS) + r")_[0-9a-f]{6}$")
TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Keys the store writes into run.json itself; a record's meta cannot use them.
RESERVED = ("run_id", "kind", "created", "format", "metrics", "tables")
# list()'s first columns; the headline metrics follow.
LIST_COLUMNS = ("run_id", "created", "kind", "source", "strategy", "universe", "n_symbols", "timeframe",
                "start", "end", "objective", "method", "git_commit")
_TMP_PREFIX, _DEL_PREFIX = ".tmp-", ".del-"


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


# ── plain data: what goes into run.json and the Parquet tables ──────────────

def _utc(value: Any) -> Optional[pd.Timestamp]:
    """A time as a UTC Timestamp (a time without a zone is UTC); None for a missing one."""
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _iso(value: Any) -> Optional[str]:
    ts = _utc(value)
    return ts.isoformat() if ts is not None else None


def _scalar(value: Any) -> Any:
    """A plain Python value for one number, string, bool, time or None; TypeError otherwise."""
    if isinstance(value, np.generic):
        value = pd.Timestamp(value) if isinstance(value, np.datetime64) else value.item()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, pd.Timestamp)):
        return _iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _scalar(value.value)
    if value is pd.NA or value is pd.NaT:
        return None
    raise TypeError(f"not a single value: {value!r}")


def _jsonable(value: Any) -> Any:
    """A strict-JSON copy of `value`: times as UTC ISO strings, numpy scalars as Python numbers,
    an infinite or undefined float as the string 'inf', '-inf' or 'nan'."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    try:
        value = _scalar(value)
    except TypeError:
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    return value


def _number(value: Any) -> Any:
    """Undo _jsonable's spelling of a non-finite float."""
    if isinstance(value, str) and value in ("inf", "-inf", "nan"):
        return float(value)
    return value


def _dumps(obj: Any, **kw) -> str:
    return json.dumps(_jsonable(obj), allow_nan=False, **kw)


def _cell(value: Any) -> Any:
    """A table cell Parquet can hold: a dict or list becomes its JSON text."""
    if isinstance(value, (Mapping, list, tuple, set, frozenset, np.ndarray)):
        return _dumps(value, sort_keys=True)
    return value


def _parquet_ready(df: pd.DataFrame) -> pd.DataFrame:
    """The table as it is written: a plain 0..n-1 index (any other index becomes columns),
    string column names, times in UTC, nested values as JSON text, and an object column holding
    text and numbers together as text."""
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"a run's table must be a DataFrame, not {type(df).__name__}")
    plain_index = isinstance(df.index, pd.RangeIndex) and df.index.name is None
    out = df.reset_index(drop=plain_index)
    out.columns = [str(c) for c in out.columns]
    if len(set(out.columns)) != len(out.columns):
        raise ValueError(f"a run's table has repeated column names: {list(out.columns)}")
    for col in out.columns:
        s = out[col]
        if isinstance(s.dtype, pd.DatetimeTZDtype):
            out[col] = s.dt.tz_convert("UTC")
        elif pd.api.types.is_datetime64_dtype(s.dtype):
            out[col] = s.dt.tz_localize("UTC")
        elif s.dtype == object:
            present = [v for v in s if v is not None and not (isinstance(v, float) and math.isnan(v))]
            if any(isinstance(v, (Mapping, list, tuple, set, frozenset, np.ndarray)) for v in present):
                out[col] = s.map(lambda v: _cell(v) if v is not None else None)
            elif present and all(isinstance(v, (datetime, pd.Timestamp, np.datetime64)) for v in present):
                out[col] = pd.Series([_utc(v) for v in s], index=s.index, dtype="datetime64[ns, UTC]")
            elif any(isinstance(v, str) for v in present) and not all(isinstance(v, str) for v in present):
                out[col] = s.map(lambda v: v if v is None or isinstance(v, str) or
                                 (isinstance(v, float) and math.isnan(v)) else str(_jsonable(v)))
    return out


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


# ── where and how a run was made ────────────────────────────────────────────

@lru_cache(maxsize=None)
def _git() -> Tuple[Optional[str], Optional[bool]]:
    """(commit, dirty) of the repository the code runs from, best effort. MIDFREQ_GIT_COMMIT
    wins (a deployed image has no .git)."""
    env = os.environ.get("MIDFREQ_GIT_COMMIT", "").strip()
    if env:
        return env, None
    try:
        from midfreq.core.paths import repo_root
        cwd = str(repo_root())
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True,
                                timeout=5, check=True).stdout.strip() or None
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd,
                                capture_output=True, text=True, timeout=5, check=True).stdout
        return commit, bool(status.strip())
    except Exception:
        return None, None


def git_commit() -> Optional[str]:
    """The commit the running code came from: MIDFREQ_GIT_COMMIT, else `git rev-parse HEAD` in
    the repository, else None."""
    return _git()[0]


def _package_version() -> Optional[str]:
    try:
        from importlib.metadata import version
        return version("midfreq-backtest")
    except Exception:
        return None


def _provenance() -> Dict[str, Any]:
    commit, dirty = _git()
    return {"git_commit": commit, "git_dirty": dirty, "package_version": _package_version(),
            "host": socket.gethostname(), "python": platform.python_version()}


def _check_id(run_id: Any) -> str:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise KeyError(run_id)
    return run_id


def _check_table(name: Any) -> str:
    if not isinstance(name, str) or not TABLE_NAME.fullmatch(name):
        raise KeyError(name)
    return name


def _run_id(created: datetime, kind: str, manifest: Dict[str, Any], attempt: int = 0) -> str:
    body = _dumps({k: v for k, v in manifest.items() if k != "run_id"}, sort_keys=True)
    digest = hashlib.sha256((body + (f"#{attempt}" if attempt else "")).encode()).hexdigest()[:6]
    return f"{created.astimezone(timezone.utc):%Y%m%dT%H%M%S}_{kind}_{digest}"


def _prepare(record: RunRecord) -> Tuple[datetime, Dict[str, Any], Dict[str, bytes]]:
    """The manifest (without its run_id) and every file but run.json, as bytes."""
    if record.kind not in RUN_KINDS:
        raise ValueError(f"no run kind {record.kind!r}; there are {', '.join(RUN_KINDS)}")
    meta = dict(record.meta or {})
    clash = sorted(set(meta) & set(RESERVED))
    if clash:
        raise ValueError(f"a run's meta cannot use the store's keys: {', '.join(clash)}")
    meta.setdefault("source", "script")
    if meta["source"] not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}, not {meta['source']!r}")
    if meta.get("symbols") is not None:
        meta.setdefault("n_symbols", len(meta["symbols"]))
    for key, value in _provenance().items():
        meta.setdefault(key, value)

    metrics = {}
    for key, value in (record.metrics or {}).items():
        try:
            metrics[str(key)] = _scalar(value)
        except TypeError:
            raise TypeError(f"metric {key!r} is not a single value ({type(value).__name__}); "
                            f"put it in a table") from None

    files, tables = {}, {}
    for name, df in (record.tables or {}).items():
        if not isinstance(name, str) or not TABLE_NAME.fullmatch(name) or name == "metrics":
            raise ValueError(f"not a table name: {name!r} (lower case, digits and _, not 'metrics')")
        ready = _parquet_ready(df)
        files[f"{name}.parquet"] = _parquet_bytes(ready)
        tables[name] = {"rows": int(len(ready)), "columns": list(ready.columns)}
    files["metrics.parquet"] = _parquet_bytes(pd.DataFrame([metrics]) if metrics else pd.DataFrame(index=[0]))

    created = record.created or datetime.now(timezone.utc)
    created = (created.replace(tzinfo=timezone.utc) if created.tzinfo is None
               else created.astimezone(timezone.utc))
    manifest = {"run_id": None, "kind": record.kind, "created": created.isoformat(), "format": FORMAT,
                **_jsonable(meta), "metrics": _jsonable(metrics), "tables": tables}
    return created, manifest, files


def _record(manifest: Dict[str, Any], tables: Dict[str, pd.DataFrame]) -> RunRecord:
    manifest = copy.deepcopy(manifest)                  # the stores cache theirs; the caller may change this
    meta = {k: v for k, v in manifest.items() if k not in RESERVED}
    metrics = {k: _number(v) for k, v in (manifest.get("metrics") or {}).items()}
    return RunRecord(kind=manifest["kind"], meta=meta, metrics=metrics, tables=tables,
                     run_id=manifest["run_id"], created=pd.Timestamp(manifest["created"]).to_pydatetime())


def _catalogue(manifests: Sequence[Dict[str, Any]], kind: Optional[str], strategy: Optional[str],
               limit: Optional[int]) -> pd.DataFrame:
    """list()'s table, from the manifests alone."""
    if kind is not None and kind not in RUN_KINDS:
        raise ValueError(f"no run kind {kind!r}; there are {', '.join(RUN_KINDS)}")
    rows = []
    for m in manifests:
        if kind is not None and m.get("kind") != kind:
            continue
        if strategy is not None and strategy != m.get("strategy") and strategy not in (m.get("strategies") or []):
            continue
        row = {c: m.get(c) for c in LIST_COLUMNS}
        for key, value in (m.get("metrics") or {}).items():
            if key not in row:
                row[key] = _number(value)
        rows.append(row)
    df = pd.DataFrame(rows)
    df = df.reindex(columns=list(LIST_COLUMNS) + [c for c in df.columns if c not in LIST_COLUMNS])
    for col in ("created", "start", "end"):
        df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601", errors="coerce")
    df = df.sort_values(["created", "run_id"], ascending=False, kind="mergesort")
    if limit is not None and limit >= 0:
        df = df.head(limit)
    return df.reset_index(drop=True)


# ── a folder on disk ────────────────────────────────────────────────────────

def _write(path: Path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


class LocalRunStore(RunStore):
    """Runs in a folder, one sub-folder per run. Nothing is written anywhere but `root`, and
    nothing at all until the first save (a missing root lists as empty)."""

    STALE_TMP_SECONDS = 24 * 3600          # a temporary folder older than this is a crashed save

    def __init__(self, root):
        self.root = Path(root)
        self._manifests: Dict[str, Dict[str, Any]] = {}   # a saved run never changes, so cache by id
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"LocalRunStore({str(self.root)!r})"

    def save(self, record: RunRecord) -> str:
        created, manifest, files = _prepare(record)
        self.root.mkdir(parents=True, exist_ok=True)
        self._sweep()
        # in the same root, so the rename below is atomic; hidden, so it is never listed
        tmp = Path(tempfile.mkdtemp(prefix=_TMP_PREFIX, dir=self.root))
        try:
            os.chmod(tmp, 0o755)                     # mkdtemp's 0700 would stay with the run
            for name, data in files.items():
                _write(tmp / name, data)
            for attempt in range(8):
                run_id = _run_id(created, record.kind, manifest, attempt)
                if (self.root / run_id).exists():
                    continue
                manifest["run_id"] = run_id
                _write(tmp / MANIFEST, (_dumps(manifest, indent=1) + "\n").encode())
                try:
                    os.rename(tmp, self.root / run_id)
                except OSError:
                    if (self.root / run_id).exists():       # another process took the id meanwhile
                        continue
                    raise
                break
            else:
                raise RuntimeError(f"no free run id for {record.kind} at {created:%Y%m%dT%H%M%S}")
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        with self._lock:
            self._manifests[run_id] = manifest
        record.run_id, record.created = run_id, created
        return run_id

    def _sweep(self) -> None:
        """Remove what crashed saves and deletes left behind (best effort)."""
        cutoff = time.time() - self.STALE_TMP_SECONDS
        try:
            for entry in os.scandir(self.root):
                if entry.name.startswith((_TMP_PREFIX, _DEL_PREFIX)) and entry.is_dir() \
                        and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            pass

    def _ids(self) -> List[str]:
        if not self.root.is_dir():
            return []
        return [e.name for e in os.scandir(self.root) if RUN_ID.fullmatch(e.name) and e.is_dir()]

    def _read_manifest(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cached = self._manifests.get(run_id)
        if cached is not None:
            return cached
        try:
            manifest = json.loads((self.root / run_id / MANIFEST).read_text())
        except (OSError, ValueError):
            return None
        with self._lock:
            self._manifests[run_id] = manifest
        return manifest

    def _manifest(self, run_id: str) -> Dict[str, Any]:
        _check_id(run_id)
        if not (self.root / run_id / MANIFEST).is_file():
            with self._lock:
                self._manifests.pop(run_id, None)      # deleted by another process
            raise KeyError(run_id)
        manifest = self._read_manifest(run_id)
        if manifest is None:
            raise KeyError(run_id)
        return manifest

    def list(self, kind: Optional[str] = None, strategy: Optional[str] = None,
             limit: int = 200) -> pd.DataFrame:
        ids = self._ids()
        with self._lock:
            for gone in set(self._manifests) - set(ids):
                del self._manifests[gone]
        manifests = [m for m in (self._read_manifest(i) for i in ids) if m is not None]
        return _catalogue(manifests, kind, strategy, limit)

    def load(self, run_id: str) -> RunRecord:
        manifest = self._manifest(run_id)
        tables = {name: self.table(run_id, name) for name in manifest.get("tables", {})}
        return _record(manifest, tables)

    def table(self, run_id: str, name: str) -> pd.DataFrame:
        self._manifest(run_id)
        path = self.root / run_id / f"{_check_table(name)}.parquet"
        if not path.is_file():
            raise KeyError(name)
        return pd.read_parquet(path)

    def delete(self, run_id: str) -> None:
        self._manifest(run_id)
        # out of the listing in one rename, then removed
        trash = self.root / f"{_DEL_PREFIX}{run_id}-{uuid.uuid4().hex[:8]}"
        os.rename(self.root / run_id, trash)
        with self._lock:
            self._manifests.pop(run_id, None)
        shutil.rmtree(trash, ignore_errors=True)


# ── a Cloud Storage bucket ──────────────────────────────────────────────────

def _gcs_client():
    from google.cloud import storage          # the midfreq-api `gcs` extra; imported only for gs://
    return storage.Client()


class GcsRunStore(RunStore):
    """Runs in a Cloud Storage bucket, gs://bucket/prefix/<run_id>/..., for the hosted dashboard.

    save uploads the run's objects with run.json last, so a partial upload is never listed. list
    reads the manifests, re-listing the bucket at most every `max_age` seconds and downloading
    only manifests it has not seen. load and table download into `cache_dir`, where a run stays
    (a saved run never changes). Saving needs roles/storage.objectCreator; deleting, objectAdmin.
    """

    def __init__(self, url: str, cache_dir: Optional[Path] = None, max_age: float = 30.0,
                 client_factory: Optional[Callable[[], Any]] = None, clock: Callable[[], float] = time.monotonic):
        if not url.startswith("gs://"):
            raise ValueError(f"not a gs:// location: {url!r}")
        bucket, _, prefix = url[len("gs://"):].partition("/")
        if not bucket:
            raise ValueError(f"no bucket in {url!r}")
        self.url = url
        self.bucket_name, self.prefix = bucket, prefix.strip("/")
        self._base = f"{self.prefix}/" if self.prefix else ""
        self.cache_dir = Path(cache_dir) if cache_dir else (
            Path(tempfile.gettempdir()) / "midfreq-runs" / bucket / (self.prefix.replace("/", "_") or "_"))
        self.max_age = max_age
        self._client_factory = client_factory or _gcs_client
        self._clock = clock
        self._lock = threading.RLock()
        self._bucket_obj = None
        self._manifests: Dict[str, Tuple[Any, Dict[str, Any]]] = {}   # id -> (generation, manifest)
        self._listed_at: Optional[float] = None

    def __repr__(self) -> str:
        return f"GcsRunStore({self.url!r})"

    def _bucket(self):
        if self._bucket_obj is None:
            self._bucket_obj = self._client_factory().bucket(self.bucket_name)
        return self._bucket_obj

    def _blobs(self, run_id: str) -> Dict[str, Any]:
        """The objects of one run: file name -> blob."""
        prefix = f"{self._base}{run_id}/"
        return {b.name[len(prefix):]: b for b in self._bucket().list_blobs(prefix=prefix)
                if "/" not in b.name[len(prefix):]}

    def refresh(self, force: bool = False) -> None:
        """Re-read the bucket's manifests if the last listing is older than max_age."""
        with self._lock:
            now = self._clock()
            if not force and self._listed_at is not None and now - self._listed_at < self.max_age:
                return
            try:
                found = {}
                for blob in self._bucket().list_blobs(prefix=self._base):
                    run_id, _, name = blob.name[len(self._base):].partition("/")
                    if name == MANIFEST and RUN_ID.fullmatch(run_id):
                        found[run_id] = blob
                new = [(i, b) for i, b in found.items()
                       if i not in self._manifests or self._manifests[i][0] != b.generation]
                with ThreadPoolExecutor(max_workers=16) as pool:   # a cold start reads every manifest
                    texts = list(pool.map(lambda ib: self._try_download(ib[1]), new))
                for (run_id, blob), text in zip(new, texts):
                    if text is not None:
                        self._manifests[run_id] = (blob.generation, json.loads(text))
                for gone in set(self._manifests) - set(found):
                    del self._manifests[gone]
            except Exception:
                if self._listed_at is None:
                    raise                              # never listed: say so rather than show nothing
                # otherwise keep serving the last listing until the next attempt
            self._listed_at = now

    @staticmethod
    def _try_download(blob) -> Optional[bytes]:
        try:
            return blob.download_as_bytes()
        except Exception:
            return None

    def _manifest(self, run_id: str) -> Dict[str, Any]:
        _check_id(run_id)
        with self._lock:
            if run_id in self._manifests:
                return self._manifests[run_id][1]
        blob = self._blobs(run_id).get(MANIFEST)       # saved since the last listing, perhaps
        text = self._try_download(blob) if blob is not None else None
        if text is None:
            raise KeyError(run_id)
        manifest = json.loads(text)
        with self._lock:
            self._manifests[run_id] = (blob.generation, manifest)
        return manifest

    def save(self, record: RunRecord) -> str:
        created, manifest, files = _prepare(record)
        bucket = self._bucket()
        for attempt in range(8):
            run_id = _run_id(created, record.kind, manifest, attempt)
            if not self._blobs(run_id):
                break
        else:
            raise RuntimeError(f"no free run id for {record.kind} at {created:%Y%m%dT%H%M%S}")
        manifest["run_id"] = run_id
        done = []
        try:
            for name, data in files.items():
                bucket.blob(f"{self._base}{run_id}/{name}").upload_from_string(
                    data, content_type="application/vnd.apache.parquet")
                done.append(name)
            blob = bucket.blob(f"{self._base}{run_id}/{MANIFEST}")
            blob.upload_from_string(_dumps(manifest, indent=1) + "\n", content_type="application/json")
        except BaseException:
            for name in done:                          # best effort; unlisted either way
                try:
                    bucket.blob(f"{self._base}{run_id}/{name}").delete()
                except Exception:
                    pass
            raise
        with self._lock:
            self._manifests[run_id] = (getattr(blob, "generation", None), manifest)
        record.run_id, record.created = run_id, created
        return run_id

    def list(self, kind: Optional[str] = None, strategy: Optional[str] = None,
             limit: int = 200) -> pd.DataFrame:
        self.refresh()
        with self._lock:
            manifests = [m for _, m in self._manifests.values()]
        return _catalogue(manifests, kind, strategy, limit)

    def load(self, run_id: str) -> RunRecord:
        manifest = self._manifest(run_id)
        tables = {name: self.table(run_id, name) for name in manifest.get("tables", {})}
        return _record(manifest, tables)

    def table(self, run_id: str, name: str) -> pd.DataFrame:
        manifest = self._manifest(run_id)
        _check_table(name)
        if name != "metrics" and name not in manifest.get("tables", {}):
            raise KeyError(name)
        path = self.cache_dir / run_id / f"{name}.parquet"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.part")
            try:
                self._bucket().blob(f"{self._base}{run_id}/{name}.parquet").download_to_filename(str(part))
                os.replace(part, path)
            finally:
                part.unlink(missing_ok=True)
        return pd.read_parquet(path)

    def delete(self, run_id: str) -> None:
        self._manifest(run_id)
        blobs = self._blobs(run_id)
        if MANIFEST in blobs:                          # out of the listing first
            blobs.pop(MANIFEST).delete()
        for blob in blobs.values():
            blob.delete()
        with self._lock:
            self._manifests.pop(run_id, None)
        shutil.rmtree(self.cache_dir / run_id, ignore_errors=True)


# ── which store ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _store(where: str) -> RunStore:
    return GcsRunStore(where) if where.startswith("gs://") else LocalRunStore(Path(where))


def run_store_for(where) -> RunStore:
    """The store at a folder or gs://bucket/prefix; one per location for the life of the process,
    so a bucket's listing is reused."""
    where = str(where).strip()
    if not where.startswith("gs://"):
        where = str(Path(where).expanduser().resolve())
    return _store(where)


def default_run_store() -> RunStore:
    """The store this process saves to: MIDFREQ_RUNS (a folder, or gs://bucket/prefix), else
    <repo>/runs."""
    where = os.environ.get("MIDFREQ_RUNS", "").strip()
    if not where:
        from midfreq.core.paths import repo_root
        where = str(repo_root() / "runs")
    return run_store_for(where)


# ── records: what each kind of run saves ────────────────────────────────────

# grid_search's metric columns; the rest of a row are the parameters
GRID_METRICS = ("return_pct", "sharpe_ratio", "max_drawdown", "win_rate", "profit_factor",
                "total_trades", "alpha", "_objective")
TRADE_COLUMNS = ("timestamp", "symbol", "action", "qty", "price", "commission", "cash_after", "exit_reason")
WINDOW_COLUMNS = ("window", "train_start", "train_end", "test_start", "test_end")


def describe_risk(risk_manager: Any) -> Optional[Dict[str, Any]]:
    """A RiskManager's settings as plain data: the stops, the drawdown halt and the sizer with its
    settings. A mapping (the dashboard's risk settings) is kept as it is; None stays None."""
    if risk_manager is None:
        return None
    if isinstance(risk_manager, Mapping):
        return _jsonable(risk_manager)
    out = {key: _scalar(getattr(risk_manager, key, None))
           for key in ("stop_loss_pct", "trailing_stop_pct", "take_profit_pct", "max_drawdown_pct")}
    sizer = getattr(risk_manager, "position_sizer", None)
    out["sizer"] = type(sizer).__name__ if sizer is not None else None
    out["sizer_params"] = {}
    for key, value in (vars(sizer).items() if sizer is not None else ()):
        if not key.startswith("_"):
            try:
                out["sizer_params"][key] = _scalar(value)
            except TypeError:
                pass
    return out


def _strategy_name(strategy: Any) -> str:
    """The registry name of a strategy given by name or by class."""
    if isinstance(strategy, str):
        return strategy
    from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY
    for name, info in STRATEGY_REGISTRY.items():
        if info.get("class") is strategy:
            return name
    return getattr(strategy, "__name__", str(strategy))


def _timeframe(timeframe: Any) -> Optional[str]:
    return None if timeframe is None else str(getattr(timeframe, "value", timeframe))


def _headline(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    """The single values of a metrics dict (open_positions and other nested values left out)."""
    out = {}
    for key, value in (metrics or {}).items():
        try:
            out[str(key)] = _scalar(value)
        except TypeError:
            pass
    return out


def _meta(*, strategy, params, symbols, timeframe, start, end, initial_capital, commission_rate,
          slippage_bps, risk, universe, source, data_cutoff, extra, **more) -> Dict[str, Any]:
    symbols = list(symbols) if symbols is not None else None
    meta = {"source": source, "strategy": strategy, "params": params, "symbols": symbols,
            "n_symbols": len(symbols) if symbols is not None else None, "universe": universe,
            "timeframe": _timeframe(timeframe), "start": _iso(start), "end": _iso(end),
            "initial_capital": initial_capital, "commission_rate": commission_rate,
            "slippage_bps": slippage_bps, "risk": describe_risk(risk), **more,
            "data_cutoff": _iso(data_cutoff)}
    meta.update(extra or {})
    return _jsonable(meta)


def latest_candle(portfolio: Any, so_far: Any = None) -> Optional[pd.Timestamp]:
    """The later of `so_far` and the last candle the portfolio's backtest replayed (its per-candle
    equity curve has one point per candle time): a run's data cut-off, kept as it goes."""
    curve = getattr(portfolio, "equity_curve_detailed", None) or getattr(portfolio, "equity_curve", None)
    last = _utc(curve[-1]["timestamp"]) if curve else None
    so_far = _utc(so_far)
    if last is None or (so_far is not None and so_far >= last):
        return so_far
    return last


def equity_table(portfolio: Any) -> pd.DataFrame:
    """(t, equity): the per-candle curve when there is one, else the per-trade curve."""
    curve = getattr(portfolio, "equity_curve_detailed", None) or getattr(portfolio, "equity_curve", None) or []
    return pd.DataFrame({"t": pd.Series([_utc(e["timestamp"]) for e in curve], dtype="datetime64[ns, UTC]"),
                         "equity": pd.Series([float(e["equity"]) for e in curve], dtype="float64")})


def trades_table(portfolio: Any) -> pd.DataFrame:
    """Every trade, as Portfolio.trade_history records it."""
    history = list(getattr(portfolio, "trade_history", None) or [])
    df = pd.DataFrame(history)
    cols = [c for c in TRADE_COLUMNS if c in df.columns or c != "exit_reason"]
    df = df.reindex(columns=cols + [c for c in df.columns if c not in cols])
    df["timestamp"] = pd.Series([_utc(t) for t in df["timestamp"]], index=df.index, dtype="datetime64[ns, UTC]")
    return df


def _positions_table(metrics: Mapping[str, Any]) -> Optional[pd.DataFrame]:
    positions = (metrics or {}).get("open_positions")
    if not isinstance(positions, Mapping) or not positions:
        return None
    return pd.DataFrame([{"symbol": sym, **(pos if isinstance(pos, Mapping) else {"qty": pos})}
                         for sym, pos in positions.items()])


def record_from_backtest(portfolio: Any, metrics: Mapping[str, Any], *, strategy: Any,
                         params: Mapping[str, Any], symbols: Sequence[str], timeframe: Any,
                         start: Any = None, end: Any = None, risk: Any = None,
                         slippage_bps: Optional[float] = None, universe: Optional[str] = None,
                         source: str = "script", meta: Optional[Mapping[str, Any]] = None) -> RunRecord:
    """One Backtester.run: its portfolio (after the run) and the metrics it returned.

    Tables: equity (t, equity), trades, and positions (open at the end) when there are any.
    Costs and capital come from the portfolio; slippage from the metrics unless given. `risk` is
    the RiskManager (or the settings it was built from). `meta` adds to or overrides the manifest.
    """
    tables = {"equity": equity_table(portfolio), "trades": trades_table(portfolio)}
    positions = _positions_table(metrics)
    if positions is not None:
        tables["positions"] = positions
    headline = _headline(metrics)
    headline["n_open_positions"] = len((metrics or {}).get("open_positions") or {})
    run_meta = _meta(strategy=_strategy_name(strategy), params=dict(params or {}), symbols=symbols,
                     timeframe=timeframe, start=start, end=end,
                     initial_capital=getattr(portfolio, "initial_cash", None),
                     commission_rate=getattr(portfolio, "commission_rate", None),
                     slippage_bps=slippage_bps if slippage_bps is not None else (metrics or {}).get("slippage_bps"),
                     risk=risk, universe=universe, source=source, data_cutoff=latest_candle(portfolio), extra=meta)
    return RunRecord(kind="backtest", meta=run_meta, metrics=headline, tables=tables)


def record_from_compare(entries: Sequence[Mapping[str, Any]], *, timeframe: Any, start: Any = None,
                        end: Any = None, risk: Any = None, commission_rate: Optional[float] = None,
                        slippage_bps: Optional[float] = None, initial_capital: Optional[float] = None,
                        universe: Optional[str] = None, source: str = "script",
                        meta: Optional[Mapping[str, Any]] = None) -> RunRecord:
    """A comparison: several strategies, each run on its own portfolio over the same dates.

    `entries`: one mapping per strategy run, with `strategy`, `params`, `metrics` (what
    Backtester.run returned), and optionally `label` (default: the strategy), `symbols` and
    `portfolio`. An entry with no metrics (no candles) is left out and named in meta["skipped"].
    Tables: strategies (one row per strategy, best return first: label, strategy, params as JSON,
    n_symbols and its metrics), equity (label, t, equity) and trades (label and the trade), both
    long. The headline metrics are the best strategy's, with best_strategy and n_strategies.
    """
    rows, equity, trades, skipped, labels, all_symbols, params_by_label = [], [], [], [], [], [], {}
    first_portfolio, last_candle = None, None
    for entry in entries:
        name = _strategy_name(entry["strategy"])
        label = str(entry.get("label") or name)
        metrics = entry.get("metrics") or {}
        if not metrics:
            skipped.append(label)
            continue
        labels.append(label)
        params_by_label[label] = dict(entry.get("params") or {})
        symbols = list(entry.get("symbols") or [])
        all_symbols.extend(s for s in symbols if s not in all_symbols)
        rows.append({"label": label, "strategy": name, "params": _dumps(params_by_label[label], sort_keys=True),
                     "n_symbols": len(symbols), **_headline(metrics)})
        portfolio = entry.get("portfolio")
        if portfolio is not None:
            if first_portfolio is None:
                first_portfolio = portfolio
            eq = equity_table(portfolio)
            eq.insert(0, "label", label)
            equity.append(eq)
            tr = trades_table(portfolio)
            tr.insert(0, "label", label)
            trades.append(tr)
            last_candle = latest_candle(portfolio, last_candle)
    if not rows:
        raise ValueError("a comparison with no strategy that produced metrics")
    table = pd.DataFrame(rows)
    if "return_pct" in table.columns:
        table = table.sort_values("return_pct", ascending=False, kind="mergesort").reset_index(drop=True)
    best = {k: table[k].iloc[0] for k in table.columns}       # per column, so no int becomes a float
    headline = {**_headline({k: v for k, v in best.items() if k not in ("label", "strategy", "params")}),
                "best_strategy": best["label"], "n_strategies": len(table)}
    tables = {"strategies": table}
    if equity:
        tables["equity"] = pd.concat(equity, ignore_index=True)
    if trades:
        tables["trades"] = pd.concat(trades, ignore_index=True)
    first_metrics = next(e.get("metrics") for e in entries if e.get("metrics"))
    run_meta = _meta(strategy=None, params=params_by_label, symbols=all_symbols, timeframe=timeframe,
                     start=start, end=end,
                     initial_capital=initial_capital if initial_capital is not None
                     else getattr(first_portfolio, "initial_cash", None),
                     commission_rate=commission_rate if commission_rate is not None
                     else getattr(first_portfolio, "commission_rate", None),
                     slippage_bps=slippage_bps if slippage_bps is not None else first_metrics.get("slippage_bps"),
                     risk=risk, universe=universe, source=source, data_cutoff=last_candle, extra=meta,
                     strategies=labels, skipped=skipped)
    return RunRecord(kind="compare", meta=run_meta, metrics=headline, tables=tables)


def record_from_grid_search(grid: pd.DataFrame, *, strategy: Any, symbols: Sequence[str], timeframe: Any,
                            start: Any, end: Any, objective: str, initial_capital: Optional[float] = None,
                            commission_rate: Optional[float] = None, slippage_bps: Optional[float] = None,
                            risk: Any = None, max_combinations: Optional[int] = None,
                            grid_size: Optional[int] = None, data_cutoff: Any = None,
                            universe: Optional[str] = None, source: str = "script",
                            meta: Optional[Mapping[str, Any]] = None) -> RunRecord:
    """A grid search: grid_search's table (params and metrics per combination, best first) as
    the grid table; the best combination's metrics as the headline, with objective_value (the
    value ranked on), combinations (rows) and grid_size; its params as meta["best_params"]."""
    if grid is None or grid.empty:
        raise ValueError("a grid search with no results")
    param_cols = [c for c in grid.columns if c not in GRID_METRICS]
    best = {k: grid[k].iloc[0] for k in grid.columns}         # per column, so no int becomes a float
    headline = _headline({k: best[k] for k in GRID_METRICS if k in grid.columns and k != "_objective"})
    if "_objective" in grid.columns:
        headline["objective_value"] = _scalar(best["_objective"])
    headline.update(combinations=len(grid), grid_size=grid_size)
    run_meta = _meta(strategy=_strategy_name(strategy), params=None, symbols=symbols, timeframe=timeframe,
                     start=start, end=end, initial_capital=initial_capital, commission_rate=commission_rate,
                     slippage_bps=slippage_bps, risk=risk, universe=universe, source=source,
                     data_cutoff=data_cutoff, extra=meta, objective=objective, method="grid",
                     max_combinations=max_combinations,
                     best_params={k: _scalar(best[k]) for k in param_cols})
    return RunRecord(kind="grid_search", meta=run_meta, metrics=headline, tables={"grid": grid.copy()})


def _field(window: Any, name: str, default: Any = None) -> Any:
    if isinstance(window, Mapping):
        return window.get(name, default)
    return getattr(window, name, default)


def windows_table(windows: Sequence[Any]) -> pd.DataFrame:
    """One row per walk-forward window: window, train/test dates, the chosen params as columns,
    then every train metric prefixed is_ and every test metric prefixed oos_ (a nested value,
    such as open_positions, as JSON text). A window is a WalkForwardWindow or a mapping with its
    field names."""
    rows = []
    for w in windows:
        row = {"window": _field(w, "window_id", _field(w, "window")),
               **{k: _utc(_field(w, k)) for k in ("train_start", "train_end", "test_start", "test_end")}}
        for key, value in (_field(w, "best_params") or {}).items():
            row[key if key not in row else f"param_{key}"] = value
        for prefix, metrics in (("is_", _field(w, "train_metrics")), ("oos_", _field(w, "test_metrics"))):
            for key, value in (metrics or {}).items():
                row[f"{prefix}{key}"] = _cell(value)
        rows.append(row)
    df = pd.DataFrame(rows)
    for col in WINDOW_COLUMNS[1:]:
        if col in df.columns:
            df[col] = pd.Series(list(df[col]), index=df.index, dtype="datetime64[ns, UTC]")
    return df


def record_from_walk_forward(result: Mapping[str, Any], *, strategy: Any, symbols: Sequence[str],
                             timeframe: Any, start: Any, end: Any, objective: str, n_splits: int,
                             train_pct: float, initial_capital: Optional[float] = None,
                             commission_rate: Optional[float] = None, slippage_bps: Optional[float] = None,
                             risk: Any = None, param_grid_size: Optional[int] = None,
                             data_cutoff: Any = None, universe: Optional[str] = None, source: str = "script",
                             meta: Optional[Mapping[str, Any]] = None) -> RunRecord:
    """A walk-forward: what walk_forward returned ({'windows': [...], 'summary': {...}}).

    Tables: windows (windows_table) and skipped (window_id, test dates, reason) when windows were
    skipped. The headline metrics are the summary's single values: n_windows, total_windows,
    positive_windows, avg_train_return, avg_oos_return, consistency_pct, train_return_per_day,
    oos_return_per_day, wf_efficiency, plus n_skipped. (Its oos_returns and train_returns are the
    windows table's oos_return_pct and is_return_pct.)"""
    windows = list(result.get("windows") or [])
    summary = dict(result.get("summary") or {})
    if not windows:
        raise ValueError(f"a walk-forward with no windows{': ' + summary['error'] if summary.get('error') else ''}")
    headline = _headline({k: v for k, v in summary.items() if k not in ("oos_returns", "train_returns")})
    skipped = list(summary.get("skipped_windows") or [])
    headline["n_skipped"] = len(skipped)
    tables = {"windows": windows_table(windows)}
    if skipped:
        sk = pd.DataFrame(skipped)
        for col in ("test_start", "test_end"):
            if col in sk.columns:
                sk[col] = pd.Series([_utc(v) for v in sk[col]], index=sk.index, dtype="datetime64[ns, UTC]")
        tables["skipped"] = sk
    run_meta = _meta(strategy=_strategy_name(strategy), params=None, symbols=symbols, timeframe=timeframe,
                     start=start, end=end, initial_capital=initial_capital, commission_rate=commission_rate,
                     slippage_bps=slippage_bps, risk=risk, universe=universe, source=source,
                     data_cutoff=data_cutoff, extra=meta, objective=objective, method="walk_forward",
                     n_splits=n_splits, train_pct=train_pct, param_grid_size=param_grid_size)
    return RunRecord(kind="walk_forward", meta=run_meta, metrics=headline, tables=tables)


def save_quietly(store: Optional[RunStore], build: Callable[[], RunRecord]) -> Optional[str]:
    """Build a record and save it; on any failure warn (RuntimeWarning) and return None, so a run
    that finished is never lost to a failed save. The optimizer's run_store hooks use this."""
    if store is None:
        return None
    try:
        return store.save(build())
    except Exception as e:                      # noqa: BLE001 - the run's own result comes first
        warnings.warn(f"the run was not saved to {store!r}: {e!r}", RuntimeWarning, stacklevel=3)
        return None


# ── promotion into results/ ─────────────────────────────────────────────────

def promote(run_id: str, store: Optional[RunStore] = None, results_dir: Optional[Path] = None) -> List[Path]:
    """Copy a run into results/ (or `results_dir`) as committed files: run_<id>.json (the
    manifest, with the headline metrics and the CSV each table went to) and run_<id>_<table>.csv
    per table. Refuses (FileExistsError, nothing written) if any of them exists already."""
    store = store or default_run_store()
    if results_dir is None:
        from midfreq.core.paths import results_dir as _results_dir
        results_dir = _results_dir()
    results_dir = Path(results_dir)
    record = store.load(run_id)
    targets = {name: results_dir / f"run_{run_id}_{name}.csv" for name in record.tables}
    summary = results_dir / f"run_{run_id}.json"
    existing = [p for p in (summary, *targets.values()) if p.exists()]
    if existing:
        raise FileExistsError(f"already in {results_dir}: {', '.join(p.name for p in existing)}")
    results_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": run_id, "kind": record.kind, "created": _iso(record.created), **record.meta,
                "metrics": record.metrics,
                "tables": {name: {"rows": len(df), "columns": list(map(str, df.columns)), "file": targets[name].name}
                           for name, df in record.tables.items()},
                "promoted": datetime.now(timezone.utc).isoformat()}
    written = []
    with open(summary, "x") as f:                      # "x": never over a file that appeared meanwhile
        f.write(_dumps(manifest, indent=1) + "\n")
    written.append(summary)
    for name, df in record.tables.items():
        with open(targets[name], "x", newline="") as f:
            df.to_csv(f, index=False)
        written.append(targets[name])
    return written


# ── the command line ────────────────────────────────────────────────────────

# the headline metrics `list` prints when any run has them, and how
_SHOWN = {"return_pct": "{:.2f}", "sharpe_ratio": "{:.2f}", "max_drawdown": "{:.2f}", "total_trades": "{:.0f}",
          "avg_oos_return": "{:.2f}", "avg_train_return": "{:.2f}", "consistency_pct": "{:.0f}",
          "wf_efficiency": "{:.2f}", "best_strategy": "{}", "combinations": "{:.0f}"}


def _list_text(df: pd.DataFrame) -> str:
    if df.empty:
        return "no saved runs"
    start, end = df["start"].dt.strftime("%Y-%m-%d"), df["end"].dt.strftime("%Y-%m-%d")
    out = pd.DataFrame({"run_id": df["run_id"], "created (UTC)": df["created"].dt.strftime("%Y-%m-%d %H:%M"),
                        "source": df["source"], "strategy": df["strategy"].fillna(""),
                        "symbols": [("" if pd.isna(n) else f"{n:.0f}") for n in df["n_symbols"]],
                        "dates": [("" if pd.isna(a) and pd.isna(b) else f"{'' if pd.isna(a) else a} to "
                                   f"{'' if pd.isna(b) else b}") for a, b in zip(start, end)]})
    for col, fmt in _SHOWN.items():
        if col in df.columns and df[col].notna().any():
            out[col] = ["" if v is None or (isinstance(v, float) and math.isnan(v)) else fmt.format(v)
                        for v in df[col]]
    return out.to_string(index=False)


def _show_text(record: RunRecord) -> str:
    lines = [f"{record.run_id}  ({record.kind}, created {_iso(record.created)})"]
    for key, value in record.meta.items():
        if key == "symbols" and isinstance(value, list) and len(value) > 10:
            value = ", ".join(value[:10]) + f", ... ({len(value)} in all)"
        lines.append(f"  {key}: {value if not isinstance(value, (dict, list)) else _dumps(value)}")
    lines.append("metrics:")
    lines += [f"  {k}: {v}" for k, v in record.metrics.items()]
    lines.append("tables:")
    lines += [f"  {name}: {len(df)} rows x {len(df.columns)} columns ({', '.join(map(str, df.columns[:12]))}"
              f"{', ...' if len(df.columns) > 12 else ''})" for name, df in record.tables.items()]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m midfreq.backtest.runstore",
                                     description="The saved runs: list, show, promote into results/, remove.")
    parser.add_argument("--store", help="a folder or gs://bucket/prefix (default: MIDFREQ_RUNS, else <repo>/runs)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list", help="the saved runs, newest first")
    p_list.add_argument("--kind", choices=RUN_KINDS)
    p_list.add_argument("--strategy")
    p_list.add_argument("--limit", type=int, default=50)
    for name, text in (("show", "one run: how it was run, its metrics and tables"),
                       ("promote", "copy a run into results/ as JSON and CSV (never overwrites)"),
                       ("rm", "remove a run")):
        p = sub.add_parser(name, help=text)
        p.add_argument("run_id")
        if name == "promote":
            p.add_argument("--to", type=Path, default=None, help="the folder to write to (default: results/)")
    args = parser.parse_args(argv)
    store = run_store_for(args.store) if args.store else default_run_store()
    try:
        if args.command == "list":
            print(_list_text(store.list(kind=args.kind, strategy=args.strategy, limit=args.limit)))
        elif args.command == "show":
            print(_show_text(store.load(args.run_id)))
        elif args.command == "promote":
            for path in promote(args.run_id, store=store, results_dir=args.to):
                print(f"wrote {path}")
        elif args.command == "rm":
            store.delete(args.run_id)
            print(f"removed {args.run_id}")
    except KeyError:
        print(f"no run {getattr(args, 'run_id', '')!r} in {store!r}", file=sys.stderr)
        return 1
    except FileExistsError as e:
        print(f"not promoted: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
