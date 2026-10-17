#include <cmath>
#include <stdexcept>

#include "midfreq/engine/market_data.hpp"
#include "midfreq/engine/strategy.hpp"

namespace midfreq::engine {

int64_t Params::get_int(const std::string& key, int64_t fallback) const {
    auto it = values_.find(key);
    if (it == values_.end()) return fallback;
    const double v = it->second;
    if (!std::isfinite(v) || std::floor(v) != v) throw std::invalid_argument("parameter " + key + " must be an integer");
    return static_cast<int64_t>(v);
}

double Params::get(const std::string& key, double fallback) const {
    auto it = values_.find(key);
    return it == values_.end() ? fallback : it->second;
}

Strategy::Strategy(StrategyContext ctx) : pf_(ctx.portfolio), risk_(ctx.risk), n_symbols_(ctx.data.n_symbols()) {}

double Strategy::mark_price(SymbolId, const Position& pos) const { return pos.entry_price; }

int64_t Strategy::trade_qty(SymbolId symbol, double price) {
    if (!risk_) return 100;
    const TradeStats stats = pf_.trade_stats();
    // each held position at its own latest price; the one being traded at `price`
    PySum held = make_sum();
    for (const auto& p : pf_.positions())
        held.add(static_cast<double>(p.qty) * (p.symbol == symbol ? price : mark_price(p.symbol, p)));
    const double equity = pf_.cash() + held.value();
    return risk_->calculate_position_size(equity, price, stats.win_rate, stats.avg_win, stats.avg_loss);
}

bool Strategy::check_risk_exits(SymbolId symbol, const Bar& bar) {
    if (!risk_) return false;
    const int64_t position = pf_.position_qty(symbol);
    if (position <= 0) return false;
    risk_->update_peak_price(symbol, bar.close);
    const ExitReason reason = risk_->check_exit_signals(symbol, bar.close, pf_.entry_price(symbol));
    if (reason == ExitReason::None) return false;
    pf_.execute_trade(symbol, false, position, bar.close, bar.ts, reason);
    risk_->clear_position(symbol);
    return true;
}

bool Strategy::is_halted() {
    if (!risk_) return false;
    PySum held = make_sum();
    for (const auto& p : pf_.positions()) held.add(static_cast<double>(p.qty) * mark_price(p.symbol, p));
    return risk_->is_trading_halted(pf_.cash() + held.value(), pf_.initial_cash());
}

void Strategy::enter(SymbolId symbol, int64_t qty, double price, Timestamp ts) {
    pf_.execute_trade(symbol, true, qty, price, ts);
    if (risk_) risk_->register_entry(symbol, price);
}

void Strategy::exit(SymbolId symbol, int64_t qty, double price, Timestamp ts, ExitReason reason) {
    pf_.execute_trade(symbol, false, qty, price, ts, reason);
    if (risk_) risk_->clear_position(symbol);
}

double HistoryStrategy::mark_price(SymbolId symbol, const Position& pos) const {
    if (static_cast<size_t>(symbol) < history_.size() && !history_[symbol].empty()) return history_[symbol].back();
    return pos.entry_price;
}

std::deque<double>& HistoryStrategy::prices(SymbolId symbol) {
    if (history_.empty()) history_.resize(n_symbols_);
    return history_[symbol];
}

}  // namespace midfreq::engine
