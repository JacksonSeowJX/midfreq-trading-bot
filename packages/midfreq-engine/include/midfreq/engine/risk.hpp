// Risk rules after midfreq.core.risk_manager: stop-loss, trailing stop, take-profit, the drawdown
// circuit breaker and the four position sizers.
#pragma once

#include <optional>
#include <vector>

#include "midfreq/engine/types.hpp"

namespace midfreq::engine {

enum class SizingMethod : uint8_t { FixedQuantity, FixedFractional, Kelly, EqualDollar };

struct SizerConfig {
    SizingMethod method = SizingMethod::FixedQuantity;
    int64_t qty = 100;           // FixedQuantity
    double risk_pct = 0.02;      // FixedFractional
    double fraction = 0.5;       // Kelly
    double max_pct = 0.25;       // Kelly
    int64_t n_positions = 2;     // EqualDollar
    double cost_buffer = 0.004;  // EqualDollar
};

struct RiskConfig {
    std::optional<double> stop_loss_pct, trailing_stop_pct, take_profit_pct;
    double max_drawdown_pct = 0.10;
    SizerConfig sizer;
};

int64_t size_position(const SizerConfig& sizer, double equity, double entry_price, std::optional<double> stop_price,
                      std::optional<double> win_rate, std::optional<double> avg_win, std::optional<double> avg_loss);

class RiskManager {
public:
    RiskManager(const RiskConfig& config, size_t n_symbols);

    void update_peak_price(SymbolId s, double price);
    void register_entry(SymbolId s, double entry_price);
    void clear_position(SymbolId s);
    ExitReason check_exit_signals(SymbolId s, double price, double entry_price) const;
    int64_t calculate_position_size(double equity, double entry_price, std::optional<double> win_rate,
                                    std::optional<double> avg_win, std::optional<double> avg_loss) const;
    bool is_trading_halted(double equity, double initial_equity);

    const RiskConfig& config() const { return config_; }

private:
    RiskConfig config_;
    std::vector<double> peak_;
    std::vector<char> has_peak_;
    bool halted_ = false;
};

}  // namespace midfreq::engine
