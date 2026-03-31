import pandas as pd
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.strategy import MovingAverageCrossover
from core.backtester import Backtester
from core.models import Timeframe

def main():
    print("--- Mid-Frequency Strategy Engine Demo ---")
    
    # Initialize Core Components
    storage = DataStorage()
    portfolio = Portfolio(initial_cash=100000.0, commission_rate=0.001)
    backtester = Backtester(storage=storage, portfolio=portfolio)
    
    # We will test Tencent on the 1-minute historical dataset we already downloaded
    symbols_to_test = ["HK.00700"]
    timeframe = Timeframe.MIN_1
    
    # Run the backtest using our SMA crossover strategy 
    # (Fast MA 5 mins, Slow MA 20 mins)
    metrics = backtester.run(
        MovingAverageCrossover, 
        symbols=symbols_to_test, 
        timeframe=timeframe,
        fast_period=5,
        slow_period=20
    )
    
    # Output Trade Log and Final Performance
    print("\n[ Trade Log ]")
    portfolio.print_trade_log()
    
    if metrics:
        print("\n[ Final Metrics ]")
        print(f"Initial Cash:   ${metrics['initial_cash']:,.2f}")
        print(f"Final Equity:   ${metrics['final_equity']:,.2f}")
        print(f"Return:         {metrics['return_pct']:.4f}%")
        print(f"Total Trades:   {metrics['total_trades']}")
        
        # Unwind any active positions computationally at the close price to see true value
        print("\n[ Open Positions (Mark-to-Market) ]")
        if metrics['open_positions']:
            for sym, pos in metrics['open_positions'].items():
                print(f"  {sym}: {pos['qty']:,.0f} shares @ Average Entry: ${pos['entry_price']:,.2f}")
        else:
            print("  None. Portfolio represents 100% Cash.")
            
if __name__ == "__main__":
    main()
