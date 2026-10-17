#include "midfreq/engine/metrics.hpp"

#include <cmath>
#include <limits>
#include <unordered_map>

#include "midfreq/engine/pymath.hpp"

namespace midfreq::engine {

std::vector<double> daily_returns(const std::vector<EquityPoint>& curve) {
    std::vector<double> returns;
    if (curve.size() < 2) return returns;
    // the last equity of each UTC day, days in order of first appearance (a dict keyed by date)
    std::vector<double> daily;
    std::unordered_map<int64_t, size_t> index;
    for (const auto& p : curve) {
        const int64_t day = utc_day(p.ts);
        auto it = index.find(day);
        if (it == index.end()) {
            index.emplace(day, daily.size());
            daily.push_back(p.equity);
        } else {
            daily[it->second] = p.equity;
        }
    }
    if (daily.size() < 2) return returns;
    returns.reserve(daily.size() - 1);
    for (size_t i = 1; i < daily.size(); ++i)
        if (daily[i - 1] != 0) returns.push_back((daily[i] - daily[i - 1]) / daily[i - 1]);
    return returns;
}

double sharpe_ratio(const std::vector<double>& returns) {
    if (returns.empty()) return 0.0;

    const double n = static_cast<double>(returns.size());
    const double mean = fsum(returns) / n;
    double std_dev = 0.0;
    if (returns.size() > 1) {
        // compensated two-pass variance: sum((x - m)^2) - (sum(x - m))^2 / n
        std::vector<double> dev(returns.size()), sq(returns.size());
        for (size_t i = 0; i < returns.size(); ++i) {
            dev[i] = returns[i] - mean;
            sq[i] = dev[i] * dev[i];
        }
        const double s1 = fsum(dev);
        const double ss = fsum(sq) - s1 * s1 / n;
        std_dev = std::sqrt(ss / (n - 1.0));
    }
    return std_dev > 0 ? mean / std_dev * py_pow(252.0, 0.5) : 0.0;
}

double max_drawdown_pct(const std::vector<EquityPoint>& curve) {
    if (curve.size() < 2) return 0.0;
    double peak = curve.front().equity, max_dd = 0.0;
    for (const auto& p : curve) {
        if (p.equity > peak) peak = p.equity;
        const double dd = py_div(peak - p.equity, peak) * 100;
        if (dd > max_dd) max_dd = dd;
    }
    return max_dd;
}

Metrics compute_metrics(const Portfolio& pf, const std::vector<EquityPoint>& detailed,
                        const std::vector<double>& last_price, const std::vector<char>& seen) {
    Metrics m;
    double position_value = 0.0;
    for (const auto& p : pf.positions())
        if (seen[p.symbol]) position_value += static_cast<double>(p.qty) * last_price[p.symbol];
    const double total_equity = pf.cash() + position_value;

    m.initial_cash = pf.initial_cash();
    m.final_equity = total_equity;
    m.return_pct = py_div(total_equity - pf.initial_cash(), pf.initial_cash()) * 100;
    m.total_trades = pf.trades().size();
    m.cash_balance = pf.cash();

    // win rate and profit factor over BUY->SELL pairs
    size_t wins = 0, losses = 0;
    double gross_profit = 0.0, gross_loss = 0.0;
    std::unordered_map<SymbolId, double> buy_price;
    for (const auto& t : pf.trades()) {
        if (t.is_buy) {
            buy_price[t.symbol] = t.price;
        } else {
            auto it = buy_price.find(t.symbol);
            if (it == buy_price.end()) continue;
            const double pnl = (t.price - it->second) * static_cast<double>(t.qty);
            if (pnl > 0) {
                ++wins;
                gross_profit += pnl;
            } else {
                ++losses;
                gross_loss += std::fabs(pnl);
            }
            buy_price.erase(it);
        }
    }
    const size_t completed = wins + losses;
    m.win_rate = completed > 0 ? static_cast<double>(wins) / static_cast<double>(completed) * 100 : 0.0;
    m.profit_factor = gross_loss > 0   ? gross_profit / gross_loss
                      : gross_profit > 0 ? std::numeric_limits<double>::infinity()
                                         : 0.0;

    const std::vector<EquityPoint>& source = detailed.empty() ? pf.trade_equity() : detailed;
    m.daily_returns = daily_returns(source);
    m.sharpe_ratio = sharpe_ratio(m.daily_returns);
    m.max_drawdown = max_drawdown_pct(source);
    return m;
}

double benchmark_return_pct(double initial_cash, const std::vector<SymbolId>& first_order,
                            const std::vector<double>& first_price, const std::vector<double>& last_price,
                            const std::vector<char>& seen) {
    if (first_order.empty()) return 0.0;
    const double capital_per_stock = initial_cash / static_cast<double>(first_order.size());
    double total = 0.0;
    for (SymbolId s : first_order) {
        if (seen[s] && first_price[s] > 0) {
            const double shares = capital_per_stock / first_price[s];
            total += shares * last_price[s];
        }
    }
    return py_div(total - initial_cash, initial_cash) * 100;
}

}  // namespace midfreq::engine
