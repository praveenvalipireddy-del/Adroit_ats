"""Browser test (Playwright + Microsoft Edge): the Applications tracker window.

Consultants -> "Applications" opens that consultant's tracker: the drafted job is listed as "Draft
created"; changing the status / follow-up date / notes saves; History shows who changed what;
"+ Add application" tracks one made outside the app; "Check Gmail replies" (stubbed, read-only)
marks a replied job "Recruiter responded"; the Excel link points at that consultant; Reporting ->
"All applications" lists every consultant. Gmail is never contacted. Labelled test fixtures.
Run: python test_application_tracker_browser.py
"""
import email
import email.policy
import io
import os
import socket
import sys
import tempfile
import threading
from datetime import date

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_tracker_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import application_tracker as at  # noqa: E402
import docx  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        pass

    def select(self, box, readonly=False):
        assert readonly, "the reply check must open the INBOX read-only"
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        return ("OK", [b"3"]) if '"rec-a@vendor.example"' in criteria else ("OK", [b""])

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP
import reply_inbox  # noqa: E402
assert reply_inbox.imaplib.IMAP4_SSL is FakeIMAP

EMAIL, PASSWORD = "trkui@example.invalid", "TrkUi-Test-1"
admin = models.create_user("Tracker UI Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
d = docx.Document()
d.add_paragraph("VISAKH TRACKUI resume fixture")
buf = io.BytesIO()
d.save(buf)
cid = models.create_candidate("Visakh Trackui", "visakh-trkui@example.invalid", title="DevOps Engineer", gmail_account="visakh-trkui@example.invalid",
                              assigned_user_id=admin_id)
models.update_candidate(cid, gmail_app_password="test-app-password-not-real", country="United States")
models.save_resume_file(cid, "Visakh_original.docx", buf.getvalue())
cid2 = models.create_candidate("Koushik Trackui", "koushik-trkui@example.invalid", title="Java Developer", assigned_user_id=admin_id)
ja = models.save_or_update_scraped_job({"title": "DevOps Lead Trackui", "company": "Vendor Test Co", "source": "Dice", "recruiter_email": "rec-a@vendor.example",
                                        "url": "https://www.dice.com/job-detail/trkui-a", "description": "DevOps"})
ja = ja["id"] if isinstance(ja, dict) else ja
jk = models.save_or_update_scraped_job({"title": "Java Dev Trackui", "company": "Other Test Co", "source": "Dice",
                                        "url": "https://www.dice.com/job-detail/trkui-k", "description": "Java"})
jk = jk["id"] if isinstance(jk, dict) else jk
gm.create_candidate_draft(cid, ja, acting_user_id=admin_id)          # tracked as "Draft created"
conn = models.get_db_connection()
at.upsert(conn, jk, cid2, admin_id, stage="Applied", apply_method="portal")
conn.close()

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "tracker.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


ROW = "#trk-list .trk-item"
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.92.0" in page.content(), "cache-buster not bumped to 5.92.0")
        page.wait_for_function("state.consultants && state.consultants.length >= 2", timeout=15000)

        # Consultants -> Applications & Inbox (Visakh): a short list, details in the side panel
        page.click("a.nav-item[data-tab=candidates]")
        page.click(f".btn-open-tracker[onclick='openTracker({cid})']")
        page.wait_for_selector(ROW, timeout=10000)
        check(page.input_value("#trk-consultant") == str(cid), "opened for Visakh")
        check(page.is_visible("#ai-tab-apps.active") and page.is_visible("#ai-tab-reqs") and page.is_visible("#ai-tab-replies"),
              "one window with Applications / New requirements / Recruiter replies tabs")
        rows = page.locator(ROW)
        txt = rows.first.inner_text()
        check(rows.count() == 1 and "DevOps Lead Trackui" in txt and "Draft created" in txt, f"Visakh's drafted job listed: {txt!r}")
        check("Open Gmail Drafts and press Send" in txt, f"the list says the next step: {txt!r}")
        check("Visakh" not in txt, "no consultant name in the rows for one consultant")
        check(page.inner_text("#ai-count-apps").strip() == "1", f"tab badge = 1: {page.inner_text('#ai-count-apps')!r}")
        check(page.locator("#trk-list select, #trk-list input").count() == 0, "the list has no edit boxes")

        # side panel: details + edits save
        rows.first.click()
        page.wait_for_selector("#trk-detail select[data-f=status]", timeout=5000)
        check(page.input_value("#trk-detail select[data-f=status]") == "Drafted", "status = Draft created in the panel")
        page.select_option("#trk-detail select[data-f=status]", "Applied")
        page.wait_for_timeout(600)
        page.fill("#trk-detail input[data-f=follow_up_date]", date.today().isoformat())
        page.dispatch_event("#trk-detail input[data-f=follow_up_date]", "change")
        page.wait_for_timeout(600)
        page.fill("#trk-detail textarea[data-f=notes]", "Spoke to the vendor, client is Test Bank")
        page.dispatch_event("#trk-detail textarea[data-f=notes]", "change")
        page.wait_for_timeout(800)
        conn = models.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT stage, follow_up_date, notes FROM applications WHERE job_id = ? AND candidate_id = ?", (ja, cid))
        row = dict(cur.fetchone())
        conn.close()
        check(row["stage"] == "Applied" and row["follow_up_date"] == date.today().isoformat() and "Test Bank" in row["notes"], f"saved: {row}")
        page.wait_for_function("document.getElementById('trk-history').innerText.includes('Applied')", timeout=5000)
        hist = page.inner_text("#trk-history")
        check("Draft created -> Applied" in hist and "Tracker UI Admin" in hist, f"history in the panel: {hist}")
        check("Follow up today" in page.inner_text("#trk-list"), "next step follows the follow-up date")

        # Check Gmail now (stubbed, read-only): replies + requirements in one click
        page.click("#ai-check")
        page.wait_for_function("document.getElementById('ai-msg').innerText.includes('new recruiter repl')", timeout=15000)
        page.wait_for_function("document.querySelector('#trk-list .trk-item') && document.querySelector('#trk-list .trk-item').innerText.includes('responded')", timeout=10000)

        # + Add application
        page.click("#trk-add-btn")
        page.fill("#trk-a-title", "Platform Engineer Trackui")
        page.fill("#trk-a-company", "Outside Test Corp")
        page.fill("#trk-a-url", "https://careers.example.invalid/42")
        page.select_option("#trk-a-method", "both")
        page.click("#trk-a-save")
        page.wait_for_function(f"document.querySelectorAll('{ROW}').length === 2", timeout=10000)
        check(page.get_attribute("#trk-export", "href") == f"/api/applications/export-xlsx?candidate_id={cid}", "Excel for this consultant")
        overflow = page.eval_on_selector(".ai-split", "t => t.scrollWidth - t.clientWidth")
        check(overflow <= 2, f"list + panel fit a 1440px window (overflow {overflow}px)")
        page.fill("#trk-search", "platform")
        page.wait_for_function(f"document.querySelectorAll('{ROW}').length === 1", timeout=5000)
        page.fill("#trk-search", "")
        page.screenshot(path=shot)
        page.click("#btn-close-tracker")

        # Reporting -> All applications
        page.click("a.nav-item[data-tab=reporting]")
        page.click("#btn-open-tracker-all")
        page.wait_for_function(f"document.querySelectorAll('{ROW}').length === 3", timeout=10000)
        check("Koushik Trackui" in page.inner_text("#trk-list"), "all consultants: the consultant's name in each row")
        page.check("#trk-due")
        page.wait_for_function(f"document.querySelectorAll('{ROW}').length === 1", timeout=10000)
        check("DevOps Lead Trackui" in page.inner_text("#trk-list"), "follow-ups due filter")
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
print(f"PASS: Applications & Inbox in Edge - tabs, short list with next step, side-panel edits saved, history, Gmail replies, manual add, Excel, all-consultants view, follow-ups due. Screenshot: {shot}")
