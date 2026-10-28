"""Consultant page: "Find new jobs from portals" (the portal scrapers are stubbed - no network, no Apify credit).

1. The cost is shown and confirmed before a paid portal search (India / Indeed); cancelling searches nothing.
2. Confirmed: a live search for the consultant's keyword in their market and the picked source; the new jobs appear
   in Matched jobs right away.
3. "Company career sites" starts the free background reader instead of a portal search.
Run: python test_cons_live_jobs_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_cons_live.db")
os.environ["DATABASE_URL"] = ""
os.environ["CAREER_SYNC_HOURS"] = "0"
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


calls, syncs = [], []


def fake_india(keywords, location, save_to_db=True, sources=None, job_type=""):
    calls.append({"keywords": keywords, "location": location, "sources": sources})
    jid = models.save_or_update_scraped_job({"title": "AI Automation Engineer (Live fixture)", "company": "Fixture Labs India", "source": "Indeed",
                                             "url": "https://in.indeed.com/viewjob?jk=livefixture1", "location": "Hyderabad, Telangana",
                                             "description": "Python, n8n and LLM automation. Labelled test fixture.", "country": "India"})
    return {"jobs": [], "sources": {"Indeed": {"count": 1}}}


india_job_scrapers.run_multi_source_india_scrape = fake_india
app_module._run_board_sync = lambda *a, **k: syncs.append(1)

uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "live-rec@example.invalid", "Live-1"
REC = uid(models.create_user("Lee Livetest", EMAIL, PW, role="Recruiter", region="India"))
K = models.create_candidate("Pavan Livefixture", "pavan.live@example.invalid", title="AI Automation Engineer", primary_skills="Python, n8n, LLM",
                            experience_years=4, country="India", location="Hyderabad", target_rate="8 LPA", assigned_user_id=REC)

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, dialogs = [], []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        answer = {"accept": False}
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept() if answer["accept"] else d.dismiss()))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        page.click(f".cons-card[data-id='{K}']")
        page.wait_for_selector("#cons-jobs-live")
        page.select_option("#cons-jf-source", "Indeed")
        page.wait_for_selector("#cons-jobs-live")
        page.click("#cons-jobs-live")
        page.wait_for_timeout(500)
        check(dialogs and "$0.004" in dialogs[0] and "Apify" in dialogs[0] and "AI Automation Engineer" in dialogs[0], f"cost shown first: {dialogs}")
        check(not calls, "cancel -> no portal search")
        answer["accept"] = True
        page.click("#cons-jobs-live")
        page.wait_for_function("document.getElementById('cons-jobs-list') && document.getElementById('cons-jobs-list').innerText.includes('AI Automation Engineer (Live fixture)')",
                               timeout=20000)
        check(calls and calls[0]["keywords"] == ["AI Automation Engineer"] and calls[0]["sources"] == ["Indeed"], f"live search: {calls}")
        page.select_option("#cons-jf-source", "Company site")
        page.wait_for_selector("#cons-jobs-live")
        n = len(calls)
        page.click("#cons-jobs-live")
        page.wait_for_timeout(800)
        check(len(calls) == n and syncs, "company sites: the free reader, not a portal search")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: consultant page live portal search - cost confirmed first, cancel searches nothing, new jobs shown at once, company sites use the free reader.")
