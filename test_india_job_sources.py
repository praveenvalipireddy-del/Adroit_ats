"""India jobs (Naukri / Foundit / LinkedIn): the chosen portal only, and why a portal found nothing.

1. A portal picked in the Jobs tab (e.g. Naukri) is the only one searched - no Apify credit is spent on the
   others - and another portal's jobs are never shown as if they came from it.
2. When Apify refuses (monthly credit used up, bad token, timeout) the reason is reported per portal
   ("Naukri: 0 jobs - the Apify account's monthly credit is used up ...") instead of a silent empty list.
3. A working Naukri answer (the actor's real output fields) becomes Naukri jobs.
4. Edge: the Jobs tab (India, Naukri, Live 24h Scrape) shows the per-portal status line.
Apify and LinkedIn are stubs - no network, no cost. Labelled test fixtures.
Run: python test_india_job_sources.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_india_sources.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = "test-token-not-real"
import app as app_module  # noqa: E402
import india_job_scrapers as ij  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures, calls = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


MODE = {"naukri": "no_credit"}
# the format the live actor really returns (Oct 2026 run): 'jobURL' with a full link - not the documented 'jdURL'
NAUKRI_ITEM = {"title": "Cloud Devops Engineer", "companyName": "Anlage Testco", "logoUrl": "https://img.naukimg.com/logo.gif",
               "jobURL": "https://www.naukri.com/job-listings-cloud-devops-engineer-anlage-testco-bengaluru-5-to-8-years-191026004130"}
OLD_FORMAT_ITEM = {"jobId": "211025000001", "title": "Platform Engineer", "companyName": "Infosys Testco", "jdURL": "/job-listings-platform-engineer-211025000001",
                   "locationLabel": "Bengaluru", "salaryLabel": "18-25 Lacs PA", "experienceLabel": "8-12 Yrs", "tagsAndSkills": ["AWS", "Kubernetes"]}


def fake_post(url, params=None, json=None, timeout=None):
    actor = "naukri" if ij.NAUKRI_ACTOR in url else "foundit" if ij.FOUNDIT_ACTOR in url else "indeed" if "indeed" in url else "?"
    calls.append(actor)
    if actor == "naukri":
        if MODE["naukri"] == "no_credit":
            return FakeResp(402, text='{"error":{"type":"platform-feature-disabled","message":"Monthly usage hard limit exceeded"}}')
        if MODE["naukri"] == "unreadable":
            return FakeResp(200, [{"heading": "x"}, {"heading": "y"}])
        return FakeResp(200, [NAUKRI_ITEM, OLD_FORMAT_ITEM])
    return FakeResp(200, [])


ij.requests.post = fake_post
ij.scrape_linkedin_us = lambda keyword, location="India", contract_only=False, limit=15, job_type="": (calls.append("linkedin") or [{
    "title": "Senior DevOps Engineer", "company": "Newfold Testco", "location": "Mumbai", "job_type": "Contract", "salary": "",
    "source": "LinkedIn (Live 24h)", "url": "https://www.linkedin.com/jobs/view/1000000001", "recruiter_email": "", "description": "DevOps",
    "matched_skills": keyword, "match_score": 90, "is_24h": 1}])

# ---- 1-2. only the chosen portal; the reason when it finds nothing
r = ij.run_multi_source_india_scrape(["Cloud DevOps Engineer"], sources=["Naukri"], save_to_db=False)
check(calls == ["naukri"], f"only Naukri searched: {calls}")
check(r["sources"] == {"Naukri": {"count": 0, "error": ij.LAST_ERROR["Naukri"], "note": ""}} and "monthly credit is used up" in r["sources"]["Naukri"]["error"],
      f"reason reported: {r['sources']}")
calls.clear()
r = ij.run_multi_source_india_scrape(["Cloud DevOps Engineer"], save_to_db=False)
check(calls == ["naukri", "foundit", "indeed", "linkedin"] and "Recruiter Post" not in r["sources"] and r["sources"]["LinkedIn"]["count"] == 1
      and r["sources"]["Foundit"] == {"count": 0, "error": "", "note": ""},
      f"'All' searches every portal: {calls} {r['sources']}")
check("token on the server is not valid" in ij.explain_apify_error(401, "") and "too long" in ij.explain_apify_error(408, ""), "other reasons in words")

EMAIL, PW = "india-src@example.invalid", "India-Src-1"
ADMIN = models.create_user("Ina Sourcetest", EMAIL, PW, role="Admin")
ADMIN_ID = ADMIN["id"] if isinstance(ADMIN, dict) else ADMIN
models.create_candidate("Vis Sourcetest", "vis-src@example.invalid", title="DevOps Engineer", assigned_user_id=ADMIN_ID, country="India")
client = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": ADMIN_ID, "name": "Ina", "role": "Admin", "email": EMAIL}
calls.clear()
d = client.post("/api/jobs/search", json={"query": "Cloud DevOps Engineer", "location": "India", "source": "Naukri", "country": "India",
                                          "live_scrape": True, "is_24h_only": True}).get_json()
check(calls == ["naukri"], f"the search runs only the picked portal: {calls}")
check(d["jobs"] == [] and "monthly credit" in d["scrape_sources"]["Naukri"]["error"], f"no LinkedIn jobs shown as Naukri; reason sent: {d['jobs']} {d['scrape_sources']}")

# ---- 3. Naukri works
MODE["naukri"] = "ok"
d = client.post("/api/jobs/search", json={"query": "Cloud DevOps Engineer", "location": "India", "source": "Naukri", "country": "India",
                                          "live_scrape": True, "is_24h_only": True}).get_json()
urls = {j["title"]: j["url"] for j in d["jobs"]}
check(urls.get("Cloud Devops Engineer") == NAUKRI_ITEM["jobURL"], f"live format (jobURL) read: {urls}")
direct = {j["title"]: j["url"] for j in ij.scrape_naukri("Cloud DevOps Engineer")}
check(direct.get("Platform Engineer") == "https://www.naukri.com/job-listings-platform-engineer-211025000001"
      and direct.get("Cloud Devops Engineer") == NAUKRI_ITEM["jobURL"], f"both formats read (jobURL and jdURL): {direct}")
check(all("Naukri" in j["source"] for j in d["jobs"]) and d["scrape_sources"]["Naukri"] == {"count": 2, "error": "", "note": ""},
      f"Naukri jobs shown: {[(j['title'], j['source']) for j in d['jobs']]} {d['scrape_sources']}")
# after a Live 24h Scrape the page searches the saved jobs (no_scrape, contract_only as the page sends it):
# full-time Naukri jobs located "Bengaluru" must show for location "India"
d = client.post("/api/jobs/search", json={"query": "Cloud DevOps Engineer", "location": "India", "source": "Naukri", "country": "India",
                                          "no_scrape": True, "contract_only": True, "is_24h_only": True}).get_json()
check(any(j["title"] == "Cloud Devops Engineer" for j in d["jobs"]), f"saved full-time Naukri jobs show after the live scrape: {[j['title'] for j in d['jobs']]}")
MODE["naukri"] = "unreadable"
r = ij.run_multi_source_india_scrape(["Cloud DevOps Engineer"], sources=["Naukri"], save_to_db=False)
check(r["sources"]["Naukri"]["count"] == 0 and "format this app doesn't read" in r["sources"]["Naukri"]["error"],
      f"records the app can't read are reported, not silently dropped: {r['sources']}")
MODE["naukri"] = "no_credit"

# ---- 4. the screen
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "india_sources.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.add_init_script("try { localStorage.setItem('jobsView', 'table') } catch (e) {}")   # these checks use the Jobs table view
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.4" in page.content(), "cache-buster not bumped to 5.99.4")
        page.click("a.nav-item[data-tab=jobs]")
        page.select_option("#filter-country", "India")
        page.fill("#filter-query", "Platform Engineer")
        calls.clear()
        page.select_option("#filter-source", "Foundit")
        page.wait_for_timeout(2500)
        check(calls == [], f"changing the portal alone spends no Apify credit: {calls}")
        page.select_option("#filter-source", "Naukri")
        page.wait_for_timeout(1500)
        calls.clear()
        page.click("#btn-live-scrape-trigger")
        page.wait_for_function("document.getElementById('jobs-source-status') && document.getElementById('jobs-source-status').innerText.includes('Naukri')", timeout=20000)
        note = page.inner_text("#jobs-source-status")
        check("Naukri: 0 jobs" in note and "monthly credit is used up" in note and "LinkedIn" not in note, f"status line: {note!r}")
        page.wait_for_timeout(1500)
        check(calls == ["naukri"], f"Live 24h Scrape searches only Naukri, once (no second scrape after it): {calls}")
        page.screenshot(path=shot)
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: India jobs - only the picked portal is searched, no other portal's jobs stand in, Apify reasons per portal (credit / token / timeout), Naukri jobs read, status line in Edge. Screenshot: {shot}")
