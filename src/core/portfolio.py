from typing import Dict, List, Any
from datetime import datetime
import pandas as pd

class Portfolio:
    """
    Simulated portfolio tracking cash, active positions, and trade history.
    """
    def __init__(self, initial_cash: float = 100000.0, commission_rate: float = 0.001):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.commission_rate = commission_rate  # E.g. 0.1% per trade
        
        # Format: { 'HK.00700': {'qty': 1000, 'entry_price': 480.0} }
        self.positions: Dict[str, Dict[str, float]] = {}
        
        # Trade history for metrics
        self.trade_history: List[Dict[str, Any]] = []

    def execute_trade(self, symbol: str, is_buy: bool, qty: float, price: float, timestamp: datetime):
        """
        Executes a simulated market order.
        """
        if qty <= 0:
            return

        trade_value = qty * price
        commission = trade_value * self.commission_rate
        total_cost = trade_value + commission if is_buy else trade_value - commission

        if is_buy and self.cash < total_cost:
            print(f"[{timestamp}] REJECTED BUY {qty} {symbol} @ {price}: Insufficient cash ({self.cash} < {total_cost})")
            return

        # Update cash
        self.cash += -total_cost if is_buy else total_cost

        # Update positions
        if symbol not in self.positions:
            self.positions[symbol] = {'qty': 0, 'entry_price': 0.0}
            
        pos = self.positions[symbol]
        
        if is_buy:
            # Calculate new average entry price
            new_qty = pos['qty'] + qty
            # Standard weighted average calculation
            pos['entry_price'] = ((pos['qty'] * pos['entry_price']) + (qty * price)) / new_qty
            pos['qty'] = new_qty
        else:
            # Selling
            if pos['qty'] < qty:
                print(f"[{timestamp}] REJECTED SELL {qty} {symbol}: Insufficient qty (hold {pos['qty']})")
                # Revert cash (abandon trade)
                self.cash -= total_cost
                return
                
            pos['qty'] -= qty
            # If closed out completely, reset entry price
            if pos['qty'] == 0:
                pos['entry_price'] = 0.0
                del self.positions[symbol]

        # Log trade
        self.trade_history.append({
            'timestamp': timestamp,
            'symbol': symbol,
            'action': 'BUY' if is_buy else 'SELL',
            'qty': qty,
            'price': price,
            'commission': commission,
            'cash_after': self.cash
        })

    def get_position_qty(self, symbol: str) -> float:
        return self.positions.get(symbol, {}).get('qty', 0.0)

    def calculate_metrics(self, current_prices: Dict[str, float]) -> Dict[str, Any]:
        """
        Calculate final portfolio value and equity using latest known prices.
        """
        position_value = 0.0
        for sym, pos in self.positions.items():
            if sym in current_prices:
                position_value += pos['qty'] * current_prices[sym]

        total_equity = self.cash + position_value
        return_pct = ((total_equity - self.initial_cash) / self.initial_cash) * 100

        return {
            'initial_cash': self.initial_cash,
            'final_equity': total_equity,
            'return_pct': return_pct,
            'total_trades': len(self.trade_history),
            'cash_balance': self.cash,
            'open_positions': self.positions
        }

    def print_trade_log(self):
        df = pd.DataFrame(self.trade_history)
        if df.empty:
            print("No trades executed.")
        else:
            print(df.to_string())
