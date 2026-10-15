// MarketData: every symbol's bars merged once into one time-ordered stream, held as columns.
//
// The merge is a stable sort of the symbols' bars concatenated in the order given, which is what
// the Python backtester does (pd.concat, then sort_index(kind="mergesort")): bars that share a
// timestamp come in symbol order, and a symbol's own bars keep their order. A backtest over a date
// range replays the slice [lower_bound(start), lower_bound(cutoff)) of this stream, which is the
// same sequence as filtering each symbol first and merging after, so one MarketData serves every
// window of a walk-forward and every parameter set of a grid search.
#pragma once

#include <optional>
#include <string>
#include <vector>

#include "midfreq/engine/types.hpp"

namespace midfreq::engine {

struct SymbolSeries {
    std::string name;
    std::vector<Timestamp> ts;
    std::vector<double> open, high, low, close, volume;
};

struct EventRange {
    size_t begin, end;  // [begin, end) into the merged stream
    size_t size() const { return end - begin; }
};

class MarketData {
public:
    explicit MarketData(std::vector<SymbolSeries> series);

    size_t size() const { return ts_.size(); }
    size_t n_symbols() const { return names_.size(); }
    const std::vector<std::string>& symbols() const { return names_; }

    // Events with start <= ts < cutoff; either bound may be absent.
    EventRange range(std::optional<Timestamp> start, std::optional<Timestamp> cutoff) const;

    Bar bar(size_t i) const { return Bar{ts_[i], open_[i], high_[i], low_[i], close_[i], volume_[i]}; }
    Timestamp ts(size_t i) const { return ts_[i]; }
    SymbolId symbol(size_t i) const { return sym_[i]; }

private:
    std::vector<std::string> names_;
    std::vector<Timestamp> ts_;
    std::vector<SymbolId> sym_;
    std::vector<double> open_, high_, low_, close_, volume_;
};

}  // namespace midfreq::engine
