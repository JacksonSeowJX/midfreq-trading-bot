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
        
        # Equity curve: records (timestamp, equity_value) after each trade
        self.equity_curve: List[Dict[str, Any]] = []

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
        
        # Record equity snapshot (cash + position value at trade price)
        pos_value = sum(p['qty'] * price for p in self.positions.values())
        self.equity_curve.append({
            'timestamp': timestamp,
            'equity': self.cash + pos_value
        })

    def get_position_qty(self, symbol: str) -> float:
        return self.positions.get(symbol, {}).get('qty', 0.0)

    def calculate_metrics(self, current_prices: Dict[str, float]) -> Dict[str, Any]:
        """
        Calculate final portfolio value, equity, and advanced performance metrics:
        - Sharpe Ratio (annualized, assuming 252 trading days)
        - Maximum Drawdown (largest peak-to-trough decline)
        - Win Rate (% of profitable round-trip trades)
        - Profit Factor (gross profit / gross loss)
        """
        position_value = 0.0
        for sym, pos in self.positions.items():
            if sym in current_prices:
                position_value += pos['qty'] * current_prices[sym]

        total_equity = self.cash + position_value
        return_pct = ((total_equity - self.initial_cash) / self.initial_cash) * 100

        # --- Advanced Metrics ---
        # Win Rate & Profit Factor from paired BUY/SELL trades
        wins = 0
        losses = 0
        gross_profit = 0.0
        gross_loss = 0.0
        buy_prices: Dict[str, float] = {}  # track entry price per symbol

        for trade in self.trade_history:
            sym = trade['symbol']
            if trade['action'] == 'BUY':
                buy_prices[sym] = trade['price']
            elif trade['action'] == 'SELL' and sym in buy_prices:
                pnl = (trade['price'] - buy_prices[sym]) * trade['qty']
                if pnl > 0:
                    wins += 1
                    gross_profit += pnl
                else:
                    losses += 1
                    gross_loss += abs(pnl)
                del buy_prices[sym]

        total_completed = wins + losses
        win_rate = (wins / total_completed * 100) if total_completed > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0.0

        # Sharpe Ratio from equity curve
        sharpe_ratio = 0.0
        max_drawdown = 0.0
        if len(self.equity_curve) >= 2:
            equities = [e['equity'] for e in self.equity_curve]
            # Returns between consecutive equity snapshots
            returns = [(equities[i] - equities[i-1]) / equities[i-1] for i in range(1, len(equities)) if equities[i-1] != 0]
            if returns:
                import statistics
                mean_ret = statistics.mean(returns)
                std_ret = statistics.stdev(returns) if len(returns) > 1 else 0.0
                sharpe_ratio = (mean_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0.0

            # Max Drawdown
            peak = equities[0]
            for eq in equities:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100
                if dd > max_drawdown:
                    max_drawdown = dd

        return {
            'initial_cash': self.initial_cash,
            'final_equity': total_equity,
            'return_pct': return_pct,
            'total_trades': len(self.trade_history),
            'cash_balance': self.cash,
            'open_positions': self.positions,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
        }

    def print_trade_log(self):
        df = pd.DataFrame(self.trade_history)
        if df.empty:
            print("No trades executed.")
        else:
            print(df.to_string())
