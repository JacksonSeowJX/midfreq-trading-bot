"""
Alerts for the trading node, for when a person must look at something: `AlertSink` with a no-op
default (`NullAlertSink`) and an email sink (`EmailAlertSink`) whose SMTP settings come from the
environment (`EmailSettings`; off unless the node configuration turns it on), and
`AlertDispatcher`, which delivers alerts to the sinks from a background thread, rate-limited per
subject, so a slow mail server cannot stall the node.

Records at ERROR and above become alerts through midfreq.trading.ops.logs.AlertLogHandler, which
OpsLogging installs.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


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
