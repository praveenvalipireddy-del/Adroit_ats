"""Canada team (same as India): a third team, Canadian consultants, Canada jobs, Canada vendors.

1. Teams: one or more of USA / India / Canada ('Both' still = USA + India); a Canada recruiter is locked to
   Canada, sees only Canadian consultants, can add a Canadian consultant but not an Indian one.
2. Canada jobs: "All Canada portals" = LinkedIn + Indeed Canada (Indeed asked for country "ca"); a picked portal
   is the only one searched; the jobs are saved as Canada jobs and only the Canada market shows them.
3. Recruiter posts keep only Canadian requirements; company career sites keep Canadian postings.
4. Vendors: a Canada list (.ca email -> Canada).
5. Edge: admin switches to the Canada team; the Jobs tab offers the Canada portals; the consultant form shows
   CA$ and Canadian work permits for Canada; Vendors has a Canada tab; Add Recruiter has a Canada tick box.
Apify, LinkedIn and the AI are stubs - no network, no cost. Labelled test fixtures.
Run: python test_canada_team.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_canada.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = "test-token-not-real"
import app as app_module  # noqa: E402
import canada_job_scrapers as cj  # noqa: E402
import career_boards  # noqa: E402
import india_job_scrapers as ij  # noqa: E402
import linkedin_posts  # noqa: E402
import models  # noqa: E402
import regions  # noqa: E402
import vendors  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures, calls, payloads = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


def fake_post(url, params=None, json=None, timeout=None):
    calls.append("indeed" if "indeed" in url else "other")
    payloads.append(json)
    return FakeResp(200, [{"title": "Java Developer", "url": "https://ca.indeed.com/viewjob?jk=test1", "employer": {"name": "Maple Testco"},
                           "location": {"city": "Toronto", "admin1Code": "ON", "countryCode": "CA"}, "description": {"text": "Java, Spring"}}])


ij.requests.post = fake_post
cj.scrape_linkedin_us = lambda keyword, location="Canada", contract_only=False, limit=15, job_type="": (calls.append(f"linkedin:{location}") or [{
    "title": "Senior Java Engineer", "company": "Beaver Testco", "location": "Vancouver, BC", "job_type": "Contract", "salary": "",
    "source": "LinkedIn (Live 24h)", "url": "https://www.linkedin.com/jobs/view/2000000001", "recruiter_email": "", "description": "Java",
    "matched_skills": keyword, "match_score": 90, "is_24h": 1, "country": "United States"}])

# ---- 1. teams
check(regions.team_set("Both") == {"USA", "India"} and regions.normalize_team(["Canada", "USA"]) == "USA,Canada"
      and regions.normalize_team("canada") == "Canada" and regions.normalize_team("Mars") == "", "team values")
check(regions.view_regions({"role": "Recruiter"}, "USA,Canada", "Canada") == {"Canada"}
      and regions.view_regions({"role": "Recruiter"}, "USA,Canada", "India") == {"USA", "Canada"}
      and regions.view_regions({"role": "Recruiter"}, "Canada", None) == {"Canada"}
      and regions.view_regions({"role": "Admin"}, "USA", None) == {"USA", "India", "Canada"}, "view regions follow the teams")

ADMIN_EMAIL, ADMIN_PW = "ca-admin@example.invalid", "Ca-Admin-1"
CA_EMAIL, CA_PW = "ca-rec@example.invalid", "Ca-Rec-1"
ADMIN = uid(models.create_user("Ada Canadatest", ADMIN_EMAIL, ADMIN_PW, role="Admin"))
CAREC = uid(models.create_user("Cam Canadatest", CA_EMAIL, CA_PW, role="Recruiter", region="Canada"))
c_ca = models.create_candidate("Maple Consultant", "maple-c@example.invalid", title="Java Developer", assigned_user_id=CAREC, country="Canada")
models.create_candidate("Usha Canadatest", "usha-ca@example.invalid", title="QA", assigned_user_id=CAREC, country="United States")
conn = models.get_db_connection()
check(regions.user_region(conn, CAREC) == "Canada", "a recruiter can be put on the Canada team")
conn.close()

client = app_module.app.test_client()


def as_user(i, name, role, email):
    with client.session_transaction() as s_:
        s_["user"] = {"id": i, "name": name, "role": role, "email": email}


as_user(CAREC, "Cam", "Recruiter", CA_EMAIL)
names = sorted(c["name"] for c in client.get("/api/consultants").get_json())
check(names == ["Maple Consultant"], f"a Canada recruiter sees only Canadian consultants: {names}")
check(client.post("/api/view-region", json={"region": "USA"}).status_code == 403, "a one-team recruiter can't switch")
r = client.post("/api/consultants", json={"name": "Indu Canadatest", "email": "indu-ca@example.invalid", "country": "India", "title": "QA"})
check(r.status_code == 400 and "India team" in (r.get_json() or {}).get("error", ""), f"no India consultant for a Canada recruiter: {r.get_json()}")
as_user(ADMIN, "Ada", "Admin", ADMIN_EMAIL)
r = client.post(f"/api/admin/recruiters/{CAREC}/region", json={"region": "USA,Canada"})
check(r.status_code == 200 and r.get_json()["region"] == "USA,Canada", f"admin sets two teams: {r.get_json()}")
as_user(CAREC, "Cam", "Recruiter", CA_EMAIL)
check(client.post("/api/view-region", json={"region": "Canada"}).status_code == 200, "a two-team recruiter switches to Canada")
check(client.post("/api/view-region", json={"region": "India"}).status_code == 403, "but not to a team they're not on")
client.post("/api/view-region", json={"region": ""})
as_user(ADMIN, "Ada", "Admin", ADMIN_EMAIL)
client.post(f"/api/admin/recruiters/{CAREC}/region", json={"region": "Canada"})

# ---- 2. Canada jobs
calls.clear()
res = cj.run_multi_source_canada_scrape(["Java Developer"], save_to_db=False)
check(sorted(calls) == ["indeed", "linkedin:Canada"] and "Recruiter Post" not in res["sources"], f"All Canada portals = LinkedIn + Indeed: {calls} {res['sources']}")
check(payloads[-1]["country"] == "ca" and payloads[-1]["location"] == "", f"Indeed is asked for Canada: {payloads[-1]}")
check(all(j["country"] == "Canada" for j in res["jobs"]) and {j["location"] for j in res["jobs"]} == {"Toronto, ON", "Vancouver, BC"},
      f"Canada jobs: {[(j['location'], j['country']) for j in res['jobs']]}")
calls.clear()
res = cj.run_multi_source_canada_scrape(["Java Developer"], save_to_db=False, sources=["LinkedIn"])
check(calls == ["linkedin:Canada"] and list(res["sources"]) == ["LinkedIn"], f"a picked portal only: {calls}")

as_user(ADMIN, "Ada", "Admin", ADMIN_EMAIL)
calls.clear()
d = client.post("/api/jobs/search", json={"query": "Java Developer", "location": "Canada", "source": "All", "country": "Canada",
                                          "live_scrape": True, "is_24h_only": True}).get_json()
check(sorted(calls) == ["indeed", "linkedin:Canada"] and {j["company"] for j in d["jobs"]} == {"Maple Testco", "Beaver Testco"}, f"Jobs tab Canada search: {calls} {[j['company'] for j in d['jobs']]}")
d_us = client.post("/api/jobs/search", json={"query": "Java Developer", "country": "United States", "no_scrape": True, "source": "Company site"}).get_json()
check(not any(j["company"] in ("Maple Testco", "Beaver Testco") for j in d_us["jobs"]), "Canada jobs don't show in the US market")
calls.clear()
r = client.post("/api/jobs/scrape-canada", json={"keywords": ["Java Developer"], "source": "Indeed"}).get_json()
check(r["success"] and calls == ["indeed"] and list(r["sources"]) == ["Indeed"], f"Live 24h Scrape for Canada, Indeed only: {calls} {r}")

# ---- 3. recruiter posts and career sites
posts = [{"type": "post", "content": f"We are hiring a Java developer, send resumes {n}", "linkedinUrl": f"https://www.linkedin.com/posts/test-{n}",
          "author": {"name": f"Rita {n}"}} for n in range(1, 4)]
ij_run = ij._run_actor_sync
ij._run_actor_sync = lambda actor, payload, timeout=150: (payloads.append(payload) or posts, None)
linkedin_posts._ai_batch = lambda batch: {p["n"]: {"is_requirement": True, "title": "Java Developer", "market": m}
                                           for p, m in zip(batch, ["Canada", "USA", "India"])}
out = linkedin_posts.search("Java Developer", market="Canada")
ij._run_actor_sync = ij_run
check(payloads[-1]["searchQueries"] == ["hiring Java Developer Canada"], f"the post search asks for Canada: {payloads[-1]}")
check(out["requirements"] == 1 and out["other_market"] == 2 and out["jobs"][0]["country"] == "Canada",
      f"only Canadian requirements kept: {out['requirements']} / other {out['other_market']}")
check(career_boards.country_of("Toronto, ON") == "Canada" and career_boards.country_of("Remote - Canada") == "Canada"
      and career_boards.country_of("", "CA") == "Canada" and career_boards.country_of("Dallas, TX") == "United States"
      and career_boards.country_of("Waterloo, IA") == "United States" and career_boards.country_of("London, UK") is None,
      "career sites: Canadian postings are kept as Canada")

# ---- 4. vendors
check(vendors.guess_region("+1 416 555 0100", "rita@maple-test.ca") == "Canada" and vendors.guess_region("+1 949 688 0755", "a@b.com") == "USA",
      "a .ca email goes to the Canada list")
conn = models.get_db_connection()
vendors.import_rows(conn, ADMIN, [{"company": "Maple Vendor Test", "email": "v@maple-vendor.example"}], region="Canada")
conn.close()
v = client.get("/api/vendors?region=Canada").get_json()
check(v["region"] == "Canada" and [c["email"] for c in v["contacts"]] == ["v@maple-vendor.example"] and v["region_counts"]["Canada"] == 1,
      f"Canada vendor list: {v.get('region')} {v.get('region_counts')}")

# ---- 5. the screens
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "canada.png")


def login(page, email, pw):
    page.goto(f"http://127.0.0.1:{port}/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", pw)
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard**")


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        login(page, ADMIN_EMAIL, ADMIN_PW)
        check("app.js?v=5.99.22" in page.content(), "cache-buster not bumped to 5.99.22")
        opts = page.eval_on_selector("#view-region-switch", "s => [...s.options].map(o => o.textContent.trim())")
        check(opts == ["🌐 All teams", "🇺🇸 USA team", "🇮🇳 India team", "🇨🇦 Canada team"], f"team switcher: {opts}")
        with page.expect_navigation():
            page.select_option("#view-region-switch", "Canada")
        page.wait_for_function("typeof state !== 'undefined' && state.consultants && state.consultants.length === 1", timeout=15000)
        check(page.evaluate("state.consultants[0].name") == "Maple Consultant", "Canada view: Canadian consultants only")
        check(page.input_value("#filter-country") == "Canada" and page.is_disabled("#filter-country"), "Canada view: jobs market locked to Canada")
        page.click("a.nav-item[data-tab=jobs]")
        srcs = page.eval_on_selector("#filter-source", "s => [...s.options].map(o => o.value)")
        check(srcs == ["All", "LinkedIn", "Indeed", "Recruiter Post", "Company site"], f"Canada portals: {srcs}")
        check("Indeed Canada" in page.inner_text("#filter-source"), "Indeed Canada named")
        page.screenshot(path=shot)
        # the consultant form
        page.evaluate("openConsultantModal()")
        page.select_option("#c-country", "Canada")
        page.dispatch_event("#c-country", "change")
        check("CA$" in page.inner_text("#c-rate-label"), f"CA$ rate: {page.inner_text('#c-rate-label')}")
        visas = page.eval_on_selector("#c-visa", "s => [...s.options].map(o => o.value).filter(Boolean)")
        check(visas == ["Open work permit", "Permanent resident", "Canadian citizen", "Closed work permit"], f"Canadian work permits: {visas}")
        page.keyboard.press("Escape")
        page.evaluate("document.querySelectorAll('.modal').forEach(m => m.style.display = 'none')")
        # vendors
        page.click("a.nav-item[data-tab=vendors]")
        page.wait_for_selector("#vendors-tab-canada", state="visible", timeout=10000)
        page.click("#vendors-tab-canada")
        page.wait_for_function("document.getElementById('vendors-tab-canada').classList.contains('on')", timeout=10000)
        check("(1)" in page.inner_text("#vendors-tab-canada"), f"Canada vendors tab: {page.inner_text('#vendors-tab-canada')}")
        # add recruiter
        page.evaluate("window.openAddRecruiterModal()")
        page.fill("#rec-name", "Nia Canadatest")
        page.fill("#rec-email", "nia-ca@example.invalid")
        page.fill("#rec-password", "Nia-Canada-1")
        page.uncheck("#rec-region input[value=USA]")
        page.check("#rec-region input[value=Canada]")
        page.evaluate("document.getElementById('form-add-recruiter').requestSubmit()")
        page.wait_for_timeout(1500)
        new = [u for u in models.get_users() if u["email"] == "nia-ca@example.invalid"]
        check(new and new[0].get("region") == "Canada", f"Add Recruiter saves the Canada team: {new}")
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
print(f"PASS: Canada team - teams (one or more, Canada locked), Canadian consultants only, Canada jobs from LinkedIn + Indeed Canada "
      f"(picked portal only), Canadian recruiter posts and career-site postings, Canada vendor list, screens in Edge. Screenshot: {shot}")
