#include "midfreq/engine/engine.hpp"

#include <algorithm>

namespace midfreq::engine {

BacktestResult run_backtest(const MarketData& data, const std::string& strategy, const Params& params,
                            const BacktestConfig& config, EventRange range, bool record, const Progress& progress) {
    const size_t n = data.n_symbols();
    Portfolio pf(n, config.initial_cash, config.commission_rate, config.slippage_bps, config.sum_mode, record);
    std::optional<RiskManager> risk;
    if (config.risk) risk.emplace(*config.risk, n);
    // built before the data is looked at, so a bad parameter fails even with no bars (as in Python)
    std::unique_ptr<Strategy> strat = make_strategy(strategy, params, StrategyContext{pf, risk ? &*risk : nullptr, data});

    BacktestResult res;
    res.total_events = range.size();
    if (range.size() == 0) return res;

    std::vector<double> last(n, 0.0), first(n, 0.0);
    std::vector<char> seen(n, 0);
    std::vector<SymbolId> first_order;
    std::vector<EquityPoint> equity;
    std::optional<Timestamp> last_equity_ts;
    const size_t step = std::max<size_t>(1, range.size() / 200);

    for (size_t i = range.begin; i < range.end; ++i) {
        const SymbolId s = data.symbol(i);
        const Bar bar = data.bar(i);
        last[s] = bar.close;
        if (!seen[s]) {
            seen[s] = 1;
            first[s] = bar.close;
            first_order.push_back(s);
        }

        strat->on_bar(s, bar);

        // at most one equity point per timestamp, taken after its first bar
        if (!last_equity_ts || bar.ts != *last_equity_ts) {
            equity.push_back(EquityPoint{bar.ts, pf.current_equity(last, seen)});
            last_equity_ts = bar.ts;
        }
        if (progress && ((i - range.begin) % step == 0 || i + 1 == range.end))
            progress(static_cast<double>(i - range.begin + 1) / static_cast<double>(range.size()));
    }

    res.ok = true;
    res.metrics = compute_metrics(pf, equity, last, seen);
    res.metrics.benchmark_return_pct = benchmark_return_pct(pf.initial_cash(), first_order, first, last, seen);
    res.metrics.alpha = res.metrics.return_pct - res.metrics.benchmark_return_pct;
    res.rejected_orders = pf.rejected();
    res.cash = pf.cash();
    res.last_ts = last_equity_ts;
    res.positions = pf.positions();
    if (record) {
        res.trades = pf.trades();
        res.equity = std::move(equity);
        res.trade_equity = pf.trade_equity();
        res.last_price = std::move(last);
        res.seen = std::move(seen);
    }
    return res;
}

}  // namespace midfreq::engine
