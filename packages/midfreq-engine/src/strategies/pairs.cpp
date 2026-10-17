#include <algorithm>

#include "strategies.hpp"

namespace midfreq::engine {

Pairs::Pairs(StrategyContext ctx, const Params& p)
    : Strategy(ctx),
      lookback_(p.get_int("lookback", 30)),
      entry_z_(p.get("entry_z", 2.0)),
      exit_z_(p.get("exit_z", 0.5)),
      seen_(n_symbols_, 0) {
    ind::require_positive(lookback_, "lookback");
}

void Pairs::on_bar(SymbolId s, const Bar& bar) {
    if (check_risk_exits(s, bar)) return;
    if (is_halted()) return;

    // every symbol's closes are kept in Python; only the first two are ever read
    if (!seen_[s]) {
        seen_[s] = 1;
        if (order_.size() < 2) order_.push_back(s);
    }
    if (!order_.empty() && s == order_[0]) prices_a_.push_back(bar.close);
    if (order_.size() > 1 && s == order_[1]) prices_b_.push_back(bar.close);
    if (order_.size() < 2) return;

    const SymbolId sym_a = order_[0];
    if (s != order_[1]) return;

    const int64_t min_len = static_cast<int64_t>(std::min(prices_a_.size(), prices_b_.size()));
    if (min_len < lookback_) return;

    std::vector<double> spread;
    spread.reserve(static_cast<size_t>(lookback_));
    for (int64_t i = min_len - lookback_; i < min_len; ++i) {
        const size_t k = static_cast<size_t>(i);
        if (prices_b_[k] != 0) spread.push_back(py_log(prices_a_[k] / prices_b_[k]));
    }
    if (static_cast<int64_t>(spread.size()) < lookback_) return;

    const SumMode m = pf_.sum_mode();
    PySum total(m);
    for (double x : spread) total.add(x);
    const double n = static_cast<double>(spread.size());
    const double mean = total.value() / n;
    PySum sq(m);
    for (double x : spread) sq.add(py_pow(x - mean, 2.0));
    const double std_dev = py_pow(sq.value() / n, 0.5);
    if (std_dev == 0) return;

    const double z = (spread.back() - mean) / std_dev;
    const int64_t position = pf_.position_qty(sym_a);
    const double price_a = prices_a_.back();
    if (z < -entry_z_ && position == 0) {
        const int64_t qty = trade_qty(sym_a, price_a);
        if (qty > 0) enter(sym_a, qty, price_a, bar.ts);
    } else if (position > 0 && z > -exit_z_) {
        exit(sym_a, position, price_a, bar.ts, ExitReason::Signal);
    }
}

}  // namespace midfreq::engine
