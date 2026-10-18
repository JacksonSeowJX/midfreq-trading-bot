"""Operations logging: the console format, the JSON lines, context fields, the venue SDK's
console output, and alerts (never sent anywhere in a test)."""
from __future__ import annotations

import io
import json
import logging
import re
import sys

import pytest

from midfreq.trading.ops.logs import (
    AlertDispatcher, ConsoleFormatter, EmailAlertSink, EmailSettings, JsonLinesFormatter, NullAlertSink,
    OpsLogging, OpsSettings, get_logger, log_context, quiet_venue_sdk_loggers,
)

T = 1_791_984_902.25                      # 2026-10-14 13:35:02.250 UTC (exact in binary)


def _record(msg="hello", name="midfreq.trading.oms", level=logging.INFO, **extra):
    r = logging.LogRecord(name, level, __file__, 1, msg, None, None)
    r.node_time = T
    r.session_id = "S1"
    r.ops_ctx = extra
    return r


def test_console_format_has_both_clocks():
    line = ConsoleFormatter().format(_record())
    assert line == "2026-10-14 21:35:02.250 SGT | 09:35:02 ET | INFO    | oms | hello"
    assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} SGT \| \d\d:\d\d:\d\d ET \| ", line)
    line = ConsoleFormatter().format(_record("sent", client_order_id="N1-x-000001"))
    assert line.endswith("| sent [N1-x-000001]")


def test_json_lines_carry_utc_time_session_and_context():
    row = json.loads(JsonLinesFormatter().format(_record("sent", cross_section="2026-10-14 10:30",
                                                         client_order_id="N1-x-000001")))
    assert row == {"ts": "2026-10-14T13:35:02.250000Z", "level": "INFO", "component": "oms", "session": "S1",
                   "msg": "sent", "cross_section": "2026-10-14 10:30", "client_order_id": "N1-x-000001"}


def test_ops_logging_install_and_context(tmp_path):
    stream = io.StringIO()
    ops = OpsLogging(OpsSettings(), session_id="S1", log_dir=tmp_path, file_stem="ops_test",
                     time_source=lambda: T, stream=stream).install()
    try:
        log = get_logger("node")
        with log_context(cross_section="10:30", strategy="xsr"):
            log.info("decided")
        log.warning("plain", extra={"ctx": {"client_order_id": "C1"}})
        logging.getLogger("midfreq.strategy.xsr").info("from the strategy")
    finally:
        ops.uninstall()
    rows = [json.loads(l) for l in (tmp_path / "ops_test.jsonl").read_text().splitlines()]
    assert rows[0]["cross_section"] == "10:30" and rows[0]["strategy"] == "xsr" and rows[0]["component"] == "node"
    assert rows[1]["client_order_id"] == "C1" and rows[1]["level"] == "WARNING"
    assert rows[2]["component"] == "strategy.xsr"
    assert "| WARNING | node | plain [C1]" in stream.getvalue()
    n = len(stream.getvalue())
    get_logger("node").info("after uninstall")
    assert len(stream.getvalue()) == n


def test_the_venue_sdks_console_output_is_kept_out_and_warnings_forwarded(caplog):
    # A logger set up the way the Moomoo SDK sets up FTConsoleLog (non-propagating, stdout).
    sdk = logging.getLogger("FTConsoleLogTest")
    sdk.handlers[:] = []
    sdk.addHandler(logging.StreamHandler(sys.stdout))
    sdk.propagate = False
    sdk.setLevel(logging.INFO)
    quiet_venue_sdk_loggers({"FTConsoleLogTest": "sdk.test"})
    assert not any(type(h) is logging.StreamHandler for h in sdk.handlers)
    with caplog.at_level(logging.INFO, logger="midfreq.sdk.test"):
        sdk.info("connected to OpenD")
        sdk.warning("quota nearly used")
    assert [r.getMessage() for r in caplog.records if r.name == "midfreq.sdk.test"] == ["quota nearly used"]
    # a logger the SDK creates later cannot add its console handler back
    quiet_venue_sdk_loggers({"FTConsoleLogLater": "sdk.later"})
    later = logging.getLogger("FTConsoleLogLater")
    later.addHandler(logging.StreamHandler(sys.stdout))
    assert not any(type(h) is logging.StreamHandler for h in later.handlers)


def test_email_settings_come_from_the_environment_only_when_complete():
    assert EmailSettings.from_env({}) is None
    assert EmailSettings.from_env({"MIDFREQ_ALERT_SMTP_HOST": "smtp.x"}) is None
    s = EmailSettings.from_env({"MIDFREQ_ALERT_SMTP_HOST": "smtp.x", "MIDFREQ_ALERT_TO": "a@x, b@x",
                                "MIDFREQ_ALERT_SMTP_USER": "u", "MIDFREQ_ALERT_SMTP_PASSWORD": "p",
                                "MIDFREQ_ALERT_SMTP_PORT": "2525"})
    assert (s.host, s.port, list(s.recipients), s.sender, s.starttls) == ("smtp.x", 2525, ["a@x", "b@x"], "u", True)


def test_email_sink_and_rate_limit():
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append(("connect", host, port))

        def starttls(self):
            sent.append(("starttls",))

        def login(self, u, p):
            sent.append(("login", u))

        def send_message(self, msg):
            sent.append(("send", msg["Subject"], msg["To"]))

        def quit(self):
            pass
    settings = EmailSettings(host="smtp.x", port=587, user="u", password="p", sender="n@x", recipients=["a@x"])
    clock = {"t": 0.0}
    d = AlertDispatcher([EmailAlertSink(settings, smtp_factory=FakeSMTP)], min_interval_s=300,
                        clock=lambda: clock["t"], threaded=False)
    assert d.alert("stream stalled", "no bar for 75 min")
    assert not d.alert("stream stalled", "again")                 # within 5 minutes: dropped
    clock["t"] = 301
    assert d.alert("stream stalled", "again")
    assert [s[0] for s in sent] == ["connect", "starttls", "login", "send"] * 2
    assert sent[3][1] == "[midfreq-node] ERROR: stream stalled"
    assert not AlertDispatcher([NullAlertSink()]).alert("x")      # the default sends nothing


def test_error_records_become_alerts(tmp_path):
    got = []

    class Sink(NullAlertSink.__mro__[1]):
        def send(self, subject, body, level="ERROR"):
            got.append((subject, level))
    ops = OpsLogging(OpsSettings(console=False), session_id="S", log_dir=tmp_path, alert_sinks=[Sink()],
                     time_source=lambda: T).install()
    try:
        get_logger("oms").error("reconcile found an unknown order")
        get_logger("oms").warning("only a warning")
    finally:
        ops.uninstall()
    assert got == [("oms: reconcile found an unknown order", "ERROR")]
