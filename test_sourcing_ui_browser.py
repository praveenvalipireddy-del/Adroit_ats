"""Browser test (Playwright + Microsoft Edge) for the Sourcing tab after People Data Labs was removed.

Starts the real Flask app on a throwaway SQLite DB, logs in through the real login form, opens
Sourcing and checks: no JavaScript errors, the data-source list offers Apify only, the search-depth
control is visible, no PDL text anywhere, and the team pool (incl. people PDL found earlier) shows.

COST SAFETY: "Search LinkedIn" is never clicked, every request to api.apify.com and the paid
search endpoints is aborted, and the Apify token is a dummy value - this test cannot spend money.
Run: python test_sourcing_ui_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = "dummy-test-token-not-real"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = "dummy-test-token-not-real"
import app as app_module  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

EMAIL, PASSWORD = "ui-test-admin@example.invalid", "UiTest-Only-Pass-123"
models.create_user("UI Test Admin", EMAIL, PASSWORD, role="Admin")
conn = models.get_db_connection()
cur = conn.cursor()
for src, slug, name in (("pdl", "a", "Test Pool Person From PDL"), ("apify", "b", "Test Pool Person From Apify")):
    cur.execute("INSERT INTO sourced_candidates (source, search_year, profile_url, name, bachelor_year, bachelor_college, master_university) "
                "VALUES (?, '2019', ?, ?, '2019', 'Test College (India)', 'Test University (USA)')",
                (src, f"https://www.linkedin.com/in/test-only-{slug}.example.invalid", name))
conn.commit()
conn.close()

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"

failures, js_errors, blocked = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def block(route):
    blocked.append(route.request.url)
    route.abort()


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.route("**/api.apify.com/**", block)
        page.route("**/api/students/search-start**", block)
        page.route("**/api/students/search-poll**", block)

        page.goto(base + "/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")

        check("app.js?v=5.34.0" in page.content(), "cache-buster not bumped to app.js?v=5.34.0")

        page.click("a.nav-item[data-tab=sourcing]")
        page.select_option("#filter-student-bachelor-year", "2019")
        page.wait_for_function(
            "document.querySelector('#students-table-body') && "
            "document.querySelector('#students-table-body').innerText.includes('Test Pool Person From Apify')",
            timeout=15000)

        options = page.eval_on_selector_all("#filter-student-source option", "els => els.map(e => [e.value, e.textContent.trim()])")
        check(options == [["apify", "Apify LinkedIn (paid: about $0.20 per 25 profiles)"]], f"data-source options: {options}")
        check(page.is_visible("#filter-student-depth"), "Apify search-depth control is not visible")
        check(page.query_selector("#filter-student-pdl-size") is None, "PDL records-to-fetch control still on the page")

        table = page.inner_text("#students-table-body")
        check("Test Pool Person From PDL" in table, "person found earlier by PDL is missing from the pool")
        check("Test Pool Person From Apify" in table, "Apify pool person missing")

        body = page.inner_text("body")
        check("People Data Labs" not in body, "page text still mentions People Data Labs")
        status = page.inner_text("#students-search-status") if page.is_visible("#students-search-status") else ""
        check("People Data Labs" not in status, f"status line mentions PDL: {status}")

        page.screenshot(path=os.path.join(tmp_dir, "sourcing.png"), full_page=False)
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
check(not blocked, f"a paid Apify call was attempted (blocked): {blocked}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", os.path.join(tmp_dir, "sourcing.png"))
    sys.exit(1)
print(f"PASS: Sourcing tab in Edge - Apify-only source, depth control shown, pool incl. PDL-found people, no JS errors, no paid calls. Screenshot: {os.path.join(tmp_dir, 'sourcing.png')}")
