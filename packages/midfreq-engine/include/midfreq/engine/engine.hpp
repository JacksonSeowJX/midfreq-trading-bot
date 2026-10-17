// The backtest loop (midfreq.backtest.backtester.Backtester.run) and the parallel batch runner
// the optimiser uses.
//
// One run: replay the merged bars in time order into a strategy; before each bar its symbol's
// latest price is updated, after it an equity point is recorded at most once per timestamp (on the
// first bar of that timestamp, as the Python backtester does). At the end: metrics, and the
// buy-and-hold benchmark (equal capital in every symbol at its first price).
#pragma once

#include <functional>
#include <optional>
#include <string>
#include <vector>

#include "midfreq/engine/market_data.hpp"
#include "midfreq/engine/metrics.hpp"
#include "midfreq/engine/portfolio.hpp"
#include "midfreq/engine/risk.hpp"
#include "midfreq/engine/strategy.hpp"

namespace midfreq::engine {

struct BacktestConfig {
    double initial_cash = 100000.0;
    double commission_rate = 0.0016;
    double slippage_bps = 0.0;
    SumMode sum_mode = SumMode::Neumaier;
    std::optional<RiskConfig> risk;  // none: the strategy runs without a risk manager
};

struct BacktestResult {
    bool ok = false;  // false: no bars in the range (the Python backtester returns {})
    Metrics metrics;
    size_t total_events = 0;
    size_t rejected_orders = 0;
    std::vector<Trade> trades;
    std::vector<EquityPoint> equity;        // per timestamp (equity_curve_detailed)
    std::vector<EquityPoint> trade_equity;  // per trade (equity_curve)
    std::vector<Position> positions;        // open at the end, in dict order
    double cash = 0.0;
    std::vector<double> last_price;  // per symbol (latest_prices), valid where seen
    std::vector<char> seen;
    std::optional<Timestamp> last_ts;  // the last bar replayed: the run's data cut-off
};

using Progress = std::function<void(double)>;

BacktestResult run_backtest(const MarketData& data, const std::string& strategy, const Params& params,
                            const BacktestConfig& config, EventRange range, bool record = true,
                            const Progress& progress = nullptr);

struct BatchItem {
    BacktestResult result;
    std::string error;       // empty when the run succeeded
    std::string error_type;  // "ValueError", "OverflowError", "ZeroDivisionError", "RuntimeError"
};

// Runs every parameter set over the same data and range on `threads` threads (0: all cores).
// Results come back in the order of `params`; a run that throws is reported, not propagated.
std::vector<BatchItem> run_batch(const MarketData& data, const std::string& strategy,
                                 const std::vector<Params>& params, const BacktestConfig& config, EventRange range,
                                 unsigned threads = 0, bool record = false);

}  // namespace midfreq::engine
