"""
The bot's own record, behind one interface.

The trading bot writes a JSON-lines log per session (live_sessions/session_*.jsonl) and its
console output (console_*.log). The API reads them through a RecordStore and never talks to
the bot process. The record is either a local folder (the VM's, or this machine's copy fetched
with ops/fetch_live_logs.sh) or a Cloud Storage bucket the VM uploads to (GcsMirrorRecordStore),
so a hosted dashboard need not share a disk with the VM.

A store only lists and reads files by name. Ids that are not in its own listing are refused,
which is what keeps a request from naming a path outside the record.
"""
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SESSION_GLOB = "session_*.jsonl"
CONSOLE_GLOB = "console_*.log"


class RecordStore(ABC):
    """Read-only access to the session logs and console logs of the trading bot."""

    @abstractmethod
    def available(self) -> bool:
        """Whether the record can be read at all (the folder or bucket exists)."""

    @abstractmethod
    def session_ids(self) -> List[str]:
        """Every recorded session: its log's file name without .jsonl. Any order."""

    @abstractmethod
    def read_session(self, session_id: str) -> str:
        """The session log's text. KeyError if `session_id` is not in session_ids()."""

    @abstractmethod
    def console_logs(self) -> List[str]:
        """File names of the console logs."""

    @abstractmethod
    def read_console_log(self, name: str) -> str:
        """A console log's text. KeyError if `name` is not in console_logs()."""

    @abstractmethod
    def local_dir(self) -> Path:
        """A local folder holding the session logs, for readers that take a folder
        (midfreq.research.forward_test.load). A remote store syncs to a cache folder and
        returns that."""


class LocalDirRecordStore(RecordStore):
    """The record as a folder on this machine (live_sessions/)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def available(self) -> bool:
        return self.root.is_dir()

    def _names(self, pattern: str) -> List[str]:
        if not self.root.is_dir():
            return []
        return [p.name for p in self.root.glob(pattern) if p.is_file()]

    def session_ids(self) -> List[str]:
        return [n[:-len(".jsonl")] for n in self._names(SESSION_GLOB)]

    def read_session(self, session_id: str) -> str:
        if session_id not in self.session_ids():
            raise KeyError(session_id)
        return (self.root / f"{session_id}.jsonl").read_text(errors="replace")

    def console_logs(self) -> List[str]:
        return self._names(CONSOLE_GLOB)

    def read_console_log(self, name: str) -> str:
        if name not in self.console_logs():
            raise KeyError(name)
        return (self.root / name).read_text(errors="replace")

    def local_dir(self) -> Path:
        return self.root

    def __repr__(self) -> str:
        return f"LocalDirRecordStore({str(self.root)!r})"


class GcsMirrorRecordStore(RecordStore):
    """The record in a Cloud Storage bucket (gs://bucket/prefix), mirrored into a local cache
    folder and read from there, for a dashboard that does not share a disk with the VM.

    The VM uploads live_sessions/ to the bucket every few minutes (ops/vm/sync_logs.sh). Any
    read refreshes the mirror if it is older than `max_age` seconds, downloading only objects
    whose generation changed; objects deleted from the bucket are removed from the mirror. Reads
    then go through a LocalDirRecordStore over the mirror, so ids are validated the same way.
    Needs only read access to the bucket (roles/storage.objectViewer).
    """

    def __init__(self, source: str, mirror_dir: Optional[Path] = None, max_age: float = 60.0,
                 client_factory: Optional[Callable[[], Any]] = None, clock: Callable[[], float] = time.monotonic):
        if not source.startswith("gs://"):
            raise ValueError(f"not a gs:// source: {source!r}")
        bucket, _, prefix = source[len("gs://"):].partition("/")
        self.bucket_name, self.prefix = bucket, prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.mirror = Path(mirror_dir) if mirror_dir else Path(tempfile.mkdtemp(prefix="midfreq-record-"))
        self.max_age = max_age
        self._client_factory = client_factory or _gcs_client
        self._clock = clock
        self._lock = threading.Lock()
        self._synced_at: Optional[float] = None
        self._generations: Dict[str, int] = {}
        self._ok = False
        self._local = LocalDirRecordStore(self.mirror)

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            now = self._clock()
            if not force and self._synced_at is not None and now - self._synced_at < self.max_age:
                return
            try:
                bucket = self._client_factory().bucket(self.bucket_name)
                seen, changed = {}, []
                for blob in bucket.list_blobs(prefix=self.prefix):
                    name = blob.name[len(self.prefix):]
                    if not name or "/" in name:          # only the files directly under the prefix
                        continue
                    seen[name] = blob.generation
                    if self._generations.get(name) != blob.generation:
                        changed.append((name, blob))
                if changed:
                    # in parallel: a cold start mirrors a few hundred small files at once
                    self.mirror.mkdir(parents=True, exist_ok=True)
                    with ThreadPoolExecutor(max_workers=16) as pool:
                        list(pool.map(lambda nb: nb[1].download_to_filename(str(self.mirror / nb[0])), changed))
                    for name, blob in changed:
                        self._generations[name] = blob.generation
                for name in set(self._generations) - set(seen):
                    (self.mirror / name).unlink(missing_ok=True)
                    del self._generations[name]
                self._ok = True
            except Exception:
                # keep serving the last mirror; available() reports whether one exists
                self._ok = self._ok and self.mirror.is_dir()
            self._synced_at = now

    def available(self) -> bool:
        self.refresh()
        return self._ok

    def session_ids(self) -> List[str]:
        self.refresh()
        return self._local.session_ids()

    def read_session(self, session_id: str) -> str:
        self.refresh()
        return self._local.read_session(session_id)

    def console_logs(self) -> List[str]:
        self.refresh()
        return self._local.console_logs()

    def read_console_log(self, name: str) -> str:
        self.refresh()
        return self._local.read_console_log(name)

    def local_dir(self) -> Path:
        self.refresh()
        return self.mirror

    def __repr__(self) -> str:
        return f"GcsMirrorRecordStore('gs://{self.bucket_name}/{self.prefix}')"


def _gcs_client():
    from google.cloud import storage          # the `gcs` extra; imported only for a gs:// source
    return storage.Client()


@lru_cache(maxsize=None)
def record_store_for(source: str) -> RecordStore:
    """The store for Settings.live_sessions: a local folder, or gs://bucket/prefix (mirrored).
    One store per source for the life of the process, so a bucket mirror is reused."""
    if str(source).startswith("gs://"):
        return GcsMirrorRecordStore(str(source))
    return LocalDirRecordStore(Path(source))
