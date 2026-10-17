// Performance metrics after Portfolio.calculate_metrics and Backtester._calculate_benchmark.
//
// Everything is computed with the same operations in the same order as the Python code, except
// the Sharpe ratio's mean and standard deviation: Python's statistics module computes those in
// exact rational arithmetic; here they are an exactly rounded sum and a compensated two-pass
// variance, which agree to within an ulp or two. So the daily returns are returned as well, and
// the Python adapter finishes the Sharpe ratio with the statistics module itself.
#pragma once

#include <vector>

#include "midfreq/engine/types.hpp"
#include "midfreq/engine/portfolio.hpp"

namespace midfreq::engine {

struct Metrics {
    double initial_cash = 0.0;
    double final_equity = 0.0;
    double return_pct = 0.0;
    size_t total_trades = 0;
    double cash_balance = 0.0;
    double sharpe_ratio = 0.0;
    double max_drawdown = 0.0;
    double win_rate = 0.0;
    double profit_factor = 0.0;
    double benchmark_return_pct = 0.0;
    double alpha = 0.0;
    // the day-on-day returns the Sharpe ratio is taken over, so Python can redo that last step
    // in exact arithmetic (statistics.mean/stdev) and match the reference bit for bit
    std::vector<double> daily_returns;
};

// Returns from the last equity of each UTC day to the next (empty under two points or days).
std::vector<double> daily_returns(const std::vector<EquityPoint>& curve);
// The annualised (252 days) Sharpe ratio of those returns.
double sharpe_ratio(const std::vector<double>& returns);
double max_drawdown_pct(const std::vector<EquityPoint>& curve);

Metrics compute_metrics(const Portfolio& pf, const std::vector<EquityPoint>& detailed,
                        const std::vector<double>& last_price, const std::vector<char>& seen);

// Equal capital in each symbol at its first price, held to its last: the return in percent.
double benchmark_return_pct(double initial_cash, const std::vector<SymbolId>& first_order,
                            const std::vector<double>& first_price, const std::vector<double>& last_price,
                            const std::vector<char>& seen);

}  // namespace midfreq::engine
