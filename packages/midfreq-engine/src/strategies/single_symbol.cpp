// The single-symbol indicator strategies: SMA crossover, RSI, MACD, Bollinger Bands, z-score
// mean reversion and the ensemble vote. Each follows its Python on_data line for line.
#include "strategies.hpp"

namespace midfreq::engine {

namespace {

// Append a close, dropping the oldest once the list is longer than `keep` (prices.pop(0)).
void push_capped(std::deque<double>& prices, double close, size_t keep) {
    prices.push_back(close);
    if (prices.size() > keep) prices.pop_front();
}

}  // namespace

// ── MovingAverageCrossover ──────────────────────────────────────────

SmaCrossover::SmaCrossover(StrategyContext ctx, const Params& p)
    : HistoryStrategy(ctx), fast_(p.get_int("fast_period", 10)), slow_(p.get_int("slow_period", 50)) {
    ind::require_positive(fast_, "fast_period");
    ind::require_positive(slow_, "slow_period");
}

void SmaCrossover::on_bar(SymbolId s, const Bar& bar) {
    if (check_risk_exits(s, bar)) return;
    if (is_halted()) return;
    auto& prices = this->prices(s);
    push_capped(prices, bar.close, static_cast<size_t>(slow_ + 1));
    if (static_cast<int64_t>(prices.size()) <= slow_) return;

    const SumMode m = pf_.sum_mode();
    const double fast_ma = ind::tail_sum(prices, fast_, m) / static_cast<double>(fast_);
    const double slow_ma = ind::tail_sum(prices, slow_, m) / static_cast<double>(slow_);
    const double prev_fast = ind::slice_sum(prices, -(fast_ + 1), -1, m) / static_cast<double>(fast_);
    const double prev_slow = ind::slice_sum(prices, -(slow_ + 1), -1, m) / static_cast<double>(slow_);

    const int64_t position = pf_.position_qty(s);
    if (prev_fast <= prev_slow && fast_ma > slow_ma) {
        if (position == 0) {
            const int64_t qty = trade_qty(s, bar.close);
            if (qty > 0) enter(s, qty, bar.close, bar.ts);
        }
    } else if (prev_fast >= prev_slow && fast_ma < slow_ma) {
        if (position > 0) exit(s, position, bar.close, bar.ts, ExitReason::Signal);
    }
}

// ── RSIStrategy ─────────────────────────────────────────────────────

Rsi::Rsi(StrategyContext ctx, const Params& p)
    : HistoryStrategy(ctx),
      period_(p.get_int("rsi_period", 14)),
      oversold_(p.get("oversold", 30)),
      overbought_(p.get("overbought", 70)) {
    ind::require_positive(period_, "rsi_period");
}

void Rsi::on_bar(SymbolId s, const Bar& bar) {
    if (check_risk_exits(s, bar)) return;
    if (is_halted()) return;
    auto& prices = this->prices(s);
    push_capped(prices, bar.close, static_cast<size_t>(period_ + 2));
    if (static_cast<int64_t>(prices.size()) < period_ + 1) return;

    const double value = ind::rsi(prices, period_, pf_.sum_mode());
    const int64_t position = pf_.position_qty(s);
    if (value < oversold_ && position == 0) {
        const int64_t qty = trade_qty(s, bar.close);
        if (qty > 0) enter(s, qty, bar.close, bar.ts);
    } else if (value > overbought_ && position > 0) {
        exit(s, position, bar.close, bar.ts, ExitReason::Signal);
    }
}

// ── MACDStrategy ────────────────────────────────────────────────────

Macd::Macd(StrategyContext ctx, const Params& p)
    : HistoryStrategy(ctx),
      fast_(p.get_int("fast_ema", 12)),
      slow_(p.get_int("slow_ema", 26)),
      signal_(p.get_int("signal_period", 9)) {
    ind::require_positive(fast_, "fast_ema");
    ind::require_positive(slow_, "slow_ema");
    ind::require_positive(signal_, "signal_period");
}

void Macd::on_bar(SymbolId s, const Bar& bar) {
    if (check_risk_exits(s, bar)) return;
    if (is_halted()) return;
    auto& prices = this->prices(s);
    prices.push_back(bar.close);
    const int64_t min_len = slow_ + signal_ + 1;
    if (static_cast<int64_t>(prices.size()) < min_len) return;
    const size_t max_keep = static_cast<size_t>(min_len + 50);
    while (prices.size() > max_keep) prices.pop_front();

    ind::MacdCross x{};
    if (!ind::macd_cross(prices, fast_, slow_, signal_, pf_.sum_mode(), x)) return;
    const int64_t position = pf_.position_qty(s);
    if (x.macd_prev <= x.signal_prev && x.macd_curr > x.signal_curr) {
        if (position == 0) {
            const int64_t qty = trade_qty(s, bar.close);
            if (qty > 0) enter(s, qty, bar.close, bar.ts);
        }
    } else if (x.macd_prev >= x.signal_prev && x.macd_curr < x.signal_curr) {
        if (position > 0) exit(s, position, bar.close, bar.ts, ExitReason::Signal);
    }
}

// ── BollingerBandsStrategy ──────────────────────────────────────────

Bollinger::Bollinger(StrategyContext ctx, const Params& p)
    : HistoryStrategy(ctx), period_(p.get_int("bb_period", 20)), num_std_(p.get("num_std", 2.0)) {
    ind::require_positive(period_, "bb_period");
}

void Bollinger::on_bar(SymbolId s, const Bar& bar) {
    if (check_risk_exits(s, bar)) return;
    if (is_halted()) return;
    auto& prices = this->prices(s);
    push_capped(prices, bar.close, static_cast<size_t>(period_ + 2));
    if (static_cast<int64_t>(prices.size()) < period_) return;

    const ind::Band b = ind::window_band(prices, period_, pf_.sum_mode());
    const double upper = b.mean + num_std_ * b.std;
    const double lower = b.mean - num_std_ * b.std;
    const int64_t position = pf_.position_qty(s);
    if (bar.close <= lower && position == 0) {
        const int64_t qty = trade_qty(s, bar.close);
        if (qty > 0) enter(s, qty, bar.close, bar.ts);
    } else if (bar.close >= upper && position > 0) {
        exit(s, position, bar.close, bar.ts, ExitReason::Signal);
    }
}

// ── ZScoreMeanReversion ─────────────────────────────────────────────

ZScore::ZScore(StrategyContext ctx, const Params& p)
    : HistoryStrategy(ctx),
      lookback_(p.get_int("lookback", 20)),
      entry_z_(p.get("entry_z", 2.0)),
      exit_z_(p.get("exit_z", 0.5)) {
    ind::require_positive(lookback_, "lookback");
}

void ZScore::on_bar(SymbolId s, const Bar& bar) {
    if (check_risk_exits(s, bar)) return;
    if (is_halted()) return;
    auto& prices = this->prices(s);
    push_capped(prices, bar.close, static_cast<size_t>(lookback_ + 2));
    if (static_cast<int64_t>(prices.size()) < lookback_) return;

    const ind::Band b = ind::window_band(prices, lookback_, pf_.sum_mode());
    if (b.std == 0) return;
    const double z = (bar.close - b.mean) / b.std;
    const int64_t position = pf_.position_qty(s);
    if (z < -entry_z_ && position == 0) {
        const int64_t qty = trade_qty(s, bar.close);
        if (qty > 0) enter(s, qty, bar.close, bar.ts);
    } else if (position > 0 && z > -exit_z_) {
        exit(s, position, bar.close, bar.ts, ExitReason::Signal);
    }
}

// ── EnsembleStrategy ────────────────────────────────────────────────

Ensemble::Ensemble(StrategyContext ctx, const Params& p)
    : HistoryStrategy(ctx),
      threshold_(p.get("consensus_threshold", 2)),
      rsi_period_(p.get_int("rsi_period", 14)),
      rsi_oversold_(p.get("rsi_oversold", 30)),
      rsi_overbought_(p.get("rsi_overbought", 70)),
      macd_fast_(p.get_int("macd_fast", 12)),
      macd_slow_(p.get_int("macd_slow", 26)),
      macd_signal_(p.get_int("macd_signal", 9)),
      bb_period_(p.get_int("bb_period", 20)),
      bb_num_std_(p.get("bb_num_std", 2.0)) {
    ind::require_positive(rsi_period_, "rsi_period");
    ind::require_positive(macd_fast_, "macd_fast");
    ind::require_positive(macd_slow_, "macd_slow");
    ind::require_positive(macd_signal_, "macd_signal");
    ind::require_positive(bb_period_, "bb_period");
}

void Ensemble::on_bar(SymbolId s, const Bar& bar) {
    if (check_risk_exits(s, bar)) return;
    if (is_halted()) return;
    auto& prices = this->prices(s);
    push_capped(prices, bar.close, 100);
    if (prices.size() < 30) return;

    const SumMode m = pf_.sum_mode();
    int vote = 0;
    const double r = ind::rsi(prices, rsi_period_, m);
    if (r < rsi_oversold_) {
        vote += 1;
    } else if (r > rsi_overbought_) {
        vote -= 1;
    }

    ind::MacdCross x{};
    if (ind::macd_cross(prices, macd_fast_, macd_slow_, macd_signal_, m, x)) {
        if (x.macd_prev <= x.signal_prev && x.macd_curr > x.signal_curr) {
            vote += 1;
        } else if (x.macd_prev >= x.signal_prev && x.macd_curr < x.signal_curr) {
            vote -= 1;
        }
    }

    // window = prices[-period:]; voted only when it is a full window
    if (static_cast<int64_t>(std::min<size_t>(prices.size(), static_cast<size_t>(bb_period_))) == bb_period_) {
        const ind::Band b = ind::window_band(prices, bb_period_, m);
        const double upper = b.mean + bb_num_std_ * b.std;
        const double lower = b.mean - bb_num_std_ * b.std;
        if (bar.close <= lower) {
            vote += 1;
        } else if (bar.close >= upper) {
            vote -= 1;
        }
    }

    const int64_t position = pf_.position_qty(s);
    if (vote >= threshold_ && position == 0) {
        const int64_t qty = trade_qty(s, bar.close);
        if (qty > 0) enter(s, qty, bar.close, bar.ts);
    } else if (vote <= -threshold_ && position > 0) {
        exit(s, position, bar.close, bar.ts, ExitReason::Signal);
    }
}

}  // namespace midfreq::engine
