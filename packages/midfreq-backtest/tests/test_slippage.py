"""Slippage models: always against the trader, of the expected size, reproducible when random."""
import math

import pytest

from midfreq.backtest.slippage import (
    FixedBpsSlippage, FixedTickSlippage, NoSlippage, RandomSlippage, SpreadSlippage,
    SquareRootImpactSlippage, VolatilitySlippage,
)
from midfreq.core.model import OrderSide

from mft_market import bar, instrument, quote, state

BUY, SELL = OrderSide.BUY, OrderSide.SELL

ALL = [NoSlippage(), FixedBpsSlippage(), FixedTickSlippage(2), SpreadSlippage(), VolatilitySlippage(0.2),
       SquareRootImpactSlippage(), RandomSlippage(seed=3)]


@pytest.mark.parametrize("model", ALL, ids=lambda m: type(m).__name__)
def test_never_in_the_traders_favour(model):
    s = state(bar(100, 101, 99, 100, v=5000, i=0), bar(100, 102, 99, 101, v=5000, i=1),
              bar(101, 101, 98, 99, v=5000, i=2))
    for _ in range(20):
        assert model.adjust(100.0, BUY, 500, s, instrument()) >= 100.0
        assert model.adjust(100.0, SELL, 500, s, instrument()) <= 100.0


def test_fixed_bps_default_and_exact_arithmetic():
    m = FixedBpsSlippage()
    assert m.bps == 5.0
    assert m.adjust(312.47, BUY, 1) == 312.47 * (1.0 + 5.0 / 10000.0)        # midfreq.backtest.backtester's maths
    assert m.adjust(312.47, SELL, 1) == 312.47 * (1.0 - 5.0 / 10000.0)
    with pytest.raises(ValueError):
        FixedBpsSlippage(-1)


def test_fixed_ticks_use_the_instruments_increment():
    m = FixedTickSlippage(3)
    assert m.adjust(10.0, BUY, 1, None, instrument(tick=0.02)) == pytest.approx(10.06)
    assert m.adjust(10.0, SELL, 1, None, instrument(tick=0.02)) == pytest.approx(9.94)


def test_spread_slippage_quoted_or_assumed():
    m = SpreadSlippage()
    assert m.assumed_half_spread_bps == 3.33
    assert m.adjust(100.0, BUY, 1, state(quote(99.8, 100.2))) == pytest.approx(100.2)
    assert m.adjust(100.0, SELL, 1, state(bar(100, 100, 100, 100))) == pytest.approx(100.0 * (1 - 3.33e-4))


def test_volatility_slippage_is_a_share_of_the_range():
    s = state(bar(100, 104, 98, 101))
    assert VolatilitySlippage(0.25).adjust(101.0, BUY, 1, s) == pytest.approx(101.0 + 1.5)
    assert VolatilitySlippage(0.25).adjust(101.0, BUY, 1, state()) == 101.0


def test_square_root_impact_grows_with_the_square_root_of_size():
    s = state(*[bar(100, 101, 99, c, v=10_000, i=k) for k, c in enumerate([100, 101, 99.5, 100.5, 100])])
    m = SquareRootImpactSlippage(k=1.0, assumed_half_spread_bps=0.0)
    small = m.adjust(100.0, BUY, 100, s) - 100.0
    large = m.adjust(100.0, BUY, 400, s) - 100.0
    assert large == pytest.approx(2 * small)                       # 4x the size, 2x the cost
    rets = [math.log(b / a) for a, b in zip([100, 101, 99.5, 100.5], [101, 99.5, 100.5, 100])]
    mean = sum(rets) / 4
    sigma = math.sqrt(sum((r - mean) ** 2 for r in rets) / 3)
    assert small == pytest.approx(sigma * 100.0 * math.sqrt(100 / 10_000))
    with_spread = SquareRootImpactSlippage(k=1.0).adjust(100.0, SELL, 100, s)
    assert with_spread == pytest.approx(100.0 - small - 100.0 * 3.33e-4)
    one_bar = state(bar(100, 102, 98, 100, v=10_000))               # range / close until returns exist
    assert m.adjust(100.0, BUY, 100, one_bar) == pytest.approx(100.0 + 0.04 * 100.0 * 0.1)


def test_random_slippage_is_reproducible_with_a_seed():
    a = [RandomSlippage(5, 2, seed=11).adjust(100.0, BUY, 1) for _ in range(1)]
    first = RandomSlippage(5, 2, seed=11)
    second = RandomSlippage(5, 2, seed=11)
    xs = [first.adjust(100.0, BUY, 1) for _ in range(50)]
    assert xs == [second.adjust(100.0, BUY, 1) for _ in range(50)] and xs[0] == a[0]
    assert xs != [RandomSlippage(5, 2, seed=12).adjust(100.0, BUY, 1) for _ in range(50)]
    mean_bps = (sum(xs) / len(xs) - 100.0) / 100.0 * 10000
    assert 3.5 < mean_bps < 6.5
