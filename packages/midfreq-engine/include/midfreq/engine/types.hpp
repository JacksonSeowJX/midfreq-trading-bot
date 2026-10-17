// Shared value types: timestamps, symbol ids, bars, trades and exit reasons.
#pragma once

#include <cstdint>
#include <string>

namespace midfreq::engine {

using Timestamp = int64_t;  // nanoseconds since the Unix epoch, as the data's UTC index
using SymbolId = int32_t;   // index into MarketData::symbols()

constexpr int64_t kNanosPerDay = 86'400'000'000'000LL;

// The UTC calendar day of a timestamp (pandas Timestamp.date() on a UTC index).
inline int64_t utc_day(Timestamp ts) {
    int64_t d = ts / kNanosPerDay;
    if (ts % kNanosPerDay < 0) --d;
    return d;
}

struct Bar {
    Timestamp ts;
    double open, high, low, close, volume;
};

// Why a position was sold. The Python backtester records these as strings in trade_history.
enum class ExitReason : uint8_t { None = 0, Signal, Rebalance, RegimeChange, StopLoss, TrailingStop, TakeProfit };

inline const char* exit_reason_name(ExitReason r) {
    switch (r) {
        case ExitReason::Signal: return "signal";
        case ExitReason::Rebalance: return "rebalance";
        case ExitReason::RegimeChange: return "regime_change";
        case ExitReason::StopLoss: return "stop_loss";
        case ExitReason::TrailingStop: return "trailing_stop";
        case ExitReason::TakeProfit: return "take_profit";
        case ExitReason::None: break;
    }
    return "";
}

struct Trade {
    Timestamp ts;
    SymbolId symbol;
    bool is_buy;
    int64_t qty;
    double price;  // the fill price, after slippage
    double commission;
    double cash_after;
    ExitReason reason;
};

struct EquityPoint {
    Timestamp ts;
    double equity;
};

}  // namespace midfreq::engine
