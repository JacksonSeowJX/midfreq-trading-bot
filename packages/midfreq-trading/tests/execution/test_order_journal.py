"""OrderJournal: append-only orders_<date>.jsonl, rebuilt by load() (midfreq.trading.journal)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from midfreq.core.model import (
    ClientOrderId, InstrumentId, Order, OrderCanceled, OrderFilled, OrderSide, OrderStatus, OrderType,
    StrategyId, TimeInForce, TradeId,
)
from midfreq.trading.execution.journal import (
    OrderJournal, format_order_id, order_from_dict, order_to_dict, parse_order_id,
)

AAPL = InstrumentId.from_str("AAPL.XNAS")
S1 = StrategyId("XSReversal-001")


class WallClock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def make_order(seq=1, day="20261015", **kw) -> Order:
    fields = dict(side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=100.0, price=150.25,
                  time_in_force=TimeInForce.DAY, reference_price=150.0, tags={"note": "x"}, ts_init=123)
    fields.update(kw)
    return Order(format_order_id(S1, day, seq), S1, AAPL, **fields)


def test_order_ids_format_and_parse():
    cid = format_order_id(S1, "20261015", 7)
    assert str(cid) == "XSReversal-001-20261015-0007"
    assert parse_order_id(cid) == ("XSReversal-001", "20261015", 7)
    assert parse_order_id("XSReversal-001-20261015-12345") == ("XSReversal-001", "20261015", 12345)
    assert parse_order_id("O-XSReversal-001-000001") is None
    assert parse_order_id("anything") is None


def test_order_round_trips_through_its_dict():
    order = make_order(status=OrderStatus.PARTIALLY_FILLED, filled_qty=40.0, avg_px=150.2, commission=0.3,
                       reason="r", ts_last=999)
    order.tags["venue_order_id"] = "V1"
    back = order_from_dict(json.loads(json.dumps(order_to_dict(order))))
    assert order_to_dict(back) == order_to_dict(order)
    assert back.client_order_id == order.client_order_id and back.instrument_id == AAPL
    assert back.status is OrderStatus.PARTIALLY_FILLED and back.time_in_force is TimeInForce.DAY


def test_write_is_one_json_line_with_utc_wall_time_and_is_on_disk_before_returning(tmp_path):
    wall = WallClock(datetime(2026, 10, 15, 14, 0, 1, 250000, tzinfo=timezone.utc))
    journal = OrderJournal(tmp_path, fsync=True, wall_clock=wall)
    order = make_order()
    order.tags["venue_order_id"] = "V9"
    journal.record_order(order, ts_ns=5)
    # Read through a separate handle while the journal is still open: the line must be there.
    lines = (tmp_path / "orders_20261015.jsonl").read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "order" and record["ts"] == 5
    assert record["time"] == "2026-10-15T14:00:01.250000+00:00"
    assert record["venue_order_id"] == "V9"
    assert record["order"]["client_order_id"] == "XSReversal-001-20261015-0001"
    journal.close()


def test_load_rebuilds_orders_from_their_last_snapshot_trade_ids_and_sequences(tmp_path):
    journal = OrderJournal(tmp_path, wall_clock=WallClock(datetime(2026, 10, 15, 14, tzinfo=timezone.utc)))
    a, b = make_order(1), make_order(2, quantity=10.0)
    journal.record_order(a, 1)
    journal.record_order(b, 1)
    a.status, a.filled_qty, a.avg_px = OrderStatus.FILLED, 100.0, 150.1
    fill = OrderFilled(a.client_order_id, S1, AAPL, 2, trade_id=TradeId("T1"), side=OrderSide.BUY,
                       last_qty=100.0, last_px=150.1)
    journal.record_event(fill, 2, a, applied=True, outcome="apply")
    ignored = OrderFilled(a.client_order_id, S1, AAPL, 3, trade_id=TradeId("T2"), side=OrderSide.BUY,
                          last_qty=1.0, last_px=150.1)
    journal.record_event(ignored, 3, a, applied=False, outcome="illegal")
    b.status, b.reason = OrderStatus.CANCELED, "unfilled after 3 attempts"
    journal.record_event(OrderCanceled(b.client_order_id, S1, AAPL, 4, reason="CANCELLED_ALL"), 4, b)
    journal.close()

    state = OrderJournal(tmp_path).load(lookback_days=None)
    assert list(state.orders) == [a.client_order_id, b.client_order_id]
    assert state.orders[a.client_order_id].status is OrderStatus.FILLED
    assert state.orders[a.client_order_id].filled_qty == 100.0
    assert state.orders[b.client_order_id].reason == "unfilled after 3 attempts"
    # (venue, the order's key, trade id): no venue order id here, so the client order id; the
    # fill not applied is not remembered
    assert state.trade_ids == {("XNAS", str(a.client_order_id), "T1")}
    assert state.sequences == {("XSReversal-001", "20261015"): 2}
    assert state.open_orders() == []
    assert state.records == 5 and state.bad_lines == 0


def test_load_skips_a_torn_last_line(tmp_path):
    journal = OrderJournal(tmp_path, wall_clock=WallClock(datetime(2026, 10, 15, tzinfo=timezone.utc)))
    journal.record_order(make_order(1, status=OrderStatus.SUBMITTED), 1)
    journal.close()
    with open(tmp_path / "orders_20261015.jsonl", "a") as f:
        f.write('{"kind": "order", "ts": 2, "order": {"client_order_id": "XSRev')   # crash mid-write
    state = OrderJournal(tmp_path).load(lookback_days=None)
    assert state.bad_lines == 1 and len(state.orders) == 1
    assert [o.client_order_id for o in state.open_orders()] == [ClientOrderId("XSReversal-001-20261015-0001")]


def test_a_new_utc_day_starts_a_new_file_and_load_reads_both(tmp_path):
    wall = WallClock(datetime(2026, 10, 15, 23, 59, 59, tzinfo=timezone.utc))
    journal = OrderJournal(tmp_path, wall_clock=wall)
    journal.record_order(make_order(1), 1)
    wall.now += timedelta(seconds=2)
    journal.record_order(make_order(1, day="20261016"), 2)
    journal.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["orders_20261015.jsonl", "orders_20261016.jsonl"]
    state = OrderJournal(tmp_path, wall_clock=wall).load()
    assert len(state.orders) == 2
    assert state.sequences == {("XSReversal-001", "20261015"): 1, ("XSReversal-001", "20261016"): 1}


def test_lookback_leaves_out_old_files(tmp_path):
    wall = WallClock(datetime(2026, 9, 1, tzinfo=timezone.utc))
    journal = OrderJournal(tmp_path, wall_clock=wall)
    journal.record_order(make_order(1, day="20260901"), 1)
    wall.now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    journal.record_order(make_order(1), 2)
    journal.close()
    assert len(journal.load(lookback_days=30).orders) == 1
    assert len(journal.load(lookback_days=None).orders) == 2


def test_load_of_an_empty_or_missing_directory(tmp_path):
    assert OrderJournal(tmp_path / "nope").load().orders == {}
    assert OrderJournal(tmp_path).load().records == 0


def test_a_write_that_fails_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    journal = OrderJournal(blocker / "journal")
    with pytest.raises(OSError):
        journal.record_order(make_order(), 1)


def test_m11_a_torn_last_line_does_not_swallow_the_next_record(tmp_path):
    """M11 (safety review): after a crash mid-write the file ends without a newline; the next
    record starts on a line of its own, so only the torn line is lost."""
    journal = OrderJournal(tmp_path, wall_clock=WallClock(datetime(2026, 10, 15, 14, tzinfo=timezone.utc)))
    a = make_order(1)
    journal.record_order(a, 1)
    journal.close()
    path = journal.path_for("20261015")
    with open(path, "a") as f:
        f.write('{"kind": "event", "time": "torn')                  # the crash
    again = OrderJournal(tmp_path, wall_clock=WallClock(datetime(2026, 10, 15, 14, 5, tzinfo=timezone.utc)))
    b = make_order(2)
    again.record_order(b, 2)
    again.close()
    state = OrderJournal(tmp_path).load(lookback_days=None)
    assert list(state.orders) == [a.client_order_id, b.client_order_id]
    assert state.bad_lines == 1
