"""
The bot's own record, behind one interface.

The trading bot writes a JSON-lines log per session (live_sessions/session_*.jsonl) and its
console output (console_*.log). The API reads them through a RecordStore and never talks to
the bot process. Today the record is a local folder (the VM's, or this machine's copy fetched
with ops/fetch_live_logs.sh); a store backed by a GCS bucket the VM uploads to is the planned
second implementation, so a hosted dashboard need not share a disk with the VM.

A store only lists and reads files by name. Ids that are not in its own listing are refused,
which is what keeps a request from naming a path outside the record.
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

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


def record_store_for(source: str) -> RecordStore:
    """The store for Settings.live_sessions: a local folder now. The GCS-backed store will be
    chosen here for a gs://bucket/prefix source."""
    if str(source).startswith("gs://"):
        raise NotImplementedError("a GCS-backed record store is not implemented yet; "
                                  "point live_sessions at a local folder")
    return LocalDirRecordStore(Path(source))
