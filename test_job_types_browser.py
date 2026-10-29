"""Job type filter (Full-time / Contract / Part-time) in the Jobs tab.

1. job_types: wording -> type; each portal's filter code; portals that can't filter a type.
2. Scrapers ask each portal for the type: Dice employmentType, LinkedIn f_JT, Naukri jobTypeFilter,
   Foundit employmentType; 'All types' sends no filter; a portal that can't filter the type is skipped
   with the reason (Foundit + Contract, Naukri + Part-time).
3. LinkedIn never invents a rate ('$85 - $105/hr (C2C)') when the posting shows none.
4. Saved jobs are filtered by type; jobs that don't state one show under 'All types' only.
5. Edge: the Job type dropdown (US default Contract, India default All types) and the type badge.
Portals are stubs - no network, no cost. Labelled test fixtures. Run: python test_job_types_browser.py
"""
import os
import socket
import sys
import tempfile
import threading
from datetime import datetime, timezone

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_job_types.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = "test-token-not-real"
import app as app_module  # noqa: E402
import india_job_scrapers as ij  # noqa: E402
import job_types  # noqa: E402
import models  # noqa: E402
import us_job_scrapers as us  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures, gets, posts = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---- 1. types and codes
check([job_types.canonical(x) for x in ("Contract (C2C)", "Permanent", "Part Time", "FTE", "", "All")] ==
      ["Contract", "Full-time", "Part-time", "Full-time", "", ""], "wording -> type")
check(job_types.portal_code("Foundit", "Contract") is None and job_types.portal_code("Naukri", "Part-time") is None
      and job_types.portal_code("Naukri", "Full-time") == "P" and job_types.portal_code("LinkedIn", "") == "", "portal codes")

# ---- 2-3. the scrapers ask each portal for the type
LI_CARD = ('<ul><li><h3 class="base-search-card__title">Senior Java Engineer</h3><h4 class="base-search-card__subtitle">Acme Typetest</h4>'
           '<span class="job-search-card__location">Austin, TX</span><a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/123"></a>'
           '<time datetime="2026-10-22"></time></li></ul>')


class Resp:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text, self._p = status, text, payload

    def json(self):
        return self._p


def fake_get(url, headers=None, timeout=None, **k):
    gets.append(url)
    return Resp(200, LI_CARD if "linkedin.com" in url else "<html></html>")


def fake_post(url, params=None, json=None, timeout=None):
    posts.append((url, dict(json or {})))
    return Resp(200, payload=[])


us.requests.get = fake_get
ij.requests.post = fake_post

us.run_multi_source_us_scrape(["Java"], job_type="Full-time", save_to_db=False)
dice = [u for u in gets if "dice.com" in u]
li = [u for u in gets if "linkedin.com" in u]
check(dice and "filters.employmentType=FULLTIME" in dice[-1], f"Dice asked for full-time: {dice[-1:]}")
check(li and "f_JT=F" in li[-1] and "contract" not in li[-1].lower(), f"LinkedIn asked for full-time, no 'contract' keyword: {li[-1:]}")
gets.clear()
us.run_multi_source_us_scrape(["Java"], job_type="", save_to_db=False)
check(all("employmentType" not in u and "f_JT" not in u for u in gets), f"All types: no type filter sent: {gets}")
gets.clear()
li_jobs = us.scrape_linkedin_us("Java", job_type="Contract")
check(li_jobs and li_jobs[0]["salary"] == "" and li_jobs[0]["job_type"] == "Contract (C2C)", f"no invented rate; type from the filter: {li_jobs[:1]}")
check(li_jobs and job_types.classify(li_jobs[0]) == "Contract", "LinkedIn contract job classified")
li_any = us.scrape_linkedin_us("Java", job_type="")
check(li_any and li_any[0]["job_type"] == "", f"All types: LinkedIn job not labelled contract: {li_any[:1]}")

r = ij.run_multi_source_india_scrape(["Java"], sources=["Naukri", "Foundit"], job_type="Contract", save_to_db=False)
naukri_calls = [p for p in posts if ij.NAUKRI_ACTOR in p[0]]
check(naukri_calls and naukri_calls[-1][1].get("jobTypeFilter") == "C", f"Naukri asked for contract: {naukri_calls[-1:]}")
check(not [p for p in posts if ij.FOUNDIT_ACTOR in p[0]] and "can't filter Contract" in r["sources"]["Foundit"]["error"],
      f"Foundit skipped for Contract, with the reason: {r['sources']}")
posts.clear()
r = ij.run_multi_source_india_scrape(["Java"], sources=["Foundit"], job_type="Part-time", save_to_db=False)
check(posts and posts[-1][1].get("employmentType") == "Part Time", f"Foundit asked for part-time: {posts[-1:]}")

# ---- 4. saved jobs filtered by type
now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
for title, jt, desc in [("Java Contract Typetest", "Contract (C2C)", "Java role"), ("Java Fulltime Typetest", "Full-time", "Java role"),
                        ("Java Parttime Typetest", "", "Java developer - this is a part-time role, 20 hrs/week"), ("Java Unstated Typetest", "", "Java role")]:
    models.save_or_update_scraped_job({"title": title, "company": "Typeco", "location": "Austin, TX", "job_type": jt, "description": desc,
                                       "source": "LinkedIn (Live 24h)", "url": f"https://example.invalid/{title}", "is_24h": 1})
conn = models.get_db_connection()
conn.cursor().execute("UPDATE jobs SET scraped_at = ?", (now,))
conn.commit()
conn.close()
pick = lambda tf: sorted(j["title"] for j in models.get_jobs(query="Typetest", type_filter=tf, country="United States"))  # noqa: E731
check(pick("Full-time") == ["Java Fulltime Typetest"], f"Full-time: {pick('Full-time')}")
check(pick("Contract") == ["Java Contract Typetest"], f"Contract: {pick('Contract')}")
check(pick("Part-time") == ["Java Parttime Typetest"], f"Part-time from the description: {pick('Part-time')}")
check(len(pick("")) == 4, f"All types includes jobs that don't state one: {pick('')}")
models.save_or_update_scraped_job({"title": "Java Pasted Typetest", "company": "Typeco", "location": "Austin, TX", "description": "Java role",
                                   "source": "Manual Paste", "url": ""})
check("Java Pasted Typetest" in pick("Full-time") and "Java Pasted Typetest" in pick("Contract"),
      "a pasted requirement that doesn't state a type is never hidden by the type filter")

# ---- 5. the screen
us.requests.get = lambda *a, **k: Resp(200, "<html></html>")
EMAIL, PW = "types-admin@example.invalid", "Types-1"
models.create_user("Tia Typetest", EMAIL, PW, role="Admin")
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "job_types.png")
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
        check("app.js?v=5.99.11" in page.content(), "cache-buster not bumped to 5.99.11")
        page.click("a.nav-item[data-tab=jobs]")
        check(page.input_value("#filter-job-type") == "Contract", "US default: Contract")
        page.fill("#filter-query", "Typetest")
        page.select_option("#filter-job-type", "Full-time")
        page.click("#btn-search-jobs")
        page.wait_for_function("document.getElementById('jobs-table-body').innerText.includes('Typetest')", timeout=10000)
        body = page.inner_text("#jobs-table-body")
        check("Java Fulltime Typetest" in body and "Java Contract Typetest" not in body, f"Full-time filter on screen: {body[:200]!r}")
        check(page.locator(".job-type-badge").first.inner_text() == "Full-time", "type badge shown")
        check("Rate on Discussion" not in body and "C2C" not in body, f"no invented rate / C2C on a full-time job: {body[:300]!r}")
        page.select_option("#filter-job-type", "")
        page.wait_for_function("document.getElementById('jobs-table-body').innerText.includes('Java Unstated Typetest')", timeout=10000)
        page.screenshot(path=shot)
        page.select_option("#filter-country", "India")
        page.wait_for_timeout(500)
        check(page.input_value("#filter-job-type") == "", "India default: All types")
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
print(f"PASS: job type filter - wording, portal codes (Dice / LinkedIn / Naukri / Foundit), unsupported portals skipped, no invented LinkedIn rate, saved jobs filtered, dropdown defaults + badge in Edge. Screenshot: {shot}")
