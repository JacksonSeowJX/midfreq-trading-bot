#include "midfreq/engine/pymath.hpp"

#include <utility>

namespace midfreq::engine {

namespace detail {
double (*volatile pow_fn)(double, double) = ::pow;
}

double fsum(const std::vector<double>& xs) {
    // Shewchuk's exact partials, then the correctly rounded total (CPython's math_fsum).
    std::vector<double> partials;
    for (double x : xs) {
        size_t i = 0;
        for (double y : partials) {
            if (std::fabs(x) < std::fabs(y)) std::swap(x, y);
            const double hi = x + y;
            const double lo = y - (hi - x);
            if (lo != 0.0) partials[i++] = lo;
            x = hi;
        }
        partials.resize(i);
        if (x != 0.0) partials.push_back(x);
    }
    if (partials.empty()) return 0.0;
    size_t n = partials.size();
    double hi = partials[--n], lo = 0.0;
    while (n > 0) {
        const double x = hi;
        const double y = partials[--n];
        hi = x + y;
        const double yr = hi - x;
        lo = y - yr;
        if (lo != 0.0) break;
    }
    // round half to even across the remaining partials
    if (n > 0 && ((lo < 0.0 && partials[n - 1] < 0.0) || (lo > 0.0 && partials[n - 1] > 0.0))) {
        const double y = lo * 2.0;
        const double x = hi + y;
        const double yr = x - hi;
        if (y == yr) hi = x;
    }
    return hi;
}

}  // namespace midfreq::engine
