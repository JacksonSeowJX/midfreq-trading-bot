"""
Capture dashboard screenshots for the report, reproducibly.

The report needs figures of the running dashboard. Taking them by hand
means they drift out of date silently and cannot be regenerated after a
UI change, which matters here because the dashboard was wrong until
2026-09-26: US backtests were charged Hong Kong fees and the S&P 100 was
unreachable, so any screenshot taken before that shows incorrect output.

Chrome's --screenshot alone is not enough. Streamlit renders over a
websocket after load, and --virtual-time-budget fast-forwards VIRTUAL
time while the server responds in real time, so the capture lands on the
grey skeleton. Playwright can wait for real content to appear.

Usage:
    python3 scripts/capture_dashboard.py [--port 8601] [--out DIR]
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8601)
    ap.add_argument('--out', default=str(REPO / 'images' / 'dashboard'))
    ap.add_argument('--width', type=int, default=1680)
    ap.add_argument('--height', type=int, default=1050)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright missing. pip install playwright && playwright install chromium")
        return 1

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    url = f"http://localhost:{args.port}"

    # (filename, sidebar button text, what must appear before capturing)
    SHOTS = [
        ('01_overview',  '📋 All Results Overview', 'Results tested'),
        ('02_research',  'Browse Research Results', 'Research Studies'),
        ('03_live',      'Show Live Account & Sessions', 'Live Paper Trading'),
    ]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': args.width, 'height': args.height})
        page.goto(url, wait_until='networkidle', timeout=60_000)
        # Streamlit paints a skeleton first; wait for the real sidebar control
        page.wait_for_selector('text=Strategy Control Panel', timeout=60_000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(out / '00_home.png'), full_page=True)
        print(f"  00_home.png")

        for name, button, marker in SHOTS:
            try:
                page.get_by_role('button', name=button).click(timeout=20_000)
                page.wait_for_selector(f'text={marker}', timeout=60_000)
                page.wait_for_timeout(3500)      # let plotly finish drawing
                page.screenshot(path=str(out / f'{name}.png'), full_page=True)
                print(f"  {name}.png")
            except Exception as e:
                print(f"  {name}: FAILED — {type(e).__name__}: {str(e)[:90]}")

        browser.close()

    files = sorted(out.glob('*.png'))
    print(f"\n{len(files)} screenshot(s) in {out}")
    for f in files:
        print(f"   {f.name:22s} {f.stat().st_size/1024:>7.0f} KB")
    return 0


if __name__ == '__main__':
    sys.exit(main())
