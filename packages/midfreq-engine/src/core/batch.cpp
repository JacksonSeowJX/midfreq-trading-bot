#include <atomic>
#include <stdexcept>
#include <thread>

#include "midfreq/engine/engine.hpp"

namespace midfreq::engine {

std::vector<BatchItem> run_batch(const MarketData& data, const std::string& strategy,
                                 const std::vector<Params>& params, const BacktestConfig& config, EventRange range,
                                 unsigned threads, bool record) {
    std::vector<BatchItem> out(params.size());
    if (params.empty()) return out;
    unsigned n_threads = threads ? threads : std::max(1u, std::thread::hardware_concurrency());
    n_threads = std::min<unsigned>(n_threads, static_cast<unsigned>(params.size()));

    // each run owns its portfolio, risk manager and strategy; the market data is shared, read-only
    std::atomic<size_t> next{0};
    auto worker = [&]() {
        for (;;) {
            const size_t i = next.fetch_add(1);
            if (i >= params.size()) return;
            BatchItem& item = out[i];
            try {
                item.result = run_backtest(data, strategy, params[i], config, range, record, nullptr);
            } catch (const ZeroDivisionError& e) {
                item.error = e.what();
                item.error_type = "ZeroDivisionError";
            } catch (const std::overflow_error& e) {
                item.error = e.what();
                item.error_type = "OverflowError";
            } catch (const std::logic_error& e) {  // invalid_argument, domain_error
                item.error = e.what();
                item.error_type = "ValueError";
            } catch (const std::exception& e) {
                item.error = e.what();
                item.error_type = "RuntimeError";
            }
        }
    };
    std::vector<std::thread> pool;
    for (unsigned t = 1; t < n_threads; ++t) pool.emplace_back(worker);
    worker();
    for (auto& t : pool) t.join();
    return out;
}

}  // namespace midfreq::engine
