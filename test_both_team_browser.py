"""Recruiters who work for both teams (USA + India).

1. Admin puts a recruiter on 'Both'; USA / India / Both are the only teams.
2. A 'Both' recruiter can be assigned US and Indian consultants, sees both (only their own), can add a
   consultant of either country, switches the view (Both / USA / India) and is not locked to one Jobs market.
3. A USA-only recruiter still can't switch, can't add an Indian consultant and can't be given one.
4. Edge: the Both recruiter has the team switcher and both consultants; the admin roster offers 'Both'.
Labelled test fixtures. Run: python test_both_team_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_both_team.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import regions  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0, "sources": {}}
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
ADMIN_EMAIL, ADMIN_PW = "both-admin@example.invalid", "Both-Admin-1"
ADMIN = uid(models.create_user("Ada Bothtest", ADMIN_EMAIL, ADMIN_PW, role="Admin"))
MAHESH_EMAIL, MAHESH_PW = "mahesh-both@example.invalid", "Both-2"
MAHESH = uid(models.create_user("Mahesh Bothtest", MAHESH_EMAIL, MAHESH_PW, role="Recruiter", region="USA"))
USONLY_EMAIL, USONLY_PW = "usonly-both@example.invalid", "Both-3"
USONLY = uid(models.create_user("Usa Onlytest", USONLY_EMAIL, USONLY_PW, role="Recruiter", region="USA"))
us_c = models.create_candidate("Usha Bothtest", "usha-both@example.invalid", title="Java Developer", assigned_user_id=ADMIN)
in_c = models.create_candidate("Indu Bothtest", "indu-both@example.invalid", title="Data Engineer", assigned_user_id=ADMIN, country="India")
other_c = models.create_candidate("Other Bothtest", "other-both@example.invalid", title="QA", assigned_user_id=USONLY)

client = app_module.app.test_client()


def as_user(uid_, name, role, email):
    with client.session_transaction() as s_:
        s_.clear()
        s_["user"] = {"id": uid_, "name": name, "role": role, "email": email}


def names():
    return sorted(c["name"] for c in client.get("/api/consultants").get_json())


# ---- 1. admin sets the team
as_user(ADMIN, "Ada", "Admin", ADMIN_EMAIL)
r = client.post(f"/api/admin/recruiters/{MAHESH}/region", json={"region": "Both"})
check(r.status_code == 200 and r.get_json()["region"] == "Both", f"Mahesh on Both: {r.get_json()}")
r = client.post(f"/api/admin/recruiters/{MAHESH}/region", json={"region": "Mars"})
check(r.status_code == 400 and "USA, India or Both" in r.get_json()["error"], "only USA / India / Both")
roster = {u["email"]: u.get("region") for u in client.get("/api/admin/recruiters").get_json()}
check(roster.get(MAHESH_EMAIL) == "Both", f"roster shows Both: {roster.get(MAHESH_EMAIL)}")
for cid in (us_c, in_c):
    r = client.post(f"/api/admin/candidates/{cid}/reassign", json={"assigned_user_id": MAHESH})
    check(r.status_code == 200, f"a Both recruiter can get a {'US' if cid == us_c else 'Indian'} consultant: {r.get_json()}")
r = client.post(f"/api/admin/candidates/{in_c}/reassign", json={"assigned_user_id": USONLY})
check(r.status_code == 400 and "India consultant" in r.get_json()["error"], "a USA-only recruiter still can't get an Indian consultant")

# ---- 2. the Both recruiter
as_user(MAHESH, "Mahesh", "Recruiter", MAHESH_EMAIL)
check(names() == ["Indu Bothtest", "Usha Bothtest"], f"sees US + India consultants, only their own: {names()}")
check(client.post("/api/view-region", json={"region": "India"}).status_code == 200 and names() == ["Indu Bothtest"], "India view")
check(client.post("/api/view-region", json={"region": "USA"}).status_code == 200 and names() == ["Usha Bothtest"], "USA view")
client.post("/api/view-region", json={"region": ""})
check(names() == ["Indu Bothtest", "Usha Bothtest"], "back to both")
check(app_module._team_country_problem({"id": MAHESH, "role": "Recruiter"}, "India") == "", "can add an Indian consultant")
with app_module.app.test_request_context():
    from flask import session as fs
    fs["user"] = {"id": MAHESH, "name": "Mahesh", "role": "Recruiter"}
    check(app_module._job_country_for(fs["user"], "India") == "India" and app_module._job_country_for(fs["user"], "United States") == "United States",
          "Jobs market not locked for a Both recruiter")

# ---- 3. a USA-only recruiter is unchanged
as_user(USONLY, "Usa", "Recruiter", USONLY_EMAIL)
check(client.post("/api/view-region", json={"region": "India"}).status_code == 403, "a one-team recruiter can't switch")
check("India team" in app_module._team_country_problem({"id": USONLY, "role": "Recruiter"}, "India"), "and can't add an Indian consultant")
check(names() == ["Other Bothtest"], f"sees only their own: {names()}")

# ---- 4. the screen
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "both_team.png")


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
        login(page, MAHESH_EMAIL, MAHESH_PW)
        check("app.js?v=5.99.1" in page.content(), "cache-buster not bumped to 5.99.1")
        check(page.is_visible("#view-region-switch") and not page.locator("#team-badge").count(), "the Both recruiter has the team switcher")
        page.click("a.nav-item[data-tab=candidates]")
        page.click("#cons-view-table")       # the table view (cards are the default)
        page.wait_for_selector("#consultants-table-body tr", timeout=10000)
        txt = page.inner_text("#consultants-table-body")
        check("Usha Bothtest" in txt and "Indu Bothtest" in txt and "Other Bothtest" not in txt, "both consultants listed")
        check(page.locator(".btn-trigger-reassign").count() == 0, "still no admin controls")
        page.select_option("#view-region-switch", "India")
        page.wait_for_load_state("load")
        page.click("a.nav-item[data-tab=candidates]")
        page.click("#cons-view-table")       # the table view (cards are the default)
        page.wait_for_function("!document.getElementById('consultants-table-body').innerText.includes('Usha Bothtest')", timeout=10000)
        check("Indu Bothtest" in page.inner_text("#consultants-table-body"), "India view in the screen")
        page.screenshot(path=shot)

        page2 = browser.new_page(viewport={"width": 1500, "height": 950})
        page2.on("pageerror", lambda e: js_errors.append(str(e)))
        login(page2, USONLY_EMAIL, USONLY_PW)
        check(not page2.locator("#view-region-switch").count() and page2.is_visible("#team-badge"), "a one-team recruiter has the badge, no switcher")

        page3 = browser.new_page(viewport={"width": 1500, "height": 950})
        page3.on("pageerror", lambda e: js_errors.append(str(e)))
        login(page3, ADMIN_EMAIL, ADMIN_PW)
        page3.evaluate("loadRecruiters()")
        page3.wait_for_selector(f".recruiter-region[data-id='{MAHESH}']", state="attached", timeout=10000)
        check(page3.eval_on_selector(f".recruiter-region[data-id='{MAHESH}']", "s => s.value") == "Both", "admin roster shows Both for Mahesh")
        check("Both (USA + India)" in page3.inner_html("#rec-region"), "Add Recruiter offers Both")
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
print(f"PASS: recruiters on both teams - admin sets Both, US + Indian consultants assigned and listed, view switch, add either country, jobs market free; one-team recruiters unchanged; Edge. Screenshot: {shot}")
