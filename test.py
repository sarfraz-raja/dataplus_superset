# from playwright.sync_api import sync_playwright
# from pathlib import Path
# import os

# SUPERSET_URL = "http://dyserver:8088"
# USERNAME = "admin"
# PASSWORD = "admin123"


# def export_dashboard(
#     dashboard_id: int,
#     export_type: str = "pdf",   # pdf | png
#     output_dir: str = os.path.join(os.getcwd(), "uploads", "dashboards"),
# ):
#     Path(output_dir).mkdir(parents=True, exist_ok=True)

#     ext = "pdf" if export_type == "pdf" else "png"

#     output_file = os.path.join(
#         output_dir,
#         f"dashboard_{dashboard_id}.{ext}"
#     )

#     with sync_playwright() as p:

#         browser = p.chromium.launch(
#             headless=True,
#             args=[
#                 "--no-sandbox",
#                 "--disable-dev-shm-usage",
#                 "--disable-gpu",
#             ],
#         )

#         context = browser.new_context(
#             accept_downloads=True,
#             viewport={
#                 "width": 1920,
#                 "height": 1080,
#             },
#         )

#         page = context.new_page()

#         # -------------------
#         # LOGIN
#         # -------------------

#         page.goto(
#             f"{SUPERSET_URL}/login/",
#             wait_until="networkidle",
#             timeout=120000,
#         )

#         page.fill("#username", USERNAME)
#         page.fill("#password", PASSWORD)

#         page.locator("button[type='submit']").click()

#         page.wait_for_timeout(5000)

#         # -------------------
#         # DASHBOARD
#         # -------------------

#         page.goto(
#             f"{SUPERSET_URL}/superset/dashboard/{dashboard_id}/",
#             wait_until="networkidle",
#             timeout=120000,
#         )

#         page.wait_for_timeout(15000)

#         # -------------------
#         # OPEN 3 DOT MENU
#         # -------------------

#         menu_candidates = [
#             '[aria-haspopup="menu"]',
#             '[aria-label*="menu"]',
#             '[aria-label*="Menu"]',
#         ]

#         menu_found = False

#         for selector in menu_candidates:

#             try:
#                 count = page.locator(selector).count()

#                 if count > 0:
#                     page.locator(selector).last.click()
#                     page.wait_for_timeout(2000)
#                     menu_found = True
#                     break

#             except Exception:
#                 pass

#         if not menu_found:
#             raise Exception(
#                 "Unable to open dashboard action menu. Inspect 3-dot button selector."
#             )

#         # -------------------
#         # DOWNLOAD MENU
#         # -------------------

#         try:
#             page.get_by_text(
#                 "Download",
#                 exact=True
#             ).hover()

#             page.wait_for_timeout(1500)

#         except Exception:

#             page.get_by_text(
#                 "Download",
#                 exact=True
#             ).click()

#             page.wait_for_timeout(1500)

#         # -------------------
#         # EXPORT
#         # -------------------

#         with page.expect_download(
#             timeout=120000
#         ) as download_info:

#             if export_type.lower() == "pdf":

#                 page.get_by_text(
#                     "Export to PDF",
#                     exact=True
#                 ).click()

#             else:

#                 page.get_by_text(
#                     "Download as Image",
#                     exact=True
#                 ).click()

#         download = download_info.value

#         download.save_as(output_file)

#         browser.close()

#     return output_file


# if __name__ == "__main__":

#     pdf = export_dashboard(
#         dashboard_id=28,
#         export_type="pdf"
#     )

#     print("PDF:", pdf)

#     png = export_dashboard(
#         dashboard_id=28,
#         export_type="png"
#     )

#     print("PNG:", png)



from playwright.sync_api import sync_playwright
from pathlib import Path
import os

SUPERSET_URL = "http://dyserver:8088"
USERNAME = "admin"
PASSWORD = "admin123"

SESSION_FILE = "superset_session.json"


def ensure_login(page, context, dashboard_id):
    """
    Dashboard open karo.
    Agar login page pe redirect ho gaya to login karo.
    """

    dashboard_url = (
        f"{SUPERSET_URL}/superset/dashboard/{dashboard_id}/"
    )

    page.goto(
        dashboard_url,
        wait_until="networkidle",
        timeout=120000,
    )

    # Session expired / not logged in
    if "/login" in page.url:

        print("Session expired. Logging in...")

        page.wait_for_selector(
            "#username",
            timeout=60000
        )

        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)

        page.locator(
            "button[type='submit']"
        ).click()

        page.wait_for_timeout(5000)

        page.goto(
            dashboard_url,
            wait_until="networkidle",
            timeout=120000,
        )

        # Save session
        context.storage_state(
            path=SESSION_FILE
        )

        print("Login successful. Session saved.")

    else:

        print("Already logged in.")


def open_download_menu(page):

    menu_candidates = [
        '[aria-haspopup="menu"]',
        '[aria-label*="menu"]',
        '[aria-label*="Menu"]',
    ]

    menu_found = False

    for selector in menu_candidates:

        try:

            count = page.locator(
                selector
            ).count()

            if count > 0:

                page.locator(
                    selector
                ).last.click()

                page.wait_for_timeout(
                    2000
                )

                menu_found = True

                break

        except Exception:
            pass

    if not menu_found:
        raise Exception(
            "Unable to open dashboard action menu."
        )

    try:

        page.get_by_text(
            "Download",
            exact=True
        ).hover()

        page.wait_for_timeout(
            1500
        )

    except Exception:

        page.get_by_text(
            "Download",
            exact=True
        ).click()

        page.wait_for_timeout(
            1500
        )


def export_file(
    page,
    export_type,
    output_dir
):

    with page.expect_download(
        timeout=120000
    ) as download_info:

        if export_type == "pdf":

            page.get_by_text(
                "Export to PDF",
                exact=True
            ).click()

        elif export_type == "png":

            page.get_by_text(
                "Download as Image",
                exact=True
            ).click()

        else:

            raise ValueError(
                "export_type must be pdf or png"
            )

    download = download_info.value

    filename = (
        download.suggested_filename
    )

    output_file = os.path.join(
        output_dir,
        filename
    )

    download.save_as(
        output_file
    )

    print(
        f"{export_type.upper()} saved:",
        output_file
    )

    return output_file


def export_dashboard(
    dashboard_id: int,
    output_dir: str = os.path.join(
        os.getcwd(),
        "uploads",
        "dashboards"
    ),
):

    Path(output_dir).mkdir(
        parents=True,
        exist_ok=True
    )

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        # Reuse session if available
        if os.path.exists(
            SESSION_FILE
        ):

            context = (
                browser.new_context(
                    accept_downloads=True,
                    viewport={
                        "width": 1920,
                        "height": 1080,
                    },
                    storage_state=SESSION_FILE,
                )
            )

            print(
                "Using saved session..."
            )

        else:

            context = (
                browser.new_context(
                    accept_downloads=True,
                    viewport={
                        "width": 1920,
                        "height": 1080,
                    },
                )
            )

            print(
                "No session file found."
            )

        page = context.new_page()

        # Login only if needed
        ensure_login(
            page,
            context,
            dashboard_id
        )

        page.wait_for_timeout(
            15000
        )

        # Load full dashboard
        previous_height = 0

        while True:

            current_height = (
                page.evaluate(
                    "document.body.scrollHeight"
                )
            )

            if (
                current_height
                == previous_height
            ):
                break

            page.evaluate(
                f"window.scrollTo(0,{current_height})"
            )

            page.wait_for_timeout(
                2000
            )

            previous_height = (
                current_height
            )

        page.evaluate(
            "window.scrollTo(0,0)"
        )

        page.wait_for_timeout(
            3000
        )

        # --------------------
        # PDF
        # --------------------

        open_download_menu(
            page
        )

        pdf_file = export_file(
            page,
            export_type="pdf",
            output_dir=output_dir,
        )

        page.wait_for_timeout(
            3000
        )

        # --------------------
        # PNG
        # --------------------

        open_download_menu(
            page
        )

        png_file = export_file(
            page,
            export_type="png",
            output_dir=output_dir,
        )

        browser.close()

    return {
        "pdf": pdf_file,
        "png": png_file,
    }


if __name__ == "__main__":

    result = export_dashboard(
        dashboard_id=28
    )

    print(result)

