"""Browser test (Playwright + Microsoft Edge): Sourcing tracker - change a candidate's status,
open the comment box, add a comment, see it in the row, filter by status; a comment with HTML is
shown as text. No network beyond the local app. People are labelled test fixtures.
Run: python test_sourcing_tracker_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_trk_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import linkedin_ingest as li  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

EMAIL, PASSWORD = "trk-ui@example.invalid", "TrkUi-Test-1"
models.create_user("Trk Recruiter", EMAIL, PASSWORD, role="Recruiter")


def item(slug, first):
    return {"linkedinUrl": f"https://www.linkedin.com/in/{slug}.example.invalid", "firstName": first, "lastName": "Uitrk",
            "headline": "Data Engineer", "location": {"linkedinText": "Dallas, Texas"}, "currentPosition": [],
            "education": [{"schoolName": "CBIT", "degree": "B.Tech", "endDate": {"year": 2020}},
                          {"schoolName": "UNT", "degree": "MS", "endDate": {"year": 2023}}]}


li.ingest_profiles(li.ApifyProfileProvider().to_profiles([item("trk-ui-1", "Ravi"), item("trk-ui-2", "Sita")]), "apify", None)

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
failures, js_errors, dialogs = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


shot = os.path.join(tmp_dir, "tracker.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        page.goto(base + "/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.44.0" in page.content(), "cache-buster not bumped to 5.44.0")

        page.click("a.nav-item[data-tab=sourcing]")
        page.click(".edu-tab[data-filter=A]")
        page.fill("#edu-inst-input", "CBIT")
        page.wait_for_selector(".edu-inst-option")
        page.click(".edu-inst-option")
        page.wait_for_function("document.querySelectorAll('#edu-results tr[data-pid]').length === 2")
        headers = page.eval_on_selector_all("#edu-table thead th", "els => els.map(e => e.textContent.trim())")
        check(headers[-3:] == ["Status", "Comments", "Captured"], f"headers: {headers}")

        ravi = page.locator("#edu-results tr[data-pid]", has_text="Ravi Uitrk")
        pid = ravi.get_attribute("data-pid")
        check(ravi.locator(".trk-status").input_value() == "New", "default status New")

        # status change
        ravi.locator(".trk-status").select_option("Interested")
        page.wait_for_function(f"document.querySelector('.trk-status[data-id=\"{pid}\"]').getAttribute('data-current') === 'Interested'")

        # comment box
        ravi.locator(".trk-open").click()
        page.wait_for_selector(f"#trk-row-{pid}", state="visible")
        page.wait_for_function(f"document.getElementById('trk-thread-{pid}').innerText.includes('Status: New')")
        page.fill(f"#trk-new-{pid}", "Called - interested, expects $60/hr <b>bold?</b>")
        page.click(f".trk-save[data-id='{pid}']")
        page.wait_for_function(f"document.getElementById('trk-thread-{pid}').innerText.includes('expects $60/hr')")
        thread_html = page.inner_html(f"#trk-thread-{pid}")
        check("&lt;b&gt;bold?&lt;/b&gt;" in thread_html and "<b>bold?</b>" not in thread_html, "comment HTML must be shown as text")
        check("Trk Recruiter" in page.inner_text(f"#trk-thread-{pid}") and page.locator(f"#trk-thread-{pid} .trk-del").count() == 1,
              "author shown + delete on own comment")
        cell = ravi.locator(".trk-comments").inner_text()
        check("💬 1" in cell and "expects $60/hr" in cell and "Trk Recruiter" in cell, f"row comment cell: {cell!r}")
        # the comment panel stays fully inside the visible table area even when the table scrolls sideways
        page.eval_on_selector("#edu-results-wrap", "el => { el.scrollLeft = el.scrollWidth; }")
        page.wait_for_timeout(200)
        inside = page.evaluate(f"""() => {{ const w = document.getElementById('edu-results-wrap').getBoundingClientRect();
            const p = document.querySelector('#trk-row-{pid} .trk-panel').getBoundingClientRect();
            return p.left >= w.left - 1 && p.right <= w.right + 1; }}""")
        check(inside, "comment panel must stay inside the visible table area when scrolled sideways")
        page.screenshot(path=shot)

        # status filter + persistence after a fresh search
        page.select_option("#edu-status-filter", "Interested")
        page.wait_for_function("document.querySelectorAll('#edu-results tr[data-pid]').length === 1")
        check("Ravi Uitrk" in page.inner_text("#edu-results") and "Sita Uitrk" not in page.inner_text("#edu-results"), "status filter")
        page.select_option("#edu-status-filter", "New")
        page.wait_for_function("document.querySelectorAll('#edu-results tr[data-pid]').length === 1 && document.getElementById('edu-results').innerText.includes('Sita')")
        page.select_option("#edu-status-filter", "")
        page.wait_for_function("document.querySelectorAll('#edu-results tr[data-pid]').length === 2")
        page.reload()
        page.click("a.nav-item[data-tab=sourcing]")
        page.click(".edu-tab[data-filter=A]")
        page.fill("#edu-inst-input", "CBIT")
        page.wait_for_selector(".edu-inst-option")
        page.click(".edu-inst-option")
        page.wait_for_function("document.querySelectorAll('#edu-results tr[data-pid]').length === 2")
        check(page.locator(f".trk-status[data-id='{pid}']").input_value() == "Interested", "status kept after reload")

        # delete own comment (confirm dialog accepted)
        page.locator(f"#edu-results tr[data-pid='{pid}'] .trk-open").click()
        page.wait_for_selector(f"#trk-thread-{pid} .trk-del")
        page.click(f"#trk-thread-{pid} .trk-del")
        page.wait_for_function(f"!document.getElementById('trk-thread-{pid}').innerText.includes('expects $60/hr')")
        check(dialogs and "Delete this comment?" in dialogs[-1], f"confirm dialog: {dialogs}")
        check("💬 Add" in page.locator(f"#edu-results tr[data-pid='{pid}'] .trk-comments").inner_text(), "cell resets after delete")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: Sourcing tracker in Edge - status change, comments (safe text), row summary, status filter, kept after reload, delete. Screenshot: {shot}")
