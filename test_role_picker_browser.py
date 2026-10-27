"""Role picker (like NextRaise): categories -> roles with real job counts; picking roles filters the jobs (labelled fixtures).

1. Titles map to roles on whole words ("JavaScript" is not "Java"; "Automotive Domain" is not AI).
2. /api/jobs/role-counts: counts per role and category for the market / date filters; a job counts once per category.
3. /api/jobs/search with roles: only jobs whose title is one of the picked roles (OR between roles).
4. Edge, Jobs page: "All roles" -> open -> categories with counts -> search roles -> pick two -> "Show jobs" -> only those
   jobs; the button says "<first> +1"; a chip × removes one.
5. Edge, consultant page: picking a role replaces the title keyword and filters Matched jobs.
Run: python test_role_picker_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_roles.db")
os.environ["DATABASE_URL"] = ""
os.environ["CAREER_SYNC_HOURS"] = "0"
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import models  # noqa: E402
import roles_catalog as rc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---- 1. matching
check(rc.roles_of("Senior Java Full Stack Developer") == ["Java Developer", "Full Stack Developer"], "java full stack")
check("Java Developer" not in rc.roles_of("JavaScript Engineer") and "Frontend Developer" in rc.roles_of("JavaScript Engineer"), "javascript is not java")
check(rc.roles_of("UI Designer - Automotive Domain") == ["UI / UX Designer"], "no AI in 'Domain'")
check(rc.matches("AI Automation Engineer", ["AI / ML Engineer"]) and not rc.matches("Data Analyst", ["AI / ML Engineer"]), "matches")

# ---- fixtures
uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "roles-rec@example.invalid", "Roles-1"
REC = uid(models.create_user("Rae Rolestest", EMAIL, PW, role="Recruiter", region="USA"))
K = models.create_candidate("Kai Rolesfixture", "kai.roles@example.invalid", title="Senior Data Analyst", primary_skills="SQL, Power BI",
                            country="United States", assigned_user_id=REC)
for i, title in enumerate(["Data Analyst (RP fixture)", "Senior Java Developer (RP fixture)", "DevOps Engineer (RP fixture)",
                           "Marketing Operations Manager (RP fixture)", "Power BI Developer (RP fixture)"]):
    models.save_or_update_scraped_job({"title": title, "company": f"Fixture Co {i}", "source": "Dice", "url": f"https://www.dice.com/job-detail/rp-{i}",
                                       "location": "Dallas, TX", "job_type": "Contract (C2C)", "description": "Labelled test fixture.", "country": "United States"})

client = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Rae", "role": "Recruiter", "email": EMAIL}

# ---- 2. counts
d = client.post("/api/jobs/role-counts", json={"country": "United States", "posted_within_hours": 24}).get_json()
cats = {c["name"]: c for c in d["categories"]}
roles = {r["label"]: r["count"] for c in d["categories"] for r in c["roles"]}
check(roles["Data Analyst"] == 1 and roles["Java Developer"] == 1 and roles["DevOps Engineer"] == 1 and roles["Marketing Operations"] == 1
      and roles["BI / Power BI Developer"] == 1, f"role counts: { {k: v for k, v in roles.items() if v} }")
check(cats["Data & Analytics"]["count"] == 2 and cats["Marketing"]["count"] == 1 and cats["Finance & Accounting"]["count"] == 0, "category counts")
check(len(d["categories"]) == len(rc.CATALOG), "every category listed, even with 0")

# ---- 3. search by roles
base = {"query": "", "country": "United States", "location": "United States", "source": "All", "is_24h_only": True, "type_filter": "", "no_scrape": True}
titles = sorted(j["title"] for j in client.post("/api/jobs/search", json={**base, "roles": ["Data Analyst", "DevOps Engineer"]}).get_json()["jobs"])
check(titles == ["Data Analyst (RP fixture)", "DevOps Engineer (RP fixture)"], f"OR between roles: {titles}")
titles = [j["title"] for j in client.post("/api/jobs/search", json={**base, "roles": ["Not A Role"]}).get_json()["jobs"]]
check(len(titles) >= 5, "an unknown role is ignored (not an empty list)")

# ---- 4 + 5. Edge
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "role_picker.png")
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
        page.click("a.nav-item[data-tab=jobs]")
        page.select_option("#filter-job-type", "")
        page.wait_for_function("document.querySelectorAll('#jobs-cards .job-card').length >= 5", timeout=15000)
        check(page.inner_text("#jobs-roles .rp-btn").startswith("All roles"), "starts with All roles")
        page.click("#jobs-roles .rp-btn")
        page.wait_for_selector("#jobs-roles .rp-cat-h")
        check("DATA & ANALYTICS" in page.inner_text("#jobs-roles .rp-cats").upper(), "categories shown")
        page.fill("#jobs-roles .rp-search", "devops")
        page.wait_for_selector("#jobs-roles .rp-role:has-text('DevOps Engineer')")
        shown = page.locator("#jobs-roles .rp-role").all_inner_texts()
        check(len(shown) == 3 and not any("Java" in s for s in shown), f"search narrows to the 'Cloud & DevOps' roles: {shown}")
        page.check("#jobs-roles .rp-role:has-text('DevOps Engineer') input")
        page.fill("#jobs-roles .rp-search", "")
        page.locator("#jobs-roles .rp-search").dispatch_event("input")
        page.locator("#jobs-roles .rp-cat-h:has-text('Data & Analytics')").click()
        page.check("#jobs-roles .rp-role:has-text('Data Analyst') input")
        check(page.locator("#jobs-roles .rp-chip").count() == 2, "picked roles as chips")
        page.screenshot(path=shot)
        page.click("#jobs-roles .rp-done")
        page.wait_for_function("document.querySelectorAll('#jobs-cards .job-card').length === 2", timeout=15000)
        cards = page.inner_text("#jobs-cards")
        check("DevOps Engineer (RP fixture)" in cards and "Data Analyst (RP fixture)" in cards and "Java" not in cards, f"only the picked roles: {cards[:200]!r}")
        check("+1" in page.inner_text("#jobs-roles .rp-btn"), f"button label: {page.inner_text('#jobs-roles .rp-btn')!r}")
        page.click("#jobs-roles .rp-btn")
        page.click("#jobs-roles .rp-chip:has-text('DevOps') button")
        page.click("#jobs-roles .rp-done")
        page.wait_for_function("document.querySelectorAll('#jobs-cards .job-card').length === 1", timeout=15000)

        # consultant page
        page.click("a.nav-item[data-tab=candidates]")
        page.click(f".cons-card[data-id='{K}']")
        page.wait_for_selector("#cons-jf-roles .rp-btn")
        page.click("#cons-jf-roles .rp-btn")
        page.wait_for_selector("#cons-jf-roles .rp-cat-h")
        page.fill("#cons-jf-roles .rp-search", "power bi")
        page.check("#cons-jf-roles .rp-role:has-text('Power BI') input")
        page.click("#cons-jf-roles .rp-done")
        page.wait_for_function("document.getElementById('cons-jobs-list') && document.getElementById('cons-jobs-list').innerText.includes('Power BI Developer (RP fixture)') "
                               "&& !document.getElementById('cons-jobs-list').innerText.includes('Data Analyst (RP fixture)')", timeout=15000)
        check(page.input_value("#cons-jf-query") == "", "the picked role replaces the title keyword")
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
print(f"PASS: role picker - whole-word role matching, real counts per role / category, OR search, Jobs page picker (search, chips, "
      f"show jobs, remove), consultant page picker. Screenshot: {shot}")
