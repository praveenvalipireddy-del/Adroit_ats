"""Matched jobs shows which jobs the consultant already applied to, so the recruiter can focus on the rest.

1. An applied job carries "✓ Applied · Applied in portal · <date>" and sits below the jobs not yet applied.
2. The same posting found again on another portal (same title + company, new link) shows as applied too.
3. A job only saved for later says "🔖 Saved" and keeps its place.
4. "Hide them" hides the applied jobs; the choice is remembered; "Show them" brings them back.
5. A job marked applied elsewhere shows after pressing Search (no stale list).
6. The Jobs page shows the same marks for the picked consultant.
Run: python test_applied_jobs_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_applied_jobs.db")
os.environ["DATABASE_URL"] = ""
os.environ["CAREER_SYNC_HOURS"] = "0"
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import application_tracker  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
jid = lambda r: r["id"] if isinstance(r, dict) else r  # noqa: E731
EMAIL, PW = "applied-rec@example.invalid", "Applied-1"
REC = uid(models.create_user("Ava Appliedtest", EMAIL, PW, role="Recruiter", region="India"))
CAND = models.create_candidate("Ravi Appliedfixture", "ravi.fixture@example.invalid", title="Data Analyst", primary_skills="SQL, Power BI, Python",
                               experience_years=4, country="India", assigned_user_id=REC)


def job(title, n, company=None, **kw):
    return jid(models.save_or_update_scraped_job({
        "title": title, "company": company or f"Fixture Co {n}", "source": "Naukri", "url": f"https://www.naukri.com/job-listings-applied-{n}",
        "location": "Hyderabad", "country": "India", "description": "Labelled test fixture. SQL, Power BI and Python, 3-5 years of experience."}, **kw))


APPLIED = job("Data Analyst Applied (AJ fixture)", 1)
DUP = job("Data Analyst Applied (AJ fixture)", 2, company="Fixture Co 1", match_title_company=False)   # the same posting on another portal
SAVED = job("Data Analyst Saved (AJ fixture)", 3)
FRESH = job("Data Analyst Fresh (AJ fixture)", 4)
LATER = job("Data Analyst Later (AJ fixture)", 5)
check(DUP != APPLIED, "fixture: the duplicate posting is its own row")
conn = models.get_db_connection()
application_tracker.upsert(conn, APPLIED, CAND, REC, stage="Applied", apply_method="portal")
application_tracker.upsert(conn, SAVED, CAND, REC, stage="Saved")
conn.close()

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()

TITLES = "els => els.map(e => e.closest('.cons-job').querySelector('.cons-job-title').innerText)"
CARD = "#cons-jobs-list .cons-job:has(.cons-job-title:text-is('{}'))"
js_errors, shot = [], os.path.join(tmp_dir, "applied.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.19" in page.content(), "cache-buster not bumped to 5.99.19")

        page.wait_for_selector(".cons-card:has-text('Ravi Appliedfixture')", timeout=15000)
        page.locator(".cons-card:has-text('Ravi Appliedfixture')").first.click()
        page.wait_for_function("document.querySelectorAll('#cons-jobs-list .cons-job').length >= 4", timeout=15000)

        # 1-3: marks and order
        applied_cards = page.locator("#cons-jobs-list .cons-job.is-applied")
        check(applied_cards.count() == 2, f"the applied job and its copy on another portal are marked: {applied_cards.count()}")
        status = page.locator("#cons-jobs-list .cons-job.is-applied .cons-job-status").first.inner_text()
        check(status.startswith("✓ Applied in portal") and "Applied · Applied" not in status, f"applied badge: {status!r}")
        saved = page.locator(CARD.format("Data Analyst Saved (AJ fixture)"))
        check("🔖 Saved" in saved.inner_text() and saved.get_attribute("class").find("is-applied") < 0, "saved job: Saved badge, not counted as applied")
        check(page.locator(CARD.format("Data Analyst Fresh (AJ fixture)") + " .cons-job-status").count() == 0, "a fresh job has no badge")
        order = page.eval_on_selector_all("#cons-jobs-list .cons-job .cons-job-title", "els => els.map(e => e.innerText)")
        first_applied = next(i for i, t in enumerate(order) if "Applied (AJ" in t)
        check(all("Applied (AJ" in t for t in order[first_applied:]), f"applied jobs at the bottom: {order}")
        bar = page.inner_text("#cons-jobs-list .cons-applied-bar")
        check("Already applied to 2" in bar, f"bar counts the applied jobs: {bar!r}")
        page.screenshot(path=shot, full_page=True)

        # 4: hide, remembered, show
        page.click("#cons-jobs-list .cons-applied-toggle")
        check(page.locator("#cons-jobs-list .cons-job.is-applied").count() == 0, "Hide them hides the applied jobs")
        check(page.locator("#cons-jobs-list .cons-job").count() >= 3, "the other jobs stay")
        page.click("#cons-jf-go")
        page.wait_for_timeout(1200)
        check(page.locator("#cons-jobs-list .cons-job.is-applied").count() == 0 and "hidden" in page.inner_text("#cons-jobs-list .cons-applied-bar"),
              "hiding is remembered on the next search")

        # 5: applied elsewhere (job page / extension) -> Search shows it
        page.evaluate(f"fetch('/api/jobs/{LATER}/apply-status', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{status:'email', candidate_id:{CAND}}})}})")
        page.wait_for_timeout(500)
        page.click("#cons-jf-go")
        page.wait_for_function("document.querySelector('#cons-jobs-list .cons-applied-bar') && document.querySelector('#cons-jobs-list .cons-applied-bar').innerText.includes('Already applied to 3')", timeout=15000)
        check(page.locator(CARD.format("Data Analyst Later (AJ fixture)")).count() == 0, "the newly applied job is hidden too")
        page.click("#cons-jobs-list .cons-applied-toggle")
        later = page.locator(CARD.format("Data Analyst Later (AJ fixture)") + " .cons-job-status")
        check(later.count() == 1 and "Email sent" in later.inner_text(), "Show them brings it back, marked Email sent")

        # 6: the Jobs page, same consultant
        page.click("a.nav-item[data-tab=jobs]")
        page.select_option("#jobs-side-consultant", str(CAND))
        page.wait_for_function("document.querySelectorAll('#jobs-cards .cons-job.is-applied').length === 3", timeout=15000)
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
print(f"PASS: applied jobs are marked (Edge). Screenshot: {shot}")
