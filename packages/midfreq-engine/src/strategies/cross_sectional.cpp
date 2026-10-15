#include <algorithm>
#include <unordered_set>

#include "strategies.hpp"

namespace midfreq::engine {

CrossSectional::CrossSectional(StrategyContext ctx, const Params& p, bool momentum)
    : HistoryStrategy(ctx),
      momentum_(momentum),
      lookback_(p.get_int("lookback", 10)),
      top_n_(p.get_int("top_n", 2)),
      rebalance_every_(std::max<int64_t>(1, p.get_int("rebalance_every", 1))),
      pending_slot_(n_symbols_, -1),
      next_rebalance_at_(rebalance_every_) {
    ind::require_positive(lookback_, "lookback");
    ind::require_positive(top_n_, "top_n");
}

void CrossSectional::clear_pending() {
    for (const auto& e : pending_) pending_slot_[e.first] = -1;
    pending_.clear();
}

double CrossSectional::mark_price(SymbolId s, const Position& pos) const {
    const int32_t i = pending_slot_[s];
    if (i >= 0) return pending_[static_cast<size_t>(i)].second.close;
    return HistoryStrategy::mark_price(s, pos);
}

void CrossSectional::on_bar(SymbolId s, const Bar& bar) {
    // a bar older than the cross-section being assembled (live stragglers; never in a backtest)
    if (current_ts_ && bar.ts < *current_ts_) return;
    // a new timestamp completes the previous cross-section
    if (current_ts_ && bar.ts != *current_ts_) {
        if (closed_ts_ != current_ts_) close_cross_section();
        clear_pending();
    }
    current_ts_ = bar.ts;
    if (pending_slot_[s] >= 0) {
        pending_[static_cast<size_t>(pending_slot_[s])].second = bar;
    } else {
        pending_slot_[s] = static_cast<int32_t>(pending_.size());
        pending_.emplace_back(s, bar);
    }

    // every symbol's history stays current, held or not
    auto& prices = this->prices(s);
    prices.push_back(bar.close);
    if (static_cast<int64_t>(prices.size()) > lookback_ + 2) prices.pop_front();

    check_risk_exits(s, bar);
}

void CrossSectional::close_cross_section() {
    ++count_;
    closed_ts_ = current_ts_;
    // backtest settings: trading always allowed, any cross-section complete, no deferral
    if (count_ >= next_rebalance_at_) {
        rebalance();
        while (next_rebalance_at_ <= count_) next_rebalance_at_ += rebalance_every_;
    }
}

bool CrossSectional::rebalance() {
    if (is_halted()) return false;

    // scores in pending (arrival) order, then a stable sort ascending: worst first
    std::vector<std::pair<SymbolId, double>> ranked;
    ranked.reserve(pending_.size());
    for (const auto& [sym, bar] : pending_) {
        const auto& prices = history_[sym];
        if (static_cast<int64_t>(prices.size()) <= lookback_) continue;
        const double past = prices[prices.size() - static_cast<size_t>(lookback_ + 1)];
        if (past > 0) ranked.emplace_back(sym, (bar.close / past) - 1.0);
    }
    if (static_cast<int64_t>(ranked.size()) < top_n_) return false;
    std::stable_sort(ranked.begin(), ranked.end(),
                     [](const auto& a, const auto& b) { return a.second < b.second; });

    std::vector<SymbolId> basket;
    const size_t n = static_cast<size_t>(top_n_);
    if (momentum_) {
        // reversed(ranked[-top_n:]): the leaders, best first
        for (size_t k = 0; k < n; ++k) basket.push_back(ranked[ranked.size() - 1 - k].first);
    } else {
        for (size_t k = 0; k < n; ++k) basket.push_back(ranked[k].first);
    }
    const std::unordered_set<SymbolId> members(basket.begin(), basket.end());

    // sell what fell out of the basket, in position order (a snapshot of the keys)
    std::vector<SymbolId> held_symbols;
    for (const auto& p : pf_.positions()) held_symbols.push_back(p.symbol);
    for (SymbolId sym : held_symbols) {
        if (pending_slot_[sym] < 0 || members.count(sym)) continue;
        const int64_t qty = pf_.position_qty(sym);
        if (qty > 0) {
            const Bar& c = pending_[static_cast<size_t>(pending_slot_[sym])].second;
            exit(sym, qty, c.close, c.ts, ExitReason::Rebalance);
        }
    }

    // buy new members in rank order, never holding more than top_n
    const double budget = position_budget();
    for (SymbolId sym : basket) {
        int64_t held = 0;
        for (const auto& p : pf_.positions())
            if (p.qty > 0) ++held;
        if (held >= top_n_) break;
        if (pf_.position_qty(sym) == 0) {
            const Bar& c = pending_[static_cast<size_t>(pending_slot_[sym])].second;
            const int64_t qty = basket_qty(sym, c.close, budget);
            if (qty > 0) enter(sym, qty, c.close, c.ts);
        }
    }
    return true;
}

double CrossSectional::position_budget() const {
    double equity = pf_.cash();
    for (const auto& p : pf_.positions()) {
        const int32_t i = pending_slot_[p.symbol];
        if (i >= 0) equity += static_cast<double>(p.qty) * pending_[static_cast<size_t>(i)].second.close;
    }
    return equity / static_cast<double>(std::max<int64_t>(1, top_n_));
}

int64_t CrossSectional::basket_qty(SymbolId s, double price, double budget) {
    if (risk_) return trade_qty(s, price);
    if (price <= 0) return 0;
    // size against the price actually paid: slippage marks the buy up, commission is on top
    const double cost_mult = (1.0 + pf_.slippage_mult()) * (1.0 + pf_.commission_rate());
    return py_int(budget / (price * cost_mult));
}

}  // namespace midfreq::engine
