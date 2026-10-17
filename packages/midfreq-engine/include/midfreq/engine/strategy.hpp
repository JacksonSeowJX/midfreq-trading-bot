// The strategy interface, and the BaseStrategy helpers every candle strategy shares
// (midfreq.strategies.candle_strategies.BaseStrategy): sizing, risk exits and the drawdown halt.
#pragma once

#include <deque>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "midfreq/engine/portfolio.hpp"
#include "midfreq/engine/risk.hpp"
#include "midfreq/engine/types.hpp"

namespace midfreq::engine {

class MarketData;

// Strategy parameters by name. Integer parameters are checked to be whole numbers.
class Params {
public:
    Params() = default;
    explicit Params(std::map<std::string, double> values) : values_(std::move(values)) {}

    void set(const std::string& key, double value) { values_[key] = value; }
    int64_t get_int(const std::string& key, int64_t fallback) const;
    double get(const std::string& key, double fallback) const;
    const std::map<std::string, double>& values() const { return values_; }

private:
    std::map<std::string, double> values_;
};

struct StrategyContext {
    Portfolio& portfolio;
    RiskManager* risk;  // null without a risk manager
    const MarketData& data;
};

class Strategy {
public:
    explicit Strategy(StrategyContext ctx);
    virtual ~Strategy() = default;
    Strategy(const Strategy&) = delete;
    Strategy& operator=(const Strategy&) = delete;

    virtual void on_bar(SymbolId symbol, const Bar& bar) = 0;

protected:
    // BaseStrategy._mark_price: the latest known price of a held symbol. The base has no price
    // history, so it is the entry price; strategies that keep one override this.
    virtual double mark_price(SymbolId symbol, const Position& pos) const;

    // BaseStrategy._get_trade_qty: the risk manager's size at `price`, or 100 shares without one.
    int64_t trade_qty(SymbolId symbol, double price);
    // BaseStrategy._check_risk_exits: sells on a stop or target; true if it did.
    bool check_risk_exits(SymbolId symbol, const Bar& bar);
    // BaseStrategy._is_halted: the drawdown circuit breaker, each position at its own mark.
    bool is_halted();

    // A buy or a signal exit, with the risk manager told as the Python strategies tell it.
    void enter(SymbolId symbol, int64_t qty, double price, Timestamp ts);
    void exit(SymbolId symbol, int64_t qty, double price, Timestamp ts, ExitReason reason);

    PySum make_sum() const { return PySum(pf_.sum_mode()); }

    Portfolio& pf_;
    RiskManager* risk_;
    size_t n_symbols_;
};

// A strategy that keeps each symbol's recent closes (its `history` dict), which is also what
// _mark_price falls back on.
class HistoryStrategy : public Strategy {
public:
    using Strategy::Strategy;

protected:
    double mark_price(SymbolId symbol, const Position& pos) const override;
    std::deque<double>& prices(SymbolId symbol);

    std::vector<std::deque<double>> history_;
};

std::unique_ptr<Strategy> make_strategy(const std::string& name, const Params& params, StrategyContext ctx);
std::vector<std::string> strategy_names();

}  // namespace midfreq::engine
