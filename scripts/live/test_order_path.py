"""
Prove the live order path against the REAL moomoo paper broker.

The end-to-end replay (replay_live_sessions.py) exercises the engine against
a simulated broker; this exercises the one piece it cannot: the real API
calls inside MoomooPaperGateway.place_and_confirm. Three cases:

  1. a marketable BUY  -> must report FILLED, with dealt qty and price
  2. a marketable SELL -> must report FILLED and flatten the position
  3. a BUY priced far below the market -> must NOT fill, must be CANCELLED,
     and must report dealt_qty 0, leaving no live order behind

Run during HK market hours on the HK paper account, which is flat and not
used by the live forward test. Refuses to start if the symbol is already
held. Ends by verifying the account holds nothing and has no open orders.

Usage:
    python3 scripts/live/test_order_path.py [SYMBOL] [QTY]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from moomoo import OpenQuoteContext, TrdMarket, TrdEnv, RET_OK
from core.order_gateway import MoomooPaperGateway

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else 'HK.00700'
QTY = int(sys.argv[2]) if len(sys.argv) > 2 else 100


def main():
    q = OpenQuoteContext(host='127.0.0.1', port=11111)
    ret, snap = q.get_market_snapshot([SYMBOL]); q.close()
    if ret != RET_OK:
        print("snapshot failed:", snap); return 1
    px = float(snap['last_price'][0])

    gw = MoomooPaperGateway(trd_market=TrdMarket.HK)
    held = (gw.get_positions() or {}).get(SYMBOL, {}).get('qty', 0)
    if held:
        print(f"ABORT: {SYMBOL} already held ({held}); not disturbing it"); gw.close(); return 1
    print(f"{SYMBOL} last {px:.2f}\n")
    ok = True

    r = gw.place_and_confirm(SYMBOL, True, QTY, px)
    print(f"1. marketable BUY : ok={r['ok']} dealt={r['dealt_qty']} @ {r['dealt_price']} "
          f"limit {r['limit']} status={r['message']}")
    ok &= r['ok'] and r['dealt_qty'] == QTY

    time.sleep(2)
    r = gw.place_and_confirm(SYMBOL, False, QTY, px)
    print(f"2. marketable SELL: ok={r['ok']} dealt={r['dealt_qty']} @ {r['dealt_price']} "
          f"limit {r['limit']} status={r['message']}")
    ok &= r['ok'] and r['dealt_qty'] == QTY

    time.sleep(2)
    r = gw.place_and_confirm(SYMBOL, True, QTY, px * 0.5, wait_s=8)
    print(f"3. unfillable BUY : ok={r['ok']} dealt={r['dealt_qty']} limit {r['limit']} "
          f"status={r['message']}   (expect not filled, CANCELLED)")
    ok &= (not r['ok']) and r['dealt_qty'] == 0 and 'CANCEL' in str(r['message']).upper()

    time.sleep(2)
    held = (gw.get_positions() or {}).get(SYMBOL, {}).get('qty', 0)
    ctx = gw._get_context()
    ret, orders = ctx.order_list_query(trd_env=TrdEnv.SIMULATE, refresh_cache=True)
    live = [] if ret != RET_OK else [o for _, o in orders.iterrows()
                                     if o['code'] == SYMBOL and str(o['order_status']) in
                                     ('SUBMITTED', 'SUBMITTING', 'WAITING_SUBMIT', 'FILLED_PART')]
    print(f"\nafter: {SYMBOL} held={held}, live orders left={len(live)}")
    ok &= held == 0 and not live
    gw.close()
    print("\nORDER PATH VERIFIED" if ok else "\n*** ORDER PATH FAILED — do not run live ***")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
