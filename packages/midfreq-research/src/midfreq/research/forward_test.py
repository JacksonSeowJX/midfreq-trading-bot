"""
The S&P 100 forward test, read back from its recorded session logs.

The live view could only show this test through the broker: the paper
account's total (about US$1,000,000, of which the test trades US$100,000)
and, when OpenD is running, its positions and orders. Without OpenD, which
is how anyone but the VM sees it, the trades, the holdings and the profit
on the US$100,000 were not visible anywhere.

Everything here comes from `live_sessions/session_*.jsonl`, which the VM
commits daily, so it works from a fresh clone:

  profit / loss   the broker's reported account total at each session's
                  start and end, minus the total when the test began. The
                  test is the only activity on the US account, so the
                  change in the account IS the test's result, net of fees
                  and including any dividends the broker credits.
  trades          every fill the engine booked, with the profit realised
                  on each sale (before fees).
  holdings        the positions recorded at the end of the latest session,
                  valued at that session's last recorded candle.
"""
import json
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

STRATEGY_PREFIX = 'CrossSectional'


def _events(path: Path):
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue                      # a line cut short by a crash
    return out


def load(session_dir, capital: float = 100_000.0, market: str = 'US') -> Optional[Dict[str, Any]]:
    """Summary, equity series, trades and holdings of the forward test, or None if no session exists."""
    sessions = []
    for f in sorted(Path(session_dir).glob('session_*.jsonl')):
        ev = _events(f)
        start = next((e for e in ev if e.get('type') == 'session_start'), None)
        if not start or not str(start.get('strategy', '')).startswith(STRATEGY_PREFIX):
            continue
        syms = start.get('symbols') or []
        if not syms or syms[0].split('.')[0].upper() != market:
            continue
        sessions.append((f, ev, start))
    if not sessions:
        return None

    baseline = None
    points, trade_rows, book = [], [], {}
    holdings_src, last_closes, as_of = None, {}, None
    for f, ev, start in sessions:
        day = pd.to_datetime(f.stem.split('_')[1], format='%Y%m%d')     # session date, New York
        for e in ev:
            t = e.get('type')
            total = (e.get('account') or {}).get('total_assets')
            if t in ('session_start', 'session_end') and total:
                if baseline is None:
                    baseline = float(total)
                ts = pd.to_datetime(e.get('timestamp'), errors='coerce')
                points.append({'timestamp': ts, 'pnl': float(total) - baseline})
                as_of = ts
            if t == 'trade':
                sym, qty, px = e['symbol'], float(e['qty']), float(e['price'])
                row = {'Date': day.date(), 'Decided on candle': str(e.get('timestamp'))[:16],
                       'Side': e['side'], 'Stock': sym, 'Shares': qty, 'Price': px,
                       'Value': qty * px, 'Realised P&L': None, 'Realised %': None}
                pos = book.setdefault(sym, {'qty': 0.0, 'cost': 0.0})
                if e['side'] == 'BUY':
                    pos['cost'] = (pos['qty'] * pos['cost'] + qty * px) / (pos['qty'] + qty)
                    pos['qty'] += qty
                else:
                    if pos['cost'] > 0:
                        row['Realised P&L'] = (px - pos['cost']) * qty
                        row['Realised %'] = (px / pos['cost'] - 1) * 100
                    pos['qty'] = max(0.0, pos['qty'] - qty)
                trade_rows.append(row)
            if t == 'candle_close' and e.get('close') is not None:
                last_closes[e['symbol']] = float(e['close'])
            if t == 'session_end' and e.get('positions') is not None:
                holdings_src = e['positions']

    equity = (pd.DataFrame(points).dropna().drop_duplicates('timestamp').sort_values('timestamp')
              if points else pd.DataFrame(columns=['timestamp', 'pnl']))
    if len(equity):
        equity['value'] = capital + equity['pnl']
    trades = pd.DataFrame(trade_rows)

    hold_rows = []
    for sym, p in (holdings_src or {}).items():
        qty, entry = float(p['qty']), float(p['entry_price'])
        last = last_closes.get(sym, entry)
        hold_rows.append({'Stock': sym, 'Shares': qty, 'Bought at': entry, 'Last price': last,
                          'Value': qty * last, 'Unrealised P&L': (last - entry) * qty,
                          'Unrealised %': (last / entry - 1) * 100 if entry else 0.0})
    holdings = pd.DataFrame(hold_rows)

    pnl = float(equity['pnl'].iloc[-1]) if len(equity) else 0.0
    rebalances = int(trades.groupby('Date').ngroups) if len(trades) else 0
    return {
        'capital': capital,
        'pnl': pnl,
        'pnl_pct': pnl / capital * 100 if capital else 0.0,
        'value': capital + pnl,
        'sessions': len(sessions),
        'rebalances': rebalances,
        'orders': len(trades),
        'realised': float(trades['Realised P&L'].dropna().sum()) if len(trades) else 0.0,
        'started': sessions[0][0].stem.split('_')[1],
        'first_trade': str(trades['Date'].iloc[0]) if len(trades) else None,
        'as_of': as_of,
        'equity': equity,
        'trades': trades,
        'holdings': holdings,
    }
