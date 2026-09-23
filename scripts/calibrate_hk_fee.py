"""
Measure the actual HK commission from a paper fill, by cash difference.

Portfolio.HK_FEE_RATE is 0.16% per side, and Section 3.5 of the report
describes it as "calibrated directly against real fills observed during
live paper trading". The commit that introduced it itemises the figure
as commission + platform fee + 0.1% stamp duty + levies, which reads
more like a build-up from the published schedule than a measurement.

The distinction matters, because the US paper account charges nothing at
all: order_fee_query returns "Paper trading is not supported" and a
US$72,912 round trip cost 49 cents. If HK paper behaves the same way,
then the 0.16% was never measured and the report's wording is wrong.

The fee API is unavailable on paper, so this measures by cash delta:

    fee = (cash_before - cash_after) - (dealt_qty * dealt_avg_price)

Using the actual fill price rather than an assumed one removes any
price-movement confound, so what remains is the fee alone.

Paper environment only. Buys one board lot, measures, then flattens and
verifies the account is flat before exiting.

Usage:
    python3 scripts/calibrate_hk_fee.py [SYMBOL] [QTY]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from moomoo import (OpenSecTradeContext, OpenQuoteContext, TrdEnv, TrdMarket,
                    TrdSide, OrderType, SecurityFirm, RET_OK)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else 'HK.00700'
QTY = int(sys.argv[2]) if len(sys.argv) > 2 else 100      # Tencent board lot
FILL_WAIT = 90


def cash(trd):
    ret, acc = trd.accinfo_query(trd_env=TrdEnv.SIMULATE)
    return float(acc['cash'][0]) if ret == RET_OK else None


def position_qty(trd, code):
    ret, pos = trd.position_list_query(trd_env=TrdEnv.SIMULATE)
    if ret != RET_OK or pos.empty:
        return 0.0
    row = pos[pos.code == code]
    return float(row['qty'].iloc[0]) if not row.empty else 0.0


def await_fill(trd, order_id, label):
    """Poll until the order is filled, returning (dealt_qty, dealt_avg_price)."""
    for _ in range(FILL_WAIT // 3):
        ret, orders = trd.order_list_query(trd_env=TrdEnv.SIMULATE)
        if ret == RET_OK and not orders.empty:
            row = orders[orders.order_id == order_id]
            if not row.empty:
                r = row.iloc[0]
                if float(r['dealt_qty']) > 0 and str(r['order_status']).startswith('FILLED'):
                    return float(r['dealt_qty']), float(r['dealt_avg_price'])
        time.sleep(3)
    print(f"  [!] {label} did not fill within {FILL_WAIT}s", flush=True)
    return None, None


def main():
    quote = OpenQuoteContext(host='127.0.0.1', port=11111)
    trd = OpenSecTradeContext(filter_trdmarket=TrdMarket.HK, host='127.0.0.1',
                              port=11111, security_firm=SecurityFirm.FUTUSECURITIES)

    existing = position_qty(trd, SYMBOL)
    if existing:
        print(f"ABORT: already holding {existing} of {SYMBOL}. "
              f"Refusing to disturb an existing position.", flush=True)
        quote.close(); trd.close(); return

    ret, snap = quote.get_market_snapshot([SYMBOL])
    if ret != RET_OK:
        print("snapshot failed:", snap); quote.close(); trd.close(); return
    px = float(snap['last_price'][0])
    print(f"{SYMBOL} last {px:.2f} | buying {QTY} on the paper account", flush=True)

    c0 = cash(trd)
    ret, data = trd.place_order(price=round(px * 1.01, 2), qty=QTY, code=SYMBOL,
                                trd_side=TrdSide.BUY, order_type=OrderType.NORMAL,
                                trd_env=TrdEnv.SIMULATE)
    if ret != RET_OK:
        print("BUY rejected:", data); quote.close(); trd.close(); return
    oid = data['order_id'][0]
    dealt_qty, dealt_px = await_fill(trd, oid, 'buy')
    time.sleep(3)
    c1 = cash(trd)

    print(f"\ncash before : {c0:,.2f}")
    print(f"cash after  : {c1:,.2f}")
    if dealt_qty:
        notional = dealt_qty * dealt_px
        fee = (c0 - c1) - notional
        print(f"filled      : {dealt_qty:.0f} @ {dealt_px:.4f}  = {notional:,.2f} notional")
        print(f"cash moved  : {c0 - c1:,.2f}")
        print(f"IMPLIED FEE : {fee:,.4f}   ({fee / notional * 100:.5f}% of notional)")
        print(f"\n  HK_FEE_RATE in the codebase is 0.16000% per side.")
        print(f"  a 0.16% fee on this trade would have been {notional * 0.0016:,.2f}")
        if abs(fee) < 0.01:
            print("  => paper charged NOTHING. The 0.16% cannot have come from a paper fill.")

    # Flatten, and verify
    print("\nflattening...", flush=True)
    held = position_qty(trd, SYMBOL)
    if held > 0:
        ret, d = trd.place_order(price=round(px * 0.97, 2), qty=held, code=SYMBOL,
                                 trd_side=TrdSide.SELL, order_type=OrderType.NORMAL,
                                 trd_env=TrdEnv.SIMULATE)
        if ret == RET_OK:
            await_fill(trd, d['order_id'][0], 'sell')
    time.sleep(3)
    remaining = position_qty(trd, SYMBOL)
    print(f"remaining {SYMBOL}: {remaining:.0f}" +
          ("  ACCOUNT FLAT" if remaining == 0 else "  *** STILL HOLDING, NEEDS MANUAL CLOSE ***"))
    print(f"final cash: {cash(trd):,.2f}")

    quote.close(); trd.close()


if __name__ == "__main__":
    main()
