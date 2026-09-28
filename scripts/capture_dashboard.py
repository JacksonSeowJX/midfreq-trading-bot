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
grey skeleton. Playwright waits for real content to appear.

Only the main content area is captured (not the sidebar), with a tall
viewport so nothing is cut off, and pages with several sections are split
at their section headings so each report figure shows one thing.

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
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    from PIL import Image

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    url = f"http://localhost:{args.port}"

    def settle(page, marker):
        page.wait_for_selector(f'text={marker}', timeout=60_000)
        page.wait_for_timeout(4000)                     # plotly finishes drawing

    def grab_main(page, path):
        main = page.locator('[data-testid="stAppViewBlockContainer"]').first   # main content, no sidebar (Streamlit 1.37)
        main.screenshot(path=str(path))
        return main.bounding_box()

    def split_at(page, src, box, cuts, names):
        """Crop a main-area capture at the y positions of the given headings."""
        img = Image.open(src)
        scale = img.height / box['height']
        ys = [0]
        for heading in cuts:
            hb = page.get_by_text(heading, exact=True).first.bounding_box()
            ys.append(int((hb['y'] - box['y'] - 8) * scale))
        ys.append(img.height)
        for (top, bot), name in zip(zip(ys, ys[1:]), names):
            img.crop((0, max(0, top), img.width, bot)).save(out / name)
            print(f"  {name}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 2600}, device_scale_factor=1.5)
        page.goto(url, wait_until='networkidle', timeout=60_000)
        page.wait_for_selector('text=Strategy Control Panel', timeout=60_000)

        # All Results Overview
        page.get_by_role('button', name='📋 All Results Overview').click()
        settle(page, 'Results tested')
        grab_main(page, out / 'overview.png'); print("  overview.png")

        # Research Studies
        page.get_by_role('button', name='Browse Research Results').click()
        settle(page, 'Select a study')
        grab_main(page, out / 'research.png'); print("  research.png")

        # Live, HK then US; split into account section and session section
        page.get_by_role('button', name='Show Live Account & Sessions').click()
        settle(page, 'Live Paper Trading')
        for mkt, label in (('hk', 'Hong Kong (HKD)'), ('us', 'United States (USD)')):
            page.get_by_text(label).first.click()
            page.wait_for_timeout(5000)
            src = out / f'_live_{mkt}_full.png'
            box = grab_main(page, src)
            has_sessions = page.get_by_text('Forward-Test Session History', exact=True).count() > 0
            if has_sessions:
                split_at(page, src, box, ['Forward-Test Session History'],
                         [f'live_{mkt}_account.png', f'live_{mkt}_sessions.png'])
            else:
                Image.open(src).save(out / f'live_{mkt}_account.png'); print(f"  live_{mkt}_account.png")
            src.unlink()
        browser.close()

    for f in sorted(out.glob('*.png')):
        print(f"   {f.name:26s} {Image.open(f).size}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
