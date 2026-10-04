"""
Close HK paper positions that no live session is managing any more.

Pausing the HK roster (to free the 100-unit subscription quota for the
S&P 100 forward test) left its open positions with nothing running to
exit them: the stop-loss and take-profit live inside a session, so with
no session they can never fire. HK.09888 was sitting at +6.76% against a
--take-profit of 5%, i.e. already past the level a running session would
have sold at.

Leaving it would put unmanaged P&L into the HK track record, attributable
to no strategy decision. This closes what the roster would have closed.

Only sells. Never opens a position, and never touches a symbol passed in
--keep. Paper environment only.

Usage:
    python3 scripts/live/flatten_hk_orphans.py [--dry-run] [--keep HK.00700 ...]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from moomoo import (OpenSecTradeContext, OpenQuoteContext, TrdEnv, TrdMarket,
                    TrdSide, OrderType, SecurityFirm, RET_OK)

FILL_WAIT = 90


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--keep', nargs='*', default=[])
    args = ap.parse_args()

    quote = OpenQuoteContext(host='127.0.0.1', port=11111)
    trd = OpenSecTradeContext(filter_trdmarket=TrdMarket.HK, host='127.0.0.1',
                              port=11111, security_firm=SecurityFirm.FUTUSECURITIES)

    ret, pos = trd.position_list_query(trd_env=TrdEnv.SIMULATE)
    if ret != RET_OK:
        print("position query failed:", pos)
        quote.close(); trd.close(); return
    if pos.empty:
        print("HK paper account is already flat — nothing to do.")
        quote.close(); trd.close(); return

    print(f"{len(pos)} open HK position(s):")
    todo = []
    for _, r in pos.iterrows():
        code, qty = r['code'], float(r['qty'])
        pl = float(r.get('pl_val', 0)); plr = float(r.get('pl_ratio', 0))
        keep = code in args.keep
        print(f"   {code}: qty={qty:.0f} P&L={pl:+,.2f} ({plr:+.2f}%)"
              + ("   KEEPING (in --keep)" if keep else ""))
        if not keep and qty > 0:
            todo.append((code, qty))

    if not todo:
        print("nothing to close.")
        quote.close(); trd.close(); return
    if args.dry_run:
        print(f"\n--dry-run: would sell {len(todo)} position(s), placing no orders.")
        quote.close(); trd.close(); return

    for code, qty in todo:
        ret, snap = quote.get_market_snapshot([code])
        if ret != RET_OK:
            print(f"  {code}: snapshot failed, skipping ({snap})")
            continue
        px = float(snap['last_price'][0])
        # Price through the bid so a market-hours sell fills promptly.
        ret, data = trd.place_order(price=round(px * 0.97, 2), qty=qty, code=code,
                                   trd_side=TrdSide.SELL, order_type=OrderType.NORMAL,
                                   trd_env=TrdEnv.SIMULATE)
        if ret != RET_OK:
            print(f"  {code}: SELL rejected — {data}")
            continue
        oid = data['order_id'][0]
        print(f"  {code}: SELL {qty:.0f} submitted at {px*0.97:.2f} (last {px:.2f})")
        for _ in range(FILL_WAIT // 3):
            r2, orders = trd.order_list_query(trd_env=TrdEnv.SIMULATE)
            if r2 == RET_OK and not orders.empty:
                row = orders[orders.order_id == oid]
                if not row.empty and str(row.iloc[0]['order_status']).startswith('FILLED'):
                    print(f"     filled {float(row.iloc[0]['dealt_qty']):.0f} "
                          f"@ {float(row.iloc[0]['dealt_avg_price']):.4f}")
                    break
            time.sleep(3)
        else:
            print(f"     did not fill within {FILL_WAIT}s")

    time.sleep(3)
    ret, pos2 = trd.position_list_query(trd_env=TrdEnv.SIMULATE)
    remaining = 0 if (ret == RET_OK and pos2.empty) else (len(pos2) if ret == RET_OK else -1)
    print(f"\nremaining HK positions: {remaining}"
          + ("   ACCOUNT FLAT" if remaining == 0 else "   *** check manually ***"))
    ret, acc = trd.accinfo_query(trd_env=TrdEnv.SIMULATE)
    if ret == RET_OK:
        print(f"final: cash={float(acc['cash'][0]):,.2f} "
              f"total_assets={float(acc['total_assets'][0]):,.2f}")

    quote.close(); trd.close()


if __name__ == '__main__':
    main()
