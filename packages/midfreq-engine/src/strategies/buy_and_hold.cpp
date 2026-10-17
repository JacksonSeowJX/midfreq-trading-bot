#include "strategies.hpp"

namespace midfreq::engine {

namespace {

RiskConfig overlay_config(const Params& p) {
    RiskConfig c;
    const double stop = p.get("stop_loss_pct", 0.0), trail = p.get("trailing_stop_pct", 0.0);
    if (stop > 0) c.stop_loss_pct = stop;
    if (trail > 0) c.trailing_stop_pct = trail;
    return c;  // max drawdown 10%, a fixed 100 shares: RiskManager's defaults
}

}  // namespace

BuyAndHold::BuyAndHold(StrategyContext ctx, const Params& p)
    : Strategy(ctx), overlay_(overlay_config(p), n_symbols_), done_(n_symbols_, 0) {
    risk_ = &overlay_;
}

void BuyAndHold::on_bar(SymbolId s, const Bar& bar) {
    if (check_risk_exits(s, bar)) {
        done_[s] = 1;
        return;
    }
    if (done_[s]) return;
    if (pf_.position_qty(s) == 0) {
        const int64_t qty = trade_qty(s, bar.close);
        if (qty > 0) enter(s, qty, bar.close, bar.ts);
        done_[s] = 1;
    }
}

}  // namespace midfreq::engine
