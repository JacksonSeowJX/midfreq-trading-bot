// Portfolio: cash, positions and the trade log, after midfreq.core.portfolio.Portfolio with the
// backtester's slippage wrapper folded in.
//
// Positions keep Python dict order (a new symbol goes to the end, a closed one is removed), since
// equity is a compensated sum over them and the order changes the last bit.
#pragma once

#include <optional>
#include <vector>

#include "midfreq/engine/pymath.hpp"
#include "midfreq/engine/types.hpp"

namespace midfreq::engine {

struct Position {
    SymbolId symbol;
    int64_t qty;
    double entry_price;
};

// Portfolio.get_trade_stats(): win rate and average win/loss over BUY->SELL pairs, kept as a
// running fold over the trade log instead of a rescan on every call.
struct TradeStats {
    std::optional<double> win_rate, avg_win, avg_loss;
};

class Portfolio {
public:
    Portfolio(size_t n_symbols, double initial_cash, double commission_rate, double slippage_bps,
              SumMode sum_mode, bool record_trade_equity);

    // Portfolio.execute_trade behind the slippage wrapper: buys pay price*(1+s), sells get
    // price*(1-s). Returns false for a rejected order (not enough cash, or not enough shares).
    bool execute_trade(SymbolId symbol, bool is_buy, int64_t qty, double price, Timestamp ts,
                       ExitReason reason = ExitReason::None);

    double cash() const { return cash_; }
    double initial_cash() const { return initial_cash_; }
    double commission_rate() const { return commission_rate_; }
    // The fraction a basket strategy budgets for slippage (0 when slippage is off).
    double slippage_mult() const { return slippage_mult_; }
    SumMode sum_mode() const { return sum_mode_; }

    const std::vector<Position>& positions() const { return positions_; }
    int64_t position_qty(SymbolId s) const;
    double entry_price(SymbolId s) const;
    bool has_position_entry(SymbolId s) const { return slot_[s] >= 0; }

    // Portfolio.get_current_equity(latest prices); `seen` marks symbols with a price.
    double current_equity(const std::vector<double>& prices, const std::vector<char>& seen) const;

    TradeStats trade_stats() const;

    const std::vector<Trade>& trades() const { return trades_; }
    const std::vector<EquityPoint>& trade_equity() const { return trade_equity_; }
    size_t rejected() const { return rejected_; }

private:
    void erase_position(size_t i);

    double initial_cash_, cash_, commission_rate_, slippage_mult_;
    SumMode sum_mode_;
    bool record_trade_equity_;
    std::vector<Position> positions_;
    std::vector<int32_t> slot_;  // symbol -> index into positions_, or -1
    std::vector<Trade> trades_;
    std::vector<EquityPoint> trade_equity_;
    size_t rejected_ = 0;

    // get_trade_stats() fold state
    std::vector<double> last_buy_;
    std::vector<char> has_buy_;
    PySum wins_, losses_;
    size_t n_wins_ = 0, n_losses_ = 0;
};

}  // namespace midfreq::engine
