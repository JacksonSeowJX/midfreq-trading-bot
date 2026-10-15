#include "midfreq/engine/portfolio.hpp"

#include <cmath>

namespace midfreq::engine {

Portfolio::Portfolio(size_t n_symbols, double initial_cash, double commission_rate, double slippage_bps,
                     SumMode sum_mode, bool record_trade_equity)
    : initial_cash_(initial_cash),
      cash_(initial_cash),
      commission_rate_(commission_rate),
      // the backtester installs its slippage wrapper only for slippage_bps > 0
      slippage_mult_(slippage_bps > 0 ? slippage_bps / 10000.0 : 0.0),
      sum_mode_(sum_mode),
      record_trade_equity_(record_trade_equity),
      slot_(n_symbols, -1),
      last_buy_(n_symbols, 0.0),
      has_buy_(n_symbols, 0),
      wins_(sum_mode),
      losses_(sum_mode) {}

int64_t Portfolio::position_qty(SymbolId s) const {
    const int32_t i = slot_[s];
    return i < 0 ? 0 : positions_[static_cast<size_t>(i)].qty;
}

double Portfolio::entry_price(SymbolId s) const {
    const int32_t i = slot_[s];
    return i < 0 ? 0.0 : positions_[static_cast<size_t>(i)].entry_price;
}

void Portfolio::erase_position(size_t i) {
    slot_[positions_[i].symbol] = -1;
    positions_.erase(positions_.begin() + static_cast<std::ptrdiff_t>(i));
    for (size_t k = i; k < positions_.size(); ++k) slot_[positions_[k].symbol] = static_cast<int32_t>(k);
}

bool Portfolio::execute_trade(SymbolId symbol, bool is_buy, int64_t qty, double price, Timestamp ts,
                              ExitReason reason) {
    if (slippage_mult_ > 0) price = is_buy ? price * (1.0 + slippage_mult_) : price * (1.0 - slippage_mult_);
    if (qty <= 0) return false;

    const double trade_value = static_cast<double>(qty) * price;
    const double commission = trade_value * commission_rate_;
    const double total_cost = is_buy ? trade_value + commission : trade_value - commission;

    if (is_buy && cash_ < total_cost) {
        ++rejected_;
        return false;
    }
    cash_ += is_buy ? -total_cost : total_cost;

    if (slot_[symbol] < 0) {
        slot_[symbol] = static_cast<int32_t>(positions_.size());
        positions_.push_back(Position{symbol, 0, 0.0});
    }
    const size_t i = static_cast<size_t>(slot_[symbol]);
    Position& pos = positions_[i];

    if (is_buy) {
        const int64_t new_qty = pos.qty + qty;
        pos.entry_price = ((static_cast<double>(pos.qty) * pos.entry_price) + (static_cast<double>(qty) * price)) /
                          static_cast<double>(new_qty);
        pos.qty = new_qty;
    } else {
        if (pos.qty < qty) {
            cash_ -= total_cost;  // undo, as the Python portfolio does (not always bit-exact)
            ++rejected_;
            return false;
        }
        pos.qty -= qty;
        if (pos.qty == 0) erase_position(i);
    }

    const double cash_after = cash_;
    trades_.push_back(Trade{ts, symbol, is_buy, qty, price, commission, cash_after, reason});

    // get_trade_stats() pairs each SELL with the last BUY of the symbol
    if (is_buy) {
        last_buy_[symbol] = price;
        has_buy_[symbol] = 1;
    } else if (has_buy_[symbol]) {
        const double pnl = (price - last_buy_[symbol]) * static_cast<double>(qty);
        if (pnl > 0) {
            wins_.add(pnl);
            ++n_wins_;
        } else {
            losses_.add(std::fabs(pnl));
            ++n_losses_;
        }
        has_buy_[symbol] = 0;
    }

    if (record_trade_equity_) {
        // every position at this trade's price (the Python portfolio's per-trade curve)
        PySum pos_value(sum_mode_);
        for (const auto& p : positions_) pos_value.add(static_cast<double>(p.qty) * price);
        trade_equity_.push_back(EquityPoint{ts, cash_ + pos_value.value()});
    }
    return true;
}

double Portfolio::current_equity(const std::vector<double>& prices, const std::vector<char>& seen) const {
    PySum value(sum_mode_);
    for (const auto& p : positions_) value.add(static_cast<double>(p.qty) * (seen[p.symbol] ? prices[p.symbol] : 0.0));
    return cash_ + value.value();
}

TradeStats Portfolio::trade_stats() const {
    TradeStats st;
    const size_t total = n_wins_ + n_losses_;
    if (total > 0) st.win_rate = static_cast<double>(n_wins_) / static_cast<double>(total);
    if (n_wins_ > 0) st.avg_win = wins_.value() / static_cast<double>(n_wins_);
    if (n_losses_ > 0) st.avg_loss = losses_.value() / static_cast<double>(n_losses_);
    return st;
}

}  // namespace midfreq::engine
