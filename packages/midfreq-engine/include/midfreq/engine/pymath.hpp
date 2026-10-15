// Arithmetic that rounds exactly as CPython does, so the engine makes the same decisions as the
// Python backtester down to the last bit.
//
// - PySum: the built-in sum() over floats. Since Python 3.12 it uses Neumaier's compensated
//   summation (gh-100425); before, it added left to right. A moving-average crossover compares two
//   such sums, so the mode has to match the interpreter that produced the reference results.
// - py_pow: float ** float, which CPython hands to the C library's pow() (never x*x, never sqrt).
//   It is called through a volatile pointer so the compiler cannot rewrite pow(x, 2.0) as x*x.
// - py_int: int(float): truncation, with OverflowError/ValueError for inf/nan as Python raises.
// - py_max/py_min: Python's max(a, b)/min(a, b), which keep the first argument on ties and NaN.
// - fsum: an exactly rounded sum (Shewchuk), used by the metrics.
#pragma once

#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace midfreq::engine {

enum class SumMode : uint8_t { Naive, Neumaier };

class ZeroDivisionError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class PySum {
public:
    explicit PySum(SumMode mode) : mode_(mode) {}

    void add(double x) {
        if (!started_) {
            // sum() starts from the int 0, and 0 + x is the float x (0.0 + x)
            hi_ = 0.0 + x;
            started_ = true;
            return;
        }
        if (mode_ == SumMode::Naive) {
            hi_ += x;
            return;
        }
        const double t = hi_ + x;
        if (std::fabs(hi_) >= std::fabs(x)) {
            c_ += (hi_ - t) + x;
        } else {
            c_ += (x - t) + hi_;
        }
        hi_ = t;
    }

    // What sum() returns: 0 for no items, else the running sum plus the compensation.
    double value() const {
        if (!started_) return 0.0;
        if (mode_ == SumMode::Neumaier && c_ != 0.0 && std::isfinite(c_)) return hi_ + c_;
        return hi_;
    }

    bool empty() const { return !started_; }

private:
    SumMode mode_;
    bool started_ = false;
    double hi_ = 0.0;
    double c_ = 0.0;
};

namespace detail {
extern double (*volatile pow_fn)(double, double);
}

// float ** float as CPython's float_pow computes it, for the cases the strategies reach.
inline double py_pow(double iv, double iw) {
    if (iw == 0.0) return 1.0;
    if (std::isnan(iv) || std::isnan(iw) || std::isinf(iv) || std::isinf(iw)) return detail::pow_fn(iv, iw);
    const bool iw_is_int = std::floor(iw) == iw;
    const bool iw_is_odd = iw_is_int && std::fmod(std::fabs(iw), 2.0) == 1.0;
    if (iv == 0.0) {
        if (iw < 0.0) throw ZeroDivisionError("0.0 cannot be raised to a negative power");
        return iw_is_odd ? iv : 0.0;
    }
    bool negate = false;
    if (iv < 0.0) {
        if (!iw_is_int) throw std::domain_error("negative number cannot be raised to a fractional power");
        iv = -iv;
        negate = iw_is_odd;
    }
    if (iv == 1.0) return negate ? -1.0 : 1.0;
    double ix = detail::pow_fn(iv, iw);
    if (negate) ix = -ix;
    if (std::isinf(ix)) throw std::overflow_error("(34, 'Result too large')");
    return ix;
}

inline int64_t py_int(double x) {
    if (std::isnan(x)) throw std::domain_error("cannot convert float NaN to integer");
    if (std::isinf(x)) throw std::overflow_error("cannot convert float infinity to integer");
    const double t = std::trunc(x);
    if (t >= 9.2233720368547758e18 || t < -9.2233720368547758e18)
        throw std::overflow_error("integer too large for the native engine");
    return static_cast<int64_t>(t);
}

inline double py_div(double a, double b) {
    if (b == 0.0) throw ZeroDivisionError("float division by zero");
    return a / b;
}

inline double py_max(double a, double b) { return b > a ? b : a; }
inline double py_min(double a, double b) { return b < a ? b : a; }

inline double py_log(double x) {
    if (!(x > 0.0)) {
        if (std::isnan(x)) return x;
        throw std::domain_error("math domain error");
    }
    return std::log(x);
}

// Exactly rounded sum of doubles (Shewchuk's algorithm, as in math.fsum), for finite inputs.
double fsum(const std::vector<double>& xs);

// Python's a[start:stop] bounds for negative or out-of-range indices: [lo, hi) into a length n.
struct SliceBounds {
    size_t lo, hi;
};
inline SliceBounds py_slice(size_t n, int64_t start, int64_t stop) {
    auto clamp = [n](int64_t i) -> size_t {
        if (i < 0) i += static_cast<int64_t>(n);
        if (i < 0) return 0;
        if (static_cast<uint64_t>(i) > n) return n;
        return static_cast<size_t>(i);
    };
    size_t lo = clamp(start), hi = clamp(stop);
    if (hi < lo) hi = lo;
    return {lo, hi};
}

}  // namespace midfreq::engine
