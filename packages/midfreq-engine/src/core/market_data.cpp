#include "midfreq/engine/market_data.hpp"

#include <algorithm>
#include <numeric>
#include <stdexcept>

#include "midfreq/engine/pymath.hpp"

namespace midfreq::engine {

MarketData::MarketData(std::vector<SymbolSeries> series) {
    size_t total = 0;
    for (const auto& s : series) {
        const size_t n = s.ts.size();
        if (s.open.size() != n || s.high.size() != n || s.low.size() != n || s.close.size() != n ||
            s.volume.size() != n)
            throw std::invalid_argument("symbol " + s.name + ": columns differ in length");
        total += n;
    }
    if (series.size() > static_cast<size_t>(INT32_MAX)) throw std::invalid_argument("too many symbols");

    // concatenate in the order given, then a stable sort by time
    std::vector<Timestamp> cat_ts;
    std::vector<SymbolId> cat_sym;
    std::vector<size_t> cat_row;
    cat_ts.reserve(total);
    cat_sym.reserve(total);
    cat_row.reserve(total);
    for (size_t k = 0; k < series.size(); ++k) {
        names_.push_back(series[k].name);
        for (size_t r = 0; r < series[k].ts.size(); ++r) {
            cat_ts.push_back(series[k].ts[r]);
            cat_sym.push_back(static_cast<SymbolId>(k));
            cat_row.push_back(r);
        }
    }
    std::vector<size_t> order(total);
    std::iota(order.begin(), order.end(), size_t{0});
    std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) { return cat_ts[a] < cat_ts[b]; });

    ts_.resize(total);
    sym_.resize(total);
    open_.resize(total);
    high_.resize(total);
    low_.resize(total);
    close_.resize(total);
    volume_.resize(total);
    for (size_t i = 0; i < total; ++i) {
        const size_t j = order[i];
        const auto& s = series[static_cast<size_t>(cat_sym[j])];
        const size_t r = cat_row[j];
        ts_[i] = cat_ts[j];
        sym_[i] = cat_sym[j];
        open_[i] = s.open[r];
        high_[i] = s.high[r];
        low_[i] = s.low[r];
        close_[i] = s.close[r];
        volume_[i] = s.volume[r];
    }
}

EventRange MarketData::range(std::optional<Timestamp> start, std::optional<Timestamp> cutoff) const {
    size_t lo = 0, hi = ts_.size();
    if (start) lo = static_cast<size_t>(std::lower_bound(ts_.begin(), ts_.end(), *start) - ts_.begin());
    if (cutoff) hi = static_cast<size_t>(std::lower_bound(ts_.begin(), ts_.end(), *cutoff) - ts_.begin());
    if (hi < lo) hi = lo;
    return {lo, hi};
}

}  // namespace midfreq::engine
