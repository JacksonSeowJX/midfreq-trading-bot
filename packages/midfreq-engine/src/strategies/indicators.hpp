// Indicators exactly as the Python strategies compute them: recomputed over the window each bar
// (no running sums, which would round differently), with Python's slice semantics.
#pragma once

#include <cmath>
#include <cstdint>
#include <deque>
#include <stdexcept>
#include <string>
#include <vector>

#include "midfreq/engine/pymath.hpp"

namespace midfreq::engine::ind {

// sum(a[start:stop])
template <class Seq>
double slice_sum(const Seq& a, int64_t start, int64_t stop, SumMode mode) {
    const SliceBounds b = py_slice(a.size(), start, stop);
    PySum s(mode);
    for (size_t i = b.lo; i < b.hi; ++i) s.add(a[i]);
    return s.value();
}

// sum(a[-k:])
template <class Seq>
double tail_sum(const Seq& a, int64_t k, SumMode mode) {
    return slice_sum(a, -k, static_cast<int64_t>(a.size()), mode);
}

// mean and population standard deviation of a[-k:] as BollingerBandsStrategy and
// ZScoreMeanReversion compute them: sum/k, sum((p - mean) ** 2)/k, then ** 0.5
struct Band {
    double mean, std;
};
template <class Seq>
Band window_band(const Seq& a, int64_t k, SumMode mode) {
    const SliceBounds b = py_slice(a.size(), -k, static_cast<int64_t>(a.size()));
    PySum s(mode);
    for (size_t i = b.lo; i < b.hi; ++i) s.add(a[i]);
    const double mean = s.value() / static_cast<double>(k);
    PySum sq(mode);
    for (size_t i = b.lo; i < b.hi; ++i) sq.add(py_pow(a[i] - mean, 2.0));
    const double variance = sq.value() / static_cast<double>(k);
    return {mean, py_pow(variance, 0.5)};
}

// RSIStrategy._compute_rsi
template <class Seq>
double rsi(const Seq& prices, int64_t period, SumMode mode) {
    const int64_t n = static_cast<int64_t>(prices.size());
    if (n < period + 1) return 50.0;
    // deltas = [prices[i] - prices[i-1] for i in 1..n-1]; recent = deltas[-period:]
    const SliceBounds b = py_slice(static_cast<size_t>(n - 1), -period, n - 1);
    PySum gains(mode), losses(mode);
    for (size_t j = b.lo; j < b.hi; ++j) {
        const double d = prices[j + 1] - prices[j];
        if (d > 0) gains.add(d);
        if (d < 0) losses.add(-d);
    }
    const double avg_gain = gains.empty() ? 0.0 : gains.value() / static_cast<double>(period);
    const double avg_loss = losses.empty() ? 0.0 : losses.value() / static_cast<double>(period);
    if (avg_loss == 0) return 100.0;
    const double rs = avg_gain / avg_loss;
    return 100.0 - (100.0 / (1.0 + rs));
}

// MACDStrategy._ema: seeded with the SMA of the first `period` prices
template <class Seq>
std::vector<double> ema(const Seq& prices, int64_t period, SumMode mode) {
    std::vector<double> out;
    const int64_t n = static_cast<int64_t>(prices.size());
    if (n < period) return out;
    const double multiplier = 2.0 / static_cast<double>(period + 1);
    out.reserve(static_cast<size_t>(n - period + 1));
    out.push_back(slice_sum(prices, 0, period, mode) / static_cast<double>(period));
    for (int64_t i = period; i < n; ++i) {
        const double last = out.back();
        out.push_back((prices[static_cast<size_t>(i)] - last) * multiplier + last);
    }
    return out;
}

// The MACD line and its signal line as MACDStrategy (and the ensemble's MACD vote) build them.
// Returns false where the Python code returns early for too little data.
struct MacdCross {
    double macd_prev, macd_curr, signal_prev, signal_curr;
};
template <class Seq>
bool macd_cross(const Seq& prices, int64_t fast, int64_t slow, int64_t signal, SumMode mode, MacdCross& out) {
    const std::vector<double> f = ema(prices, fast, mode);
    const std::vector<double> s = ema(prices, slow, mode);
    // fast_aligned = fast_vals[offset:], offset = len(fast) - len(slow); then zip with slow
    const int64_t offset = static_cast<int64_t>(f.size()) - static_cast<int64_t>(s.size());
    const SliceBounds b = py_slice(f.size(), offset, static_cast<int64_t>(f.size()));
    const size_t n = std::min(b.hi - b.lo, s.size());
    std::vector<double> macd(n);
    for (size_t i = 0; i < n; ++i) macd[i] = f[b.lo + i] - s[i];
    if (static_cast<int64_t>(macd.size()) < signal + 1) return false;
    const std::vector<double> sig = ema(macd, signal, mode);
    if (sig.size() < 2) return false;
    out = {macd[macd.size() - 2], macd.back(), sig[sig.size() - 2], sig.back()};
    return true;
}

inline void require_positive(int64_t v, const char* name) {
    if (v < 1) throw std::invalid_argument(std::string(name) + " must be at least 1 for the native engine");
}

}  // namespace midfreq::engine::ind
