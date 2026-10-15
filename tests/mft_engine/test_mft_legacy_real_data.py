"""
The legacy-equivalence check repeated on the REAL data/ cache. Slow, so opt-in:

    MFT_SLOW=1 python -m pytest tests/mft_engine/test_mft_legacy_real_data.py -q -s
    MFT_SLOW=1 MFT_DATA_DIR=/path/to/data python -m pytest ...   # a cache elsewhere

It never runs in the default suite (or the VM's preflight). The cache is only read: symbols are
checked for an existing file first, so neither DataStorage nor the legacy reader creates anything.
"""
import contextlib
import io
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from core.backtester import Backtester
from core.models import Timeframe
from core.portfolio import Portfolio
from core.storage import DataStorage
from core.strategy import STRATEGY_REGISTRY
from mft.legacy import run_legacy_backtest

REPO = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("MFT_DATA_DIR", REPO / "data"))

pytestmark = [
    pytest.mark.skipif(os.environ.get("MFT_SLOW") != "1", reason="slow; set MFT_SLOW=1 to run"),
    pytest.mark.skipif(not DATA.is_dir(), reason=f"no data cache at {DATA}"),
]


def available(symbols, tf="1h"):
    return [s for s in symbols if (DATA / s.replace(".", "_") / f"{tf}.parquet").is_file()]


def snapshot():
    return {(str(p), p.stat().st_mtime_ns) for p in DATA.iterdir()}


def compare(cls, symbols, cash, rate, slip, start, end, end_inclusive=True, **params):
    t0 = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        pf = Portfolio(initial_cash=cash, commission_rate=rate)
        legacy = Backtester(DataStorage(str(DATA)), pf, slippage_bps=slip).run(
            cls, symbols, Timeframe.HOUR_1, start_date=start, end_date=end, end_inclusive=end_inclusive,
            **params)
        t1 = time.time()
        new = run_legacy_backtest(cls, symbols, "1h", DATA, cash, rate, slip, start_date=start,
                                  end_date=end, end_inclusive=end_inclusive, **params)
    t2 = time.time()
    assert new.portfolio.trade_history == pf.trade_history
    assert new.metrics["final_equity"] == legacy["final_equity"]
    assert {k: v for k, v in new.metrics.items() if k != "open_positions"} == \
        {k: v for k, v in legacy.items() if k != "open_positions"}
    assert new.result.final_balances[new.result.currencies[0]] == pf.cash
    return legacy, t1 - t0, t2 - t1


def test_sp100_forward_test_configuration():
    """The S&P 100 reversal configuration the forward test trades, on the training window it was
    chosen on (end-exclusive, as walk-forward train segments are). Both engines must also reproduce
    the in-sample figures stored in config/sp100_forward_test.json, digit for digit."""
    before = snapshot()
    cfg = json.loads((REPO / "config" / "sp100_forward_test.json").read_text())
    symbols = available(cfg["symbols"])
    assert len(symbols) > 50
    start = datetime.fromisoformat(cfg["train_window"]["start"])
    end = datetime.fromisoformat(cfg["train_window"]["end"])
    legacy, t_old, t_new = compare(STRATEGY_REGISTRY[cfg["strategy"]]["class"], symbols, 100_000.0,
                                   cfg["commission_per_side"], cfg["slippage_bps"], start, end,
                                   end_inclusive=False, **cfg["params"])
    print(f"\nS&P 100 {cfg['params']}: {len(symbols)} symbols, {legacy['total_events']} bars, "
          f"{legacy['total_trades']} trades, return {legacy['return_pct']!r}% "
          f"(stored {cfg['in_sample']['return_pct']!r}%); Backtester {t_old:.1f}s, engine {t_new:.1f}s")
    assert legacy["return_pct"] == cfg["in_sample"]["return_pct"]
    assert legacy["sharpe_ratio"] == cfg["in_sample"]["objective"]
    assert legacy["total_trades"] == cfg["in_sample"]["trades"]
    assert snapshot() == before


def test_sp100_reversal_full_history():
    """Registry-default reversal over the whole S&P 100 cache."""
    cfg = json.loads((REPO / "config" / "sp100_forward_test.json").read_text())
    symbols = available(cfg["symbols"])
    params = {k: v["default"] for k, v in STRATEGY_REGISTRY["Cross-Sectional Reversal"]["params"].items()}
    legacy, t_old, t_new = compare(STRATEGY_REGISTRY["Cross-Sectional Reversal"]["class"], symbols,
                                   100_000.0, 0.00005, 5.0, None, None, **params)
    print(f"\nS&P 100 full history: {legacy['total_events']} bars, {legacy['total_trades']} trades; "
          f"Backtester {t_old:.1f}s, engine {t_new:.1f}s")


@pytest.mark.parametrize("name", sorted(STRATEGY_REGISTRY))
@pytest.mark.parametrize("universe, rate", [
    (["HK.00700", "HK.00005", "HK.09988", "HK.00941", "HK.01299"], Portfolio.HK_FEE_RATE),
    (["US.AAPL", "US.MSFT", "US.JPM", "US.XOM", "US.NVDA"], 0.00005),
], ids=["hk", "us"])
def test_every_registered_strategy_on_real_bars(name, universe, rate):
    symbols = available(universe)
    params = {k: v["default"] for k, v in STRATEGY_REGISTRY[name]["params"].items()}
    compare(STRATEGY_REGISTRY[name]["class"], symbols, 100_000.0, rate, 5.0,
            datetime(2025, 1, 1), datetime(2025, 12, 31), **params)
