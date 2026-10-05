"""MoomooPaperGateway.place_and_confirm against a scripted fake broker.

The replay harness swaps the whole gateway for a simulated broker, so it
never runs this code. These tests drive the real method through a fake
trade context and a fake clock, covering the cases the 2026-10-05 order
path test exposed: a cancel takes effect a moment AFTER it is requested.
"""
import time

import pandas as pd
import pytest
from moomoo import RET_OK, RET_ERROR

from core.order_gateway import MoomooPaperGateway


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakeCtx:
    """Scripted broker. `before_cancel` is the status sequence the order
    shows until a cancel is requested, `after_cancel` the sequence after it;
    each entry is (status, dealt_qty, dealt_avg_price), or None for a failed
    query. The last entry of a sequence repeats."""

    def __init__(self, before_cancel, after_cancel=(), cancel_ok=True):
        self.before, self.after = list(before_cancel), list(after_cancel)
        self.cancel_ok = cancel_ok
        self.cancels = 0
        self._i = 0

    def place_order(self, **kw):
        return RET_OK, pd.DataFrame({'order_id': ['X1']})

    def modify_order(self, op, oid, qty, price, trd_env=None):
        self.cancels += 1
        if not self.cancel_ok:
            return RET_ERROR, 'order already filled'
        if self.after:
            self._i = 0
            self.before, self.after = self.after, []
        return RET_OK, pd.DataFrame({'order_id': [oid]})

    def order_list_query(self, order_id=None, trd_env=None, refresh_cache=True):
        e = self.before[min(self._i, len(self.before) - 1)]
        self._i += 1
        if e is None:
            return RET_ERROR, 'query failed'
        status, dq, dp = e
        return RET_OK, pd.DataFrame([{'order_status': status, 'dealt_qty': dq,
                                      'dealt_avg_price': dp}])


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(time, 'time', c.time)
    monkeypatch.setattr(time, 'sleep', c.sleep)
    return c


def gateway(ctx):
    gw = MoomooPaperGateway()
    gw._trd_ctx = ctx
    return gw


def test_immediate_fill_is_booked_without_a_cancel(clock):
    ctx = FakeCtx([('SUBMITTED', 0, 0), ('FILLED_ALL', 100, 417.8)])
    r = gateway(ctx).place_and_confirm('HK.00700', True, 100, 418.0)
    assert (r['ok'], r['dealt_qty'], r['dealt_price'], r['message']) == (True, 100, 417.8, 'FILLED_ALL')
    assert r['limit'] == 422.18 and ctx.cancels == 0


def test_cancel_is_waited_for_until_the_broker_confirms_it(clock):
    # 2026-10-05: the broker still reported SUBMITTED one second after the
    # cancel request and CANCELLED_ALL a moment later.
    ctx = FakeCtx([('SUBMITTED', 0, 0)],
                  after_cancel=[('SUBMITTED', 0, 0), ('SUBMITTED', 0, 0), ('CANCELLED_ALL', 0, 0)])
    r = gateway(ctx).place_and_confirm('HK.00700', True, 100, 209.0, wait_s=8)
    assert ctx.cancels == 1
    assert (r['ok'], r['dealt_qty'], r['dealt_price'], r['message']) == (False, 0, None, 'CANCELLED_ALL')


def test_shares_filled_while_the_cancel_lands_are_booked(clock):
    # 40 filled before the cancel was sent and 20 more before it took effect:
    # the final status says 60, and 60 must be booked, not 40.
    ctx = FakeCtx([('FILLED_PART', 40, 10.0)],
                  after_cancel=[('CANCELLING_PART', 40, 10.0), ('CANCELLED_PART', 60, 10.01)])
    r = gateway(ctx).place_and_confirm('US.T', False, 100, 10.0)
    assert (r['ok'], r['dealt_qty'], r['dealt_price'], r['message']) == (True, 60, 10.01, 'CANCELLED_PART')


def test_order_is_cancelled_even_when_every_status_query_fails(clock):
    ctx = FakeCtx([None], after_cancel=[None, ('CANCELLED_ALL', 0, 0)])
    r = gateway(ctx).place_and_confirm('US.AAPL', True, 10, 250.0)
    assert ctx.cancels == 1
    assert (r['ok'], r['dealt_qty'], r['message']) == (False, 0, 'CANCELLED_ALL')


def test_refused_cancel_on_an_order_that_filled_books_the_fill(clock):
    ctx = FakeCtx([('SUBMITTED', 0, 0)] * 31 + [('FILLED_ALL', 10, 250.5)], cancel_ok=False)
    r = gateway(ctx).place_and_confirm('US.AAPL', True, 10, 250.0)
    assert ctx.cancels == 1
    assert (r['ok'], r['dealt_qty'], r['dealt_price']) == (True, 10, 250.5)


def test_an_order_the_broker_already_rejected_is_not_cancelled(clock):
    ctx = FakeCtx([('SUBMIT_FAILED', 0, 0)])
    r = gateway(ctx).place_and_confirm('US.AAPL', True, 10, 250.0)
    assert ctx.cancels == 0 and r['dealt_qty'] == 0 and r['message'] == 'SUBMIT_FAILED'


def test_us_paper_fill_that_shows_late_is_still_booked(clock):
    # 2026-10-05, US paper: the order read SUBMITTED for the whole 30s wait
    # although the broker filled it at 18s; the cancel was refused and the
    # fill showed up afterwards. Here it shows up 3 queries after the cancel,
    # which a single re-query would have missed, booking 0 shares the broker
    # had bought.
    ctx = FakeCtx([('SUBMITTED', 0, 0)] * 31 + [('SUBMITTED', 0, 0)] * 3 + [('FILLED_ALL', 179, 183.04)],
                  cancel_ok=False)
    r = gateway(ctx).place_and_confirm('US.QCOM', True, 179, 183.0)
    assert ctx.cancels == 1
    assert (r['ok'], r['dealt_qty'], r['dealt_price'], r['message']) == (True, 179, 183.04, 'FILLED_ALL')
