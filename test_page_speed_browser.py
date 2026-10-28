"""The page loads only what it needs (labelled fixtures, local server).

1. Versioned static files (app.js?v=, style.css?v=<fingerprint>) are kept by the browser for a year; an unversioned
   one is re-checked. app.js used to be sent with no-store (downloaded again on every page open).
2. style.css's version is a fingerprint of the file, so a CSS change always reaches the browser.
3. Opening Job Hunt (the Consultants cards): no jobs search, no Word-preview libraries, no outside photos, one font
   request. People without a picture get initials, not a stock photo.
4. The Word-preview libraries load the first time a preview needs them.
Run: python test_page_speed_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_speed.db")
os.environ["DATABASE_URL"] = ""
os.environ["CAREER_SYNC_HOURS"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "speed-rec@example.invalid", "Speed-1"
REC = uid(models.create_user("Sam Speedtest", EMAIL, PW, role="Recruiter", region="USA"))
models.create_candidate("Kim Speedfixture", "kim.speed@example.invalid", title="Data Analyst", country="United States", assigned_user_id=REC)

c = app_module.app.test_client()
r = c.get("/static/js/app.js?v=5.99.8")
check(r.status_code == 200 and "immutable" in r.headers.get("Cache-Control", "") and "max-age=31536000" in r.headers.get("Cache-Control", ""),
      f"versioned app.js cached for a year: {r.headers.get('Cache-Control')}")
r = c.get("/static/js/app.js")
check(r.headers.get("Cache-Control") == "no-cache", f"unversioned: re-checked: {r.headers.get('Cache-Control')}")
v = app_module.ASSET_VERSIONS["css"]
check(len(v) == 10 and v != "0", f"css fingerprint: {v}")
check(c.get(f"/static/css/style.css?v={v}").status_code == 200, "css served")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, urls = [], []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        page.on("request", lambda rq: urls.append(rq.url))
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".cons-card")
        html = page.content()
        check(f"style.css?v={v}" in html and "app.js?v=5.99.8" in html, "versioned css + js on the page")
        check(not any("/api/jobs/search" in u for u in urls), f"no jobs search on page open: {[u for u in urls if '/api/' in u]}")
        check(not any("/static/vendor/" in u for u in urls), "no Word-preview libraries on page open")
        check(not any("unsplash" in u for u in urls), "no stock photos")
        check(sum(1 for u in urls if "fonts.googleapis.com/css" in u) == 1, f"one font request: {[u for u in urls if 'fonts.' in u]}")
        check(page.locator(".sidebar-footer .avatar-initials").count() == 1 and page.inner_text(".sidebar-footer .avatar-initials") == "SS",
              "initials instead of a stock photo")
        # the Jobs tab still searches when opened
        page.click("a.nav-item[data-tab=jobs]")
        page.wait_for_function("window.performance.getEntriesByType('resource').some(e => e.name.includes('/api/jobs/search'))", timeout=15000)
        # the preview libraries arrive when needed
        check(page.evaluate("typeof window.docx") == "undefined", "docx-preview not loaded yet")
        page.evaluate("loadDocxPreview()")
        page.wait_for_function("window.docx && typeof window.docx.renderAsync === 'function'", timeout=15000)
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: page speed - versioned static files cached a year (app.js no longer no-store), css fingerprint, no jobs search / "
      "preview libraries / stock photos on page open, one font request, initials, preview libraries load on demand.")
