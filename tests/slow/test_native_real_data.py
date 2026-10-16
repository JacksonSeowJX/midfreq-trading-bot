"""
The C++ engine against the Python backtester on the REAL data/ cache. Slow, so opt-in:

    MIDFREQ_SLOW=1 uv run pytest tests/slow/test_native_real_data.py -q -s
    MIDFREQ_SLOW=1 MIDFREQ_DATA_DIR=/path/to/data uv run pytest ...   # a cache elsewhere

Every ported strategy over the S&P 100 (three years of hourly bars for the basket strategies, a
year for the per-symbol ones) and the grid search that chose the live parameters must come out
identical on both engines. The cache is only read.
"""
import contextlib
import io
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("midfreq.engine._core")

from midfreq.backtest.backtester import Backtester  # noqa: E402
from midfreq.backtest.native import NativeBacktester  # noqa: E402
from midfreq.backtest.optimizer import grid_search  # noqa: E402
from midfreq.backtest.parity import port_reason  # noqa: E402
from midfreq.core.candle import Timeframe  # noqa: E402
from midfreq.core.paths import data_dir, repo_root  # noqa: E402
from midfreq.core.portfolio import Portfolio  # noqa: E402
from midfreq.core.risk_manager import EqualDollarSizer, RiskManager  # noqa: E402
from midfreq.data.storage import DataStorage  # noqa: E402
from midfreq.strategies import candle_strategies as cs  # noqa: E402

REPO = repo_root()
DATA = Path(os.environ.get("MIDFREQ_DATA_DIR") or os.environ.get("MFT_DATA_DIR") or data_dir())
SLOW = "1" in (os.environ.get("MIDFREQ_SLOW"), os.environ.get("MFT_SLOW"))
H1 = Timeframe.HOUR_1

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not SLOW, reason="slow; set MIDFREQ_SLOW=1 to run"),
    pytest.mark.skipif(not DATA.is_dir(), reason=f"no data cache at {DATA}"),
]


def sp100():
    universe = json.loads((REPO / "config" / "symbols.json").read_text())["universes"]["sp100"]["symbols"]
    return [s for s in universe if (DATA / s.replace(".", "_") / "1h.parquet").is_file()]


def require_current(cls):
    reason = port_reason(cls)
    if reason:
        pytest.skip(f"stale C++ port, runs on Python: {reason}")


def compare(cls, symbols, start, end, risk=None, rate=0.00005, slip=5.0, **params):
    require_current(cls)
    out, times = [], []
    for native in (False, True):
        pf = Portfolio(initial_cash=100_000.0, commission_rate=rate)
        bt = (NativeBacktester(DataStorage(str(DATA)), pf, risk, slip, fallback=False) if native
              else Backtester(DataStorage(str(DATA)), pf, risk, slip))
        t = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            m = bt.run(cls, symbols, H1, start_date=start, end_date=end, **params)
        times.append(time.perf_counter() - t)
        out.append((m, pf))
    (mp, pp), (mn, pn) = out
    assert pn.trade_history == pp.trade_history
    assert pn.equity_curve_detailed == pp.equity_curve_detailed
    assert list(pn.positions.items()) == list(pp.positions.items())
    assert mn == mp
    print(f"\n{cls.__name__} {params}: {len(pp.trade_history)} trades, {mp.get('total_events')} events; "
          f"Python {times[0]:.1f}s, C++ {times[1]:.2f}s ({times[0] / times[1]:.0f}x)")


THREE_YEARS = (datetime(2023, 8, 22), datetime(2026, 8, 21))
ONE_YEAR = (datetime(2025, 8, 21), datetime(2026, 8, 21))


@pytest.mark.parametrize("cls,params", [
    (cs.CrossSectionalReversal, {"lookback": 30, "top_n": 3, "rebalance_every": 19}),
    (cs.CrossSectionalReversal, {"lookback": 5, "top_n": 2, "rebalance_every": 1}),
    (cs.CrossSectionalMomentum, {"lookback": 20, "top_n": 4, "rebalance_every": 7}),
])
def test_basket_strategies_over_three_years(cls, params):
    compare(cls, sp100(), *THREE_YEARS, **params)


def test_the_live_basket_with_its_risk_manager():
    risk = RiskManager(stop_loss_pct=0.05, max_drawdown_pct=0.15, position_sizer=EqualDollarSizer(3))
    compare(cs.CrossSectionalReversal, sp100(), *THREE_YEARS, risk=risk,
            lookback=30, top_n=3, rebalance_every=19)


@pytest.mark.parametrize("cls,params", [
    (cs.MovingAverageCrossover, {"fast_period": 5, "slow_period": 20}),
    (cs.RSIStrategy, {}),
    (cs.MACDStrategy, {}),
    (cs.BollingerBandsStrategy, {}),
    (cs.ZScoreMeanReversion, {}),
    (cs.EnsembleStrategy, {}),
    (cs.RegimeSwitchStrategy, {}),
    (cs.BuyAndHoldRiskOverlay, {"stop_loss_pct": 0.06, "trailing_stop_pct": 0.09}),
])
def test_per_symbol_strategies_over_a_year(cls, params):
    compare(cls, sp100()[:30], *ONE_YEAR, rate=0.0016, slip=0.0, **params)


def test_pairs_over_a_year():
    compare(cs.PairsTradingStrategy, ["US.KO", "US.PEP"], *ONE_YEAR)


def test_the_grid_search_that_chose_the_live_parameters():
    require_current(cs.CrossSectionalReversal)
    fwd = json.loads((REPO / "config" / "sp100_forward_test.json").read_text())
    symbols = [s for s in sp100() if s not in set(fwd["excluded_stale_symbols"])]
    kw = dict(symbols=symbols, timeframe=H1, start_date=datetime.fromisoformat(fwd["train_window"]["start"]),
              end_date=datetime.fromisoformat(fwd["train_window"]["end"]), storage=DataStorage(str(DATA)),
              objective=fwd["objective"], slippage_bps=fwd["slippage_bps"],
              commission_rate=fwd["commission_per_side"])
    with contextlib.redirect_stdout(io.StringIO()):
        nat = grid_search("Cross-Sectional Reversal", engine="native", **kw)
        py = grid_search("Cross-Sectional Reversal", engine="python", **kw)
    pd.testing.assert_frame_equal(nat, py, check_exact=True)
    best = {k: int(nat.iloc[0][k]) for k in ("lookback", "top_n", "rebalance_every")}
    assert best == fwd["params"]
