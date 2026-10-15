// C++ unit tests for the engine core, independent of Python:
//   cmake -S packages/midfreq-engine -B build/engine -DMIDFREQ_ENGINE_TESTS=ON -DMIDFREQ_ENGINE_PYTHON=OFF
//   cmake --build build/engine && ctest --test-dir build/engine --output-on-failure
// The equivalence with the Python backtester is tested from Python (tests/test_native.py).
#include <cmath>
#include <cstdio>
#include <functional>
#include <string>
#include <vector>

#include "midfreq/engine/engine.hpp"

using namespace midfreq::engine;

namespace {

int failures = 0, checks = 0;

#define CHECK(cond)                                                                    \
    do {                                                                               \
        ++checks;                                                                      \
        if (!(cond)) {                                                                 \
            ++failures;                                                                \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                              \
    } while (0)

template <class E>
bool throws(const std::function<void()>& f) {
    try {
        f();
    } catch (const E&) {
        return true;
    } catch (...) {
    }
    return false;
}

SymbolSeries series(const std::string& name, std::vector<Timestamp> ts, std::vector<double> close) {
    SymbolSeries s;
    s.name = name;
    s.ts = std::move(ts);
    s.open = s.high = s.low = s.volume = close;
    s.close = std::move(close);
    return s;
}

void test_pysum() {
    PySum n(SumMode::Neumaier), v(SumMode::Naive);
    for (double x : {1e16, 1.0, -1e16}) {
        n.add(x);
        v.add(x);
    }
    CHECK(n.value() == 1.0);
    CHECK(v.value() == 0.0);
    CHECK(PySum(SumMode::Neumaier).value() == 0.0);
    CHECK(fsum({0.1, 0.2, 0.3}) == 0.6);
}

void test_py_helpers() {
    CHECK(py_int(2.9) == 2 && py_int(-2.9) == -2);
    CHECK(throws<std::overflow_error>([] { py_int(INFINITY); }));
    CHECK(throws<std::domain_error>([] { py_int(NAN); }));
    CHECK(py_pow(-3.0, 2.0) == 9.0 && py_pow(0.0, 0.5) == 0.0 && py_pow(4.0, 0.5) == 2.0);
    CHECK(py_max(1.0, NAN) == 1.0 && std::isnan(py_max(NAN, 1.0)));
    const SliceBounds a = py_slice(5, -3, 5), b = py_slice(5, -9, -1), c = py_slice(2, -3, -1);
    CHECK(a.lo == 2 && a.hi == 5);
    CHECK(b.lo == 0 && b.hi == 4);
    CHECK(c.lo == 0 && c.hi == 1);
    CHECK(throws<ZeroDivisionError>([] { py_div(1.0, 0.0); }));
}

void test_market_data_merge() {
    MarketData d({series("A", {1, 2, 2, 5}, {10, 11, 12, 13}), series("B", {0, 2, 3}, {20, 21, 22})});
    CHECK(d.size() == 7);
    const std::vector<Timestamp> ts = {0, 1, 2, 2, 2, 3, 5};
    const std::vector<SymbolId> sym = {1, 0, 0, 0, 1, 1, 0};
    const std::vector<double> close = {20, 10, 11, 12, 21, 22, 13};
    for (size_t i = 0; i < d.size(); ++i) {
        CHECK(d.ts(i) == ts[i]);
        CHECK(d.symbol(i) == sym[i]);
        CHECK(d.bar(i).close == close[i]);
    }
    const EventRange r = d.range(2, 5);
    CHECK(r.begin == 2 && r.end == 6);
    CHECK(d.range(std::nullopt, std::nullopt).size() == 7);
}

void test_portfolio_accounting() {
    Portfolio pf(3, 10000.0, 0.001, 10.0, SumMode::Neumaier, true);
    CHECK(pf.slippage_mult() == 0.001);
    CHECK(pf.execute_trade(0, true, 10, 100.0, 1));  // fills at 100.1, commission 1.001
    CHECK(pf.position_qty(0) == 10);
    CHECK(std::fabs(pf.entry_price(0) - 100.1) < 1e-12);
    CHECK(std::fabs(pf.cash() - (10000.0 - 1001.0 - 1.001)) < 1e-9);
    CHECK(!pf.execute_trade(1, true, 1000, 100.0, 2));  // cannot afford
    CHECK(!pf.execute_trade(2, false, 5, 50.0, 3));     // sells nothing held: rejected
    CHECK(pf.has_position_entry(2) && pf.position_qty(2) == 0);  // the empty entry stays, as in Python
    CHECK(pf.rejected() == 2);
    CHECK(pf.execute_trade(0, false, 10, 110.0, 4));
    CHECK(!pf.has_position_entry(0));
    CHECK(pf.trades().size() == 2 && pf.trade_equity().size() == 2);
    const TradeStats st = pf.trade_stats();
    CHECK(st.win_rate && *st.win_rate == 1.0 && st.avg_win && !st.avg_loss);
    CHECK(!pf.execute_trade(0, true, 0, 100.0, 5));  // zero quantity: ignored
}

void test_risk_rules() {
    RiskConfig c;
    c.stop_loss_pct = 0.05;
    c.trailing_stop_pct = 0.10;
    c.take_profit_pct = 0.20;
    RiskManager rm(c, 2);
    rm.register_entry(0, 100.0);
    CHECK(rm.check_exit_signals(0, 96.0, 100.0) == ExitReason::None);
    CHECK(rm.check_exit_signals(0, 95.0, 100.0) == ExitReason::StopLoss);
    rm.update_peak_price(0, 115.0);
    CHECK(rm.check_exit_signals(0, 103.0, 100.0) == ExitReason::TrailingStop);
    CHECK(rm.check_exit_signals(0, 120.0, 100.0) == ExitReason::TakeProfit);
    CHECK(rm.is_trading_halted(89000.0, 100000.0));
    CHECK(!rm.is_trading_halted(95000.0, 100000.0));

    SizerConfig s;
    CHECK(size_position(s, 1000.0, 50.0, std::nullopt, {}, {}, {}) == 20);  // capped by cash
    s.method = SizingMethod::FixedFractional;
    CHECK(size_position(s, 100000.0, 100.0, 95.0, {}, {}, {}) == 400);    // 2% risk / $5 a share
    s.method = SizingMethod::EqualDollar;
    s.n_positions = 4;
    CHECK(size_position(s, 100000.0, 100.0, std::nullopt, {}, {}, {}) == 249);
    s.method = SizingMethod::Kelly;
    CHECK(size_position(s, 100000.0, 100.0, std::nullopt, 0.6, 2.0, 1.0) == 200);  // half of f* = 0.4
    CHECK(size_position(s, 100000.0, 100.0, std::nullopt, 0.8, 2.0, 1.0) == 250);  // capped at 25%
    CHECK(size_position(s, 100000.0, 100.0, std::nullopt, {}, {}, {}) == 20);      // 2% fallback
}

void test_engine_runs_and_batches() {
    std::vector<Timestamp> ts;
    std::vector<double> a, b;
    for (int i = 0; i < 300; ++i) {
        ts.push_back(static_cast<Timestamp>(i) * 3'600'000'000'000LL);
        a.push_back(100.0 + 10.0 * std::sin(i / 15.0));
        b.push_back(80.0 + 8.0 * std::cos(i / 11.0));
    }
    MarketData d({series("A", ts, a), series("B", ts, b)});
    BacktestConfig cfg;
    cfg.commission_rate = 0.0005;
    Params p;
    p.set("fast_period", 5);
    p.set("slow_period", 20);
    const BacktestResult r = run_backtest(d, "sma_crossover", p, cfg, d.range(std::nullopt, std::nullopt));
    CHECK(r.ok && r.total_events == 600 && !r.trades.empty());
    CHECK(r.equity.size() == 300);  // one point per timestamp
    CHECK(r.last_ts && *r.last_ts == ts.back());
    CHECK(r.metrics.total_trades == r.trades.size());
    // buys and sells alternate per symbol, and cash only moves on trades
    double cash = cfg.initial_cash;
    for (const Trade& t : r.trades) {
        const double value = static_cast<double>(t.qty) * t.price;
        cash += t.is_buy ? -(value + t.commission) : (value - t.commission);
        CHECK(std::fabs(cash - t.cash_after) < 1e-6);
    }
    CHECK(!run_backtest(d, "sma_crossover", p, cfg, d.range(1LL << 62, std::nullopt)).ok);
    CHECK(throws<std::invalid_argument>([&] { run_backtest(d, "nope", p, cfg, d.range(std::nullopt, std::nullopt)); }));

    std::vector<Params> grid;
    for (int f : {3, 5, 8, 13}) {
        Params q;
        q.set("fast_period", f);
        q.set("slow_period", 30);
        grid.push_back(q);
    }
    Params bad;
    bad.set("fast_period", 0);
    grid.push_back(bad);
    const auto out = run_batch(d, "sma_crossover", grid, cfg, d.range(std::nullopt, std::nullopt), 3);
    CHECK(out.size() == 5);
    CHECK(out[4].error_type == "ValueError");
    for (size_t i = 0; i < 4; ++i) {
        const BacktestResult one = run_backtest(d, "sma_crossover", grid[i], cfg, d.range(std::nullopt, std::nullopt));
        CHECK(out[i].error.empty());
        CHECK(out[i].result.metrics.final_equity == one.metrics.final_equity);
        CHECK(out[i].result.metrics.total_trades == one.metrics.total_trades);
    }
}

void test_cross_sectional_holds_top_n() {
    std::vector<SymbolSeries> all;
    for (int k = 0; k < 6; ++k) {
        std::vector<Timestamp> ts;
        std::vector<double> c;
        for (int i = 0; i < 200; ++i) {
            ts.push_back(i);
            c.push_back(50.0 + k + 5.0 * std::sin((i + 7 * k) / 9.0));
        }
        all.push_back(series("S" + std::to_string(k), ts, c));
    }
    MarketData d(std::move(all));
    BacktestConfig cfg;
    Params p;
    p.set("lookback", 5);
    p.set("top_n", 2);
    p.set("rebalance_every", 3);
    const BacktestResult r = run_backtest(d, "cross_sectional_reversal", p, cfg, d.range(std::nullopt, std::nullopt));
    CHECK(r.ok && !r.trades.empty());
    CHECK(r.positions.size() <= 2);
    for (const Trade& t : r.trades)
        if (!t.is_buy) CHECK(t.reason == ExitReason::Rebalance);
}

}  // namespace

int main() {
    test_pysum();
    test_py_helpers();
    test_market_data_merge();
    test_portfolio_accounting();
    test_risk_rules();
    test_engine_runs_and_batches();
    test_cross_sectional_holds_top_n();
    std::printf("%d checks, %d failed\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
