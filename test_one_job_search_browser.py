"""One job search: the Jobs page and a consultant's Matched jobs show the same filter bar, the same cards and the same jobs.

1. Both screens have the same filters, in the same order, including Experience.
2. Experience fills in with the consultant's years, can be changed, and hides jobs that need clearly more.
3. On the Jobs page the consultant sets market, role and experience; the job list then matches their Matched jobs.
4. Opening a job from a consultant's page and then going to Jobs doesn't leave a blank page.
5. "All consultants" shows the market choice and no match; Tailor there asks for a consultant first.
Run: python test_one_job_search_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_one_search.db")
os.environ["DATABASE_URL"] = ""
os.environ["CAREER_SYNC_HOURS"] = "0"
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
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
EMAIL, PW = "onesearch-rec@example.invalid", "OneSearch-1"
REC = uid(models.create_user("Oni Searchtest", EMAIL, PW, role="Recruiter", region="USA"))
_c = models.get_db_connection()
_c.cursor().execute("UPDATE users SET region = 'Both' WHERE id = ?", (REC,))
_c.commit()
_c.close()
INDIA = models.create_candidate("Asha Indiafixture", "asha.fixture@example.invalid", title="Data Analyst", primary_skills="SQL, Power BI",
                                experience_years=4, country="India", assigned_user_id=REC)
US = models.create_candidate("Ben Usfixture", "ben.fixture@example.invalid", title="Java Developer", primary_skills="Java, Spring",
                             experience_years=5, country="United States", assigned_user_id=REC)


def job(title, country, n, description):
    models.save_or_update_scraped_job({"title": title, "company": f"Fixture Co {n}", "source": "Dice", "url": f"https://www.dice.com/job-detail/one-search-{n}",
                                       "location": "Hyderabad" if country == "India" else "Dallas, TX", "job_type": "Contract", "country": country,
                                       "description": description})


job("Data Analyst Fits (OS fixture)", "India", 1, "Labelled test fixture. SQL and Power BI, 3-5 years of experience.")
job("Data Analyst Needs More (OS fixture)", "India", 2, "Labelled test fixture. SQL, requires 12+ years of experience.")
job("Java Developer US (OS fixture)", "United States", 3, "Labelled test fixture. Java and Spring, 3-5 years of experience.")
job("Data Analyst US (OS fixture)", "United States", 4, "Labelled test fixture. Must not reach the India consultant.")

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()

FIELDS = ["ROLE / SKILLS", "ROLES", "LOCATION", "POSTED WITHIN", "WORK MODEL", "JOB TYPE", "EXPERIENCE", "SOURCE"]
LABELS = "els => els.map(e => e.innerText.trim().toUpperCase())"
js_errors, shot_jobs, shot_cons = [], os.path.join(tmp_dir, "jobs.png"), os.path.join(tmp_dir, "matched.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.16" in page.content(), "cache-buster not bumped to 5.99.16")

        # ---- a consultant's Matched jobs
        page.wait_for_selector(f".cons-card[data-id='{INDIA}'], .cons-card:has-text('Asha Indiafixture')", timeout=15000)
        page.locator(".cons-card:has-text('Asha Indiafixture')").first.click()
        page.wait_for_selector("#cons-jf-exp", timeout=15000)
        check(page.eval_on_selector_all("#cons-jobs-list ~ *, .cons-filter:visible label", LABELS) == FIELDS, "Matched jobs filters in order")
        check(page.input_value("#cons-jf-exp") == "4", "Matched jobs: experience is the consultant's years")
        page.wait_for_function("document.getElementById('cons-jobs-list').innerText.includes('Data Analyst Fits')", timeout=15000)
        text = page.inner_text("#cons-jobs-list")
        check("Needs More" not in text, f"4 years hides the 12+ job: {text[:200]!r}")
        page.screenshot(path=shot_cons)
        page.select_option("#cons-jf-exp", "")
        page.wait_for_function("document.getElementById('cons-jobs-list').innerText.includes('Needs More')", timeout=15000)
        check(page.input_value("#cons-jf-exp") == "", "Experience: Any stays picked and shows the 12+ job")
        page.select_option("#cons-jf-exp", "4")
        page.wait_for_function("!document.getElementById('cons-jobs-list').innerText.includes('Needs More')", timeout=15000)
        matched = sorted(page.eval_on_selector_all("#cons-jobs-list .cons-job-title", "els => els.map(e => e.innerText)"))
        matched_buttons = page.locator("#cons-jobs-list .cons-job").first.locator(".cons-job-acts > *").all_inner_texts()

        # ---- open a job there, then Jobs: not blank (state.jobs already holds that job)
        page.locator("#cons-jobs-list .cons-job button.btn.cons-job-open").first.click()
        page.wait_for_timeout(600)

        # ---- Jobs page: the same bar, the same cards, the same jobs
        page.click("a.nav-item[data-tab=jobs]")
        page.wait_for_function("document.querySelectorAll('#jobs-cards .cons-job').length >= 1", timeout=15000)
        check(page.eval_on_selector_all("#tab-jobs .cons-filter label", LABELS) == FIELDS, "Jobs filters are the same, in the same order")
        check(page.is_hidden("#jobs-table-view"), "no table on the Jobs page")
        for gone in ("#jobs-view-cards", "#jobs-view-table", "#stat-total-jobs", "#tab-jobs .jc-logo"):
            check(page.locator(gone).count() == 0, f"old Jobs layout removed: {gone}")
        check(page.input_value("#jobs-side-consultant") == str(INDIA) or page.input_value("#jobs-side-consultant") == str(US), "a consultant is picked")
        page.select_option("#jobs-side-consultant", str(INDIA))
        page.wait_for_function("document.getElementById('jobs-cards').innerText.includes('Data Analyst Fits')", timeout=15000)
        check(page.input_value("#filter-country") == "India" and page.is_hidden("#jobs-market-wrap"), "India consultant: India jobs, no market choice")
        check(page.input_value("#filter-query") == "Data Analyst" and page.input_value("#filter-experience") == "4", "consultant sets role + experience")
        check(page.input_value("#filter-job-type") == "" and page.input_value("#filter-location") == "", "same defaults as Matched jobs")
        jobs_titles = sorted(page.eval_on_selector_all("#jobs-cards .cons-job-title", "els => els.map(e => e.innerText)"))
        check(jobs_titles == matched, f"both screens list the same jobs: {jobs_titles} / {matched}")
        check(page.locator("#jobs-cards .cons-job").first.locator(".cons-job-acts > *").all_inner_texts() == matched_buttons, "same buttons on the cards")
        check("best skills match first" in page.inner_text("#jobs-cards-count") and "India" in page.inner_text("#jobs-cards-count"), "count line")
        page.screenshot(path=shot_jobs)

        # ---- the next consultant moves the whole search
        page.select_option("#jobs-side-consultant", str(US))
        page.wait_for_function("document.getElementById('jobs-cards').innerText.includes('Java Developer US')", timeout=15000)
        text = page.inner_text("#jobs-cards")
        check(page.input_value("#filter-country") == "United States" and page.input_value("#filter-experience") == "5", "US consultant: US market, 5 years")
        check("Data Analyst Fits" not in text, "India jobs gone")

        # ---- All consultants
        page.select_option("#jobs-side-consultant", "")
        page.wait_for_function("document.getElementById('jobs-cards').innerText.includes('Data Analyst US')", timeout=15000)
        check(page.is_visible("#jobs-market-wrap") and page.input_value("#filter-experience") == "" and page.input_value("#filter-query") == "", "All consultants: market shown, filters cleared")
        check(page.locator("#jobs-cards .cons-ring").count() == 0, "no match score without a consultant")
        page.locator("#jobs-cards .cons-job-tailor").first.click()
        page.wait_for_function("document.querySelector('.toast-container').innerText.includes('Pick a consultant first')", timeout=5000)

        # ---- company sites stay reachable
        check(page.is_hidden("#jobs-side"), "company sites closed by default")
        page.click("#jobs-side-toggle")
        page.wait_for_selector("#jobs-board-url", state="visible", timeout=10000)
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshots:", shot_jobs, shot_cons)
    sys.exit(1)
print(f"PASS: one job search (Edge). Screenshots: {shot_jobs} {shot_cons}")
