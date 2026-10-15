// The ported strategies (midfreq.strategies.candle_strategies). Parameter names are the Python
// constructor's, plus the few attributes the Python classes fix internally (passed explicitly by
// the Python adapter so both sides use the same values).
#pragma once

#include <deque>
#include <optional>
#include <vector>

#include "indicators.hpp"
#include "midfreq/engine/strategy.hpp"

namespace midfreq::engine {

class SmaCrossover : public HistoryStrategy {
public:
    SmaCrossover(StrategyContext ctx, const Params& p);
    void on_bar(SymbolId s, const Bar& bar) override;

private:
    int64_t fast_, slow_;
};

class Rsi : public HistoryStrategy {
public:
    Rsi(StrategyContext ctx, const Params& p);
    void on_bar(SymbolId s, const Bar& bar) override;

private:
    int64_t period_;
    double oversold_, overbought_;
};

class Macd : public HistoryStrategy {
public:
    Macd(StrategyContext ctx, const Params& p);
    void on_bar(SymbolId s, const Bar& bar) override;

private:
    int64_t fast_, slow_, signal_;
};

class Bollinger : public HistoryStrategy {
public:
    Bollinger(StrategyContext ctx, const Params& p);
    void on_bar(SymbolId s, const Bar& bar) override;

private:
    int64_t period_;
    double num_std_;
};

class ZScore : public HistoryStrategy {
public:
    ZScore(StrategyContext ctx, const Params& p);
    void on_bar(SymbolId s, const Bar& bar) override;

private:
    int64_t lookback_;
    double entry_z_, exit_z_;
};

class Ensemble : public HistoryStrategy {
public:
    Ensemble(StrategyContext ctx, const Params& p);
    void on_bar(SymbolId s, const Bar& bar) override;

private:
    double threshold_;
    int64_t rsi_period_;
    double rsi_oversold_, rsi_overbought_;
    int64_t macd_fast_, macd_slow_, macd_signal_, bb_period_;
    double bb_num_std_;
};

// RegimeSwitchStrategy: Kaufman efficiency ratio picks an SMA-crossover leg (trend) or a
// Bollinger leg (range); a regime flip while holding sells first.
class RegimeSwitch : public HistoryStrategy {
public:
    RegimeSwitch(StrategyContext ctx, const Params& p);
    void on_bar(SymbolId s, const Bar& bar) override;

private:
    enum Regime : int8_t { kNone = -1, kTrend = 0, kRange = 1 };
    Regime classify(const std::deque<double>& prices) const;
    void trend_leg(SymbolId s, const Bar& bar, const std::deque<double>& prices, int64_t position);
    void range_leg(SymbolId s, const Bar& bar, const std::deque<double>& prices, int64_t position);

    int64_t lookback_;
    double er_threshold_;
    int64_t fast_, slow_, bb_period_;
    double num_std_;
    int64_t min_dwell_, keep_;
    std::vector<int8_t> regime_;       // committed regime per symbol, kNone before the first
    std::vector<int8_t> pending_;      // hysteresis candidate, kNone when there is none
    std::vector<int64_t> pending_n_;
};

// PairsTradingStrategy: trades the first of the first two symbols to report on the z-score of
// their log price ratio, evaluated on the second symbol's bars.
class Pairs : public Strategy {
public:
    Pairs(StrategyContext ctx, const Params& p);
    void on_bar(SymbolId s, const Bar& bar) override;

private:
    int64_t lookback_;
    double entry_z_, exit_z_;
    std::vector<char> seen_;
    std::vector<SymbolId> order_;  // the first two symbols, in order of first price
    std::vector<double> prices_a_, prices_b_;
};

// CrossSectionalReversal (and Momentum): rank every symbol's recent return each cross-section
// (the bars sharing a timestamp), rebalance into the bottom (top) `top_n` every
// `rebalance_every` cross-sections, equal dollars per name.
class CrossSectional : public HistoryStrategy {
public:
    CrossSectional(StrategyContext ctx, const Params& p, bool momentum);
    void on_bar(SymbolId s, const Bar& bar) override;

protected:
    double mark_price(SymbolId s, const Position& pos) const override;

private:
    void close_cross_section();
    bool rebalance();
    double position_budget() const;
    int64_t basket_qty(SymbolId s, double price, double budget);
    void clear_pending();

    bool momentum_;
    int64_t lookback_, top_n_, rebalance_every_;
    std::optional<Timestamp> current_ts_, closed_ts_;
    std::vector<std::pair<SymbolId, Bar>> pending_;  // dict order
    std::vector<int32_t> pending_slot_;
    int64_t count_ = 0, next_rebalance_at_;
};

// BuyAndHoldRiskOverlay: buys each symbol once and holds, leaving only by its own stop-loss or
// trailing stop (its own risk manager; the run's is ignored, as in Python).
class BuyAndHold : public Strategy {
public:
    BuyAndHold(StrategyContext ctx, const Params& p);
    void on_bar(SymbolId s, const Bar& bar) override;

private:
    RiskManager overlay_;
    std::vector<char> done_;
};

}  // namespace midfreq::engine
