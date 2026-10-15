"""
Broker snapshots: an account as the venue reports it, written to a file that a dashboard which
cannot reach the venue can show.

Only the trading VM can read the Moomoo accounts (OpenD runs there), so cron runs this every five
minutes (ops/vm/broker_snapshot.sh) and writes `broker_<account id>.json` into the bot's record
folder, which reaches the hosted dashboard's bucket with the session logs (ops/vm/sync_logs.sh).
The dashboard API serves the file as the account's view, as of its publish time.

    python -m midfreq.data.snapshots --out-dir ~/midfreq/runtime/live_sessions   # every `publish: true` account
    python -m midfreq.data.snapshots moomoo-us --out-dir /tmp/x                  # one account, whatever its flag

The file has the shape of the API's AccountView (id, label, connector, as_of, snapshot,
positions, orders) plus `published_by` (the host name). Nothing from the account's options or
secrets is written. Each account is one client (for Moomoo one trade context and four queries:
balance, positions, today's orders, order history), closed before the next account is read.

An account that cannot be read now leaves its previous file as it was, so the dashboard keeps
showing the last good snapshot and its age; the command reports why and exits 1. A new file
replaces the old one atomically (a temporary file in the same folder, then a rename), so a
reader or the upload never sees half a file.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import pandas as pd

from midfreq.data.adapters.accounts import Connections, account_config, create_account_client, load_accounts
from midfreq.data.adapters.base import (
    AccountSnapshot, AccountUnavailableError, ConnectorError, OrderReport, PositionReport,
)
from midfreq.data.adapters.registry import ConnectorRegistry

ORDER_DAYS = 14

# Account ids become file names: no separators, nothing hidden.
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

__all__ = ["ORDER_DAYS", "main", "publish", "published_ids", "snapshot_name"]


def snapshot_name(account_id: str) -> str:
    """The file an account's snapshot is published as: broker_<account id>.json."""
    if not _SAFE_ID.fullmatch(account_id):
        raise ValueError(f"account id {account_id!r} cannot be a file name; use letters, digits, '.', '_', '-'")
    return f"broker_{account_id}.json"


def published_ids(connections: Connections = None) -> List[str]:
    """The accounts with `publish: true` in the accounts section, in file order."""
    return [account_id for account_id, cfg in load_accounts(connections).items() if cfg["publish"]]


def publish(account_id: str, out_dir: Union[str, Path], connections: Connections = None,
            registry: Optional[ConnectorRegistry] = None) -> Path:
    """
    Read one configured account (snapshot, positions, orders of the last ORDER_DAYS days) and
    write it to `out_dir`/broker_<account_id>.json, replacing the previous snapshot. Returns the
    file. AccountUnavailableError if the account cannot be read now (nothing is written);
    KeyError for an id that is not configured; ConnectorError for an entry no connector serves.
    """
    name = snapshot_name(account_id)
    cfg = account_config(account_id, connections)
    client = create_account_client(account_id, connections, registry=registry)
    try:
        ok, reason = client.available()
        if not ok:
            raise AccountUnavailableError(reason or "unavailable")
        snap, positions, orders = client.snapshot(), client.positions(), client.orders(ORDER_DAYS)
    finally:
        client.close()
    return _write(Path(out_dir), name, _document(cfg, snap, positions, orders))


def _document(cfg: Dict[str, Any], snap: AccountSnapshot, positions: Sequence[PositionReport],
              orders: Sequence[OrderReport]) -> Dict[str, Any]:
    """The AccountView the API serves, built from the client's reports. Only the entry's id,
    label and connector are taken from the configuration: never its options or secrets."""
    return {
        "id": cfg["id"], "label": cfg["label"], "connector": cfg["connector"],
        "as_of": _iso(snap.ts) or _iso(pd.Timestamp.now(tz="UTC")),
        "published_by": socket.gethostname(),
        "snapshot": {"currency": snap.currency, "total_assets": _num(snap.total_assets),
                     "cash": _num(snap.cash), "market_value": _num(snap.market_value)},
        "positions": [{"symbol": p.symbol, "quantity": _num(p.quantity), "average_price": _num(p.average_price),
                       "market_price": _num(p.market_price), "market_value": _num(p.market_value),
                       "unrealized_pnl": _num(p.unrealized_pnl), "instrument_id": p.instrument_id}
                      for p in positions],
        "orders": [{"order_id": str(o.order_id), "symbol": o.symbol, "side": o.side, "quantity": _num(o.quantity),
                    "filled_quantity": _num(o.filled_quantity), "price": _num(o.price),
                    "average_fill_price": _num(o.average_fill_price), "status": o.status,
                    "created": _iso(o.created), "updated": _iso(o.updated)}
                   for o in orders],
    }


def _write(out_dir: Path, name: str, document: Dict[str, Any]) -> Path:
    """`document` as out_dir/name, atomically. The temporary file is hidden and never matches
    broker_*.json, so nothing takes it for a snapshot."""
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=1, allow_nan=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=out_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)                  # like the rest of the record (mkstemp makes it 0600)
        os.replace(tmp, out_dir / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return out_dir / name


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _iso(ts: Any) -> Optional[str]:
    """A time as ISO 8601 in UTC (a time without a zone is taken as UTC)."""
    ts = pd.Timestamp(ts) if ts is not None else pd.NaT
    if pd.isna(ts):
        return None
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.isoformat()


# ── command line ─────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None, connections: Connections = None,
         registry: Optional[ConnectorRegistry] = None) -> int:
    """Publish the named accounts, or every `publish: true` account. Silent when all succeed;
    each failure is one line on stderr, and the exit status is then 1. One account failing does
    not stop the others."""
    p = argparse.ArgumentParser(prog="python -m midfreq.data.snapshots",
                                description="Publish broker account snapshots as broker_<id>.json")
    p.add_argument("accounts", nargs="*", help="account ids (default: every account with publish: true)")
    p.add_argument("--out-dir", required=True, type=Path, help="the bot's record folder (live_sessions)")
    args = p.parse_args(argv)
    try:
        ids = args.accounts or published_ids(connections)
    except (OSError, ValueError) as e:
        print(f"broker snapshots: cannot read the accounts section: {e}", file=sys.stderr)
        return 1
    failed = 0
    for account_id in ids:
        try:
            publish(account_id, args.out_dir, connections, registry=registry)
        except KeyError as e:
            reason = e.args[0] if e.args else str(e)
        except (ConnectorError, OSError, ValueError) as e:      # AccountUnavailableError is a ConnectorError
            reason = str(e) or type(e).__name__
        except Exception as e:                                  # anything else: say what, keep going
            reason = f"{type(e).__name__}: {e}"
        else:
            continue
        failed += 1
        print(f"broker snapshot {account_id}: {reason} (the previous snapshot, if any, is kept)", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
