"""Browser test (Playwright + Microsoft Edge): Consultants -> Browse Jobs -> Optimize Resume.

1. Clicking "Browse Jobs" on a consultant (Karun) makes Karun the Target Candidate on every job row
   (it used to stay on the previously selected / first consultant).
2. The Jobs table has a "Resume" column right after "Recruiter Email" with an "Optimize Resume"
   button. It opens the Resume Optimizer with the row's Target Candidate selected (their resume on
   file loaded) and the job description filled in:
     - a pasted requirement ("Manual Paste") -> its full text, green note;
     - a scraped posting (only a one-line summary is stored) -> summary + posting link, and an
       amber note asking for the full description.
   It never runs the AI by itself (no /api/resume-bot/optimize call).

COST SAFETY: live job scrapers are replaced by stubs, and every request to the AI optimizer,
Apify or any non-local host is aborted. Consultants and jobs are labelled test fixtures.
Run: python test_jobs_optimizer_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_jobs_opt.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = ""
import app as app_module  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

# no live scraping during the test
us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}

# Posting reads are stubbed (no network): the Dice test posting "has" a full description, the
# LinkedIn one is gone. Counts calls so the test can prove the 2nd click is served from the DB.
import job_description_fetch  # noqa: E402

DICE_FULL = ("FULL DICE DESCRIPTION (test fixture)\nResponsibilities:\n- Build Power BI dashboards\n- Write complex SQL\n"
             "Requirements: 5+ years SQL and Python. " + "Detail. " * 30)
fetch_calls = []


def _fake_fetch(url):
    fetch_calls.append(url)
    if url and "00000000-0000-4000-8000-000000000001" in url:
        return DICE_FULL, ""
    return None, "The posting is no longer available."


job_description_fetch.fetch_full_description = _fake_fetch

EMAIL, PASSWORD = "jobs-opt-admin@example.invalid", "JobsOpt-Test-1"
admin = models.create_user("Jobs Opt Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin

first_id = models.create_candidate("Aaa First Testcase", "first@example.invalid", title="Java Developer", assigned_user_id=admin_id)
# Like the real Karun: a resume FILE on record but no extracted text (the optimizer must still load it).
import io  # noqa: E402

import docx  # noqa: E402

_doc = docx.Document()
_doc.add_paragraph("KARUN TEST RESUME")
_doc.add_paragraph("Data Analyst with SQL, Python and Power BI (test fixture).")
_buf = io.BytesIO()
_doc.save(_buf)
karun_id = models.create_candidate("Karun Testcase", "karun@example.invalid", title="Data Analyst", assigned_user_id=admin_id,
                                   resume_filename="karun_test.docx")
models.save_resume_file(karun_id, "karun_test.docx", _buf.getvalue())
FULL_JD = ("Data Analyst (Contract) - Test Client. Responsibilities: build Power BI dashboards, write complex SQL, "
           "automate reporting in Python, partner with finance stakeholders, document data lineage, validate data quality, "
           "support month-end close analytics, and present insights to leadership. Requirements: 5+ years SQL, Power BI, "
           "Python (pandas), Azure Synapse a plus, strong communication. This is a pasted test requirement.")
assert len(FULL_JD) >= 400
pasted_id = models.save_or_update_scraped_job({
    "title": "Data Analyst Pasted Test", "company": "Test Client", "location": "Remote", "job_type": "Contract (C2C)",
    "salary": "$60/hr", "source": "Manual Paste", "url": "", "recruiter_email": "rec@example.invalid",
    "description": FULL_JD, "matched_skills": "Data Analyst", "match_score": 95})
scraped_id = models.save_or_update_scraped_job({
    "title": "Data Analyst Scraped Test", "company": "Test Corp", "location": "Dallas, TX", "job_type": "Contract",
    "salary": "$55/hr", "source": "Dice", "url": "https://www.dice.com/job-detail/00000000-0000-4000-8000-000000000001",
    "description": "Dice US Contract Requisition: Data Analyst Scraped Test at Test Corp (Dallas, TX). Pay Rate: $55/hr",
    "matched_skills": "Data Analyst", "match_score": 90})
gone_id = models.save_or_update_scraped_job({
    "title": "Data Analyst Gone Test", "company": "Gone Corp", "location": "Remote", "source": "LinkedIn (Live 24h)",
    "url": "https://www.linkedin.com/jobs/view/data-analyst-gone-test-9999999999", "description": "Summary only (test).",
    "matched_skills": "Data Analyst", "match_score": 90})
gone_id = gone_id["id"] if isinstance(gone_id, dict) else gone_id
pasted_id = pasted_id["id"] if isinstance(pasted_id, dict) else pasted_id
scraped_id = scraped_id["id"] if isinstance(scraped_id, dict) else scraped_id

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"

failures, js_errors, blocked = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def guard(route):
    url = route.request.url
    if url.startswith(base) and "/api/resume-bot/optimize" not in url:
        route.continue_()
    elif url.startswith(base) or "api.apify.com" in url:
        blocked.append(url)
        route.abort()
    else:
        route.continue_()   # fonts/CDN for rendering only


shot = os.path.join(tmp_dir, "optimizer.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.route("**/*", guard)

        page.goto(base + "/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.61.0" in page.content(), "cache-buster not bumped to 5.61.0")

        # 1. Browse Jobs on Karun (not the first consultant)
        page.click("a.nav-item[data-tab=candidates]")
        btn = page.locator(f"button[onclick^='browseJobsForCandidate({karun_id},']").first
        btn.wait_for(state="visible", timeout=10000)
        btn.click()
        page.wait_for_selector(f".job-row[data-job-id='{pasted_id}']", timeout=20000)
        page.wait_for_timeout(500)
        selected = page.eval_on_selector_all(".job-consultant-select", "els => els.map(e => e.value)")
        check(selected and all(v == str(karun_id) for v in selected),
              f"every row's Target Candidate should be Karun ({karun_id}) after Browse Jobs, got {selected}")

        # 2. column order: Resume right after Recruiter Email
        headers = [h.strip() for h in page.eval_on_selector_all("#jobs-data-table thead th", "els => els.map(e => e.textContent)")]
        i = next((k for k, h in enumerate(headers) if h.startswith("Recruiter Email")), -1)
        check(i >= 0 and headers[i + 1] == "Resume", f"Resume column should follow Recruiter Email: {headers}")
        cells = page.locator(f".job-row[data-job-id='{pasted_id}'] td").count()
        check(cells == len(headers), f"row has {cells} cells for {len(headers)} headers")

        # 3a. pasted requirement -> full JD, Karun's resume, no AI call
        page.click(f".job-row[data-job-id='{pasted_id}'] .btn-optimize-job")
        page.wait_for_selector("#tab-resumebot.active", timeout=5000)
        page.wait_for_function("document.getElementById('resumebot-resume-text').value.includes('KARUN TEST RESUME')", timeout=10000)
        check(page.input_value("#resumebot-consultant-select") == str(karun_id), "optimizer should have Karun selected")
        check(page.input_value("#resumebot-jd-text") == FULL_JD, "pasted requirement's full description should be filled in")
        note = page.inner_text("#resumebot-jd-note")
        check("filled in" in note and "Data Analyst Pasted Test" in note, f"full-JD note: {note!r}")
        page.screenshot(path=shot)

        # 3b. Dice posting -> the full description is read from the posting and filled in
        page.click("a.nav-item[data-tab=jobs]")
        page.wait_for_selector(f".job-row[data-job-id='{scraped_id}']")
        page.click(f".job-row[data-job-id='{scraped_id}'] .btn-optimize-job")
        page.wait_for_selector("#tab-resumebot.active", timeout=5000)
        page.wait_for_function("document.getElementById('resumebot-jd-text').value.startsWith('FULL DICE DESCRIPTION')", timeout=10000)
        note = page.inner_text("#resumebot-jd-note")
        check("Full job description read from the posting" in note, f"fetched-JD note: {note!r}")
        check(page.get_attribute("#resumebot-jd-note a", "href") == "https://www.dice.com/job-detail/00000000-0000-4000-8000-000000000001",
              "posting link in note")
        check(not page.is_disabled("#btn-run-resume-optimization"), "Optimize button must be enabled again after reading")
        page.screenshot(path=shot)
        # second click: served from the database, no second read of the posting
        page.click("a.nav-item[data-tab=jobs]")
        page.click(f".job-row[data-job-id='{scraped_id}'] .btn-optimize-job")
        page.wait_for_function("document.getElementById('resumebot-jd-text').value.startsWith('FULL DICE DESCRIPTION')", timeout=10000)
        check(len(fetch_calls) == 1, f"the posting should be read once, then reused: {fetch_calls}")

        # 3c. LinkedIn posting that can't be read -> summary stays, amber note with the reason
        page.click("a.nav-item[data-tab=jobs]")
        page.click(f".job-row[data-job-id='{gone_id}'] .btn-optimize-job")
        page.wait_for_function("document.getElementById('resumebot-jd-note').innerText.includes('no longer available')", timeout=10000)
        jd = page.input_value("#resumebot-jd-text")
        check("Data Analyst Gone Test - Gone Corp" in jd and "Summary only (test)." in jd, f"summary kept on failure: {jd!r}")
        check("Only a short summary" in page.inner_text("#resumebot-jd-note"), "paste instruction on failure")
        check(not page.is_disabled("#btn-run-resume-optimization"), "Optimize button re-enabled after a failed read")
        page.wait_for_timeout(500)
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
check(not blocked, f"an AI/paid call was attempted (blocked): {blocked}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: Browse Jobs selects the clicked consultant; Optimize Resume fills the full JD (pasted / read from posting once / honest fallback); no AI call. Screenshot: {shot}")
