# midfreq-engine

The backtester's hot path in C++17, behind a pybind11 module: the merged event stream, the event
loop, portfolio accounting, the risk rules, the metrics and eleven of the thirteen candle
strategies. It reproduces `midfreq.backtest.backtester.Backtester` exactly: the same trades, the
same equity curve and the same metrics, bit for bit, only faster: about 50 times for a 3-year
S&P 100 backtest, 15 to 20 times for a small one (where loading the data dominates), and several
hundred to over a thousand times for a grid search or a walk-forward.

```
Python   midfreq.backtest.optimizer      grid_search, walk_forward      (unchanged API, engine=)
           │
         midfreq.backtest.native         NativeBacktester  (drop-in for Backtester)
           │                             BatchRunner       (many parameter sets, one dataset)
           │                             eligibility checks, fallback to Python, results → Python shapes
───────────┼───────────────────────────────────────────────────────── pybind11 (midfreq.engine._core)
C++        │  MarketData       every symbol's bars merged once (stable sort), columnar, read-only
           │  run_backtest     the event loop: latest prices → strategy → one equity point per timestamp
           │  run_batch        a thread pool over parameter sets, GIL released, one MarketData shared
           │  Portfolio        cash, positions in dict order, trade log, slippage, trade stats
           │  RiskManager      stops, trailing stop, take-profit, drawdown halt, four sizers
           │  Strategy         BaseStrategy's helpers; the 11 ported strategies; a name registry
           │  Metrics          return, drawdown, win rate, profit factor, benchmark, daily returns
           └─ pymath.hpp       CPython's arithmetic: sum(), float ** float, int(), max(), fsum
```

## Why the results are identical, not just close

A crossover strategy compares two moving averages; if one of them differs in the last bit, a tie
can break the other way and the whole trade list diverges. So the engine does each calculation
with the same operations, in the same order, rounded the way CPython rounds them:

- **`sum()`** over floats uses Neumaier's compensated summation since Python 3.12 (left to right
  before). `PySum` does whichever the interpreter does (`midfreq.engine.sum_mode()`).
- **`x ** 2`, `v ** 0.5`** go to the C library's `pow()`, not `x*x` or `sqrt`. It is called through
  a volatile pointer, so the compiler cannot substitute either.
- **No fused multiply-add**: built with `-ffp-contract=off` and no fast-math, because CPython
  rounds every operation separately.
- **Python's semantics**, not C++'s: slicing with negative and out-of-range indices, `int()`
  truncation and its exceptions, `max()`/`min()` keeping the first argument on ties, dict order for
  positions (a closed position is removed, a reopened one goes to the end), a rejected sell leaving
  an empty position entry behind, the stable sort that ranks the cross-section.
- **The Sharpe ratio**: Python's `statistics.mean`/`stdev` work in exact rational arithmetic. The
  engine returns each run's daily returns, and the adapter finishes the Sharpe ratio with the
  `statistics` module itself. (The engine's own Sharpe ratio agrees to within an ulp or two.)

The equivalence suite (`packages/midfreq-backtest/tests/test_native.py`) runs every ported
strategy against the Python backtester with each sizer, with and without slippage, over date
windows, and checks trades, curves, positions and metrics for equality. `tests/slow/test_native_real_data.py`
repeats this on the real S&P 100 cache, including the grid search that chose the live parameters.

## What runs natively, and what does not

`midfreq.backtest.native` runs a case in C++ only when it is certain to match:

| Ported (exact class only) | Stays on Python |
|---|---|
| SMA Crossover, RSI, MACD, Bollinger Bands, Z-Score, Pairs, Ensemble, Regime Switch, Cross-Sectional Reversal and Momentum, Buy & Hold + Risk Overlay | HMM Regime Switch (hmmlearn), ML Direction Classifier (scikit-learn), any subclass of a ported class |

It also needs a fresh plain `Portfolio`, a plain `RiskManager` with one of the four sizers, and
UTC-indexed bars with no missing closes. Under `engine="auto"` (the default) anything else runs on
the Python backtester, and `NativeBacktester.fallback_reason` says why; `engine="native"` raises
`NativeUnavailable` instead. `MIDFREQ_ENGINE=python` turns the C++ engine off everywhere.

## Speed

`bench/bench_engine.py` on the real S&P 100 cache (100 symbols, hourly bars, 22 Aug 2023 to
21 Aug 2026), US fees plus 5 bps, on the development Mac (Apple silicon, 10 cores). Every C++ output is
identical to Python's.

| Scenario | Python | C++, 1 thread | C++, 10 cores | Faster by |
|---|---:|---:|---:|---:|
| Backtest: cross-sectional reversal (30, 3, 19), 3 years, 522,182 bars | 9.2 s | 0.18 s | 0.19 s | ~50x |
| Backtest: SMA crossover 5/20 on each of the 100 symbols, 3 years | 9.7 s | 0.19 s | 0.21 s | ~50x |
| Grid search: the 96 reversal combinations over the forward test's 84-day training window | 80 s | 0.24 s | 0.22 s | ~350x |
| Walk-forward: configuration A, 9 windows x 96 combinations, 3 years | 689 s | 1.0 s | 0.44 s | ~700x / ~1,600x |

Times include loading the data. The grid search picks the live parameters (30, 3, 19) on both
engines, and the walk-forward reproduces the alpha study's in-sample and out-of-sample results.

A large single backtest is about 50 times faster: the loop no longer goes through pandas'
`iterrows`, pydantic candles and Python dicts. A small one (30 symbols, one year, 53k bars) is 13
to 20 times faster, because reading the files and handing the trades back to Python are then
most of the time (`tests/slow/test_native_real_data.py -s` prints these per strategy). Optimisation gains far more, because the data is loaded and
merged once instead of once per parameter set, and the runs share it across cores with the GIL
released.

## The trade-offs

- **A compiler in the build.** `uv sync` compiles the extension (scikit-build-core, CMake,
  pybind11), and wheels are per platform. The VM's release bundle leaves the engine out: the VM
  trades and never backtests, and anything that does fall back to Python there.
- **Two implementations to keep in step.** A change to a strategy in Python must be made in C++
  too. The equivalence suite fails the build if they drift, and a strategy without a port simply
  runs on Python.
- **New strategies are not fast for free.** Writing a strategy in Python still works everywhere;
  it is fast only once it is ported. The C++ interface (`Strategy`, `HistoryStrategy`) mirrors
  `BaseStrategy`, so a port reads line for line like the Python.
- **Backtests only.** Live trading stays on the Python engine that runs the forward test.

## Building and testing

```bash
uv sync                                   # builds the extension (rebuilt when the C++ changes)
uv run pytest packages/midfreq-engine packages/midfreq-backtest/tests/test_native.py
MIDFREQ_SLOW=1 uv run pytest tests/slow/test_native_real_data.py -s     # the real cache

# the C++ library and its unit tests alone, no Python
cmake -S packages/midfreq-engine -B build/engine -DMIDFREQ_ENGINE_TESTS=ON -DMIDFREQ_ENGINE_PYTHON=OFF
cmake --build build/engine -j && ctest --test-dir build/engine --output-on-failure

uv run python packages/midfreq-engine/bench/bench_engine.py            # the speed table above
```

On some macOS Command Line Tools installs a stub `include/c++/v1` next to clang hides the SDK's
C++ headers ("'vector' file not found"); `CMakeLists.txt` detects this and uses the SDK's copy.
