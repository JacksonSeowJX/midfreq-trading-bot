"""
Operations logging for the trading node: what the node is doing, readable by a person on the
console and by a machine in a JSON-lines file, plus a heartbeat and alerts.

The original engine wrote everything with print(); finding a failure meant reading a console
log. Here every component logs through Python `logging` under the `midfreq` logger, and
`OpsLogging.install()` adds two handlers:

- console: `2026-10-14 21:35:02.911 SGT | 09:35:02 ET | INFO    | node | message`. Both clocks,
  because the VM runs on Singapore time and the market on New York time.
- `ops_<stamp>_<node>.jsonl`: one JSON object per record with the time in UTC, the level, the
  component, the session id and any context fields (cross-section, client order id, strategy)
  set with `log_context(...)` or passed as `extra={"ctx": {...}}`.

Times come from the node's clock, so a replay driven by a test clock logs the replayed time.

The Moomoo SDK prints its own INFO lines to stdout through a logger called `FTConsoleLog`; those
are kept out of the node's console, and its WARNING and worse are forwarded into the node's log
under the component `sdk.moomoo`. Nothing here imports a venue's SDK: the SDK's logger is reached
by name only.

Alerts: `AlertSink` with a no-op default and an email sink whose SMTP settings come from the
environment (off unless the node configuration turns it on). Records at ERROR and above reach the
sinks, rate-limited per message, from a background thread so a slow mail server cannot stall the
node.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import queue
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

ROOT_LOGGER = "midfreq"
# Loggers whose names start with these are shown without the prefix in the component column.
_COMPONENT_PREFIXES = ("midfreq.trading.", "midfreq.")

# The Moomoo SDK's own console logger (moomoo/common/ft_logger.py: FTLog). It does not propagate
# and writes INFO and up to stdout through its own StreamHandler.
VENUE_SDK_CONSOLE_LOGGERS = {"FTConsoleLog": "sdk.moomoo"}

_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("midfreq_ops_context", default={})


def get_logger(component: str) -> logging.Logger:
    """The logger for one part of the node: get_logger('oms') -> 'midfreq.trading.oms'."""
    return logging.getLogger(f"midfreq.trading.{component}")


def component_name(logger_name: str) -> str:
    for prefix in _COMPONENT_PREFIXES:
        if logger_name.startswith(prefix):
            return logger_name[len(prefix):]
    return logger_name


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach fields (cross_section=..., client_order_id=..., strategy=...) to every record logged
    inside the block, on this thread."""
    merged = dict(_context.get())
    merged.update({k: v for k, v in fields.items() if v is not None})
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> Dict[str, Any]:
    return dict(_context.get())


class _ContextFilter(logging.Filter):
    """Copies the context fields and the node clock's time onto each record."""

    def __init__(self, time_source: Callable[[], float], session_id: str):
        super().__init__()
        self.time_source = time_source
        self.session_id = session_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "node_time"):
            try:
                record.node_time = float(self.time_source())
            except Exception:
                record.node_time = record.created
        record.session_id = self.session_id
        ctx = dict(_context.get())
        extra = getattr(record, "ctx", None)
        if isinstance(extra, Mapping):
            ctx.update(extra)
        record.ops_ctx = ctx
        return True


class ConsoleFormatter(logging.Formatter):
    """`YYYY-MM-DD HH:MM:SS.mmm SGT | HH:MM:SS ET | LEVEL | component | message`."""

    def __init__(self, primary_tz: str = "Asia/Singapore", primary_label: str = "SGT",
                 secondary_tz: str = "America/New_York", secondary_label: str = "ET"):
        super().__init__()
        self.primary = ZoneInfo(primary_tz)
        self.primary_label = primary_label
        self.secondary = ZoneInfo(secondary_tz)
        self.secondary_label = secondary_label

    def format(self, record: logging.LogRecord) -> str:
        t = getattr(record, "node_time", record.created)
        utc = datetime.fromtimestamp(t, tz=timezone.utc)
        p = utc.astimezone(self.primary)
        s = utc.astimezone(self.secondary)
        stamp = f"{p:%Y-%m-%d %H:%M:%S}.{p.microsecond // 1000:03d} {self.primary_label} | " \
                f"{s:%H:%M:%S} {self.secondary_label}"
        msg = record.getMessage()
        ctx = getattr(record, "ops_ctx", None) or {}
        coid = ctx.get("client_order_id")
        if coid and str(coid) not in msg:
            msg = f"{msg} [{coid}]"
        line = f"{stamp} | {record.levelname:<7} | {component_name(record.name)} | {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per record: ts (UTC, ISO 8601), level, component, session, msg, context."""

    def format(self, record: logging.LogRecord) -> str:
        t = getattr(record, "node_time", record.created)
        ts = datetime.fromtimestamp(t, tz=timezone.utc)
        out: Dict[str, Any] = {
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond:06d}Z",
            "level": record.levelname,
            "component": component_name(record.name),
            "session": getattr(record, "session_id", None),
            "msg": record.getMessage(),
        }
        for k, v in (getattr(record, "ops_ctx", None) or {}).items():
            if k not in out:
                out[k] = _jsonable(v)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


# ── the venue SDK's console output ───────────────────────────────────────────────────────────

class _SdkForwarder(logging.Handler):
    """Re-emits a venue SDK's WARNING and worse into the node's own log."""

    def __init__(self, target: str):
        super().__init__(level=logging.WARNING)
        self.target = logging.getLogger(f"{ROOT_LOGGER}.{target}")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.target.log(record.levelno, "%s", record.getMessage())
        except Exception:
            pass


class _QuietLogger(logging.Logger):
    """A logger that ignores console StreamHandlers added to it later (the SDK adds one when it
    is imported, after the node has already set its logging up)."""

    def addHandler(self, hdlr: logging.Handler) -> None:      # noqa: N802 (logging's API)
        if _is_console_handler(hdlr):
            return
        super().addHandler(hdlr)


def _is_console_handler(h: logging.Handler) -> bool:
    # Exactly a StreamHandler: its subclasses (FileHandler, the SDK's rotating file log) stay.
    return type(h) is logging.StreamHandler


def quiet_venue_sdk_loggers(loggers: Optional[Mapping[str, str]] = None) -> List[str]:
    """Keep venue SDKs' console output out of the node's console and forward their WARNING and
    worse into it. Safe to call again (after a connector imported its SDK). Returns the names of
    the loggers it handled."""
    done = []
    for name, target in (loggers or VENUE_SDK_CONSOLE_LOGGERS).items():
        existing = logging.Logger.manager.loggerDict.get(name)
        if existing is None or isinstance(existing, logging.PlaceHolder):
            previous = logging.getLoggerClass()
            logging.setLoggerClass(_QuietLogger)
            try:
                lg = logging.getLogger(name)
            finally:
                logging.setLoggerClass(previous)
        else:
            lg = existing
        for h in list(lg.handlers):
            if _is_console_handler(h):
                lg.removeHandler(h)
        if not any(isinstance(h, _SdkForwarder) for h in lg.handlers):
            logging.Logger.addHandler(lg, _SdkForwarder(target))
        lg.propagate = False
        done.append(name)
    return done


# ── alerts ───────────────────────────────────────────────────────────────────────────────────

class AlertSink(ABC):
    """Where alerts go: a person must look at something."""

    @abstractmethod
    def send(self, subject: str, body: str, level: str = "ERROR") -> None:
        ...

    def close(self) -> None:
        pass


class NullAlertSink(AlertSink):
    """The default: alerts are only logged."""

    def send(self, subject: str, body: str, level: str = "ERROR") -> None:
        return None


@dataclass
class EmailSettings:
    host: str
    port: int = 587
    user: Optional[str] = None
    password: Optional[str] = None
    sender: str = ""
    recipients: Sequence[str] = field(default_factory=list)
    starttls: bool = True
    timeout_s: float = 10.0

    ENV = {
        "host": "MIDFREQ_ALERT_SMTP_HOST", "port": "MIDFREQ_ALERT_SMTP_PORT",
        "user": "MIDFREQ_ALERT_SMTP_USER", "password": "MIDFREQ_ALERT_SMTP_PASSWORD",
        "sender": "MIDFREQ_ALERT_FROM", "recipients": "MIDFREQ_ALERT_TO",
        "starttls": "MIDFREQ_ALERT_SMTP_STARTTLS",
    }

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> Optional["EmailSettings"]:
        """Settings from MIDFREQ_ALERT_* variables; None unless at least the host and a recipient
        are set. The password is only ever read from the environment."""
        env = os.environ if env is None else env
        host = env.get(cls.ENV["host"], "").strip()
        to = [r.strip() for r in env.get(cls.ENV["recipients"], "").split(",") if r.strip()]
        if not host or not to:
            return None
        user = env.get(cls.ENV["user"]) or None
        return cls(host=host, port=int(env.get(cls.ENV["port"], "587") or 587), user=user,
                   password=env.get(cls.ENV["password"]) or None,
                   sender=env.get(cls.ENV["sender"]) or (user or f"midfreq-node@{host}"),
                   recipients=to,
                   starttls=env.get(cls.ENV["starttls"], "1").strip().lower() not in ("0", "false", "no"))


class EmailAlertSink(AlertSink):
    """Sends each alert as a plain-text email. `smtp_factory(host, port, timeout=...)` is
    smtplib.SMTP unless a test replaces it."""

    def __init__(self, settings: EmailSettings, smtp_factory: Optional[Callable[..., Any]] = None,
                 subject_prefix: str = "[midfreq-node]"):
        self.settings = settings
        if smtp_factory is None:
            import smtplib
            smtp_factory = smtplib.SMTP
        self._smtp_factory = smtp_factory
        self.subject_prefix = subject_prefix

    def send(self, subject: str, body: str, level: str = "ERROR") -> None:
        from email.message import EmailMessage
        s = self.settings
        msg = EmailMessage()
        msg["Subject"] = f"{self.subject_prefix} {level}: {subject}"[:200]
        msg["From"] = s.sender
        msg["To"] = ", ".join(s.recipients)
        msg.set_content(body)
        server = self._smtp_factory(s.host, s.port, timeout=s.timeout_s)
        try:
            if s.starttls:
                server.starttls()
            if s.user and s.password:
                server.login(s.user, s.password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass


class AlertDispatcher:
    """Delivers alerts to the sinks from one background thread, rate-limited per subject."""

    def __init__(self, sinks: Sequence[AlertSink], min_interval_s: float = 300.0,
                 clock: Callable[[], float] = time.time, threaded: bool = True):
        self.sinks = [s for s in sinks if not isinstance(s, NullAlertSink)]
        self.min_interval_s = min_interval_s
        self._clock = clock
        self._last: Dict[str, float] = {}
        self._threaded = threaded and bool(self.sinks)
        self._q: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self.sent: List[tuple] = []
        if self._threaded:
            self._thread = threading.Thread(target=self._run, name="midfreq-alerts", daemon=True)
            self._thread.start()

    def alert(self, subject: str, body: str = "", level: str = "ERROR") -> bool:
        """Queue an alert; False when an identical subject was sent within min_interval_s."""
        if not self.sinks:
            return False
        now = self._clock()
        last = self._last.get(subject)
        if last is not None and now - last < self.min_interval_s:
            return False
        self._last[subject] = now
        item = (subject, body or subject, level)
        if self._threaded:
            self._q.put(item)
        else:
            self._deliver(item)
        return True

    def _deliver(self, item: tuple) -> None:
        for sink in self.sinks:
            try:
                sink.send(*item)
                self.sent.append(item)
            except Exception as exc:     # an alert that cannot be sent must never stop the node
                print(f"[ops] alert via {type(sink).__name__} failed: {exc}", file=sys.stderr)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            self._deliver(item)

    def close(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(timeout)
            self._thread = None
        for sink in self.sinks:
            try:
                sink.close()
            except Exception:
                pass


class AlertLogHandler(logging.Handler):
    """Turns records at ERROR and above into alerts."""

    def __init__(self, dispatcher: AlertDispatcher, level: int = logging.ERROR):
        super().__init__(level=level)
        self.dispatcher = dispatcher

    def emit(self, record: logging.LogRecord) -> None:
        try:
            subject = f"{component_name(record.name)}: {record.getMessage()}"[:160]
            body = record.getMessage()
            ctx = getattr(record, "ops_ctx", None)
            if ctx:
                body += "\n\n" + json.dumps(_jsonable(ctx), indent=1)
            if record.exc_info:
                body += "\n\n" + logging.Formatter().formatException(record.exc_info)
            self.dispatcher.alert(subject, body, record.levelname)
        except Exception:
            pass


# ── installing it ────────────────────────────────────────────────────────────────────────────

@dataclass
class OpsSettings:
    level: str = "INFO"
    console: bool = True
    json_file: bool = True
    primary_tz: str = "Asia/Singapore"
    primary_label: str = "SGT"
    secondary_tz: str = "America/New_York"
    secondary_label: str = "ET"
    email_alerts: bool = False
    alert_min_interval_s: float = 300.0


class OpsLogging:
    """Installs the node's console and JSON handlers on the `midfreq` logger, and removes them
    again on `uninstall()` (so tests and repeated nodes in one process do not stack handlers)."""

    def __init__(self, settings: Optional[OpsSettings] = None, *, session_id: str = "",
                 log_dir: Optional[Path] = None, file_stem: Optional[str] = None,
                 time_source: Callable[[], float] = time.time, stream=None,
                 alert_sinks: Optional[Sequence[AlertSink]] = None, env: Optional[Mapping[str, str]] = None):
        self.settings = settings or OpsSettings()
        self.session_id = session_id
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.file_stem = file_stem or f"ops_{session_id or 'node'}"
        self.time_source = time_source
        self.stream = stream if stream is not None else sys.stdout
        self._env = env
        self._explicit_sinks = alert_sinks
        self.handlers: List[logging.Handler] = []
        self.json_path: Optional[Path] = None
        self.alerts: Optional[AlertDispatcher] = None
        self._previous_level: Optional[int] = None
        self._installed = False

    def install(self) -> "OpsLogging":
        if self._installed:
            return self
        root = logging.getLogger(ROOT_LOGGER)
        self._previous_level = root.level
        root.setLevel(getattr(logging, self.settings.level.upper(), logging.INFO))
        ctx = _ContextFilter(self.time_source, self.session_id)
        s = self.settings
        if s.console:
            h = logging.StreamHandler(self.stream)
            h.setFormatter(ConsoleFormatter(s.primary_tz, s.primary_label, s.secondary_tz, s.secondary_label))
            h.addFilter(ctx)
            self.handlers.append(h)
        if s.json_file and self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.json_path = self.log_dir / f"{self.file_stem}.jsonl"
            h = logging.FileHandler(self.json_path, encoding="utf-8")
            h.setFormatter(JsonLinesFormatter())
            h.addFilter(ctx)
            self.handlers.append(h)
        sinks: List[AlertSink] = list(self._explicit_sinks or [])
        if s.email_alerts and not sinks:
            email = EmailSettings.from_env(self._env)
            if email is not None:
                sinks.append(EmailAlertSink(email))
            else:
                root.warning("email alerts are on but MIDFREQ_ALERT_SMTP_HOST / MIDFREQ_ALERT_TO are not set")
        self.alerts = AlertDispatcher(sinks or [NullAlertSink()], s.alert_min_interval_s)
        if self.alerts.sinks:
            h = AlertLogHandler(self.alerts)
            h.addFilter(ctx)
            self.handlers.append(h)
        for h in self.handlers:
            root.addHandler(h)
        quiet_venue_sdk_loggers()
        self._installed = True
        return self

    def alert(self, subject: str, body: str = "", level: str = "ERROR") -> bool:
        return self.alerts.alert(subject, body, level) if self.alerts is not None else False

    def uninstall(self) -> None:
        if not self._installed:
            return
        root = logging.getLogger(ROOT_LOGGER)
        for h in self.handlers:
            root.removeHandler(h)
            try:
                h.flush()
                if isinstance(h, logging.FileHandler):
                    h.close()
            except Exception:
                pass
        self.handlers = []
        if self.alerts is not None:
            self.alerts.close()
        if self._previous_level is not None:
            root.setLevel(self._previous_level)
        self._installed = False


# ── heartbeat and resident memory ────────────────────────────────────────────────────────────

def resident_memory_mb() -> Optional[float]:
    """The process's resident memory: current on Linux (/proc), peak elsewhere."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except OSError:
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024.0 * 1024.0) if sys.platform == "darwin" else peak / 1024.0, 1)
    except Exception:
        return None


def describe_heartbeat(hb: Any) -> str:
    """One readable line for a midfreq.trading.interfaces.Heartbeat."""
    if hb.last_bar_ts:
        last = datetime.fromtimestamp(hb.last_bar_ts / 1e9, tz=timezone.utc).strftime("%H:%M:%S UTC")
    else:
        last = "none yet"
    lag = f"{hb.stream_lag_s:.0f}s" if hb.stream_lag_s is not None else "n/a"
    held = ", ".join(f"{k} {v:g}" for k, v in sorted(hb.positions.items())) or "none"
    mem = f"{hb.memory_mb:.0f} MB" if hb.memory_mb is not None else "n/a"
    return f"heartbeat: last bar {last}, stream silent {lag}, open orders {hb.open_orders}, held {held}, rss {mem}"
