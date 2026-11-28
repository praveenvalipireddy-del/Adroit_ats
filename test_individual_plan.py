"""Individual plan: one login markets ONE profile - their own (individuals.py).

1. Admin creates an Individual: login + their one profile together (title required, country, daily limit);
   only Recruiter / Admin / Individual are account types; nobody can reassign a consultant to an Individual.
2. Isolation: an Individual sees and edits only their own profile - can't add a second one, delete it, open someone
   else's, or use any recruiter / admin / AI / paid route (an allowlist, so new routes stay closed).
2b. Resume tailoring and the Apollo hiring team are open (own profile only); Gmail drafts / AI emails / the shared
   Contacts list are not.
3. Jobs: free portals by default; Naukri India and LinkedIn recruiter posts only when the admin turns paid portals
   on for that person, with their own daily cap (default 5); a daily limit on live portal searches; a search needs a login (it can start a paid scrape).
4. Extension: lists only their profile; tailoring and AI form answers work (own profile only).
5. Disabled: can't log in, and an open session stops working.
6. Edge: the Individual's screen (My profile, Jobs, My applications, Autofill extension; no recruiter buttons;
   free sources; usage counter), saving the profile, My applications; the admin's Add-user form and roster.
Job portals and the AI are stubs - no network, no cost. Labelled test fixtures. Run: python test_individual_plan.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_individual.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import canada_job_scrapers  # noqa: E402
import india_job_scrapers  # noqa: E402
import individuals  # noqa: E402
import models  # noqa: E402
import resume_bot  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures, scrapes = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def fake_us(*a, **k):
    scrapes.append(("us", k.get("sources")))
    return {"jobs": [], "count": 0, "sources": {}}


def fake_india(*a, **k):
    scrapes.append(("india", k.get("sources")))
    return {"jobs": [], "count": 0, "sources": {}}


def no_ai(*a, **k):
    raise AssertionError("the AI must not be called for an Individual")


us_job_scrapers.run_multi_source_us_scrape = fake_us
india_job_scrapers.run_multi_source_india_scrape = fake_india
canada_job_scrapers.run_multi_source_canada_scrape = fake_india
resume_bot.analyze_for_review = no_ai

uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
ADMIN_EMAIL, ADMIN_PW = "indiv-admin@example.invalid", "Indiv-Admin-1"
ADMIN = uid(models.create_user("Ada Indivtest", ADMIN_EMAIL, ADMIN_PW, role="Admin"))
REC = uid(models.create_user("Rita Indivtest", "rita-indiv@example.invalid", "Indiv-2", role="Recruiter", region="USA"))
OTHER = models.create_candidate("Otto Indivtest", "otto-indiv@example.invalid", title="QA Engineer", assigned_user_id=REC)

client = app_module.app.test_client()


def as_user(user_id, name, role, email):
    with client.session_transaction() as s_:
        s_.clear()
        s_["user"] = {"id": user_id, "name": name, "role": role, "email": email}


def search(**body):
    body.setdefault("query", "Data Analyst")
    return client.post("/api/jobs/search", json=body)


# ---- 1. admin creates Individuals
as_user(ADMIN, "Ada", "Admin", ADMIN_EMAIL)
r = client.post("/api/admin/recruiters", json={"name": "Ivy Indivtest", "email": "ivy-indiv@example.invalid", "password": "Ivy-pass-1",
                                               "role": "Individual", "country": "United States"})
check(r.status_code == 400 and "title" in r.get_json()["error"], f"a job title is required: {r.get_json()}")
r = client.post("/api/admin/recruiters", json={"name": "X", "email": "x-indiv@example.invalid", "password": "x-pass", "role": "Superuser"})
check(r.status_code == 400, "only Recruiter / Admin / Individual")
r = client.post("/api/admin/recruiters", json={"name": "Ivy Indivtest", "email": "ivy-indiv@example.invalid", "password": "Ivy-pass-1",
                                               "role": "Individual", "title": "Data Analyst", "country": "United States", "daily_limit": 2})
check(r.status_code == 200 and r.get_json().get("candidate_id"), f"Individual created: {r.get_json()}")
IVY, IVY_CAND = r.get_json()["user"]["id"], r.get_json()["candidate_id"]
r = client.post("/api/admin/recruiters", json={"name": "Indra Indivtest", "email": "indra-indiv@example.invalid", "password": "Indra-pass-1",
                                               "role": "Individual", "title": "Java Developer", "country": "India"})
INDRA = r.get_json()["user"]["id"]
cand = models.get_candidate_by_id(IVY_CAND)
check(cand and cand["assigned_user_id"] == IVY and cand["title"] == "Data Analyst" and cand["country"] == "United States",
      f"their profile is created and theirs: {cand}")
roster = {u["email"]: u for u in client.get("/api/admin/recruiters").get_json()}
ivy_row = roster.get("ivy-indiv@example.invalid") or {}
check(ivy_row.get("role") == "Individual" and ivy_row.get("daily_limit") == 2 and ivy_row.get("disabled") is False
      and ivy_row.get("searches_today") == 0, f"roster shows the Individual with limit and status: {ivy_row}")
r = client.post(f"/api/admin/candidates/{OTHER}/reassign", json={"assigned_user_id": IVY})
check(r.status_code == 400 and "Individual" in r.get_json()["error"], f"no consultant can be given to an Individual: {r.get_json()}")

# ---- 2. isolation
as_user(IVY, "Ivy Indivtest", "Individual", "ivy-indiv@example.invalid")
mine = client.get("/api/consultants").get_json()
check([c["id"] for c in mine] == [IVY_CAND], f"sees only their own profile: {[c['id'] for c in mine]}")
check(client.post("/api/consultants", json={"name": "Second", "email": "second@example.invalid"}).status_code == 403, "can't add a second profile")
check(client.get(f"/api/consultants/{OTHER}").status_code in (403, 404), "can't open someone else's profile")
check(client.delete(f"/api/consultants/{IVY_CAND}").status_code == 403, "can't delete their profile")
r = client.put(f"/api/consultants/{IVY_CAND}", json={"title": "Senior Data Analyst", "autofill_answers": {"linkedin_url": "https://www.linkedin.com/in/ivy-test"}})
check(r.status_code == 200 and models.get_candidate_by_id(IVY_CAND)["title"] == "Senior Data Analyst", "can edit their own profile")
r = client.put(f"/api/consultants/{IVY_CAND}", json={"country": "India"})
check(r.status_code == 400, "can't move themselves to another country's team")
for method, path in [("GET", "/api/vendors"), ("GET", "/api/activity"), ("POST", "/api/ai/personalize-draft"), ("POST", "/api/jobs/scrape-us"),
                     ("GET", "/api/admin/recruiters"), ("POST", "/api/outreach/create-draft"), ("POST", "/api/students/search"),
                     ("GET", "/api/replies"), ("GET", "/api/requirements"), ("POST", "/api/career-boards/sync"), ("GET", "/api/contacts"),
                     ("POST", f"/api/admin/individuals/{IVY}"), ("POST", "/api/view-region")]:
    r = client.open(path, method=method, json={})
    check(r.status_code == 403, f"{method} {path} is closed to an Individual: {r.status_code}")
r = client.post("/api/applications", json={"candidate_id": OTHER, "job_title": "QA", "company": "Example Co"})
check(r.status_code in (403, 404), f"can't add an application for someone else: {r.status_code}")
check(client.get("/api/individual/me").get_json().get("profile_id") == IVY_CAND, "/api/individual/me gives their profile")
# resume tailoring + Apollo are open, but only for their own profile
r = client.post("/api/resume-bot/optimize", json={"candidate_id": OTHER, "use_stored_file": True, "jd_text": "Python SQL " * 30})
check(r.status_code in (400, 403, 404), f"can't tailor someone else's resume: {r.status_code}")
check(client.get(f"/api/optimized-resumes?candidate_id={OTHER}").status_code in (403, 404), "can't list someone else's tailored resumes")
check(client.get(f"/api/optimized-resumes?candidate_id={IVY_CAND}").status_code == 200, "can list their own tailored resumes")
check(client.get(f"/api/consultants/{IVY_CAND}/rules").status_code == 200, "resume rules for their own profile")
check(client.post("/api/super-apply/search", json={"job_id": 999999}).status_code != 403, "Apollo hiring team search is open")
check(client.post("/api/super-apply/reveal", json={"job_id": 999999, "person_id": "x"}).status_code != 403, "Apollo reveal is open")

# ---- 3. jobs: free portals by default; paid portals only when the admin turns them on, with their own cap
scrapes.clear()
r = search(source="Indeed", live_scrape=True)
check(r.status_code == 200 and r.get_json()["source"] == "All" and scrapes == [("us", None)],
      f"US: a paid source becomes the free portals: {r.get_json().get('source')} {scrapes}")
r = search(source="Recruiter Post", live_scrape=True)
check(r.get_json()["source"] == "All" and scrapes[-1] == ("us", None), f"paid portals are off by default: {r.get_json()['source']}")
u = r.get_json()["usage"]
check(u == {"searches_today": 2, "daily_limit": 2, "paid": False}, f"two live searches counted: {u}")
scrapes.clear()
r = search(source="Dice", live_scrape=True)
check(r.get_json()["limit_reached"] is True and scrapes == [], f"third live search is over the limit - no scrape: {scrapes}")
r = search(source="Company site")
check(r.status_code == 200 and r.get_json()["source"] == "Company site", "company career sites still work")
as_user(ADMIN, "Ada", "Admin", ADMIN_EMAIL)
d = client.post(f"/api/admin/individuals/{IVY}", json={"paid": True}).get_json()
check(d["paid"] is True and d["paid_limit"] == 5 and d["paid_today"] == 0, f"admin turns paid portals on (default 5 a day): {d}")
check(client.post(f"/api/admin/individuals/{IVY}", json={"paid_limit": 0}).status_code == 400, "paid cap must be 1..500")
client.post(f"/api/admin/individuals/{IVY}", json={"paid_limit": 1})
roster = {x["id"]: x for x in client.get("/api/admin/recruiters").get_json()}
check(roster[IVY]["paid"] is True and roster[IVY]["paid_limit"] == 1, "the roster shows paid portals and the cap")
as_user(IVY, "Ivy Indivtest", "Individual", "ivy-indiv@example.invalid")
scrapes.clear()
r = search(source="Recruiter Post", live_scrape=True)
check(r.get_json()["source"] == "Recruiter Post" and scrapes == [("us", ["Recruiter Post"])],
      f"with paid portals on, recruiter posts search (free daily limit already used - paid has its own cap): {scrapes}")
check(r.get_json()["usage"]["paid_today"] == 1, "paid search counted against the paid cap")
scrapes.clear()
r = search(source="Recruiter Post", live_scrape=True)
check(r.get_json()["limit_reached"] is True and scrapes == [], "second paid search is over the paid cap - no scrape")
as_user(INDRA, "Indra Indivtest", "Individual", "indra-indiv@example.invalid")
scrapes.clear()
r = search(source="Naukri", country="United States", live_scrape=True)
check(scrapes == [("india", ["LinkedIn"])], f"Naukri is off for an Individual without paid portals: {scrapes}")
as_user(ADMIN, "Ada", "Admin", ADMIN_EMAIL)
client.post(f"/api/admin/individuals/{INDRA}", json={"paid": True})
as_user(INDRA, "Indra Indivtest", "Individual", "indra-indiv@example.invalid")
scrapes.clear()
r = search(source="Naukri", country="United States", live_scrape=True)
check(scrapes == [("india", ["Naukri"])], f"Naukri search stays in the Individual's India market: {scrapes}")
scrapes.clear()
r = search(source="Recruiter Post", no_scrape=True)
check(not scrapes and r.get_json()["usage"]["paid_today"] == 1, "changing to recruiter posts does not spend a search")
r = search(source="Recruiter Post", live_scrape=True)
check(scrapes == [("india", ["Recruiter Post"])], "only LinkedIn recruiter posts requested")
r = search(source="Foundit", live_scrape=True)
check(scrapes[-1] == ("india", ["LinkedIn"]), "Foundit still falls back to the free India source")
with client.session_transaction() as s_:
    s_.clear()
check(client.post("/api/jobs/search", json={"country": "India", "source": "Naukri"}).status_code == 401, "a job search needs a login")

# ---- 4. extension
as_user(IVY, "Ivy Indivtest", "Individual", "ivy-indiv@example.invalid")
key = client.post("/api/extension/key").get_json()["key"]
H = {"Authorization": f"Bearer {key}"}
me = client.get("/api/ext/me", headers=H).get_json()
check([c["id"] for c in me["consultants"]] == [IVY_CAND], f"the extension lists only their profile: {me['consultants']}")
check(client.post("/api/ext/tailor", headers=H, json={"candidate_id": IVY_CAND}).status_code != 403, "extension tailoring is open")
check(client.post("/api/ext/tailor", headers=H, json={"candidate_id": OTHER}).status_code == 404, "but not for someone else")
ai_calls = []
app_module._ext_ai = lambda system, prompt, max_tokens: ai_calls.append(prompt) or '{"q1": "I enjoy turning data into decisions."}'
r = client.post("/api/ext/answers", headers=H, json={"candidate_id": IVY_CAND, "questions": [{"id": "q1", "label": "Why do you want this job?"}],
                                                      "job": {"title": "Data Analyst", "company": "Example Co", "description": "SQL " * 30}})
check(r.status_code == 200 and len(ai_calls) == 1, f"AI form answers work for an Individual: {r.status_code} {r.get_json()}")
check(client.post("/api/ext/answers", headers=H, json={"candidate_id": OTHER, "questions": [{"id": "q1", "label": "Why?"}]}).status_code == 404,
      "but not for someone else's profile")
r = client.post("/api/ext/match", headers=H, json={"candidate_id": IVY_CAND, "job": {"title": "Data Analyst", "description": "SQL Python Power BI " * 10}})
check(r.status_code == 200, f"match works without AI (keyword check): {r.status_code}")

# ---- 5. disabled
as_user(ADMIN, "Ada", "Admin", ADMIN_EMAIL)
check(client.post(f"/api/admin/individuals/{IVY}", json={"disabled": True}).get_json()["disabled"] is True, "admin disables")
check(client.post(f"/api/admin/individuals/{IVY}", json={"daily_limit": 0}).status_code == 400, "limit must be 1..500")
as_user(IVY, "Ivy Indivtest", "Individual", "ivy-indiv@example.invalid")
check(client.get("/api/consultants").status_code == 401, "an open session stops working")
with client.session_transaction() as s_:
    s_.clear()
r = client.post("/login", data={"email": "ivy-indiv@example.invalid", "password": "Ivy-pass-1"})
check(r.status_code == 200 and b"disabled" in r.data, "a disabled Individual can't log in")
as_user(ADMIN, "Ada", "Admin", ADMIN_EMAIL)
client.post(f"/api/admin/individuals/{IVY}", json={"disabled": False, "daily_limit": 5})

# ---- 6. the screen
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "individual.png")


def login(page, email, pw):
    page.goto(f"http://127.0.0.1:{port}/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", pw)
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard**")


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        login(page, "ivy-indiv@example.invalid", "Ivy-pass-1")
        check("app.js?v=5.99.27" in page.content(), "cache-buster not bumped to 5.99.27")
        page.wait_for_selector("#tab-jobs.active", timeout=10000)
        visible = [t.strip() for t in page.locator(".nav-menu .nav-item:visible span").all_inner_texts() if t.strip()]
        check([v for v in visible if v != "LIVE 24h"] == ["My profile", "Jobs", "Resume Optimizer", "My applications", "Autofill extension"],
              f"the Individual menu: {visible}")
        for sel in ("#btn-header-scrape-us", "#btn-open-paste-draft-modal", "#btn-open-add-consultant", "a[data-tab=vendors]", "a[data-tab=sourcing]"):
            check(not page.is_visible(sel), f"{sel} hidden for an Individual")
        opts = page.eval_on_selector("#filter-source", "s => [...s.options].map(o => o.value)")
        check(opts == ["All", "LinkedIn", "Dice", "ZipRecruiter", "Monster", "Recruiter Post", "Company site"], f"US Individual sources (paid portals on): {opts}")
        page.wait_for_function("document.getElementById('indiv-usage').textContent.includes('of 5 · paid: 1 of 1')", timeout=10000)
        page.click("a.nav-item[data-tab=myprofile]")
        page.wait_for_function("document.getElementById('mp-name').value === 'Ivy Indivtest'", timeout=10000)
        check(page.input_value("#mp-title") == "Senior Data Analyst" and page.input_value("#mp-a-linkedin_url") == "https://www.linkedin.com/in/ivy-test",
              "My profile shows their profile and answers")
        page.fill("#mp-location", "Austin, TX")
        page.click("#mp-save")
        page.wait_for_function("document.getElementById('mp-msg').textContent === 'Saved.'", timeout=10000)
        check(models.get_candidate_by_id(IVY_CAND)["location"] == "Austin, TX", "the profile is saved")
        page.screenshot(path=shot)
        page.click("a.nav-item[data-tab=resumebot]")
        page.wait_for_selector("#tab-resumebot.active", timeout=10000)
        check(page.is_visible("#btn-run-resume-optimization"), "the Resume Optimizer opens for an Individual")
        page.click("#nav-my-applications")
        page.wait_for_selector("#modal-tracker", state="visible", timeout=10000)
        check(not page.is_visible("#ai-tab-replies") and not page.is_visible("#ai-check"), "My applications: no Gmail inbox tabs")

        page2 = browser.new_page(viewport={"width": 1440, "height": 950})
        page2.on("pageerror", lambda e: js_errors.append(str(e)))
        login(page2, ADMIN_EMAIL, ADMIN_PW)
        page2.evaluate("switchTab('settings'); openAddRecruiterModal()")
        page2.select_option("#rec-role", "Individual")
        check(page2.is_visible("#rec-title") and not page2.is_visible("#rec-teams-group"), "Add user: Individual shows title / country / limit, not teams")
        page2.wait_for_selector(f".indiv-toggle[data-id='{IVY}']", timeout=10000)
        check(page2.inner_text(f".indiv-toggle[data-id='{IVY}']") == "Disable", "roster: Disable button for an Individual")
        row = f".indiv-row[data-id='{IVY}']"
        check(page2.is_checked(f"{row} .indiv-paid-on") and page2.input_value(f"{row} .indiv-paid-limit") == "1", "roster: Paid portals ticked with its cap")
        page2.evaluate("closeAddRecruiterModal()")
        page2.uncheck(f"{row} .indiv-paid-on")
        page2.wait_for_function(f"!document.querySelector(\"{row} .indiv-paid-limit\")", timeout=10000)
        c_ = models.get_db_connection()
        check(not app_module.individuals.has_paid(c_, IVY), "unticking turns paid portals off")
        c_.close()
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
print(f"PASS: Individual plan - admin creates login + profile; only their own profile; resume tailoring + Apollo open (own profile); "
      f"recruiter/admin/Gmail routes closed; market sources + daily limit; disabled accounts locked out; Edge screens. Screenshot: {shot}")
