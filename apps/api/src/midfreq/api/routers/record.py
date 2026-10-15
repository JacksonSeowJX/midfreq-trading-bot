"""
The bot's own record: the S&P 100 forward test and every recorded session, read from the
session logs through a RecordStore. Nothing here asks the broker or the bot process.

Times the bot stamps from its own clock (session_start, session_end) have no zone and are the
VM's Singapore time; they are returned in UTC. Candle and trade times are returned as the bot
recorded them.
"""
import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from midfreq.api import schemas as s
from midfreq.api.convert import plain, utc
from midfreq.api.deps import get_record_store, get_settings
from midfreq.api.settings import Settings
from midfreq.api.store import RecordStore
from midfreq.research import forward_test as ft

router = APIRouter()

DEFAULT_CAPITAL = 100_000.0

# The bot prints "Session log: live_sessions/session_<...>.jsonl" when it opens a session, which
# is how a console log is matched to its session. Runs that crash before that line have no
# session log, so they are not sessions here.
SESSION_LINE = re.compile(r"Session log: (\S+\.jsonl)")
CRASH = re.compile(r"Traceback|CRITICAL")


# ── reading the logs ────────────────────────────────────────────────────────

def parse_events(text: str) -> List[Dict[str, Any]]:
    """The events of a session log; a line cut short by a crash is skipped."""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            events.append(e)
    return events


def crash_index(store: RecordStore) -> Dict[str, bool]:
    """Session id -> whether its console log has a Traceback or CRITICAL line, for every
    session that has a console log."""
    out: Dict[str, bool] = {}
    for name in store.console_logs():
        try:
            text = store.read_console_log(name)
        except (KeyError, OSError):
            continue
        m = SESSION_LINE.search(text)
        if not m:
            continue
        sid = m.group(1).replace("\\", "/").rsplit("/", 1)[-1][:-len(".jsonl")]
        out[sid] = out.get(sid, False) or bool(CRASH.search(text))
    return out


def _first(events: List[Dict[str, Any]], kind: str) -> Dict[str, Any]:
    return next((e for e in events if e.get("type") == kind), {})


def _total_assets(event: Dict[str, Any]) -> Optional[float]:
    value = (event.get("account") or {}).get("total_assets")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _market(symbols: List[Any]) -> str:
    return str(symbols[0]).split(".")[0].upper() if symbols else "?"


def summarise(session_id: str, events: List[Dict[str, Any]], crashed: bool, tz: str) -> s.SessionSummary:
    start, end = _first(events, "session_start"), _first(events, "session_end")
    symbols = start.get("symbols") or []
    # The engine's own count when the session ended; early sessions counted trades there
    # without logging a trade event for each.
    counted = sum(1 for e in events if e.get("type") == "trade")
    trades = end.get("trades") if isinstance(end.get("trades"), int) else counted
    return s.SessionSummary(
        id=session_id, market=_market(symbols), strategy=str(start.get("strategy") or "?"),
        timeframe=start.get("timeframe"), symbols=len(symbols),
        started_at=utc(start.get("timestamp"), tz), ended_at=utc(end.get("timestamp"), tz),
        start_total_assets=_total_assets(start), end_total_assets=_total_assets(end),
        candles=sum(1 for e in events if e.get("type") == "candle_close"),
        trades=trades, crashed=crashed)


def _forward_test_capital(settings: Settings, market: str) -> float:
    """The capital the forward test is capped at (config/sp100_forward_test.json)."""
    if market != "US":
        return DEFAULT_CAPITAL
    try:
        return float(json.loads(settings.forward_test_file.read_text()).get("capital", DEFAULT_CAPITAL))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_CAPITAL


# ── routes ──────────────────────────────────────────────────────────────────

@router.get("/api/forward-test", response_model=s.ForwardTest, tags=["bot record"],
            responses={404: {"description": "no forward-test session for this market"}})
def forward_test(market: str = Query("US", pattern="^(US|HK)$"),
                 store: RecordStore = Depends(get_record_store),
                 settings: Settings = Depends(get_settings)):
    d = None
    if store.available():
        d = ft.load(store.local_dir(), capital=_forward_test_capital(settings, market), market=market)
    if d is None:
        raise HTTPException(status_code=404, detail=f"no forward-test session for {market}")
    tz = settings.record_timezone

    equity = []
    for row in d["equity"].to_dict("records"):
        ts = utc(row.get("timestamp"), tz)
        if ts is not None:
            pnl = float(row["pnl"])
            equity.append(s.EquityPoint(timestamp=ts, pnl=pnl, value=d["capital"] + pnl))
    trades = [s.ForwardTrade(date=r["Date"], decided_on_candle=str(r["Decided on candle"]),
                             side=r["Side"], stock=r["Stock"], shares=r["Shares"], price=r["Price"],
                             value=r["Value"], realised_pnl=plain(r["Realised P&L"]),
                             realised_pct=plain(r["Realised %"]))
              for r in d["trades"].to_dict("records")]
    holdings = [s.Holding(stock=r["Stock"], shares=r["Shares"], bought_at=r["Bought at"],
                          last_price=r["Last price"], value=r["Value"],
                          unrealised_pnl=r["Unrealised P&L"], unrealised_pct=r["Unrealised %"])
                for r in d["holdings"].to_dict("records")]
    return s.ForwardTest(
        market=market, capital=d["capital"], pnl=d["pnl"], pnl_pct=d["pnl_pct"], value=d["value"],
        sessions=d["sessions"], rebalances=d["rebalances"], orders=d["orders"],
        realised=d["realised"], started=d["started"], first_trade=d["first_trade"],
        as_of=utc(d["as_of"], tz), equity=equity, trades=trades, holdings=holdings)


@router.get("/api/sessions", response_model=List[s.SessionSummary], tags=["bot record"])
def sessions(market: Optional[str] = Query(None, pattern="^(US|HK)$"), limit: int = Query(50, ge=1, le=500),
             store: RecordStore = Depends(get_record_store),
             settings: Settings = Depends(get_settings)):
    """Recorded sessions, newest first."""
    crashed = crash_index(store)
    out: List[s.SessionSummary] = []
    # Ids are session_<YYYYMMDD>_<HHMMSS>_..., so newest first is reverse name order.
    for sid in sorted(store.session_ids(), reverse=True):
        try:
            events = parse_events(store.read_session(sid))
        except (KeyError, OSError):
            continue                      # removed since it was listed
        summary = summarise(sid, events, crashed.get(sid, False), settings.record_timezone)
        if market and summary.market != market:
            continue
        out.append(summary)
        if len(out) >= limit:
            break
    return out


@router.get("/api/sessions/{session_id}", response_model=s.SessionDetail, tags=["bot record"],
            responses={404: {"description": "no such session"}})
def session(session_id: str, store: RecordStore = Depends(get_record_store),
            settings: Settings = Depends(get_settings)):
    if session_id not in store.session_ids():
        raise HTTPException(status_code=404, detail="no such session")
    try:
        events = parse_events(store.read_session(session_id))
    except (KeyError, OSError):
        raise HTTPException(status_code=404, detail="no such session")
    tz = settings.record_timezone
    summary = summarise(session_id, events, crash_index(store).get(session_id, False), tz)

    # The broker's account total at session start and end: the account's true value (a
    # candle_close `equity` is only that session's own partial view of a shared account).
    equity, first = [], None
    for e in events:
        total = _total_assets(e)
        ts = utc(e.get("timestamp"), tz)
        if e.get("type") in ("session_start", "session_end") and total and ts is not None:
            first = total if first is None else first
            equity.append(s.EquityPoint(timestamp=ts, pnl=total - first, value=total))

    trades = []
    for e in events:
        if e.get("type") != "trade":
            continue
        side, ts = str(e.get("side", "")).upper(), utc(e.get("timestamp"))
        try:
            qty, price = float(e["qty"]), float(e["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if side in ("BUY", "SELL") and ts is not None:
            trades.append(s.SessionTrade(timestamp=ts, symbol=str(e.get("symbol", "?")), side=side,
                                         qty=qty, price=price))

    return s.SessionDetail(summary=summary, equity=equity, trades=trades,
                           event_counts=dict(Counter(str(e.get("type")) for e in events)))
