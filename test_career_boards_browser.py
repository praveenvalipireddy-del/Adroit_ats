"""Jobs from company career portals + the Jobs tab as cards (labelled fixtures; the portal feeds are stubbed - no network).

1. Links -> portal + company (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable); anything else is refused.
2. Where a job is: US / India / neither (other countries are not saved).
3. Each portal's feed is read into jobs: title, location, country, link, posting date, type, description;
   postings older than two weeks are skipped.
4. Sync saves them as "Company site (<portal>)" with scraped_at = the POSTING time (an old posting never looks
   new) and never refreshes them; a failing board records its error and the others still run.
5. Companies found in the apply links of jobs we already have are added; the starter list loads once.
6. Routes: list, add by link (checked first), admin-only pause/remove, background sync, Save a job for a consultant.
7. Jobs search: "Posted within" widens the 24h window; source "Company site"; the full posting is not sent in the list.
8. Edge: the Jobs tab opens on cards (match ring for the chosen consultant, Save, details, Apply), the right panel
   lists the company sites and adds one; the Table view is still there and remembered.
Run: python test_career_boards_browser.py
"""
import os
import socket
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_boards.db")
os.environ["DATABASE_URL"] = ""
os.environ["CAREER_SYNC_HOURS"] = "0"
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import career_boards as cb  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


NOW = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

# ---- 1. links
P = cb.parse_board_url
check(P("https://job-boards.greenhouse.io/coherehealth/jobs/8037611003")["slug"] == "coherehealth", "greenhouse job link")
check(P("boards.greenhouse.io/embed/job_board?for=FixtureCo")["slug"] == "fixtureco", "greenhouse embed link")
check(P("https://jobs.lever.co/meesho/494d8ba6")["ats"] == "lever" and P("https://jobs.ashbyhq.com/ramp")["ats"] == "ashby", "lever / ashby")
check(P("https://paypal.wd1.myworkdayjobs.com/en-US/jobs/job/Austin/123")["slug"] == "paypal|wd1|jobs", "workday with language segment")
check(P("https://jobs.smartrecruiters.com/ServiceNow/744000")["slug"] == "ServiceNow" and P("https://apply.workable.com/huggingface/j/ABC/")["slug"] == "huggingface",
      "smartrecruiters / workable")
check(P("https://kidvento.keka.com/careers") is None and P("https://www.naukri.com/job-listings-1") is None, "unsupported portals refused")
links = cb.find_board_links("Apply here: https://jobs.lever.co/fixturelever/abc and https://boards.greenhouse.io/fixturegh/jobs/1 . Thanks")
check([(b["ats"], b["slug"]) for b in links] == [("lever", "fixturelever"), ("greenhouse", "fixturegh")], f"links in text: {links}")

# ---- 2. countries
C = cb.country_of
check(C("Hyderabad, Telangana, India") == "India" and C("Austin, TX") == "United States" and C("Remote - US") == "United States", "basic countries")
check(C("Dublin, Ireland") is None and C("Tokyo, Japan") is None and C("Hybrid") is None, "other / unknown places not saved")
check(C("", "in") == "India" and C("", "US") == "United States", "country codes")
check(C("New York, NY; Bengaluru, India") == "United States" and C("Bengaluru, India; New York, NY") == "India", "first place wins")

# ---- 3. feeds (stubbed)
FEEDS = {
    "https://boards-api.greenhouse.io/v1/boards/fixturegh/jobs": {"jobs": [
        {"id": 1, "title": "Data Analyst (Fixture GH)", "location": {"name": "Dallas, TX"}, "absolute_url": "https://job-boards.greenhouse.io/fixturegh/jobs/1",
         "first_published": iso(NOW - timedelta(hours=5))},
        {"id": 2, "title": "Old Analyst (Fixture GH)", "location": {"name": "Dallas, TX"}, "absolute_url": "https://job-boards.greenhouse.io/fixturegh/jobs/2",
         "first_published": iso(NOW - timedelta(days=40))},
        {"id": 3, "title": "Paris Analyst (Fixture GH)", "location": {"name": "Paris, France"}, "absolute_url": "https://job-boards.greenhouse.io/fixturegh/jobs/3",
         "first_published": iso(NOW - timedelta(hours=2))}]},
    # only the recent US job's description is fetched - the old and the Paris job never are
    "https://boards-api.greenhouse.io/v1/boards/fixturegh/jobs/1": {"content": "&lt;p&gt;We need &lt;b&gt;SQL&lt;/b&gt; and Power BI. Labelled fixture.&lt;/p&gt;"},
    "https://boards-api.greenhouse.io/v1/boards/fixturegh": {"name": "Fixture GH Inc"},
    "https://api.lever.co/v0/postings/fixturelever?mode=json": [
        {"text": "Java Developer (Fixture Lever)", "categories": {"location": "Bengaluru, Karnataka", "commitment": "Full-time"},
         "hostedUrl": "https://jobs.lever.co/fixturelever/abc", "createdAt": int((NOW - timedelta(days=3)).timestamp() * 1000),
         "descriptionPlain": "Java and Spring Boot. Labelled fixture.", "lists": [{"text": "Requirements", "content": "<li>5 years Java</li>"}]}],
    "https://api.ashbyhq.com/posting-api/job-board/fixtureashby": {"jobs": [
        {"title": "Contract SQL Analyst (Fixture Ashby)", "location": "New York, NY", "jobUrl": "https://jobs.ashbyhq.com/fixtureashby/1",
         "publishedAt": iso(NOW - timedelta(hours=1)), "employmentType": "Contract", "descriptionPlain": "SQL reporting. Labelled fixture."}]},
    # Phenom: a 100-row page would ask for the next; this one is short, so it is the whole list
    ("phenom-list", 0): {"refineSearch": {"data": {"jobs": [
        {"jobSeqNo": "FXUS1ENUS", "jobId": "JP-1", "title": "Java Developer (Fixture Phenom)", "cityStateCountry": "Dallas, Texas, USA",
         "postedDate": NOW.strftime("%Y-%m-%dT%H:%M:%S.000+0000"), "type": "Contractor"},
        {"jobSeqNo": "FXUS2ENUS", "jobId": "JP-2", "title": "Old Role (Fixture Phenom)", "cityStateCountry": "Dallas, Texas, USA",
         "postedDate": (NOW - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S.000+0000"), "type": "Contractor"},
        {"jobSeqNo": "FXCA3ENUS", "jobId": "JP-3", "title": "Toronto Role (Fixture Phenom)", "cityStateCountry": "Toronto, Ontario, Canada",
         "postedDate": NOW.strftime("%Y-%m-%dT%H:%M:%S.000+0000"), "type": "Contractor"}]}}},
    ("phenom-detail", "FXUS1ENUS"): {"jobDetail": {"data": {"job": {"description": "<p>Java, Spring and Kafka. Labelled fixture.</p>"}}}},
    "https://fixturewd.wd5.myworkdayjobs.com/wday/cxs/fixturewd/Ext/jobs": {"total": 2, "jobPostings": [
        {"title": "Cloud Engineer (Fixture WD)", "externalPath": "/job/Austin/Cloud_1", "postedOn": "Posted Today", "locationsText": "Austin, TX"},
        {"title": "Stale (Fixture WD)", "externalPath": "/job/Austin/Old_2", "postedOn": "Posted 30+ Days Ago", "locationsText": "Austin, TX"}]},
    "https://fixturewd.wd5.myworkdayjobs.com/wday/cxs/fixturewd/Ext/job/Austin/Cloud_1": {"jobPostingInfo": {
        "title": "Cloud Engineer (Fixture WD)", "location": "Austin, Texas, United States of America", "timeType": "Full time",
        "externalUrl": "https://fixturewd.wd5.myworkdayjobs.com/Ext/job/Austin/Cloud_1", "jobDescription": "<p>AWS and Terraform. Labelled fixture.</p>"}},
}
calls = []


def fake_http(url, body=None):
    calls.append(url if body is None or url != "https://fixtureph.example.com/widgets" else f"{url}#{body.get('ddoKey')}:{body.get('jobSeqNo') or body.get('from')}")
    if url == "https://fixtureph.example.com/widgets":
        key = ("phenom-list", body["from"]) if body["ddoKey"] == "refineSearch" else ("phenom-detail", body["jobSeqNo"])
        if body["ddoKey"] == "refineSearch" and body.get("selected_fields") != {"category": ["Systems & Software"]}:
            raise RuntimeError("the category to keep must be sent to Phenom")
        return FEEDS[key]
    if url not in FEEDS:
        raise RuntimeError(f"404 (fixture) {url}")
    return FEEDS[url]


cb.http_json = fake_http
cb.http_text = lambda url: (url, "<html>not a careers portal (fixture)</html>")   # never the real internet in tests
gh = cb.fetch_board("greenhouse", "fixturegh", "Fixture GH Inc")
check([j["title"] for j in gh] == ["Data Analyst (Fixture GH)"], f"greenhouse: recent US job only: {[j['title'] for j in gh]}")
check(gh and "SQL" in gh[0]["description"] and "<b>" not in gh[0]["description"] and gh[0]["country"] == "United States", f"greenhouse text: {gh}")
lv = cb.fetch_board("lever", "fixturelever", "Fixture Lever")
check(lv and lv[0]["country"] == "India" and lv[0]["job_type"] == "Full-time" and "5 years Java" in lv[0]["description"], f"lever: {lv}")
wd = cb.fetch_board("workday", "fixturewd|wd5|Ext", "Fixture WD")
check([j["title"] for j in wd] == ["Cloud Engineer (Fixture WD)"] and wd[0]["country"] == "United States" and "Terraform" in wd[0]["description"],
      f"workday: today's job read, 30+ days skipped: {wd}")
check(not any("Old_2" in u for u in calls), "an old Workday posting's details are never fetched")
check(not any(u.endswith("/fixturegh/jobs/2") or u.endswith("/fixturegh/jobs/3") for u in calls),
      "greenhouse: an old or non-US/India posting's description is never fetched")
ph = cb.fetch_board("phenom", "fixtureph.example.com|us|en_us|Systems & Software", "Fixture Phenom")
check([j["title"] for j in ph] == ["Java Developer (Fixture Phenom)"] and ph[0]["country"] == "United States" and ph[0]["job_type"] == "Contractor"
      and "Kafka" in ph[0]["description"] and "<p>" not in ph[0]["description"]
      and ph[0]["url"] == "https://fixtureph.example.com/us/en/job/JP-1", f"phenom: recent US job only, with its description: {ph}")
check(not any(c.endswith(":FXUS2ENUS") or c.endswith(":FXCA3ENUS") for c in calls), "phenom: an old or Canada posting's description is never fetched")
check(cb._careers_url("phenom", "careers.teksystems.com|us|en_us|") == "https://careers.teksystems.com/us/en", "phenom careers link")
_plain = cb.http_text
cb.http_text = lambda url: ("https://careers.fixturephenom.com/us/en", '<script>var phApp = {"locale":"en_us","country":"us"};</script>')
pb = cb.phenom_board("careers.fixturephenom.com")
check(pb == {"ats": "phenom", "slug": "careers.fixturephenom.com|us|en_us|", "careers_url": "https://careers.fixturephenom.com/us/en"},
      f"a pasted Phenom careers link is recognised from its page: {pb}")
cb.http_text = _plain
check(cb.phenom_board("https://example.com/careers") is None, "a page that isn't Phenom is not taken for one")

# ---- 4 + 5. the list, sync, discovery
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM career_boards")
check(cur.fetchone()[0] == len(cb.SEED), "starter list loaded once at startup")
check(cb.ensure_seed(conn) == 0, "starter list not added twice")
cur.execute("UPDATE career_boards SET active = 0")          # the real starter companies are not read in this test
conn.commit()
check(cb.add_board(conn, "https://kidvento.keka.com/careers")["status"] == "unsupported", "unsupported link")
r = cb.add_board(conn, "https://boards.greenhouse.io/fixturegh/jobs/1")
check(r["status"] == "added" and r["company"] == "Fixture GH Inc", f"added with the board's own name: {r}")
check(cb.add_board(conn, "https://job-boards.greenhouse.io/fixturegh")["status"] == "exists", "the same company isn't added twice")
check(cb.add_board(conn, "https://jobs.lever.co/nosuchfixture")["status"] == "error", "a board that doesn't answer is refused")
cb.add_board(conn, "https://jobs.ashbyhq.com/fixtureashby", company="Fixture Ashby Co")
cb.add_board(conn, "https://fixturewd.wd5.myworkdayjobs.com/Ext", company="Fixture WD Corp")
models.save_or_update_scraped_job({"title": "Indeed job with an apply link (fixture)", "company": "X", "source": "Indeed",
                                   "url": "https://www.indeed.com/viewjob?jk=fixture1", "description": "Apply at https://jobs.lever.co/fixturelever/abc"})
cur.execute("INSERT INTO career_boards (ats, slug, company, careers_url, found_via, active) VALUES ('greenhouse', 'brokenfixture', 'Broken Fixture', '', 'test', 1)")
conn.commit()
tot = cb.sync_all(conn)
check(tot["new"] == 4 and tot["errors"] == 1, f"sync: 4 new jobs (GH, Lever via discovery, Ashby, WD), 1 failing board: {tot}")
cur.execute("SELECT found_via FROM career_boards WHERE slug = 'fixturelever'")
check(cur.fetchone()[0] == "found in a job link", "company discovered from an apply link")
cur.execute("SELECT last_error FROM career_boards WHERE slug = 'brokenfixture'")
check("404" in (cur.fetchone()[0] or ""), "the failing board records its error")
cur.execute("SELECT source, scraped_at, job_type, country, full_description FROM jobs WHERE url = 'https://jobs.lever.co/fixturelever/abc'")
row = cur.fetchone()
posted = (row[1] if isinstance(row[1], datetime) else datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")).replace(tzinfo=timezone.utc)  # Postgres: a datetime
check(row[0] == "Company site (Lever)" and abs((NOW - posted) - timedelta(days=3)) < timedelta(minutes=5) and row[3] == "India",
      f"saved with the posting time: {tuple(row)}")
old = (NOW - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S")
for url in ("https://jobs.lever.co/fixturelever/old-unused", "https://jobs.lever.co/fixturelever/old-applied"):
    cur.execute("""INSERT INTO jobs (title, company, location, source, url, description, country, status, scraped_at)
                   VALUES ('Old (fixture)', 'X', 'Pune, India', 'Company site (Lever)', ?, 'old', 'India', 'Open', ?)""", (url, old))
cur.execute("SELECT id FROM jobs WHERE url = 'https://jobs.lever.co/fixturelever/old-applied'")
_job_id = cur.fetchone()[0]
conn.commit()                                                                   # SQLite: one writer at a time
_cand = models.create_candidate("Fixture Consultant (career boards)", "fixture.cb@example.com")   # Postgres checks the link
cur.execute("INSERT INTO applications (job_id, candidate_id, stage) VALUES (?, ?, 'Applied')", (_job_id, _cand))
conn.commit()
check(cb.prune_old(conn) == 1, "old company-site jobs removed")
cur.execute("SELECT url FROM jobs WHERE title = 'Old (fixture)'")
check([r[0] for r in cur.fetchall()] == ["https://jobs.lever.co/fixturelever/old-applied"], "a job an application uses is kept")
check(cb.claim_sync(conn) and not cb.claim_sync(conn), "only one sync at a time across workers")
cb.release_sync(conn)
check(cb.claim_sync(conn), "the lock is free again after a sync")
cb.release_sync(conn)
tot2 = cb.sync_all(conn)
cur.execute("SELECT scraped_at FROM jobs WHERE url = 'https://jobs.lever.co/fixturelever/abc'")
check(tot2["new"] == 0 and cur.fetchone()[0] == row[1], "a second sync adds nothing and never refreshes the posting time")
conn.close()

# ---- 6. routes
EMAIL, PW = "boards-rec@example.invalid", "Boards-1"
uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
REC = uid(models.create_user("Bea Boardstest", EMAIL, PW, role="Recruiter", region="USA"))
_c = models.get_db_connection()
_c.cursor().execute("UPDATE users SET region = 'Both' WHERE id = ?", (REC,))      # sees US and India jobs
_c.commit()
_c.close()
K = models.create_candidate("Kai Boardsfixture", "kai.boards@example.invalid", title="Data Analyst", primary_skills="SQL, Power BI, Python",
                            experience_years=5, country="United States", assigned_user_id=REC)
client = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Bea", "role": "Recruiter", "email": EMAIL}
lst = client.get("/api/career-boards").get_json()
check(lst["count"] >= len(cb.SEED) + 4 and "Greenhouse" in lst["portals"], f"list: {lst['count']}")
check(client.post("/api/career-boards", json={"url": "https://www.naukri.com/x"}).status_code == 400, "add: unsupported -> 400")
bid = [b for b in lst["boards"] if b["slug"] == "brokenfixture"][0]["id"]
check(client.delete(f"/api/career-boards/{bid}").status_code == 403, "only an admin removes a company")
sr = client.post("/api/jobs/search", json={"query": "", "country": "United States", "location": "United States", "source": "Company site",
                                           "is_24h_only": True, "type_filter": "", "no_scrape": True}).get_json()
titles = sorted(j["title"] for j in sr["jobs"])
check(titles == ["Cloud Engineer (Fixture WD)", "Contract SQL Analyst (Fixture Ashby)", "Data Analyst (Fixture GH)"], f"company jobs posted in 24h (US): {titles}")
check(all(j["source"].startswith("Company site") for j in sr["jobs"]), "source filter")
sr_in = client.post("/api/jobs/search", json={"query": "", "country": "India", "location": "India", "source": "Company site", "is_24h_only": True,
                                              "type_filter": "", "no_scrape": True}).get_json()
check(sr_in["count"] == 0, "the India job is 3 days old: not in the last 24h")
sr_in = client.post("/api/jobs/search", json={"query": "", "country": "India", "location": "India", "source": "Company site", "is_24h_only": True,
                                              "type_filter": "", "no_scrape": True, "posted_within_hours": 168}).get_json()
check([j["title"] for j in sr_in["jobs"]] == ["Java Developer (Fixture Lever)"], f"posted within a week: {sr_in['jobs']}")
jid = sr["jobs"][0]["id"]
check(client.post(f"/api/jobs/{jid}/save", json={"candidate_id": K}).get_json().get("application_id"), "Save -> Applications")
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT stage FROM applications WHERE job_id = ? AND candidate_id = ?", (jid, K))
check(cur.fetchone()[0] == "Saved", "saved stage")
conn.close()

# ---- 8. Edge
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
        check("app.js?v=5.99.13" in page.content(), "cache-buster not bumped to 5.99.13")
        page.click("a.nav-item[data-tab=jobs]")
        page.wait_for_selector("#jobs-side-consultant")
        check(page.is_hidden("#jobs-table-view") and page.is_visible("#jobs-cards"), "cards are the default Jobs view")
        page.select_option("#filter-source", "Company site")
        check(page.input_value("#filter-job-type") == "" and page.input_value("#filter-posted") == "168", "company sites -> all types, last week")
        page.wait_for_function("document.querySelectorAll('#jobs-cards .job-card').length === 3", timeout=15000)
        card = page.locator(".job-card:has-text('Data Analyst (Fixture GH)')")
        txt = card.inner_text()
        check("Fixture GH Inc" in txt and "posted" in txt and "Company site (Greenhouse)" in txt and "Apply" in txt, f"card: {txt!r}")
        check("67%" in txt, f"match ring for the consultant (SQL + Power BI of 3 skills): {txt!r}")
        card.locator(".jc-details-btn").click()
        check("We need SQL and Power BI" in card.locator(".jc-desc").inner_text(), "details show the posting text")
        card.locator(".jc-save").click()
        page.wait_for_function("[...document.querySelectorAll('.jc-save')].some(b => b.innerText.includes('Saved'))", timeout=10000)
        side = page.inner_text("#jobs-side")
        check("Kai Boardsfixture" in page.inner_text("#jobs-side-consultant") and "Company career sites" in side and "companies read for free" in side,
              f"side panel: {side[:300]!r}")
        page.fill("#jobs-board-url", "https://example.invalid/careers")
        page.click("#jobs-board-add")
        page.wait_for_function("document.getElementById('jobs-board-msg').innerText.includes('supported')", timeout=10000)
        shots["cards"] = os.path.join(tmp_dir, "jobs_cards.png")
        page.screenshot(path=shots["cards"])
        page.click("#jobs-view-table")
        check(page.is_visible("#jobs-table-body") and page.is_hidden("#jobs-cards-wrap"), "table view")
        page.reload()
        page.click("a.nav-item[data-tab=jobs]")
        page.wait_for_timeout(500)
        check(page.is_visible("#jobs-table-view") and page.is_hidden("#jobs-cards-wrap"), "table view remembered")
        page.click("#jobs-view-cards")
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
print(f"PASS: company career sites (links, countries, 5 portals' feeds, sync with posting times, discovery, starter list, routes) + "
      f"Jobs cards (ring, details, Save, side panel, add company) + table view kept. Screenshots: {shots}")
