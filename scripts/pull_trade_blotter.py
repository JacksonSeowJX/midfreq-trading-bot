"""
Pull the full order history straight from Moomoo, the authoritative
source, and save it as a permanent, git-tracked trade blotter. Session
logs never recorded individual fills before the on_trade wiring added
in live_engine.py, so this backfills everything traded up to that point;
re-running it after that just re-confirms the broker and the session
logs agree.

Usage:
    python3 scripts/pull_trade_blotter.py [days]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd
from core.order_gateway import MoomooPaperGateway

KEEP_COLS = ['create_time', 'updated_time', 'code', 'stock_name', 'trd_side',
             'order_status', 'qty', 'price', 'dealt_qty', 'dealt_avg_price', 'order_id']


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    gw = MoomooPaperGateway()
    orders = gw.list_recent_orders(days=days)
    gw.close()

    df = pd.DataFrame(orders)
    df = df[df['order_status'] == 'FILLED_ALL'][KEEP_COLS]
    df = df.sort_values('create_time').reset_index(drop=True)

    out = Path(__file__).resolve().parent.parent / 'live_sessions' / 'trade_blotter.csv'
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} filled trades to {out}")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
