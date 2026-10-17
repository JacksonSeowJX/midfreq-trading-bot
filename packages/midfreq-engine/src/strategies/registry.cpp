#include <functional>
#include <map>
#include <stdexcept>

#include "strategies.hpp"

namespace midfreq::engine {

namespace {

using Factory = std::function<std::unique_ptr<Strategy>(StrategyContext, const Params&)>;

const std::map<std::string, Factory>& registry() {
    static const std::map<std::string, Factory> r = {
        {"sma_crossover", [](StrategyContext c, const Params& p) { return std::make_unique<SmaCrossover>(c, p); }},
        {"rsi", [](StrategyContext c, const Params& p) { return std::make_unique<Rsi>(c, p); }},
        {"macd", [](StrategyContext c, const Params& p) { return std::make_unique<Macd>(c, p); }},
        {"bollinger_bands", [](StrategyContext c, const Params& p) { return std::make_unique<Bollinger>(c, p); }},
        {"zscore_mean_reversion", [](StrategyContext c, const Params& p) { return std::make_unique<ZScore>(c, p); }},
        {"pairs_trading", [](StrategyContext c, const Params& p) { return std::make_unique<Pairs>(c, p); }},
        {"ensemble_voting", [](StrategyContext c, const Params& p) { return std::make_unique<Ensemble>(c, p); }},
        {"regime_switch", [](StrategyContext c, const Params& p) { return std::make_unique<RegimeSwitch>(c, p); }},
        {"cross_sectional_reversal",
         [](StrategyContext c, const Params& p) { return std::make_unique<CrossSectional>(c, p, false); }},
        {"cross_sectional_momentum",
         [](StrategyContext c, const Params& p) { return std::make_unique<CrossSectional>(c, p, true); }},
        {"buy_and_hold_risk_overlay",
         [](StrategyContext c, const Params& p) { return std::make_unique<BuyAndHold>(c, p); }},
    };
    return r;
}

}  // namespace

std::unique_ptr<Strategy> make_strategy(const std::string& name, const Params& params, StrategyContext ctx) {
    const auto& r = registry();
    auto it = r.find(name);
    if (it == r.end()) throw std::invalid_argument("no native strategy named " + name);
    return it->second(ctx, params);
}

std::vector<std::string> strategy_names() {
    std::vector<std::string> names;
    for (const auto& kv : registry()) names.push_back(kv.first);
    return names;
}

}  // namespace midfreq::engine
