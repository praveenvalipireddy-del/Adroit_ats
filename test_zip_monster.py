"""ZipRecruiter and Monster (zip_monster_jobs.py) - paid US portals via Apify, searched only when picked.
1. The Apify input: keyword, job type, location, limits (20 / 25 jobs), full postings for ZipRecruiter.
2. Reading the results: title / company / location / type (Contract, Full-time) / salary / description; ZipRecruiter's
   own test ads (PAT_..., PAT_TestAccount) and rows with no link are left out; an actor error is reported.
3. The US runner: "All portals" never runs them; picking one runs only that one.
4. Jobs search: a recruiter's pick scrapes only that portal; an Individual gets them only with paid portals on.
5. Edge: the US source list shows both, with the cost under the list.
Apify is a stub - no network, no cost. Labelled fixtures. Run: python test_zip_monster.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_zip_monster.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import india_job_scrapers as ij  # noqa: E402
import individuals  # noqa: E402
import models  # noqa: E402
import us_job_scrapers  # noqa: E402
import zip_monster_jobs as zm  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


ZIP_ITEMS = [
    {"title": "PAT_Java", "company": "PAT_TestAccount", "location": "Wilmington, MA", "url": "https://www.ziprecruiter.com/c/x/Job/PAT/-in-x?jid=1"},
    {"title": "Java/JEE Architect (Fixture)", "company": "Fixture Staffing", "location": "Lincoln, NE", "employmentType": "CONTRACTOR",
     "salaryText": "$70 - $80 an hour", "postedDate": "2026-11-23T18:42:32Z", "url": "https://www.ziprecruiter.com/c/Fixture/Job/Java/-in-Lincoln,NE?jid=2",
     "description": "Labelled fixture posting. Java, Spring Boot and AWS. Send resumes to jobs@fixture-staffing.example."},
    {"title": "No Link Fixture", "company": "Fixture Co", "url": ""},
]
MONSTER_ITEMS = [
    {"title": "Java Full Stack Developer (Fixture)", "company": "Fixture Consulting", "location": "Livonia, MI, US", "employmentType": "FULL_TIME",
     "postedDate": "2026-11-23", "url": "https://www.monster.com/job-openings/fixture-1", "description": "Labelled fixture posting. Java and React."},
]
calls = []


def fake_actor(actor, payload, timeout=150):
    calls.append((actor, payload))
    return (ZIP_ITEMS if actor == zm.ZIP_ACTOR else MONSTER_ITEMS), None


ij._run_actor_sync = fake_actor

# ---- 1 + 2. input and reading
jobs = zm.scrape_ziprecruiter("Java Developer", location="Dallas, TX", job_type="Contract")
actor, payload = calls[-1]
check(actor == zm.ZIP_ACTOR and payload == {"searches": ["Java Developer"], "postedWithin": "week", "maxItems": 20, "maxPages": 1,
                                             "includeDetails": True, "location": "Dallas, TX", "jobType": "contract"}, f"ZipRecruiter input: {payload}")
check([j["title"] for j in jobs] == ["Java/JEE Architect (Fixture)"], f"test ad and no-link row left out: {[j['title'] for j in jobs]}")
j = jobs[0]
check(j["source"] == "ZipRecruiter (Live)" and j["job_type"] == "Contract" and j["salary"] == "$70 - $80 an hour"
      and j["location"] == "Lincoln, NE" and j["country"] == "United States", f"ZipRecruiter job read: {j}")
check(j["recruiter_email"] == "jobs@fixture-staffing.example" and "Posted 2026-11-23 on ZipRecruiter" in j["description"], "email + posted date")
jobs = zm.scrape_monster("Java Developer", location="United States", job_type="Full-time")
actor, payload = calls[-1]
check(actor == zm.MONSTER_ACTOR and payload == {"query": "Java Developer", "country": "US", "datePosted": "LAST_2_DAYS", "maxResults": 25,
                                                "descriptionFormat": "text", "employmentType": "FULL_TIME"}, f"Monster input (whole US = no location): {payload}")
check(len(jobs) == 1 and jobs[0]["source"] == "Monster (Live)" and jobs[0]["job_type"] == "Full-time" and jobs[0]["location"] == "Livonia, MI",
      f"Monster job read: {jobs}")
ij._run_actor_sync = lambda actor, payload, timeout=150: (None, "Apify credit used up (fixture)")
check(zm.scrape_monster("Java") == [] and ij.LAST_ERROR["Monster"] == "Apify credit used up (fixture)", "an actor error is reported")
ij._run_actor_sync = fake_actor

# ---- 3. the US runner
calls.clear()
for name in ("scrape_dice_us", "scrape_linkedin_us"):
    setattr(us_job_scrapers, name, lambda *a, **k: [])
us_job_scrapers.run_multi_source_us_scrape(["Java"], save_to_db=False)
check(calls == [], f"'All portals' (free) never runs ZipRecruiter / Monster: {calls}")
r = us_job_scrapers.run_multi_source_us_scrape(["Java"], save_to_db=False, sources=["Monster"])
check([c[0] for c in calls] == [zm.MONSTER_ACTOR] and r["sources"]["Monster"]["count"] == 1, f"picking Monster runs only Monster: {calls}")

# ---- 3b. ZipRecruiter Canada (ziprecruiter.ca): Canada jobs, only when picked - never "All Canada portals"
import canada_job_scrapers  # noqa: E402
calls.clear()
jobs = zm.scrape_ziprecruiter("Security Analyst", location="Canada", job_type="", country="Canada")
check(calls[-1][1].get("countryName") == "canada" and "location" not in calls[-1][1], f"ziprecruiter.ca, whole Canada: {calls[-1][1]}")
check(jobs and all(j["country"] == "Canada" for j in jobs), f"saved as Canada jobs: {[j['country'] for j in jobs]}")
canada_job_scrapers.scrape_linkedin_canada = lambda *a, **k: []
canada_job_scrapers.scrape_indeed_canada = lambda *a, **k: []
calls.clear()
canada_job_scrapers.run_multi_source_canada_scrape(["Java"], save_to_db=False)
check(calls == [], f"'All Canada portals' never runs ZipRecruiter: {calls}")
canada_job_scrapers.run_multi_source_canada_scrape(["Java"], save_to_db=False, sources=["ZipRecruiter"])
check([c[0] for c in calls] == [zm.ZIP_ACTOR] and calls[0][1]["countryName"] == "canada", f"picking ZipRecruiter in Canada: {calls}")
check("ZipRecruiter" in individuals.sources_for("Canada", True) and "ZipRecruiter" not in individuals.sources_for("Canada", False),
      "Individuals in Canada: ZipRecruiter only with paid portals")

# ---- 4. jobs search
uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
REC = uid(models.create_user("Rita Ziptest", "zip-rec@example.invalid", "Zip-pass-1", role="Recruiter", region="USA"))
IND = uid(models.create_user("Ivan Ziptest", "zip-ind@example.invalid", "Ind-pass-1", role="Individual", region="USA"))
models.create_candidate("Ivan Ziptest", "zip-ind@example.invalid", title="Java Developer", country="United States", assigned_user_id=IND)
client = app_module.app.test_client()
picked = []
us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: picked.append(k.get("sources")) or {"count": 0, "jobs": [], "sources": {}}
app_module.us_job_scrapers.run_multi_source_us_scrape = us_job_scrapers.run_multi_source_us_scrape


def search(user, source):
    with client.session_transaction() as s_:
        s_["user"] = user
    return client.post("/api/jobs/search", json={"query": "Java", "country": "United States", "source": source, "live_scrape": True}).get_json()


search({"id": REC, "name": "Rita", "role": "Recruiter", "email": "zip-rec@example.invalid"}, "ZipRecruiter")
check(picked[-1] == ["ZipRecruiter"], f"recruiter's ZipRecruiter search: {picked}")
d = search({"id": IND, "name": "Ivan", "role": "Individual", "email": "zip-ind@example.invalid"}, "Monster")
check(d["source"] == "All" and picked[-1] in (None, []), f"Individual without paid portals: Monster becomes the free portals: {d['source']} {picked[-1]}")
conn = models.get_db_connection()
individuals.set_paid(conn, IND, True)
conn.close()
d = search({"id": IND, "name": "Ivan", "role": "Individual", "email": "zip-ind@example.invalid"}, "Monster")
check(d["source"] == "Monster" and picked[-1] == ["Monster"] and d["usage"]["paid_today"] == 1, f"with paid portals on: {d['source']} {d.get('usage')}")

# ---- 5. Edge
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors = []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="msedge", headless=True)
        page = b.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", "zip-rec@example.invalid")
        page.fill("input[name=password]", "Zip-pass-1")
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.29" in page.content(), "cache-buster not bumped to 5.99.29")
        page.click("a.nav-item[data-tab=jobs]")
        page.wait_for_selector("#filter-source", timeout=10000)
        opts = page.eval_on_selector("#filter-source", "s => [...s.options].map(o => o.value)")
        check("ZipRecruiter" in opts and "Monster" in opts, f"US sources: {opts}")
        page.select_option("#filter-source", "Monster")
        page.evaluate("jobsPortalCostHint()")
        check("$0.03 per search" in page.inner_text("#jobs-portal-cost"), f"cost hint: {page.inner_text('#jobs-portal-cost')!r}")
        page.evaluate("document.getElementById('filter-country').value = 'Canada'")   # this recruiter's team (USA) locks the list
        page.evaluate("syncJobsMarketUi()")
        opts = page.eval_on_selector("#filter-source", "s => [...s.options].map(o => o.textContent.trim())")
        check("ZipRecruiter Canada" in opts and "Monster" not in opts, f"Canada sources: {opts}")
        # a consultant's Matched jobs: the portals of the consultant's market (a Canadian never sees Dice / Naukri)
        page.evaluate("""() => { consUi.openId = 999; consUi.pane = 'jobs'; consUi.jf = {999: {query: 'Security', location: '', posted: '24', type: '',
            source: 'Dice', work: '', roles: [], exp: ''}}; const d = document.createElement('div'); d.id = 'cons-test-pane'; document.body.appendChild(d);
            consPaneJobs({id: 999, name: 'Canada Fixture', title: 'Security Analyst', country: 'Canada'}, d); }""")
        page.wait_for_selector("#cons-jf-source", timeout=10000)
        opts = page.eval_on_selector("#cons-jf-source", "s => [...s.options].map(o => o.value)")
        check(opts == ["All", "Company site", "LinkedIn", "Indeed", "ZipRecruiter", "Recruiter Post", "Email"], f"Canada consultant's Matched jobs sources: {opts}")
        check(page.input_value("#cons-jf-source") == "All", "a saved Dice pick falls back to All Canada portals")
        us = page.evaluate("consJobSources('United States').map(x => x[0])")
        check("Dice" in us and "Monster" in us and "Naukri" not in us, f"US consultant's sources: {us}")
        b.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: ZipRecruiter + Monster - Apify input, results read (test ads dropped), only when picked (never 'All'), "
      "Individuals only with paid portals, US source list + cost in Edge.")
