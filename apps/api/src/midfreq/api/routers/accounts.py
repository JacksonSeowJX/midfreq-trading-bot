"""
Broker and exchange accounts, as the venue reports them, through an AccountProvider.

This is the second, independent source next to the bot's record: the dashboard shows both so
they can be reconciled, and never merges them. Every call to a venue runs on a daemon thread
with a deadline, so a gateway that is down or a socket that never answers cannot hang a request
(the moomoo SDK, for one, retries a refused connection forever without raising).

Only the trading VM reaches OpenD, so it also publishes each account it reads as a snapshot,
broker_<id>.json in the record (midfreq.data.snapshots, every 5 minutes), and an account is
served in one of two kinds (AccountInfo.kind):

- live: read through its gateway now;
- snapshot: the published file, as of its publish time (`as_of`; the web app shows the age).

With Settings.snapshot_accounts_only (the hosted dashboard) only the configured accounts that
have a snapshot are listed, and no gateway is ever probed. Otherwise an account is read live,
and a snapshot stands in only when the gateway cannot be read (available, with the live failure
as the reason).
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from midfreq.api import schemas as s
from midfreq.api.account_provider import AccountEntry, AccountProvider
from midfreq.api.convert import utc
from midfreq.api.deps import get_account_provider, get_record_store, get_settings
from midfreq.api.settings import Settings
from midfreq.api.store import RecordStore
from midfreq.data.adapters.base import AccountClient, AccountUnavailableError, ConnectorError

router = APIRouter()


class _Call:
    """`fn()` on a daemon thread. A call that outlives its deadline is abandoned, not killed;
    being a daemon it cannot keep the server from exiting."""

    def __init__(self, fn: Callable[[], Any], name: str):
        self._fn, self._result, self._error = fn, None, None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._result = self._fn()
        except BaseException as e:        # handed to the caller in result()
            self._error = e

    def join(self, timeout: float) -> None:
        self._thread.join(max(0.0, timeout))

    def result(self) -> Any:
        if self._thread.is_alive():
            raise TimeoutError
        if self._error is not None:
            raise self._error
        return self._result


def _reason(e: BaseException) -> str:
    return str(e) or type(e).__name__


def _probe(provider: AccountProvider, account_id: str) -> Tuple[bool, str]:
    client = provider.client(account_id)
    try:
        ok, reason = client.available()
        return bool(ok), "" if ok else str(reason or "unavailable")
    finally:
        client.close()


def _read(client: AccountClient, order_days: int):
    try:
        ok, reason = client.available()
        if not ok:
            raise AccountUnavailableError(reason or "unavailable")
        return client.snapshot(), client.positions(), client.orders(order_days)
    finally:
        client.close()


def _find(provider: AccountProvider, account_id: str) -> Optional[AccountEntry]:
    return next((a for a in provider.accounts() if a.id == account_id), None)


def _float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


# ── published snapshots ─────────────────────────────────────────────────────

class _BadSnapshot(Exception):
    """A published snapshot that cannot be served."""


def _snapshot_name(account_id: str) -> str:
    return f"broker_{account_id}.json"           # as midfreq.data.snapshots names it


def _published(store: RecordStore, account_id: str) -> bool:
    return _snapshot_name(account_id) in store.broker_snapshots()


def _no_nan(constant: str):
    raise ValueError(f"{constant} is not a number JSON can carry")      # the publisher never writes one


def _snapshot_view(store: RecordStore, entry: AccountEntry, order_days: int) -> s.AccountView:
    """The account's published snapshot, with the configured label and connector, and the orders
    of the last `order_days` days before its as_of. _BadSnapshot if it cannot be read."""
    name = _snapshot_name(entry.id)
    try:
        view = s.AccountView.model_validate(json.loads(store.read_broker_snapshot(name), parse_constant=_no_nan))
    except KeyError:
        raise _BadSnapshot(f"{name} is no longer in the record") from None
    except ValidationError as e:
        where = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'file'}: {err['msg']}" for err in e.errors()[:3])
        raise _BadSnapshot(f"{name} is not an account snapshot ({where})") from None
    except (ValueError, OSError) as e:           # not JSON (json.JSONDecodeError), or unreadable
        raise _BadSnapshot(f"{name} cannot be read: {_reason(e)}") from None
    if view.id != entry.id:
        raise _BadSnapshot(f"{name} holds account {view.id!r}")
    as_of = utc(view.as_of)
    since = as_of - timedelta(days=order_days)
    return view.model_copy(update={"label": entry.label, "connector": entry.connector, "as_of": as_of,
                                   "orders": [o for o in view.orders if (utc(o.created) or as_of) >= since]})


def _snapshot_info(store: RecordStore, entry: AccountEntry, live_reason: str = "") -> s.AccountInfo:
    try:
        _snapshot_view(store, entry, order_days=1)
        ok, reason = True, live_reason
    except _BadSnapshot as e:
        ok, reason = False, "; ".join(r for r in (live_reason, str(e)) if r)
    return s.AccountInfo(id=entry.id, label=entry.label, connector=entry.connector, available=ok,
                         reason=reason, kind="snapshot")


# ── routes ──────────────────────────────────────────────────────────────────

@router.get("/api/accounts", response_model=List[s.AccountInfo], tags=["accounts"])
def accounts(provider: AccountProvider = Depends(get_account_provider),
             settings: Settings = Depends(get_settings),
             store: RecordStore = Depends(get_record_store)):
    """Configured accounts and whether each can be read now (a quick probe, never hangs)."""
    # An account whose gateway cannot be read but which has a published snapshot is listed as
    # that snapshot. With snapshot_accounts_only: only the accounts with a snapshot, no probe.
    entries = provider.accounts()
    if settings.snapshot_accounts_only:
        names = set(store.broker_snapshots()) if entries else set()
        return [_snapshot_info(store, a) for a in entries if _snapshot_name(a.id) in names]

    # All probes run at once and share one deadline, so the listing takes at most probe_timeout.
    calls = [_Call(lambda a=a: _probe(provider, a.id), name=f"probe-{a.id}") for a in entries]
    deadline = time.monotonic() + settings.probe_timeout
    out, names = [], None
    for a, call in zip(entries, calls):
        call.join(deadline - time.monotonic())
        try:
            ok, reason = call.result()
        except TimeoutError:
            ok, reason = False, f"no answer within {settings.probe_timeout:g}s"
        except Exception as e:
            ok, reason = False, _reason(e)
        if not ok:
            names = set(store.broker_snapshots()) if names is None else names
            if _snapshot_name(a.id) in names:
                out.append(_snapshot_info(store, a, live_reason=reason))
                continue
        out.append(s.AccountInfo(id=a.id, label=a.label, connector=a.connector, available=ok, reason=reason))
    return out


@router.get("/api/accounts/{account_id}", response_model=s.AccountView, tags=["accounts"],
            responses={404: {"description": "no such account"},
                       503: {"description": "the account cannot be read now (gateway down, no key)"}})
def account(account_id: str, order_days: int = Query(14, ge=1, le=90),
            provider: AccountProvider = Depends(get_account_provider),
            settings: Settings = Depends(get_settings),
            store: RecordStore = Depends(get_record_store)):
    # 503 also when the account is served from a snapshot that cannot be read; 404 on the hosted
    # dashboard for a configured account without one (it is not listed there either).
    entry = _find(provider, account_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="no such account")
    if settings.snapshot_accounts_only:
        if not _published(store, account_id):
            raise HTTPException(status_code=404, detail="no such account")
        try:
            return _snapshot_view(store, entry, order_days)
        except _BadSnapshot as e:
            raise HTTPException(status_code=503, detail=str(e))

    def read():
        return _read(provider.client(account_id), order_days)

    call = _Call(read, name=f"account-{account_id}")
    call.join(settings.account_timeout)
    try:
        snap, positions, orders = call.result()
    except KeyError:                      # the factory does not know the id
        raise HTTPException(status_code=404, detail="no such account")
    except (TimeoutError, AccountUnavailableError, ConnectorError, OSError, ValueError) as e:
        # ConnectorError: an entry no connector can serve; ValueError: a malformed accounts section
        reason = (f"{account_id}: no answer within {settings.account_timeout:g}s" if isinstance(e, TimeoutError)
                  else _reason(e))
        if not _published(store, account_id):
            raise HTTPException(status_code=503, detail=reason)
        try:
            return _snapshot_view(store, entry, order_days)
        except _BadSnapshot as bad:
            raise HTTPException(status_code=503, detail=f"{reason}; {bad}")

    return s.AccountView(
        id=entry.id, label=entry.label, connector=entry.connector,
        as_of=utc(snap.ts) or datetime.now(timezone.utc),
        snapshot=s.AccountSnapshotOut(currency=snap.currency, total_assets=snap.total_assets,
                                      cash=snap.cash, market_value=snap.market_value),
        positions=[s.PositionOut(symbol=p.symbol, quantity=p.quantity, average_price=_float(p.average_price),
                                 market_price=_float(p.market_price), market_value=_float(p.market_value),
                                 unrealized_pnl=_float(p.unrealized_pnl), instrument_id=p.instrument_id)
                   for p in positions],
        orders=[s.OrderOut(order_id=str(o.order_id), symbol=o.symbol, side=o.side, quantity=o.quantity,
                           filled_quantity=o.filled_quantity, price=_float(o.price),
                           average_fill_price=_float(o.average_fill_price), status=o.status,
                           created=utc(o.created), updated=utc(o.updated))
                for o in orders])
