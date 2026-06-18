from playwright.sync_api import sync_playwright
from pathlib import Path
import os

SUPERSET_URL = "http://dyserver:8088"
USERNAME = "admin"
PASSWORD = "admin123"

DASHBOARD_ID = 28
OUTPUT_DIR = os.path.join(os.getcwd(), "uploads")

SESSION_FILE = "superset_session.json"


def run():

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=False,        # ← browser visible for debugging
            slow_mo=500,           # ← 500ms delay between actions (easy to follow)
            args=[
                "--no-sandbox",
                "--start-maximized",
            ],
        )

        storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None

        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1920, "height": 1080},
            storage_state=storage,
            no_viewport=True,      # use actual window size
        )

        page = context.new_page()

        # ── LOGIN ─────────────────────────────────────────────────────────────
        dashboard_url = f"{SUPERSET_URL}/superset/dashboard/{DASHBOARD_ID}/"

        page.goto(dashboard_url, wait_until="networkidle", timeout=120_000)

        if "/login" in page.url:
            print("Logging in...")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.locator("button[type='submit']").click()
            page.wait_for_timeout(5_000)
            page.goto(dashboard_url, wait_until="networkidle", timeout=120_000)
            context.storage_state(path=SESSION_FILE)
            print("Login done. Session saved.")
        else:
            print("Already logged in.")

        # ── WAIT FOR DASHBOARD ────────────────────────────────────────────────
        print("Waiting for dashboard to load...")
        page.wait_for_timeout(15_000)

        # ── CLICK ALL TABS (pre-warm) ─────────────────────────────────────────
        tabs = page.locator("[role='tab']")
        tab_count = tabs.count()

        if tab_count > 0:
            names = tabs.all_text_contents()
            print(f"Found {tab_count} tab(s): {names}")

            for i in range(tab_count):
                tab_name = tabs.nth(i).text_content().strip()
                print(f"  Clicking tab: {tab_name}")
                tabs.nth(i).click()
                page.wait_for_timeout(3_000)   # wait for data to load

            print("All tabs pre-warmed.")
        else:
            print("No tabs found — single dashboard.")

        # ── OPEN 3-DOT MENU ───────────────────────────────────────────────────
        print("Opening '...' menu...")

        menu_candidates = [
            '[aria-haspopup="menu"]',
            '[aria-label*="menu"]',
            '[aria-label*="Menu"]',
        ]

        menu_found = False
        for selector in menu_candidates:
            count = page.locator(selector).count()
            print(f"  Selector '{selector}' found {count} element(s)")
            if count > 0:
                page.locator(selector).last.click()
                page.wait_for_timeout(2_000)
                menu_found = True
                print(f"  Clicked last element of '{selector}'")
                break

        if not menu_found:
            raise Exception("Could not find any menu button!")

        # ── HOVER DOWNLOAD ────────────────────────────────────────────────────
        print("Hovering Download...")
        try:
            page.get_by_text("Download", exact=True).hover()
            page.wait_for_timeout(1_500)
        except Exception:
            page.get_by_text("Download", exact=True).click()
            page.wait_for_timeout(1_500)

        # ── DOWNLOAD AS IMAGE ─────────────────────────────────────────────────
        print("Clicking 'Download as Image'...")

        with page.expect_download(timeout=120_000) as dl:
            page.get_by_text("Download as Image", exact=True).click()

        download = dl.value
        filename = download.suggested_filename or f"dashboard_{DASHBOARD_ID}.png"
        out_path = os.path.join(OUTPUT_DIR, filename)
        download.save_as(out_path)
        print(f"PNG saved: {out_path}")

        # ── KEEP BROWSER OPEN FOR INSPECTION ─────────────────────────────────
        print("\nDone! Browser will stay open for 30 seconds so you can inspect...")
        page.wait_for_timeout(30_000)

        browser.close()


if __name__ == "__main__":
    run()
