"""Consultants: cards home + one page per consultant (labelled fixtures, no paid calls).

1. /api/consultants/overview: applied this week, open applications, replies waiting, interviews - only my consultants.
2. Edge: the Consultants tab opens on cards with those numbers; chips (All / US / India / Needs attention) and search filter.
3. Clicking a card opens the consultant's page: header (rate, visa, resume, Gmail), tabs:
   Matched jobs (today's jobs for their title, skills ring, Email recruiter / Apply on site),
   Applications (board by status; a card opens the tracker), Requirements & replies, Profile & resume (autofill answers).
4. "← All consultants" goes back; the Table view still has every old button and is remembered.
Run: python test_consultant_layout_browser.py
"""
import json
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_cons_layout.db")
os.environ["DATABASE_URL"] = ""
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
EMAIL, PW = "layout-rec@example.invalid", "Layout-1"
REC = uid(models.create_user("Lia Layouttest", EMAIL, PW, role="Recruiter", region="Both"))
_c = models.get_db_connection()
_c.cursor().execute("UPDATE users SET region = 'Both' WHERE id = ?", (REC,))      # works for the USA and India teams
_c.commit()
_c.close()
OTHER = uid(models.create_user("Oz Otherlayout", "layout-other@example.invalid", "Other-1", role="Recruiter", region="USA"))
K = models.create_candidate("Kiran Layoutfixture", "kiran.layout@example.invalid", title="Senior Data Analyst", primary_skills="SQL, Power BI, Python",
                            experience_years=6, target_rate="$60/hr (C2C)", visa_status="OPT / STEM", location="Dallas, TX",
                            country="United States", assigned_user_id=REC)
I = models.create_candidate("Indu Layoutfixture", "indu.layout@example.invalid", title="Java Developer", country="India",
                            target_rate="12 LPA", location="Hyderabad", assigned_user_id=REC)
X = models.create_candidate("Not Mine Fixture", "notmine@example.invalid", title="Tester", country="United States", assigned_user_id=OTHER)
models.update_candidate(K, gmail_account="kiran.layout@example.invalid", gmail_app_password="fixture-not-real",
                        autofill_answers=json.dumps({"notice_period": "2 weeks", "needs_sponsorship": "No"}))
models.save_resume_file(K, "Kiran_Layoutfixture.docx", b"fixture")
models.update_candidate(K, resume_filename="Kiran_Layoutfixture.docx")

job_match = models.save_or_update_scraped_job({"title": "Data Analyst - Power BI (Layout Fixture)", "company": "Fixture Vendor LLC", "source": "Dice",
                                               "url": "https://www.dice.com/job-detail/layout-fixture-1", "location": "Dallas, TX",
                                               "description": "SQL and Power BI reporting role. Labelled test fixture.", "recruiter_email": "rec@fixturevendor.example", "job_type": "Contract (C2C)",
                                               "country": "United States"})
job_match = job_match["id"] if isinstance(job_match, dict) else job_match
job_old = models.save_or_update_scraped_job({"title": "Reporting Analyst (Layout Fixture)", "company": "Fixture Two Inc", "source": "Dice",
                                             "url": "https://www.dice.com/job-detail/layout-fixture-2", "country": "United States",
                                             "description": "Excel reporting. Labelled test fixture."})
job_old = job_old["id"] if isinstance(job_old, dict) else job_old
conn = models.get_db_connection()
a1 = application_tracker.upsert(conn, job_match, K, REC, stage="Applied", apply_method="email")
a2 = application_tracker.upsert(conn, job_old, K, REC, stage="Interviewing")
cur = conn.cursor()
cur.execute("""INSERT INTO application_replies (application_id, candidate_id, message_id, from_email, from_name, subject, received_at, category, handled)
               VALUES (?, ?, 'layout-fixture-msg-1', 'rec@fixturevendor.example', 'Fixture Recruiter', 'Re: Data Analyst (fixture)', '2026-10-27 09:00', 'needs_info', 0)""",
            (a1, K))
conn.commit()
conn.close()

client = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Lia", "role": "Recruiter", "email": EMAIL}
ov = client.get("/api/consultants/overview").get_json()["overview"]
check(str(X) not in ov, "another recruiter's consultant is not in my overview")
k = ov.get(str(K), {})
check(k.get("applied_this_week") == 2 and k.get("open") == 2 and k.get("replies_waiting") == 1 and k.get("interviews") == 1 and k.get("total") == 2,
      f"overview numbers: {k}")
check(ov.get(str(I), {}).get("total") == 0, "a consultant with nothing shows zeros")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shots = [], {}
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.14" in page.content(), "cache-buster not bumped to 5.99.14")
        # the Dashboard (made-up pipeline, "88%" / "24 Days") is gone: the app opens on the consultant cards
        page.wait_for_selector(f".cons-card[data-id='{K}']", timeout=15000)
        check(page.locator("a.nav-item[data-tab=dashboard]").count() == 0 and page.locator("#tab-dashboard").count() == 0, "no Dashboard tab")
        check("88%" not in page.content() and "24 Days" not in page.content(), "no made-up numbers")
        check("active" in (page.get_attribute("a.nav-item[data-tab=candidates]", "class") or ""), "Consultants is the first screen")
        page.goto(f"http://127.0.0.1:{port}/dashboard#dashboard")
        page.wait_for_selector(f".cons-card[data-id='{K}']", timeout=15000)
        page.click("a.nav-item[data-tab=candidates]")
        page.wait_for_selector(f".cons-card[data-id='{K}']")
        page.wait_for_function(f"document.querySelector(\".cons-card[data-id='{K}']\").innerText.includes('Replies waiting') && "
                               f"document.querySelector(\".cons-card[data-id='{K}'] .cons-stats b.hot\")")
        card = page.inner_text(f".cons-card[data-id='{K}']")
        check("Kiran Layoutfixture" in card and "$60/hr (C2C)" in card and "OPT / STEM" in card and "Gmail connected" in card, f"card: {card!r}")
        nums = page.eval_on_selector_all(f".cons-card[data-id='{K}'] .cons-stats b", "els => els.map(e => e.innerText)")
        check(nums == ["2", "2", "1"], f"card numbers: {nums}")
        check("Gmail not connected" in page.inner_text(f".cons-card[data-id='{I}']"), "warning on a consultant without Gmail")
        check(page.locator(f".cons-card[data-id='{X}']").count() == 0, "another recruiter's consultant not shown")
        check(page.is_hidden("#cons-table-wrap"), "cards are the default view")
        shots["cards"] = os.path.join(tmp_dir, "cards.png")
        page.screenshot(path=shots["cards"])

        page.click(".cons-chip[data-chip=india]")
        check(page.locator(".cons-card").count() == 1 and page.locator(f".cons-card[data-id='{I}']").count() == 1, "India chip")
        page.click(".cons-chip[data-chip=attention]")
        check(page.locator(f".cons-card[data-id='{I}']").count() == 1, "needs attention: no Gmail")
        page.click(".cons-chip[data-chip=all]")
        page.fill("#filter-consultants-search", "indu")
        check(page.is_hidden(f".cons-card[data-id='{K}']") and page.is_visible(f".cons-card[data-id='{I}']"), "search filters cards")
        page.fill("#filter-consultants-search", "")

        # ---- the consultant's page
        page.click(f".cons-card[data-id='{K}']")
        page.wait_for_selector("#cons-workspace:not([hidden])")
        head = page.inner_text("#cons-ws-head")
        check("Kiran Layoutfixture" in head and "Kiran_Layoutfixture.docx" in head and "kiran.layout@example.invalid" in head, f"header: {head[:300]!r}")
        check(page.is_hidden("#cons-home"), "home hidden on the consultant page")
        page.wait_for_selector(".cons-job", timeout=15000)
        jobs = page.inner_text("#cons-jobs-list")
        check("Data Analyst - Power BI (Layout Fixture)" in jobs and "Email recruiter" in jobs and "Apply on site" in jobs, f"matched jobs: {jobs[:300]!r}")
        first = page.locator(".cons-job").first
        check("Power BI (Layout Fixture)" in first.inner_text() and "67%" in first.inner_text(), f"best skills match first: {first.inner_text()[:200]!r}")
        check("2 OF 3 SKILLS" in first.inner_text(), f"few listed skills -> '2 of 3 skills', not STRONG: {first.inner_text()[-60:]!r}")
        shots["jobs"] = os.path.join(tmp_dir, "ws_jobs.png")
        page.screenshot(path=shots["jobs"])
        # the search bar on the consultant's page
        check(page.input_value("#cons-jf-query") == "Data Analyst", "role prefilled from the consultant's title")
        page.fill("#cons-jf-query", "Reporting")
        page.click("#cons-jf-go")
        page.wait_for_function("document.getElementById('cons-jobs-list') && document.getElementById('cons-jobs-list').innerText.includes('Reporting Analyst (Layout Fixture)')",
                               timeout=15000)
        check("Power BI (Layout Fixture)" not in page.inner_text("#cons-jobs-list"), "search narrows the list")
        page.select_option("#cons-jf-source", "Dice")
        page.wait_for_function("document.getElementById('cons-jobs-count').innerText.includes('job')", timeout=15000)
        check(page.input_value("#cons-jf-query") == "Reporting", "filters kept after a change")
        page.select_option("#cons-jf-source", "Company site")
        page.wait_for_function("document.getElementById('cons-jobs-list').innerText.includes('No jobs match')", timeout=15000)
        page.select_option("#cons-jf-source", "All")
        page.fill("#cons-jf-query", "Data Analyst")
        page.press("#cons-jf-query", "Enter")
        page.wait_for_function("document.getElementById('cons-jobs-list').innerText.includes('Power BI (Layout Fixture)')", timeout=15000)
        page.fill("#cons-jf-query", "Analyst")
        page.select_option("#cons-jf-work", "remote")
        page.wait_for_function("document.getElementById('cons-jobs-list').innerText.includes('Reporting Analyst (Layout Fixture)') && "
                               "!document.getElementById('cons-jobs-list').innerText.includes('Power BI (Layout Fixture)')", timeout=15000)
        check(page.input_value("#cons-jf-work") == "remote", "work model: only the remote job (Dallas job stays out)")
        page.select_option("#cons-jf-work", "")

        page.click(".cons-tab[data-pane=apps]")
        page.wait_for_selector(".cons-bc")
        cols = page.eval_on_selector_all(".cons-col", "els => els.map(e => e.querySelector('h4').innerText.replace(/\\s+/g, ' '))")
        check(cols == ["Draft created 0", "Applied 1", "Responded 0", "Interview 1", "Closed 0"], f"board columns: {cols}")
        page.click(f".cons-bc[data-app='{a1}']")
        page.wait_for_selector("#modal-tracker", state="visible")
        page.click("#btn-close-tracker")
        shots["apps"] = os.path.join(tmp_dir, "ws_apps.png")
        page.screenshot(path=shots["apps"])

        page.click(".cons-tab[data-pane=reqs]")
        page.wait_for_selector("#cons-replies .cons-li")
        check("Fixture Recruiter" in page.inner_text("#cons-replies"), "reply waiting listed")
        check("No requirements found yet" in page.inner_text("#cons-reqs"), "no requirements -> honest empty text")

        page.click(".cons-tab[data-pane=profile]")
        prof = page.inner_text("#cons-ws-body")
        check("2 weeks" in prof and "SQL, Power BI, Python" in prof and "Change Gmail" in prof, f"profile pane: {prof[:300]!r}")
        shots["profile"] = os.path.join(tmp_dir, "ws_profile.png")
        page.screenshot(path=shots["profile"])

        page.click("#cons-ws-back")
        check(page.is_visible(f".cons-card[data-id='{K}']") and page.is_hidden("#cons-workspace"), "back to the cards")

        # ---- table view still has everything, and is remembered
        page.click("#cons-view-table")
        check(page.is_visible("#consultants-table-body") and page.locator(f"#consultants-table-body .btn-edit-consultant[data-id='{K}']").count() == 1,
              "table view with the old buttons")
        page.reload()
        page.click("a.nav-item[data-tab=candidates]")
        page.wait_for_selector("#consultants-table-body tr")
        check(page.is_visible("#consultants-table-body") and page.is_hidden("#cons-cards"), "table view remembered")
        page.click("#cons-view-cards")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshots:", shots)
    sys.exit(1)
print(f"PASS: consultant cards (numbers, chips, search) + consultant page (header, matched jobs, applications board, replies, profile), back, table view kept. Screenshots: {shots}")
