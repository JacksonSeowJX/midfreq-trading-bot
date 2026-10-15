#include "midfreq/engine/risk.hpp"

#include <algorithm>

#include "midfreq/engine/pymath.hpp"

namespace midfreq::engine {

namespace {

int64_t fixed_fractional(double risk_pct, double equity, double entry_price, std::optional<double> stop_price) {
    if (entry_price <= 0) return 0;
    const double risk_amount = equity * risk_pct;
    int64_t qty;
    if (stop_price && *stop_price < entry_price) {
        const double risk_per_share = entry_price - *stop_price;
        qty = risk_per_share > 0 ? py_int(risk_amount / risk_per_share) : py_int(risk_amount / entry_price);
    } else {
        qty = py_int(risk_amount / entry_price);
    }
    const int64_t max_affordable = py_int(equity / entry_price);
    return std::min(qty, max_affordable);
}

}  // namespace

int64_t size_position(const SizerConfig& s, double equity, double entry_price, std::optional<double> stop_price,
                      std::optional<double> win_rate, std::optional<double> avg_win, std::optional<double> avg_loss) {
    switch (s.method) {
        case SizingMethod::FixedQuantity: {
            const int64_t max_affordable = entry_price > 0 ? py_int(equity / entry_price) : 0;
            return std::min(s.qty, max_affordable);
        }
        case SizingMethod::FixedFractional:
            return fixed_fractional(s.risk_pct, equity, entry_price, stop_price);
        case SizingMethod::Kelly: {
            if (entry_price <= 0 || equity <= 0) return 0;
            if (!win_rate || !avg_win || !avg_loss || *avg_loss == 0)
                return fixed_fractional(0.02, equity, entry_price, std::nullopt);
            const double payoff_ratio = *avg_win / *avg_loss;
            const double loss_rate = 1.0 - *win_rate;
            double kelly_f = (*win_rate * payoff_ratio - loss_rate) / payoff_ratio;
            kelly_f = py_max(0.0, kelly_f) * s.fraction;
            kelly_f = py_min(kelly_f, s.max_pct);
            const double position_value = equity * kelly_f;
            const int64_t qty = py_int(position_value / entry_price);
            const int64_t max_affordable = py_int(equity / entry_price);
            return std::min(qty, max_affordable);
        }
        case SizingMethod::EqualDollar: {
            if (entry_price <= 0 || equity <= 0) return 0;
            const double budget = (equity / static_cast<double>(s.n_positions)) * (1.0 - s.cost_buffer);
            return py_int(budget / entry_price);
        }
    }
    return 0;
}

RiskManager::RiskManager(const RiskConfig& config, size_t n_symbols)
    : config_(config), peak_(n_symbols, 0.0), has_peak_(n_symbols, 0) {}

void RiskManager::update_peak_price(SymbolId s, double price) {
    if (has_peak_[s]) {
        peak_[s] = py_max(peak_[s], price);
    } else {
        peak_[s] = price;
        has_peak_[s] = 1;
    }
}

void RiskManager::register_entry(SymbolId s, double entry_price) {
    peak_[s] = entry_price;
    has_peak_[s] = 1;
}

void RiskManager::clear_position(SymbolId s) { has_peak_[s] = 0; }

ExitReason RiskManager::check_exit_signals(SymbolId s, double price, double entry_price) const {
    if (entry_price <= 0) return ExitReason::None;
    if (config_.stop_loss_pct) {
        const double stop_price = entry_price * (1.0 - *config_.stop_loss_pct);
        if (price <= stop_price) return ExitReason::StopLoss;
    }
    if (config_.trailing_stop_pct) {
        const double peak = has_peak_[s] ? peak_[s] : entry_price;
        const double trail_price = peak * (1.0 - *config_.trailing_stop_pct);
        if (price <= trail_price) return ExitReason::TrailingStop;
    }
    if (config_.take_profit_pct) {
        const double target_price = entry_price * (1.0 + *config_.take_profit_pct);
        if (price >= target_price) return ExitReason::TakeProfit;
    }
    return ExitReason::None;
}

int64_t RiskManager::calculate_position_size(double equity, double entry_price, std::optional<double> win_rate,
                                             std::optional<double> avg_win, std::optional<double> avg_loss) const {
    std::optional<double> stop_price;
    if (config_.stop_loss_pct) stop_price = entry_price * (1.0 - *config_.stop_loss_pct);
    return size_position(config_.sizer, equity, entry_price, stop_price, win_rate, avg_win, avg_loss);
}

bool RiskManager::is_trading_halted(double equity, double initial_equity) {
    if (initial_equity <= 0) return false;
    const double drawdown = (initial_equity - equity) / initial_equity;
    if (drawdown >= config_.max_drawdown_pct) {
        halted_ = true;
        return true;
    }
    halted_ = false;
    return false;
}

}  // namespace midfreq::engine
