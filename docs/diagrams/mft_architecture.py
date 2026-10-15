"""
Draws docs/images/mft_architecture.png, the figure in docs/ARCHITECTURE.md.

It reuses the drawing helpers of the final report's figures (~/FYP/Report/generate_diagrams.py:
the Stage grid, colours, Material Symbols icons and the fit check) so this figure matches the
report's style; that file is imported read-only, not copied or changed.

Usage:  python docs/diagrams/mft_architecture.py [path/to/generate_diagrams.py]
Needs playwright with Chromium, like the report script.
"""
import importlib.util
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "images" / "mft_architecture.png"
DEFAULT_REPORT_SCRIPT = Path.home() / "FYP" / "Report" / "generate_diagrams.py"


def load_report_helpers(path: Path):
    spec = importlib.util.spec_from_file_location("report_diagrams", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mft_architecture(d):
    s = d.Stage(930)
    C = d.COLOURS
    o, g, p = C["orange"][0], C["green"][0], C["purple"][0]

    s.zone(16, 16, 968, 140, "Connectors · mft.adapters")
    sources = (("moomoo", "candlestick_chart", "Moomoo", "OpenD; HK, US, CN, SG, JP"),
               ("ibkr", "lan", "IBKR", "TWS contracts per venue"),
               ("yf", "travel_explore", "yfinance", "Yahoo symbols, history caps"),
               ("binance", "currency_bitcoin", "Binance", "public REST, native"),
               ("ccxt", "currency_exchange", "ccxt", "Coinbase, Kraken, OKX, ..."))
    for k, (key, icon, title, sub) in enumerate(sources):
        s.card(key, 32 + k * 190, 50, 176, 92, "blue", icon, title, sub, cls="stack")
    s.card("contract", 30, 186, 940, 70, "grey", "cable", "Connector contract",
           "InstrumentProvider + DataClient.request_bars(bar_type, start, end): symbology, paging, "
           "rate limits, bars closed in UTC", cls="centre")
    for key, *_ in sources:
        x = s.side(key, "bottom")[0]
        s.wire([(x, 142), (x, 186)])

    s.card("catalog", 30, 296, 440, 86, "green", "database", "ParquetDataCatalog",
           "catalog/data/bar/&lt;bar_type&gt;/ · filled by python -m mft.data fetch")
    s.card("legacy", 530, 296, 440, 86, "green", "folder_open", "LegacyDataReader",
           "the original data/ cache, read-only; local times re-read as UTC")
    xc = s.side("catalog", "top")[0]
    s.wire([(xc, 256), (xc, 296)])
    s.label(xc + 12, 266, "bars, instruments")

    s.zone(16, 420, 968, 400, "BacktestEngine · mft.backtest", cap_dx=690, cap_dy=368)
    s.card("data", 40, 466, 260, 88, "orange", "call_merge", "DataEngine",
           "merges every venue's bars in UTC order")
    s.card("bus", 370, 466, 260, 88, "orange", "swap_calls", "MessageBus",
           "bars, slices, signals, order events")
    s.card("strat", 700, 466, 260, 88, "purple", "psychology", "Strategies",
           "native, or core ones via the legacy adapter")
    s.card("port", 40, 606, 260, 88, "green", "account_balance_wallet", "Portfolio",
           "a cash account per currency, positions")
    s.card("exch", 370, 606, 260, 88, "orange", "storefront", "SimulatedExchange",
           "one per venue: fills, lots, fees, cash")
    s.card("risk", 700, 606, 260, 88, "green", "shield", "RiskEngine",
           "core.risk_manager rules, limits")
    s.card("venues", 370, 732, 260, 72, "grey", "tune", "config/venues.json",
           "sessions, currency, lot, fees")

    x_data = s.side("data", "top")[0]
    s.wire([(x_data, 382), (x_data, 466)])
    x_leg = s.side("legacy", "bottom")[0]
    s.wire([(x_leg, 382), (x_leg, 440), (270, 440), (270, 466)])
    s.wire([s.side("data", "right"), s.side("bus", "left")])
    s.label(335, 494, "bars", mid=True)
    s.wire([s.side("bus", "right"), s.side("strat", "left")])
    s.label(665, 494, "events", mid=True)
    xs = s.side("strat", "bottom")[0]
    s.wire([(xs, 554), (xs, 606)], colour=p)
    s.label(xs + 12, 566, "submit_order", colour=p)
    s.wire([s.side("risk", "left"), s.side("exch", "right")], colour=g)
    s.label(665, 634, "passes", mid=True)
    s.wire([s.side("exch", "left"), s.side("port", "right")], colour=o)
    s.label(335, 634, "fills", mid=True)
    xe = s.side("exch", "top")[0]
    s.wire([(xe, 606), (xe, 554)], colour=o)
    s.label(xe + 12, 566, "order events", colour=o)
    s.wire([s.side("venues", "top"), s.side("exch", "bottom")])

    s.card("result", 30, 852, 940, 62, "ink", "monitoring", "BacktestResult",
           "trades, equity and metrics per currency, computed by core's metric code; no FX conversion",
           cls="dark centre")
    xp = s.side("port", "bottom")[0]
    s.wire([(xp, 694), (xp, 852)])
    return s


def main():
    report = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get("REPORT_DIAGRAMS", DEFAULT_REPORT_SCRIPT))
    d = load_report_helpers(report)
    from playwright.sync_api import sync_playwright
    OUT.parent.mkdir(parents=True, exist_ok=True)
    html = OUT.with_suffix(".html")
    html.write_text(mft_architecture(d).render(d.icon_font()))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": d.W, "height": 800}, device_scale_factor=2.6)
        page.goto(html.as_uri())
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(250)
        spill = page.evaluate(d.FIT_CHECK)
        if spill:
            print("  ! does not fit ->", spill)
        page.locator("#stage").screenshot(path=str(OUT))
        browser.close()
    html.unlink()
    print("saved", OUT)


if __name__ == "__main__":
    main()
