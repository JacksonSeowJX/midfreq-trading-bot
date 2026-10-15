// midfreq.engine._core: the pybind11 module over the C++ backtest core.
//
// Data crosses the boundary as numpy arrays, once per MarketData; runs release the GIL, and
// run_batch spreads parameter sets over threads.
#include <pybind11/functional.h>
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <thread>

#include "midfreq/engine/engine.hpp"

namespace py = pybind11;
using namespace midfreq::engine;

#ifndef MIDFREQ_ENGINE_VERSION
#define MIDFREQ_ENGINE_VERSION "dev"
#endif

namespace {

template <class T>
std::vector<T> to_vector(const py::array_t<T, py::array::c_style | py::array::forcecast>& a) {
    if (a.ndim() != 1) throw std::invalid_argument("expected a one-dimensional array");
    return std::vector<T>(a.data(), a.data() + a.size());
}

template <class T>
py::array_t<T> to_array(const std::vector<T>& v) {
    return py::array_t<T>(static_cast<py::ssize_t>(v.size()), v.data());
}

SumMode parse_sum_mode(const std::string& s) {
    if (s == "neumaier") return SumMode::Neumaier;
    if (s == "naive") return SumMode::Naive;
    throw std::invalid_argument("sum_mode must be 'neumaier' or 'naive'");
}

std::optional<double> opt_double(const py::dict& d, const char* key) {
    if (!d.contains(key) || d[key].is_none()) return std::nullopt;
    return d[key].cast<double>();
}

std::optional<RiskConfig> parse_risk(const py::object& obj) {
    if (obj.is_none()) return std::nullopt;
    const py::dict d = obj.cast<py::dict>();
    RiskConfig r;
    r.stop_loss_pct = opt_double(d, "stop_loss_pct");
    r.trailing_stop_pct = opt_double(d, "trailing_stop_pct");
    r.take_profit_pct = opt_double(d, "take_profit_pct");
    if (d.contains("max_drawdown_pct")) r.max_drawdown_pct = d["max_drawdown_pct"].cast<double>();
    if (d.contains("sizer")) {
        const py::dict s = d["sizer"].cast<py::dict>();
        const std::string m = s["method"].cast<std::string>();
        if (m == "fixed_quantity") {
            r.sizer.method = SizingMethod::FixedQuantity;
        } else if (m == "fixed_fractional") {
            r.sizer.method = SizingMethod::FixedFractional;
        } else if (m == "kelly") {
            r.sizer.method = SizingMethod::Kelly;
        } else if (m == "equal_dollar") {
            r.sizer.method = SizingMethod::EqualDollar;
        } else {
            throw std::invalid_argument("unknown sizing method " + m);
        }
        if (s.contains("qty")) r.sizer.qty = s["qty"].cast<int64_t>();
        if (s.contains("risk_pct")) r.sizer.risk_pct = s["risk_pct"].cast<double>();
        if (s.contains("fraction")) r.sizer.fraction = s["fraction"].cast<double>();
        if (s.contains("max_pct")) r.sizer.max_pct = s["max_pct"].cast<double>();
        if (s.contains("n_positions")) r.sizer.n_positions = s["n_positions"].cast<int64_t>();
        if (s.contains("cost_buffer")) r.sizer.cost_buffer = s["cost_buffer"].cast<double>();
    }
    return r;
}

Params parse_params(const py::dict& d) {
    Params p;
    for (auto item : d) {
        const auto key = item.first.cast<std::string>();
        if (py::isinstance<py::bool_>(item.second)) {
            p.set(key, item.second.cast<bool>() ? 1.0 : 0.0);
        } else {
            p.set(key, item.second.cast<double>());
        }
    }
    return p;
}

BacktestConfig make_config(double initial_cash, double commission_rate, double slippage_bps,
                           const std::string& sum_mode, const py::object& risk) {
    BacktestConfig c;
    c.initial_cash = initial_cash;
    c.commission_rate = commission_rate;
    c.slippage_bps = slippage_bps;
    c.sum_mode = parse_sum_mode(sum_mode);
    c.risk = parse_risk(risk);
    return c;
}

py::dict metrics_dict(const Metrics& m) {
    py::dict d;
    d["initial_cash"] = m.initial_cash;
    d["final_equity"] = m.final_equity;
    d["return_pct"] = m.return_pct;
    d["total_trades"] = m.total_trades;
    d["cash_balance"] = m.cash_balance;
    d["sharpe_ratio"] = m.sharpe_ratio;
    d["max_drawdown"] = m.max_drawdown;
    d["win_rate"] = m.win_rate;
    d["profit_factor"] = m.profit_factor;
    d["benchmark_return_pct"] = m.benchmark_return_pct;
    d["alpha"] = m.alpha;
    return d;
}

py::dict curve_dict(const std::vector<EquityPoint>& curve) {
    std::vector<int64_t> ts(curve.size());
    std::vector<double> eq(curve.size());
    for (size_t i = 0; i < curve.size(); ++i) {
        ts[i] = curve[i].ts;
        eq[i] = curve[i].equity;
    }
    py::dict d;
    d["ts"] = to_array(ts);
    d["equity"] = to_array(eq);
    return d;
}

py::dict result_dict(const BacktestResult& r, bool record) {
    py::dict d;
    d["ok"] = r.ok;
    d["total_events"] = r.total_events;
    d["rejected_orders"] = r.rejected_orders;
    d["last_ts"] = r.last_ts ? py::cast(*r.last_ts) : py::none();
    if (!r.ok) return d;
    d["metrics"] = metrics_dict(r.metrics);
    d["daily_returns"] = to_array(r.metrics.daily_returns);
    d["cash"] = r.cash;
    py::list positions;
    for (const auto& p : r.positions) positions.append(py::make_tuple(p.symbol, p.qty, p.entry_price));
    d["positions"] = positions;
    if (!record) return d;

    const size_t n = r.trades.size();
    std::vector<int64_t> ts(n), qty(n);
    std::vector<int32_t> sym(n);
    std::vector<uint8_t> buy(n), reason(n);
    std::vector<double> price(n), commission(n), cash_after(n);
    for (size_t i = 0; i < n; ++i) {
        const Trade& t = r.trades[i];
        ts[i] = t.ts;
        sym[i] = t.symbol;
        buy[i] = t.is_buy ? 1 : 0;
        qty[i] = t.qty;
        price[i] = t.price;
        commission[i] = t.commission;
        cash_after[i] = t.cash_after;
        reason[i] = static_cast<uint8_t>(t.reason);
    }
    py::dict trades;
    trades["ts"] = to_array(ts);
    trades["symbol"] = to_array(sym);
    trades["is_buy"] = to_array(buy);
    trades["qty"] = to_array(qty);
    trades["price"] = to_array(price);
    trades["commission"] = to_array(commission);
    trades["cash_after"] = to_array(cash_after);
    trades["reason"] = to_array(reason);
    d["trades"] = trades;
    d["equity"] = curve_dict(r.equity);
    d["trade_equity"] = curve_dict(r.trade_equity);
    d["last_price"] = to_array(r.last_price);
    std::vector<uint8_t> seen(r.seen.begin(), r.seen.end());
    d["seen"] = to_array(seen);
    return d;
}

}  // namespace

PYBIND11_MODULE(_core, m) {
    m.doc() = "The native (C++17) backtest core of midfreq: market data, portfolio, risk, strategies, engine";
    m.attr("__version__") = MIDFREQ_ENGINE_VERSION;

    py::register_exception_translator([](std::exception_ptr p) {
        try {
            if (p) std::rethrow_exception(p);
        } catch (const ZeroDivisionError& e) {
            PyErr_SetString(PyExc_ZeroDivisionError, e.what());
        }
    });

    py::class_<MarketData, std::shared_ptr<MarketData>>(m, "MarketData",
        "Every symbol's bars merged once into one time-ordered stream (a stable sort, ties in symbol order).")
        .def(py::init([](const std::vector<std::string>& names, const std::vector<py::array_t<int64_t>>& ts,
                         const std::vector<py::array_t<double>>& open, const std::vector<py::array_t<double>>& high,
                         const std::vector<py::array_t<double>>& low, const std::vector<py::array_t<double>>& close,
                         const std::vector<py::array_t<double>>& volume) {
                 const size_t k = names.size();
                 if (ts.size() != k || open.size() != k || high.size() != k || low.size() != k ||
                     close.size() != k || volume.size() != k)
                     throw std::invalid_argument("one array per symbol is needed in every column");
                 std::vector<SymbolSeries> series(k);
                 for (size_t i = 0; i < k; ++i) {
                     series[i].name = names[i];
                     series[i].ts = to_vector<int64_t>(ts[i]);
                     series[i].open = to_vector<double>(open[i]);
                     series[i].high = to_vector<double>(high[i]);
                     series[i].low = to_vector<double>(low[i]);
                     series[i].close = to_vector<double>(close[i]);
                     series[i].volume = to_vector<double>(volume[i]);
                 }
                 py::gil_scoped_release release;
                 return std::make_shared<MarketData>(std::move(series));
             }),
             py::arg("symbols"), py::arg("ts"), py::arg("open"), py::arg("high"), py::arg("low"), py::arg("close"),
             py::arg("volume"))
        .def_property_readonly("symbols", &MarketData::symbols)
        .def("__len__", &MarketData::size)
        .def("range", [](const MarketData& d, std::optional<Timestamp> start, std::optional<Timestamp> cutoff) {
                 const EventRange r = d.range(start, cutoff);
                 return py::make_tuple(r.begin, r.end);
             },
             py::arg("start") = py::none(), py::arg("cutoff") = py::none(),
             "The [begin, end) slice of events with start <= ts < cutoff.")
        .def("timestamps", [](const MarketData& d) {
            std::vector<int64_t> ts(d.size());
            for (size_t i = 0; i < d.size(); ++i) ts[i] = d.ts(i);
            return to_array(ts);
        });

    m.def(
        "run_backtest",
        [](const MarketData& data, const std::string& strategy, const py::dict& params, double initial_cash,
           double commission_rate, double slippage_bps, const std::string& sum_mode, const py::object& risk,
           std::optional<Timestamp> start, std::optional<Timestamp> cutoff, bool record, py::object progress) {
            const BacktestConfig config = make_config(initial_cash, commission_rate, slippage_bps, sum_mode, risk);
            const Params p = parse_params(params);
            const EventRange range = data.range(start, cutoff);
            Progress cb;
            if (!progress.is_none()) {
                cb = [progress](double v) {
                    py::gil_scoped_acquire gil;
                    progress(v);
                };
            }
            BacktestResult r;
            {
                py::gil_scoped_release release;
                r = run_backtest(data, strategy, p, config, range, record, cb);
            }
            return result_dict(r, record);
        },
        py::arg("data"), py::arg("strategy"), py::arg("params"), py::kw_only(), py::arg("initial_cash"),
        py::arg("commission_rate"), py::arg("slippage_bps") = 0.0, py::arg("sum_mode") = "neumaier",
        py::arg("risk") = py::none(), py::arg("start") = py::none(), py::arg("cutoff") = py::none(),
        py::arg("record") = true, py::arg("progress") = py::none(),
        "One backtest over the events with start <= ts < cutoff.");

    m.def(
        "run_batch",
        [](const MarketData& data, const std::string& strategy, const std::vector<py::dict>& params_list,
           double initial_cash, double commission_rate, double slippage_bps, const std::string& sum_mode,
           const py::object& risk, std::optional<Timestamp> start, std::optional<Timestamp> cutoff,
           unsigned threads) {
            const BacktestConfig config = make_config(initial_cash, commission_rate, slippage_bps, sum_mode, risk);
            std::vector<Params> params;
            params.reserve(params_list.size());
            for (const auto& d : params_list) params.push_back(parse_params(d));
            const EventRange range = data.range(start, cutoff);
            std::vector<BatchItem> items;
            {
                py::gil_scoped_release release;
                items = run_batch(data, strategy, params, config, range, threads, false);
            }
            py::list out;
            for (const auto& it : items) {
                py::dict d = result_dict(it.result, false);
                d["error"] = it.error.empty() ? py::none() : py::cast(it.error);
                d["error_type"] = it.error_type.empty() ? py::none() : py::cast(it.error_type);
                out.append(d);
            }
            return out;
        },
        py::arg("data"), py::arg("strategy"), py::arg("params"), py::kw_only(), py::arg("initial_cash"),
        py::arg("commission_rate"), py::arg("slippage_bps") = 0.0, py::arg("sum_mode") = "neumaier",
        py::arg("risk") = py::none(), py::arg("start") = py::none(), py::arg("cutoff") = py::none(),
        py::arg("threads") = 0,
        "Every parameter set over the same events, in parallel; results in order, errors reported per run.");

    m.def("strategies", &strategy_names, "The strategies implemented natively.");

    // the arithmetic contract with CPython, exposed for the tests
    m.def("_sum", [](const std::vector<double>& xs, const std::string& mode) {
        PySum s(parse_sum_mode(mode));
        for (double x : xs) s.add(x);
        return s.value();
    });
    m.def("_fsum", [](const std::vector<double>& xs) { return fsum(xs); });
    m.def("_pow", [](double x, double y) { return py_pow(x, y); });
    m.def("_int", [](double x) { return py_int(x); });
    m.def("exit_reasons", []() {
        std::vector<std::string> names;
        for (int r = 0; r <= static_cast<int>(ExitReason::TakeProfit); ++r)
            names.emplace_back(exit_reason_name(static_cast<ExitReason>(r)));
        return names;
    });
    m.def("build_info", []() {
        py::dict d;
#if defined(__clang__)
        d["compiler"] = std::string("clang ") + __clang_version__;
#elif defined(__GNUC__)
        d["compiler"] = std::string("gcc ") + __VERSION__;
#elif defined(_MSC_VER)
        d["compiler"] = std::string("msvc ") + std::to_string(_MSC_VER);
#endif
        d["cxx_standard"] = static_cast<long>(__cplusplus);
        d["threads"] = std::thread::hardware_concurrency();
        return d;
    });
}
