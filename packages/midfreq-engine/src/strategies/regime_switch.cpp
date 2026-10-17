#include <algorithm>
#include <cmath>

#include "strategies.hpp"

namespace midfreq::engine {

RegimeSwitch::RegimeSwitch(StrategyContext ctx, const Params& p)
    : HistoryStrategy(ctx),
      lookback_(p.get_int("regime_lookback", 20)),
      er_threshold_(p.get("er_threshold", 0.30)),
      fast_(p.get_int("fast_period", 5)),
      slow_(p.get_int("slow_period", 20)),
      bb_period_(p.get_int("bb_period", 14)),
      num_std_(p.get("num_std", 2.5)),
      min_dwell_(p.get_int("min_dwell", 1)),
      keep_(p.get_int("history_keep", std::max({slow_, bb_period_, lookback_}) + 2)),
      regime_(n_symbols_, kNone),
      pending_(n_symbols_, kNone),
      pending_n_(n_symbols_, 0) {
    ind::require_positive(lookback_, "regime_lookback");
    ind::require_positive(fast_, "fast_period");
    ind::require_positive(slow_, "slow_period");
    ind::require_positive(bb_period_, "bb_period");
    ind::require_positive(keep_, "history_keep");
}

RegimeSwitch::Regime RegimeSwitch::classify(const std::deque<double>& prices) const {
    // _efficiency_ratio: window = prices[-(L + 1):]
    const SliceBounds w = py_slice(prices.size(), -(lookback_ + 1), static_cast<int64_t>(prices.size()));
    if (static_cast<int64_t>(w.hi - w.lo) < lookback_ + 1) return kNone;
    const double net_move = std::fabs(prices[w.hi - 1] - prices[w.lo]);
    PySum path = make_sum();
    for (size_t i = w.lo + 1; i < w.hi; ++i) path.add(std::fabs(prices[i] - prices[i - 1]));
    const double path_v = path.value();
    const double er = path_v > 0 ? net_move / path_v : 0.0;
    return er >= er_threshold_ ? kTrend : kRange;
}

void RegimeSwitch::on_bar(SymbolId s, const Bar& bar) {
    if (check_risk_exits(s, bar)) return;
    if (is_halted()) return;
    auto& prices = this->prices(s);
    prices.push_back(bar.close);
    if (static_cast<int64_t>(prices.size()) > keep_) prices.pop_front();

    Regime regime = classify(prices);
    if (regime == kNone) return;

    // hysteresis: a new regime must persist min_dwell candles before it is committed
    const Regime committed = static_cast<Regime>(regime_[s]);
    if (committed != kNone && regime != committed && min_dwell_ > 1) {
        const int64_t count = pending_[s] == regime ? pending_n_[s] + 1 : 1;
        pending_[s] = regime;
        pending_n_[s] = count;
        if (count < min_dwell_) regime = committed;
    } else {
        pending_[s] = kNone;
        pending_n_[s] = 0;
    }

    const Regime prev = static_cast<Regime>(regime_[s]);
    int64_t position = pf_.position_qty(s);
    if (prev != kNone && regime != prev && position > 0) {
        exit(s, position, bar.close, bar.ts, ExitReason::RegimeChange);
        position = 0;
    }
    regime_[s] = regime;

    if (regime == kTrend) {
        trend_leg(s, bar, prices, position);
    } else {
        range_leg(s, bar, prices, position);
    }
}

void RegimeSwitch::trend_leg(SymbolId s, const Bar& bar, const std::deque<double>& prices, int64_t position) {
    if (static_cast<int64_t>(prices.size()) <= slow_) return;
    const SumMode m = pf_.sum_mode();
    const double fast_ma = ind::tail_sum(prices, fast_, m) / static_cast<double>(fast_);
    const double slow_ma = ind::tail_sum(prices, slow_, m) / static_cast<double>(slow_);
    const double prev_fast = ind::slice_sum(prices, -(fast_ + 1), -1, m) / static_cast<double>(fast_);
    const double prev_slow = ind::slice_sum(prices, -(slow_ + 1), -1, m) / static_cast<double>(slow_);
    if (prev_fast <= prev_slow && fast_ma > slow_ma && position == 0) {
        const int64_t qty = trade_qty(s, bar.close);
        if (qty > 0) enter(s, qty, bar.close, bar.ts);
    } else if (prev_fast >= prev_slow && fast_ma < slow_ma && position > 0) {
        exit(s, position, bar.close, bar.ts, ExitReason::Signal);
    }
}

void RegimeSwitch::range_leg(SymbolId s, const Bar& bar, const std::deque<double>& prices, int64_t position) {
    if (static_cast<int64_t>(prices.size()) < bb_period_) return;
    const ind::Band b = ind::window_band(prices, bb_period_, pf_.sum_mode());
    const double upper = b.mean + num_std_ * b.std;
    const double lower = b.mean - num_std_ * b.std;
    if (bar.close <= lower && position == 0) {
        const int64_t qty = trade_qty(s, bar.close);
        if (qty > 0) enter(s, qty, bar.close, bar.ts);
    } else if (bar.close >= upper && position > 0) {
        exit(s, position, bar.close, bar.ts, ExitReason::Signal);
    }
}

}  // namespace midfreq::engine
