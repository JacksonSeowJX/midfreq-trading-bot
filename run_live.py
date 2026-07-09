"""
Live Paper Trading Runner
=========================
Streams real-time candles from Moomoo and lets a strategy place paper
orders in the SIMULATE environment.

Requires: OpenD running + logged in, HK market hours (09:30-16:00 HKT).

Usage:
    python3 run_live.py                                   # defaults below
    python3 run_live.py --strategy "RSI" --symbols HK.00700 HK.00005
    python3 run_live.py --duration 30 --timeframe 1m
    python3 run_live.py --list                            # show strategies
"""
import sys
import argparse
sys.path.insert(0, 'src')

from core.models import Timeframe
from core.strategy import STRATEGY_REGISTRY
from core.risk_manager import RiskManager, SizingMethod, create_position_sizer
from core.order_gateway import MoomooPaperGateway
from core.live_engine import LiveTradingEngine
from providers.moomoo_provider import MoomooProvider

TIMEFRAMES = {'1m': Timeframe.MIN_1, '5m': Timeframe.MIN_5,
              '1h': Timeframe.HOUR_1, '1d': Timeframe.DAY_1}


def main():
    ap = argparse.ArgumentParser(description="Live paper trading via Moomoo")
    ap.add_argument('--strategy', default='Z-Score Mean Reversion',
                    help='Strategy name from the registry')
    ap.add_argument('--symbols', nargs='+', default=['HK.00700'],
                    help='Symbols to trade, e.g. HK.00700 HK.00005')
    ap.add_argument('--timeframe', default='1m', choices=TIMEFRAMES.keys())
    ap.add_argument('--duration', type=float, default=None,
                    help='Session length in minutes (default: until Ctrl-C)')
    ap.add_argument('--stop-loss', type=float, default=3.0,
                    help='Fixed stop-loss %% (0 disables)')
    ap.add_argument('--max-drawdown', type=float, default=10.0,
                    help='Circuit breaker halt at this drawdown %%')
    ap.add_argument('--qty', type=int, default=100,
                    help='Fixed shares per trade')
    ap.add_argument('--params', nargs='*', default=[],
                    help='Override strategy params, e.g. --params entry_z=1.0 lookback=15')
    ap.add_argument('--list', action='store_true', help='List strategies and exit')
    args = ap.parse_args()

    if args.list:
        for name, info in STRATEGY_REGISTRY.items():
            params = {k: v['default'] for k, v in info['params'].items()}
            print(f"{name:28s} defaults: {params}")
        return

    if args.strategy not in STRATEGY_REGISTRY:
        print(f"Unknown strategy '{args.strategy}'. Use --list to see options.")
        return

    info = STRATEGY_REGISTRY[args.strategy]
    strategy_params = {k: v['default'] for k, v in info['params'].items()}
    for override in args.params:
        key, _, val = override.partition('=')
        if key not in strategy_params:
            print(f"Unknown param '{key}' for {args.strategy}. Valid: {list(strategy_params)}")
            return
        default = strategy_params[key]
        strategy_params[key] = type(default)(float(val)) if isinstance(default, (int, float)) else val
    print(f"Strategy params: {strategy_params}")

    risk_manager = RiskManager(
        stop_loss_pct=(args.stop_loss / 100.0) if args.stop_loss > 0 else None,
        max_drawdown_pct=args.max_drawdown / 100.0,
        position_sizer=create_position_sizer(SizingMethod.FIXED_QUANTITY, qty=args.qty),
    )

    provider = MoomooProvider()
    gateway = MoomooPaperGateway()

    engine = LiveTradingEngine(
        provider=provider,
        gateway=gateway,
        strategy_class=info['class'],
        symbols=args.symbols,
        timeframe=TIMEFRAMES[args.timeframe],
        risk_manager=risk_manager,
        **strategy_params,
    )

    try:
        engine.run(duration_minutes=args.duration)
    finally:
        provider.close()
        gateway.close()


if __name__ == "__main__":
    main()
