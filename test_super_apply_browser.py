"""Super Apply - the hiring team via Apollo (stubbed: no network, no credits).

1. Company names: same company (suffixes ignored), another company is not.
2. Search (free): only people whose employer is the job's company, recruiters / talent first, the job's country sent.
3. Reveal (1 credit): a VERIFIED email is returned and saved on the job (when it has none); a guessed / unverified one
   is never returned; an unmatched person is said to cost nothing; every match is logged.
4. Not connected / a job with no company name -> a clear message, nothing called.
5. Edge: Super Apply on the consultant's Matched jobs: people listed, the credit is confirmed first, the verified email
   is shown and "Create Gmail draft" drafts the job for the consultant to that email.
Run: python test_super_apply_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_super_apply.db")
os.environ["DATABASE_URL"] = ""
os.environ["APOLLO_API_KEY"] = ""
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import apollo_api  # noqa: E402
import app as app_module  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


SC = apollo_api.same_company
check(SC("Fixture Health Inc", "Fixture Health") and SC("FIXTURE HEALTH PVT LTD", "Fixture Health"), "same company despite suffixes")
check(not SC("Fixture Health", "Other Health") and not SC("", "Fixture Health"), "different / empty company")

P1, P2, P3, P4 = "a" * 24, "b" * 24, "c" * 24, "d" * 24
calls = []


def fake_post(path, body):
    calls.append((path, body))
    if path == "/mixed_people/api_search":
        return {"people": [
            {"id": P3, "first_name": "Mona", "last_name_obfuscated": "Ma***r", "title": "Engineering Manager", "organization": {"name": "Fixture Health"}},
            {"id": P1, "first_name": "Rita", "last_name_obfuscated": "Re***r", "title": "Senior Technical Recruiter", "organization": {"name": "Fixture Health Inc"},
             "has_email": True, "linkedin_url": "https://www.linkedin.com/in/rita-fixture"},
            {"id": P2, "first_name": "Hari", "last_name_obfuscated": "H***", "title": "HR Business Partner", "organization": {"name": "Fixture Health"}},
            {"id": P4, "first_name": "Otto", "last_name_obfuscated": "O***", "title": "Recruiter", "organization": {"name": "Unrelated Staffing LLC"}}]}
    if path == "/people/match":
        if body["id"] == P1:
            return {"person": {"name": "Rita Recruiter", "email": "rita@fixturehealth.example", "email_status": "verified", "title": "Senior Technical Recruiter",
                               "organization": {"name": "Fixture Health"}}}
        if body["id"] == P2:
            return {"person": {"name": "Hari H", "email": "hari@fixturehealth.example", "email_status": "guessed", "organization": {"name": "Fixture Health"}}}
        return {"person": None}
    raise AssertionError(path)


# ---- 4. not connected
check(not apollo_api.configured(), "no key in tests")
try:
    apollo_api.search_hiring_team("Fixture Health")
    failures.append("search without a key should fail")
except apollo_api.ApolloError as ex:
    check("APOLLO_API_KEY" in str(ex), f"not connected message: {ex}")

apollo_api.http_post = fake_post
os.environ["APOLLO_API_KEY"] = "fixture-not-a-real-key"

# ---- 2. search
people = apollo_api.search_hiring_team("Fixture Health", "United States")
check([p["id"] for p in people] == [P1, P2, P3], f"own company only, recruiters first: {[(p['name'], p['title']) for p in people]}")
check(calls[-1][1]["person_locations"] == ["United States"] and calls[-1][1]["q_keywords"] == "Fixture Health", f"search body: {calls[-1][1]}")
try:
    apollo_api.search_hiring_team("(company not shown)")
    failures.append("a job without a company should not be searched")
except apollo_api.ApolloError:
    pass

# ---- 3. reveal
r = apollo_api.reveal(P1)
check(r["verified"] and r["email"] == "rita@fixturehealth.example", f"verified email: {r}")
r = apollo_api.reveal(P2)
check(not r["verified"] and r["email"] == "" and "guessed" in r["note"] and "1 credit" in r["note"], f"guessed email never used: {r}")
r = apollo_api.reveal(P3)
check(not r["matched"] and "no credit" in r["note"], f"unmatched: {r}")

# ---- routes + Edge
uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "sa-rec@example.invalid", "SuperApply-1"
REC = uid(models.create_user("Sia Superapply", EMAIL, PW, role="Recruiter", region="USA"))
K = models.create_candidate("Kit Superfixture", "kit.super@example.invalid", title="Data Analyst", primary_skills="SQL, Power BI, Python, Tableau",
                            experience_years=5, country="United States", assigned_user_id=REC)
jid = models.save_or_update_scraped_job({"title": "Data Analyst (Super Apply Fixture)", "company": "Fixture Health", "source": "Company site (Greenhouse)",
                                         "url": "https://job-boards.greenhouse.io/fixturehealth/jobs/1", "location": "Dallas, TX", "job_type": "Full-time",
                                         "description": "SQL and Power BI reporting. Labelled test fixture.", "country": "United States"})
jid = jid["id"] if isinstance(jid, dict) else jid
client = app_module.app.test_client()
check(client.post("/api/super-apply/search", json={"job_id": jid}).status_code == 401, "login required")
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Sia", "role": "Recruiter", "email": EMAIL}
check(client.post("/api/super-apply/check").get_json()["ok"], "check: connected")
d = client.post("/api/super-apply/search", json={"job_id": jid}).get_json()
check(len(d["people"]) == 3 and d["company"] == "Fixture Health", f"search route: {d}")
d = client.post("/api/super-apply/reveal", json={"job_id": jid, "person_id": P1}).get_json()
check(d["verified"] and d.get("saved_on_job") and models.get_job_by_id(jid)["recruiter_email"] == "rita@fixturehealth.example", f"saved on the job: {d}")
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM activity_logs WHERE action LIKE 'Apollo email reveal%'")
check(cur.fetchone()[0] == 1, "credit use logged")
conn.close()
models.update_job_recruiter_info(jid, email="")          # the browser run reveals again

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "super_apply.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        dialogs = []
        page.on("dialog", lambda dlg: (dialogs.append(dlg.message), dlg.accept()))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.12" in page.content(), "cache-buster not bumped to 5.99.12")
        page.evaluate("window.createJobDraft = (...a) => { window.__draft = a.slice(0, 3); }")
        page.click("a.nav-item[data-tab=candidates]")
        page.click(f".cons-card[data-id='{K}']")
        page.wait_for_selector(".cons-job .cons-job-super", timeout=15000)
        page.click(".cons-job .cons-job-super")
        page.wait_for_selector("#modal-super-apply .sa-person", timeout=10000)
        txt = page.inner_text("#modal-super-apply")
        check("Rita Re***r" in txt and "Senior Technical Recruiter" in txt and "Otto" not in txt and "1 credit" in txt, f"people listed: {txt[:400]!r}")
        page.click(f".sa-person[data-id='{P1}'] .sa-reveal")
        page.wait_for_selector(".sa-draft", timeout=10000)
        check(dialogs and "1 Apollo credit" in dialogs[0], f"the credit is confirmed first: {dialogs}")
        check("rita@fixturehealth.example" in page.inner_text("#modal-super-apply"), "verified email shown")
        page.click(f".sa-person[data-id='{P2}'] .sa-reveal")
        page.wait_for_selector(f".sa-person[data-id='{P2}'] .sa-warn", timeout=10000)
        check("hari@" not in page.inner_text("#modal-super-apply"), "the guessed email is never shown")
        page.screenshot(path=shot)
        page.click(".sa-draft")
        check(page.evaluate("window.__draft") == [jid, K, "rita@fixturehealth.example"], f"draft to the verified email: {page.evaluate('window.__draft')}")
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
print(f"PASS: Super Apply - same-company filter, recruiters first, verified emails only (guessed never used), credit confirmed + logged, "
      f"saved on the job, Gmail draft to the verified email. Screenshot: {shot}")
