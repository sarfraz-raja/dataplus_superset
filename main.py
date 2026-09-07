import json
import logging
import os
import re
import threading
import time
import zipfile
from pathlib import Path

import requests
from flask import Flask, jsonify, request, render_template, Response, send_file
from flask_cors import CORS
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from pypdf import PdfWriter
# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# =========================
# CONFIG
# =========================
SUPERSET_BASE = "http://127.0.0.1:8088"
SUPERSET_URL = f"{SUPERSET_BASE}/health"

SUPERSET_LOGIN_API = f"{SUPERSET_BASE}/api/v1/security/login"
SUPERSET_GUEST_TOKEN_API = f"{SUPERSET_BASE}/api/v1/security/guest_token/"

SUPERSET_ADMIN_USER = "admin"
SUPERSET_ADMIN_PASS = "admin123"

FLASK_PORT = 8089

UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
SESSION_FILE = os.path.join(os.getcwd(), "superset_session.json")

# Max retries for transient playwright failures
EXPORT_MAX_RETRIES = 2

# =========================
# FLASK APP
# =========================
app = Flask(__name__)

CORS(
    app,
    resources={
        r"/*": {
            "origins": [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://192.168.0.102:5173",
                "http://192.168.0.100:5173",
                "http://192.168.0.181:5173",
                "http://192.168.0.172:5173",
                "http://dyserver:5173",
                "http://dyserver2:5173",
                "http://172.19.56.29:5173",
                "http://10.249.18.230:5173",
            ]
        }
    },
    supports_credentials=True,
)

# =========================
# BROWSER SINGLETON + WARM PAGE CACHE
#
# One browser + context is reused across all requests (avoids ~3s launch overhead).
# Additionally, pages are kept alive after export and reused on the next request
# for the same target URL. On a warm hit, only page.reload() is needed — the
# browser's HTTP cache serves JS/CSS instantly; only chart data is re-fetched.
# This saves ~3-5s on every repeat export of the same dashboard/chart.
#
# _warm_pages: { target_url -> playwright Page }  (max WARM_PAGE_LIMIT entries)
# A threading.Lock serializes all Playwright calls (sync API is not thread-safe).
# =========================
_playwright = None
_browser = None
_browser_context = None
_browser_lock = threading.Lock()

WARM_PAGE_LIMIT = 3          # max open warm pages (memory guard)
_warm_pages: dict = {}       # url -> Page
_warm_page_order: list = []  # LRU order of urls


def _get_browser_context():
    global _playwright, _browser, _browser_context

    if _browser_context is not None:
        return _browser_context

    log.info("Launching Playwright browser (first time)...")
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(
        # PDF generation (page.pdf()) is only supported in headless Chromium —
        # required for the vector-quality (crisp-at-any-zoom) PDF export.
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--force-device-scale-factor=1",
        ],
    )

    storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None

    # FIX: Viewport size ko Landscape Desktop resolution (1920x1080) kar do
    # device_scale_factor=2: chart titles/text are already vector (crisp at
    # any zoom) via page.pdf(), but the chart curves themselves are drawn on
    # <canvas> by the charting library, so they're baked in as a raster
    # image at whatever resolution Chrome renders them at. Capturing at 2x
    # density (like a Retina display) doubles that resolution, so curves
    # stay sharp at higher PDF zoom levels too.
    _browser_context = _browser.new_context(
        accept_downloads=True,
        viewport={"width": 1920, "height": 1080},
        device_scale_factor=2,
        storage_state=storage,
        ignore_https_errors=True,
    )

    return _browser_context

def _reset_browser_context():
    """Close and reset everything so the next call re-creates from scratch."""
    global _browser, _browser_context, _playwright, _warm_pages, _warm_page_order

    log.warning("Resetting browser context and warm page cache...")
    _warm_pages = {}
    _warm_page_order = []

    try:
        if _browser_context:
            _browser_context.close()
    except Exception:
        pass
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    try:
        if _playwright:
            _playwright.stop()
    except Exception:
        pass

    _playwright = None
    _browser = None
    _browser_context = None


def _get_warm_page(target_url):
    """
    Return a cached warm Page for target_url if one exists and is still alive.
    Returns None if no warm page is available (cold path needed).
    """
    page = _warm_pages.get(target_url)
    if page is None:
        return None
    try:
        # Quick liveness check — if page was closed/crashed this raises
        _ = page.url
        log.info("Warm page hit for %s", target_url)
        return page
    except Exception:
        _evict_warm_page(target_url)
        return None


def _store_warm_page(target_url, page):
    """Store page in the warm cache, evicting LRU entry if limit is reached."""
    global _warm_page_order

    if target_url in _warm_pages:
        _warm_page_order.remove(target_url)
    elif len(_warm_pages) >= WARM_PAGE_LIMIT:
        oldest = _warm_page_order.pop(0)
        _evict_warm_page(oldest)

    _warm_pages[target_url] = page
    _warm_page_order.append(target_url)
    log.info("Warm page stored for %s (cache size: %d)", target_url, len(_warm_pages))


def _evict_warm_page(target_url):
    """Close and remove a warm page from the cache."""
    page = _warm_pages.pop(target_url, None)
    if target_url in _warm_page_order:
        _warm_page_order.remove(target_url)
    if page:
        try:
            page.close()
        except Exception:
            pass


# =========================
# HELPERS
# =========================
def is_superset_running():
    try:
        r = requests.get(SUPERSET_URL, timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def superset_login():
    session = requests.Session()

    r = session.post(
        SUPERSET_LOGIN_API,
        json={
            "username": SUPERSET_ADMIN_USER,
            "password": SUPERSET_ADMIN_PASS,
            "provider": "db",
            "refresh": True,
        },
    )
    r.raise_for_status()

    access_token = r.json()["access_token"]

    r = session.get(
        f"{SUPERSET_BASE}/api/v1/security/csrf_token/",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    r.raise_for_status()

    csrf_token = r.json()["result"]

    return access_token, csrf_token, session


def _validate_ids(dashboard_id, chart_id):
    """
    Check via Superset API that the given dashboard_id / chart_id actually exists.
    Returns (True, None) if valid, or (False, error_message) if not found.
    """
    try:
        access_token, _, _ = superset_login()
        headers = {"Authorization": f"Bearer {access_token}"}

        if dashboard_id:
            r = requests.get(
                f"{SUPERSET_BASE}/api/v1/dashboard/{dashboard_id}",
                headers=headers,
                timeout=10,
            )
            if r.status_code == 404:
                return False, f"Dashboard with id {dashboard_id} not found"
            if r.status_code != 200:
                return False, f"Could not verify dashboard {dashboard_id} (status {r.status_code})"

        if chart_id:
            r = requests.get(
                f"{SUPERSET_BASE}/api/v1/chart/{chart_id}",
                headers=headers,
                timeout=10,
            )
            if r.status_code == 404:
                return False, f"Chart with id {chart_id} not found"
            if r.status_code != 200:
                return False, f"Could not verify chart {chart_id} (status {r.status_code})"

    except Exception as e:
        return False, f"Superset API check failed: {e}"

    return True, None


def _get_dashboard_tabs_meta(dashboard_id):
    """
    Fetch dashboard tabs (id + name, in display order) from Superset's
    position_json — the same source of truth the dashboard UI renders from.

    Returns a dict: {dashboard_title, has_tabs, tab_count, tabs: [...]}
    Raises RuntimeError on lookup/API failure.
    """
    access_token, _, _ = superset_login()
    headers = {"Authorization": f"Bearer {access_token}"}

    r = requests.get(
        f"{SUPERSET_BASE}/api/v1/dashboard/{dashboard_id}",
        headers=headers,
        timeout=10,
    )
    if r.status_code == 404:
        raise RuntimeError(f"Dashboard with id {dashboard_id} not found")
    if r.status_code != 200:
        raise RuntimeError(f"Could not fetch dashboard {dashboard_id} (status {r.status_code})")

    result = r.json().get("result", {})
    position_json_raw = result.get("position_json")

    tabs = []
    if position_json_raw:
        position_json = json.loads(position_json_raw)

        # A dashboard can have multiple TABS containers (nested tab groups);
        # walk every one and flatten in document order using its `children`
        # array, which is the actual left-to-right tab order shown in the UI.
        for node_id, node in position_json.items():
            if not isinstance(node, dict) or node.get("type") != "TABS":
                continue
            for idx, tab_id in enumerate(node.get("children", [])):
                tab_node = position_json.get(tab_id)
                if not tab_node or tab_node.get("type") != "TAB":
                    continue
                meta = tab_node.get("meta", {})
                name = meta.get("text") or meta.get("defaultText") or meta.get("placeholder") or tab_id
                tabs.append({
                    "index": idx,
                    "id": tab_id,
                    "name": name,
                    "tabs_container_id": node_id,
                })

    return {
        "dashboard_id": dashboard_id,
        "dashboard_title": result.get("dashboard_title"),
        "has_tabs": bool(tabs),
        "tab_count": len(tabs),
        "tabs": tabs,
    }


def _build_target_url(dashboard_id, chart_id):
    """
    Return the Superset URL to navigate to for export.
    Chart takes priority over dashboard when both are provided.
    standalone=3 hides the header/navbar so the browser renders less DOM,
    which meaningfully cuts render time.
    """
    if chart_id:
        return f"{SUPERSET_BASE}/explore/?slice_id={chart_id}"
    # No standalone param — same URL as test.py which has working download menu
    return f"{SUPERSET_BASE}/superset/dashboard/{dashboard_id}/"


def _ensure_logged_in(page, context, target_url, theme="light"):
    """
    Navigate to target_url; login via form if redirected to /login.
    Theme is applied via CSS media emulation (prefers-color-scheme) which is
    what Superset/Ant Design actually reads — localStorage keys are unreliable
    across Superset versions.
    """
    color_scheme = "dark" if theme == "dark" else "light"
    page.emulate_media(color_scheme=color_scheme)

    # domcontentloaded is enough to detect the login redirect; much faster than networkidle
    page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)

    if "/login" in page.url:
        log.info("Session expired or missing — logging in via browser UI...")

        page.wait_for_selector("#username", timeout=30_000)
        page.fill("#username", SUPERSET_ADMIN_USER)
        page.fill("#password", SUPERSET_ADMIN_PASS)
        page.locator("button[type='submit']").click()

        # Wait for redirect away from /login instead of a fixed sleep
        page.wait_for_url(lambda url: "/login" not in url, timeout=30_000)

        page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)

        context.storage_state(path=SESSION_FILE)
        log.info("Login successful. Session saved to %s", SESSION_FILE)
    else:
        log.info("Existing session valid.")


def _get_tab_names(page) -> list:
    """
    Auto-detect all tab names on the dashboard.
    Returns an empty list if the dashboard has no tabs.
    """
    try:
        locator = page.locator("[role='tab']")
        count = locator.count()
        if count == 0:
            return []
        names = [n.strip() for n in locator.all_text_contents() if n.strip()]
        log.info("Detected %d tab(s): %s", len(names), names)
        return names
    except Exception as e:
        log.warning("Tab detection failed: %s", e)
        return []


def _select_tab(page, tab_name: str):
    """
    Click a dashboard tab by name and wait for its charts to finish loading.
    """
    log.info("Selecting tab: %s", tab_name)
    try:
        tab = page.get_by_role("tab", name=re.compile(re.escape(tab_name), re.IGNORECASE))
        if tab.count() == 0:
            tab = page.locator(".ant-tabs-tab, [role='tab']").filter(
                has_text=re.compile(re.escape(tab_name), re.IGNORECASE)
            )
        tab.first.click(timeout=10_000)
        log.info("Tab clicked: %s", tab_name)
    except Exception as e:
        raise RuntimeError(f"Could not click tab '{tab_name}': {e}")

    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        log.warning("networkidle after tab '%s' timed out; proceeding.", tab_name)

    _wait_for_charts_to_finish_loading(page)
    page.wait_for_timeout(500)


def _wait_for_charts_to_finish_loading(page, timeout=20_000):
    """
    Wait until every chart's loading spinner has disappeared.
    networkidle only proves the XHR/fetch has returned — the chart library
    (echarts/nvd3) still needs a render tick after that to paint the canvas/
    svg, and on a freshly-selected tab that render can lag behind
    networkidle enough to get captured mid-blank. Superset shows a spinner
    (".loading", Ant Design's ".ant-spin"/".antd5-spin") on each chart slice
    while its data/render is pending, so waiting for zero visible spinners
    is a more reliable "chart is actually painted" signal than networkidle.
    """
    spinner_selector = ".loading, .ant-spin-spinning, .antd5-spin-spinning"
    try:
        page.wait_for_function(
            """(sel) => document.querySelectorAll(sel).length === 0""",
            arg=spinner_selector,
            timeout=timeout,
        )
        log.info("No chart spinners left — charts painted.")
    except PlaywrightTimeout:
        log.warning("Chart spinners still present after %dms; proceeding anyway.", timeout)


def _wait_for_render(page):
    """
    Render wait strategy (optimized):
    1. Wait for the dashboard grid to appear in DOM (React mounted).
    2. Wait for networkidle — fires when all chart API calls finish (no requests for 500ms).
       This is the fastest accurate signal that chart data has arrived.
    3. Single scroll to wake below-fold lazy charts + short settle.
    4. Wait for per-chart loading spinners to clear (data fetched != painted).
    """
    # Step 1: grid mount — React has rendered the chart containers
    try:
        page.wait_for_selector(
            ".grid-content, .dashboard-component-chart-holder, "
            ".slice_container, [class*='chart-slice']",
            timeout=8_000,
        )
        log.info("Dashboard grid mounted.")
    except PlaywrightTimeout:
        # On tab switches this selector reliably times out anyway (the
        # elements are already in the DOM from the previous tab, so no new
        # match ever fires) — the fixed fallback below is what actually
        # carries it, so keep the timeout short instead of burning 30s here.
        log.warning("Grid selector timed out; using fixed fallback.")
        page.wait_for_timeout(5_000)

    # Step 2: networkidle = all XHR/fetch calls done = chart data loaded
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
        log.info("Network idle — charts data fetched.")
    except PlaywrightTimeout:
        log.warning("networkidle timed out; proceeding.")

    # Step 3: scroll to trigger any lazy below-fold charts, then scroll back
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(800)
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

    # Step 4: chart-level spinners can still be visible after networkidle —
    # wait for them to clear before the final settle.
    _wait_for_charts_to_finish_loading(page)

    # Minimal settle for final paint flush
    page.wait_for_timeout(800)


def _hide_scrollbars(page):
    """
    Inject CSS to hide scrollbars on every scrollable element before
    "Download as Image" runs. Superset's image export (html2canvas) doesn't
    render Chromium's overlay scrollbar thumbs correctly — they show up as
    solid black blocks along the right edge of the exported PNG. Hiding
    them (width/height still work; only the visual thumb is suppressed)
    removes the artifact without affecting layout.
    """
    page.add_style_tag(
        content="""
        *{scrollbar-width:none !important;}
        *::-webkit-scrollbar{width:0 !important;height:0 !important;display:none !important;}
        """
    )


def _fit_viewport_to_content(page):
    """
    Widen the viewport to the dashboard's actual content width before
    "Download as Image" captures it. The browser context opens at a fixed
    1920x1080 viewport, but a chart row can be wider than that; when it
    is, Superset renders it as a horizontally-scrollable carousel with a
    semi-opaque black "scroll right" arrow pinned to the row's right
    edge to hint there's more off-screen. Since the export capture only
    grabs the current viewport, that arrow (and the cut-off chart behind
    it) end up baked into the PNG as solid black blocks. Sizing the
    viewport to scrollWidth/scrollHeight first means nothing overflows,
    so Superset never shows the arrow and every row is captured in full.
    """
    try:
        size = page.evaluate(
            "() => ({w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight})"
        )
        page.set_viewport_size({"width": max(size["w"], 1920), "height": max(size["h"], 1080)})
        page.wait_for_timeout(500)
    except Exception as e:
        log.warning("Could not fit viewport to content: %s", e)


def _hide_row_hover_controls(page):
    """
    Remove Superset's per-row ".header-controls" buttons (the row-level
    "..." menu used in edit mode to recolor/delete a row) before
    "Download as Image" captures the page. They stay mounted in the DOM
    at all times, just off-screen/invisible via hover-only CSS in view
    mode — but Superset's own image-export routine force-shows hidden
    UI while capturing (same as it does for inactive tabpanels, see
    _select_tab's extra-tab capture above), which un-hides these too and
    bakes them in as a solid black block pinned to each row's right
    edge — exactly the blocks reported in exported PNGs. Setting
    display:none isn't enough since that force-show routine overrides
    it; removing the elements from the DOM means there's nothing left
    for it to force-show. They're edit-mode-only controls, irrelevant to
    an exported image, so removing them is safe.
    """
    try:
        count = page.evaluate(
            """
            () => {
                const els = document.querySelectorAll('.header-controls');
                const n = els.length;
                els.forEach(el => el.remove());
                return n;
            }
            """
        )
        log.info("Removed %d row-hover control(s) before image capture.", count)
    except Exception as e:
        log.warning("Could not hide row-hover controls: %s", e)


def _wait_for_icon_fonts(page):
    """
    Wait for all webfonts (Ant Design's icon font in particular) to finish
    loading before the image capture fires. Superset's chart/row hover
    icons (expand, "...", etc.) are icon-font glyphs; if the capture runs
    before that font has loaded, each glyph paints as a solid black
    "tofu" box instead of the icon — the black rounded blocks seen at the
    right edge of exported PNGs, one per chart row.
    """
    try:
        page.evaluate("() => document.fonts.ready")
        page.wait_for_timeout(500)
    except Exception:
        pass


def _open_download_submenu(page):
    """
    Open the dashboard '...' menu and hover over Download.
    Uses .last on menu selectors; falls back to button:has(svg) as last resort.
    """
    menu_candidates = [
        '[aria-label*="Menu"]',
        '[aria-label*="menu"]',
        '[aria-haspopup="menu"]',
        'button:has(svg)',
    ]

    menu_found = False
    for selector in menu_candidates:
        try:
            locator = page.locator(selector)
            count = locator.count()
            if count == 0:
                continue
            locator.last.click()
            page.wait_for_timeout(2_000)
            menu_found = True
            log.info("Opened menu via selector: %s (count=%d)", selector, count)
            break
        except Exception:
            pass

    if not menu_found:
        raise RuntimeError("Could not open dashboard action menu.")

    try:
        page.get_by_text("Download", exact=True).hover()
        page.wait_for_timeout(2_000)
    except Exception:
        page.get_by_text("Download", exact=True).click()
        page.wait_for_timeout(2_000)


def _trigger_download(page, export_format, output_dir, filename_hint="export"):
    """
    Click the export menu item and wait for the download to complete.
    Forces the file to be saved with filename_hint (Tab Name).
    """
    if export_format == "pdf":
        candidates = [
            re.compile(r"export to pdf", re.IGNORECASE),
            re.compile(r"download pdf", re.IGNORECASE),
        ]
    else:
        candidates = [
            re.compile(r"download as image", re.IGNORECASE),
            re.compile(r"download image", re.IGNORECASE),
            re.compile(r"export image", re.IGNORECASE),
            re.compile(r"export to image", re.IGNORECASE),
        ]

    exact_labels = {
        "pdf": "Export to PDF",
        "png": "Download as Image",
    }

    clicked = False
    # A real download normally fires within a couple seconds of the click;
    # 45s is already generous headroom. 180s just meant a broken/hung
    # download silently ate 3 minutes per attempt (and up to 3x that across
    # retries) before finally failing.
    with page.expect_download(timeout=45_000) as dl_info:

        try:
            page.get_by_text(exact_labels[export_format], exact=True).click()
            clicked = True
            log.info("Clicked: %s (exact)", exact_labels[export_format])
        except Exception:
            pass

        if not clicked:
            for pattern in candidates:
                try:
                    loc = page.get_by_text(pattern)
                    if loc.count() > 0:
                        loc.first.click()
                        clicked = True
                        log.info("Clicked export item matching: %s", pattern.pattern)
                        break
                except Exception:
                    pass

        if not clicked:
            raise RuntimeError(f"Could not find export menu item for format '{export_format}'.")

    download = dl_info.value
    ext = "pdf" if export_format == "pdf" else "png"

    # Fix: Superset default name override karke Tab Name ko file name banao
    if filename_hint:
        safe_name = _safe_filename(filename_hint)
        filename = f"{safe_name}.{ext}"
    else:
        raw_name = download.suggested_filename or f"export.{ext}"
        clean_stem = re.sub(r"-\d{4}-\d{2}-\d{2}T[\d\-\.]+Z$", "", Path(raw_name).stem)
        filename = f"{clean_stem}.{ext}"

    output_path = os.path.join(output_dir, filename)
    download.save_as(output_path)
    log.info("Downloaded: %s", output_path)

    if export_format == "png":
        _crop_export_artifact(page, output_path)

    return output_path


def _crop_export_artifact(page, image_path):
    """
    Superset's "Download as Image" pads the PNG a fixed ~16px wider than
    the actual browser viewport, with no real content in that strip (we
    verified nothing in the DOM ever sits past the viewport edge — the
    page never overflows horizontally). That dead strip renders as solid
    black wherever it overlaps a chart row, showing up as black blocks
    along the right edge of the exported image. Since content never
    exceeds the viewport width, cropping the PNG back down to it removes
    the artifact without losing anything real.
    """
    try:
        viewport = page.viewport_size
        if not viewport:
            return
        with Image.open(image_path) as img:
            if img.width > viewport["width"]:
                img.crop((0, 0, viewport["width"], img.height)).save(image_path)
                log.info(
                    "Cropped export artifact: %dpx -> %dpx wide (%s)",
                    img.width, viewport["width"], image_path,
                )
    except Exception as e:
        log.warning("Could not crop export artifact from %s: %s", image_path, e)

def _safe_filename(text: str) -> str:
    """Strip characters that are unsafe in filenames."""
    return re.sub(r'[\\/*?:"<>|]', "_", text).strip()


def _prewarm_tabs(page, tab_names: list):
    """
    Click through every tab once so their chart data is fetched and cached
    in the browser. 5s wait per tab matches working local script timing.
    """
    tabs = page.locator('[role="tab"]')
    count = tabs.count()
    log.info("Pre-warming %d tab(s)...", count)

    for i in range(count):
        try:
            tab = tabs.nth(i)
            name = (tab.text_content() or "").strip()
            log.info("Loading tab %d/%d: %s", i + 1, count, name)
            tab.click()
            page.wait_for_timeout(5_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
        except Exception as e:
            log.warning("Tab load error: %s", e)

    try:
        tabs.first.click()
    except Exception:
        pass

    log.info("All tabs pre-warmed.")


# Hard ceiling for this report type — vector PDFs from page.pdf() should
# stay far under this; a breach just gets logged for now.
PDF_HARD_CAP_BYTES = 10 * 1024 * 1024


def _isolate_grid_content(page):
    """
    Hide everything except the chart grid (Superset's navbar, dashboard
    title, filter bar and tab strip) so page.pdf() prints only the charts —
    matching what Superset's own "Export to PDF"/"Download as Image"
    capture. Walks up the DOM from .grid-content to <body>, hiding every
    sibling encountered along the way. Those siblings are by definition
    outside the grid's own ancestor chain, so this can't touch the grid's
    rendering, and — unlike matching a specific class name — it keeps
    working regardless of Superset's (build-hashed, unstable) CSS classes.
    Elements are tagged so _restore_hidden_chrome() can bring them back.

    On tabbed dashboards, Ant Design's Tabs component keeps every tab's
    pane mounted in the DOM at once (only the active one is un-hidden) so
    chart data isn't re-fetched on every switch — meaning there's one
    .grid-content per tab, all present simultaneously. A plain
    `document.querySelector('.grid-content')` always returns the first one
    in DOM order regardless of which tab is actually active, which is why
    every tab used to print the same (first) tab's content. Scoping the
    query to `.ant-tabs-tabpane-active` fixes that; the plain fallback
    keeps this working on dashboards with no tabs at all.
    """
    page.evaluate(
        """
        () => {
            let node = document.querySelector('.ant-tabs-tabpane-active .grid-content')
                    || document.querySelector('.grid-content');
            while (node && node.parentElement && node.parentElement !== document.body) {
                const parent = node.parentElement;
                for (const sib of Array.from(parent.children)) {
                    if (sib !== node) {
                        sib.setAttribute('data-pdf-hidden', 'true');
                        sib.style.display = 'none';
                    }
                }
                node = parent;
            }
        }
        """
    )


def _restore_hidden_chrome(page):
    """Undo _isolate_grid_content() so the navbar/filters/tabs work again."""
    page.evaluate(
        """
        () => {
            document.querySelectorAll('[data-pdf-hidden]').forEach(el => {
                el.style.display = '';
                el.removeAttribute('data-pdf-hidden');
            });
        }
        """
    )


def _export_tab_pdf(page, output_path: str):
    """
    Print the currently-selected tab straight from the browser
    (Playwright's page.pdf(), Chromium print-to-PDF) instead of taking a
    screenshot — text and vector graphics come out crisp at any zoom
    level, not just at screen resolution. Page size is set to the grid's
    own content size, so there's no blank space and no Portrait/Letter
    mismatch to fix up afterwards.
    """
    page.emulate_media(media="screen")
    _isolate_grid_content(page)
    try:
        page.wait_for_timeout(300)
        size = page.evaluate(
            "() => ({w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight})"
        )
        page.pdf(
            path=output_path,
            width=f"{size['w']}px",
            height=f"{size['h']}px",
            print_background=True,
            margin={"top": "0px", "right": "0px", "bottom": "0px", "left": "0px"},
        )
    finally:
        _restore_hidden_chrome(page)


def _merge_pdfs(pdf_paths: list, output_pdf_path: str):
    """Merge multiple single-page PDFs into one file."""
    writer = PdfWriter()
    for pdf_path in pdf_paths:
        writer.append(pdf_path)

    with open(output_pdf_path, "wb") as f:
        writer.write(f)

    log.info(
        "Merged %d PDF(s) into: %s (%d bytes)",
        len(pdf_paths), output_pdf_path, os.path.getsize(output_pdf_path),
    )


def _do_export(dashboard_id, chart_id, export_formats: list, theme="light", extra_tab_name=None):
    """
    Core export logic executed inside the browser lock.

    Workflow:
    1. Open dashboard.
    2. Detect all tabs using `_get_tab_names()`.
    3. Loop over tabs -> `_select_tab()` -> `_wait_for_render()` -> Native Export to PDF.
    4. Save each tab PDF using the exact Tab Name.
    5. Merge all individual Tab PDFs into one final PDF.
    6. If extra_tab_name is given, additionally select that one tab and export
       just its PNG image (used when the caller passed a tab_id — the full
       dashboard export above still happens as usual, this is an extra file).
    """
    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    target_url = _build_target_url(dashboard_id, chart_id)
    context = _get_browser_context()

    page = _get_warm_page(target_url)
    warm_hit = page is not None

    if warm_hit:
        page.emulate_media(color_scheme="dark" if theme == "dark" else "light")
        log.info("Warm reload for %s", target_url)
        try:
            page.reload(wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            log.warning("Warm reload failed (%s); falling back to cold path.", e)
            _evict_warm_page(target_url)
            warm_hit = False

    if not warm_hit:
        page = context.new_page()
        _ensure_logged_in(page, context, target_url, theme=theme)

    try:
        # Wait for the dashboard React components to mount
        _wait_for_render(page)

        tab_names = _get_tab_names(page)
        has_tabs = bool(tab_names)
        target_id = dashboard_id or chart_id

        result_files = {}

        if "pdf" in export_formats:
            final_pdf_path = os.path.join(UPLOAD_DIR, f"dashboard_{target_id}.pdf")

            if has_tabs:
                log.info("Found %d tab(s). Printing vector PDF for each tab...", len(tab_names))
                tab_pdf_paths = []

                for idx, tab_name in enumerate(tab_names):
                    log.info("Processing Tab %d/%d: '%s'", idx + 1, len(tab_names), tab_name)

                    # 1. Select the tab
                    _select_tab(page, tab_name)

                    # 2. Wait for full render
                    _wait_for_render(page)

                    # 3. Print this tab straight from the browser (vector PDF)
                    tab_pdf_path = os.path.join(UPLOAD_DIR, f"{_safe_filename(tab_name)}.pdf")
                    _export_tab_pdf(page, tab_pdf_path)
                    tab_pdf_paths.append(tab_pdf_path)

                # 4. Merge all tab PDFs into one final file
                log.info("Merging %d tab PDFs into single file...", len(tab_pdf_paths))
                _merge_pdfs(tab_pdf_paths, final_pdf_path)

                for path in tab_pdf_paths:
                    try:
                        os.remove(path)
                    except Exception as e:
                        log.warning("Could not delete temporary file %s: %s", path, e)

            else:
                log.info("No tabs detected. Printing standard single PDF...")
                _export_tab_pdf(page, final_pdf_path)

            log.info("PDF size: %d bytes", os.path.getsize(final_pdf_path))
            if os.path.getsize(final_pdf_path) > PDF_HARD_CAP_BYTES:
                log.warning(
                    "PDF (%d bytes) exceeds hard cap of %d bytes",
                    os.path.getsize(final_pdf_path), PDF_HARD_CAP_BYTES,
                )

            result_files["pdf"] = final_pdf_path

        if "png" in export_formats:
            log.info("Triggering image download...")
            _fit_viewport_to_content(page)
            _hide_scrollbars(page)
            _wait_for_icon_fonts(page)
            _hide_row_hover_controls(page)
            _open_download_submenu(page)
            png_path = _trigger_download(
                page, "png", UPLOAD_DIR, filename_hint=f"dashboard_{target_id}"
            )
            result_files["png"] = {f"dashboard_{target_id}": png_path}

        if extra_tab_name:
            # Best-effort: the dashboard/PDF export above already succeeded,
            # so a failure here (e.g. this tab's native image download hangs
            # or errors) must not blow up the whole request — that would
            # discard already-good work and trigger a full retry of the
            # expensive PDF loop for something unrelated. Log and move on.
            #
            # Uses a BRAND NEW page instead of reusing `page`: Superset never
            # unmounts a tab's chart grid once visited (it just toggles which
            # one is on top), so on `page` — which by now may have visited
            # every tab via the PDF loop above — "Download as Image" would
            # capture every previously-visited tab's charts stacked together,
            # not just this one. A fresh page that goes straight to this tab
            # and nothing else guarantees an isolated, single-tab capture.
            tab_page = None
            try:
                log.info("Exporting extra tab image for: %s (fresh page)", extra_tab_name)
                tab_page = context.new_page()
                _ensure_logged_in(tab_page, context, target_url, theme=theme)
                _wait_for_render(tab_page)
                _select_tab(tab_page, extra_tab_name)
                _wait_for_render(tab_page)
                # Superset's DOM correctly keeps inactive tab panels
                # display:none, but "Download as Image" force-shows every
                # tabpanel while capturing (same as an @media print
                # override) — so without this, the image ends up with every
                # previously-existing tab's charts stacked on top of this
                # one's. Ripping the hidden panels out of the DOM first
                # means there's nothing left for it to force-show.
                removed = tab_page.evaluate(
                    """() => {
                        const hidden = document.querySelectorAll('[role="tabpanel"][aria-hidden="true"]');
                        const ids = Array.from(hidden).map(el => el.id);
                        hidden.forEach(el => el.remove());
                        return ids;
                    }"""
                )
                log.info("Removed %d inactive tab panel(s) before capture: %s", len(removed), removed)
                _fit_viewport_to_content(tab_page)
                _hide_scrollbars(tab_page)
                _wait_for_icon_fonts(tab_page)
                _hide_row_hover_controls(tab_page)
                _open_download_submenu(tab_page)
                tab_png_path = _trigger_download(
                    tab_page, "png", UPLOAD_DIR,
                    filename_hint=f"dashboard_{target_id}_tab_{_safe_filename(extra_tab_name)}",
                )
                result_files["tab_png"] = tab_png_path
            except Exception as e:
                log.warning("Extra tab image export failed for '%s': %s", extra_tab_name, e)
                result_files["tab_png_error"] = str(e)
            finally:
                if tab_page is not None:
                    try:
                        tab_page.close()
                    except Exception:
                        pass

        _store_warm_page(target_url, page)
        return result_files

    except Exception:
        _evict_warm_page(target_url)
        try:
            page.close()
        except Exception:
            pass
        raise

def run_export_with_retry(dashboard_id, chart_id, export_formats: list, theme="light", extra_tab_name=None):
    """
    Run _do_export with retry logic.
    On failure, resets the browser context so the next attempt starts fresh.
    """
    last_error = None

    for attempt in range(1, EXPORT_MAX_RETRIES + 2):  # attempts = retries + 1
        try:
            with _browser_lock:
                return _do_export(dashboard_id, chart_id, export_formats, theme=theme, extra_tab_name=extra_tab_name)

        except Exception as exc:
            last_error = exc
            log.warning(
                "Export attempt %d/%d failed: %s",
                attempt,
                EXPORT_MAX_RETRIES + 1,
                exc,
            )
            if attempt <= EXPORT_MAX_RETRIES:
                with _browser_lock:
                    _reset_browser_context()
                time.sleep(2)

    raise last_error

# run_export_with_retry(dashboard_id=28, chart_id=None, export_formats=["pdf"])
# =========================
# ROUTES
# =========================
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "flask_up",
        "superset_running": is_superset_running(),
    })


@app.route("/api/dashboard/<int:dashboard_id>/tabs", methods=["GET"])
def dashboard_tabs(dashboard_id):
    """
    Returns the tabs available on a dashboard (id + name + order), so the
    frontend can populate an optional "Tab" dropdown next to Dashboard ID.
    has_tabs=false means the dashboard has no tabs — the frontend should
    hide/disable the tab selector and the export falls back to full-dashboard.
    """
    try:
        meta = _get_dashboard_tabs_meta(dashboard_id)
        return jsonify(meta)
    except RuntimeError as e:
        msg = str(e)
        status = 404 if "not found" in msg else 502
        return jsonify({"error": msg}), status
    except Exception as e:
        log.error("Failed to fetch tabs for dashboard %s: %s", dashboard_id, e)
        return jsonify({"error": f"Failed to fetch tabs: {e}"}), 500


@app.route("/api/superset/guest-token", methods=["POST"])
def guest_token():
    data = request.json or {}
    log.info("Guest token request: %s", data)

    try:
        access_token, csrf_token, session = superset_login()
    except Exception as e:
        log.error("Superset login/csrf failed: %s", e)
        return jsonify({"error": "Superset login / csrf failed", "details": str(e)}), 500

    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-CSRFToken": csrf_token,
        "Content-Type": "application/json",
    }

    r = session.post(SUPERSET_GUEST_TOKEN_API, json=data, headers=headers)

    if r.status_code != 200:
        return jsonify({
            "error": "Guest token failed",
            "status": r.status_code,
            "details": r.text,
        }), 500

    return jsonify(r.json())


@app.route("/export", methods=["POST"])
def export():
    """
    Export ALL tabs of a Superset dashboard as PNG + PDF (or a chosen format).
    Tabs are auto-detected — user does not need to specify them.
    All files are returned as a single ZIP archive.

    Request body (JSON):
        dashboard_id  int   — required unless chart_id is given
        chart_id      int   — optional; exports a single chart view
        format        str   — "pdf", "png", or "all" (default — both)
        theme         str   — "light" (default) or "dark"
        tab_id        str   — optional; id of a tab (from
                               GET /api/dashboard/<id>/tabs). When given, the
                               normal dashboard export above still happens as
                               usual (per `format`), and an EXTRA PNG image of
                               just that one tab is included in the response.

    Response: application/zip
        Structure inside ZIP:
            Per Technology.pdf
            Per Technology.png
            Network.pdf
            Network.png
            Down Cells.pdf
            Down Cells.png
            ...
    """
    body = request.json or {}

    dashboard_id = body.get("dashboard_id")
    chart_id = body.get("chart_id")
    fmt_raw = str(body.get("format", "all")).lower()
    theme = str(body.get("theme", "light")).lower()
    tab_id = body.get("tab_id")

    # --- Validate ---
    if not dashboard_id and not chart_id:
        return jsonify({"error": "dashboard_id or chart_id is required"}), 400

    if fmt_raw not in ("pdf", "png", "all"):
        return jsonify({"error": "format must be 'pdf', 'png', or 'all'"}), 400

    if theme not in ("light", "dark"):
        return jsonify({"error": "theme must be 'light' or 'dark'"}), 400

    if tab_id and not dashboard_id:
        return jsonify({"error": "tab_id requires dashboard_id (charts don't have tabs)"}), 400

    # if dashboard_id is not None and not isinstance(dashboard_id, int):
    #     return jsonify({"error": "dashboard_id must be an integer"}), 400

    # if chart_id is not None and not isinstance(chart_id, int):
    #     return jsonify({"error": "chart_id must be an integer"}), 400

    # Check that the given ID actually exists in Superset
    valid, err_msg = _validate_ids(dashboard_id, chart_id)
    if not valid:
        return jsonify({"error": err_msg}), 404

    # Resolve tab_id -> tab_name (Playwright selects tabs by their visible
    # name, not their internal id) and confirm it actually belongs to this
    # dashboard.
    tab_name = None
    if tab_id:
        try:
            tabs_meta = _get_dashboard_tabs_meta(dashboard_id)
        except Exception as e:
            return jsonify({"error": f"Could not resolve tab_id: {e}"}), 502

        matched = next((t for t in tabs_meta["tabs"] if t["id"] == tab_id), None)
        if not matched:
            return jsonify({
                "error": f"tab_id '{tab_id}' not found on dashboard {dashboard_id}"
            }), 400
        tab_name = matched["name"]

    export_formats = ["pdf", "png"] if fmt_raw == "all" else [fmt_raw]

    log.info(
        "Export request — dashboard_id=%s chart_id=%s formats=%s theme=%s tab_id=%s",
        dashboard_id, chart_id, export_formats, theme, tab_id,
    )

    # Run export
    # result_files = {
    #   "pdf": "/path/dashboard_28_export.pdf"          (single merged PDF)
    #   "png": { "Per Technology": "/path/Per Technology.png", ... }
    #   "tab_png": "/path/dashboard_28_tab_Introduction.png"   (only if tab_id given)
    # }
    try:
        result_files = run_export_with_retry(
            dashboard_id, chart_id, export_formats, theme=theme, extra_tab_name=tab_name
        )
    except Exception as exc:
        log.error("Export failed: %s", exc)
        return jsonify({"error": "Export failed", "details": str(exc)}), 500

    target_id = dashboard_id or chart_id

    # ── Return file paths as JSON ─────────────────────────────────────────────
    response_data = {}

    if "pdf" in result_files:
        # response_data["pdf"] = {"path": result_files["pdf"]}
        response_data["path"] = result_files["pdf"]

    if "png" in result_files:
        png_path = list(result_files["png"].values())[0]
        # response_data["png"] = {"path": png_path}
        response_data["path"] = png_path

    if "tab_png" in result_files:
        response_data["tab"] = {
            "id": tab_id,
            "name": tab_name,
            "format": "png",
            "path": result_files["tab_png"],
        }
    elif "tab_png_error" in result_files:
        response_data["tab"] = {
            "id": tab_id,
            "name": tab_name,
            "error": result_files["tab_png_error"],
        }

    return jsonify(response_data)


@app.route("/serve-file", methods=["GET"])
def serve_file():
    file_path = request.args.get("path")

    if not file_path:
        return jsonify({"error": "Provide 'path' query parameter"}), 400

    real_path = os.path.realpath(file_path)
    real_upload = os.path.realpath(UPLOAD_DIR)
    if not real_path.startswith(real_upload + os.sep) and real_path != real_upload:
        return jsonify({"error": "Access denied: path is outside uploads directory"}), 403

    if not os.path.isfile(real_path):
        return jsonify({"error": f"File not found: {os.path.basename(real_path)}"}), 404

    log.info("Serving file: %s", real_path)
    return send_file(real_path, as_attachment=True, download_name=os.path.basename(real_path))


@app.route("/dashboard")
def embed_dashboard():
    return render_template("embed.html")



if __name__ == "__main__":
    log.info("Starting Flask app on port %d (Superset assumed running)", FLASK_PORT)
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=True)
