"""Recruiter isolation: a recruiter sees and acts only on consultants assigned to them; the admin's
consultants never appear in a recruiter's login; recruiters don't get the Admin & Settings menu.

Root cause this guards against: get_candidates() also returned every consultant owned by user #1
(the admin) or unassigned, so the admin's bench showed in every recruiter's dashboard; the
"Admin & Settings" menu item itself was not role-gated (only its badge was); and several
consultant-id routes (drafts, resume upload, Gmail) never checked who owns the consultant.

Standalone script on a throwaway SQLite DB. No network, no paid calls, no Gmail calls. All people
are test fixtures with example.invalid emails.
Run: python test_recruiter_visibility.py
"""
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_vis.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = ""
import app as app_module  # noqa: E402
import models  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def uid(u):
    return u["id"] if isinstance(u, dict) else u


admin = models.authenticate_user("praveen@adroit-ai.com", "Admin@2026") or models.create_user(
    "Test Admin", "admin-vis@example.invalid", "Vis-Test-1", role="Admin")
ADMIN = {"id": uid(admin), "name": "Test Admin", "role": "Admin", "email": "a@example.invalid"}
pr = models.create_user("Pramod Test", "pramod-vis@example.invalid", "Vis-Test-2", role="Recruiter")
PRAMOD = {"id": uid(pr), "name": "Pramod Test", "role": "Recruiter", "email": "p@example.invalid"}
other = models.create_user("Other Recruiter", "other-vis@example.invalid", "Vis-Test-3", role="Recruiter")
OTHER = {"id": uid(other), "name": "Other Recruiter", "role": "Recruiter", "email": "o@example.invalid"}

client = app_module.app.test_client()


def login(u):
    with client.session_transaction() as s:
        s["user"] = u


def add(name, owner):
    login(owner)
    r = client.post("/api/consultants", json={"name": name, "email": f"{name.lower()}@example.invalid", "primary_skills": "Java"})
    check(r.status_code in (200, 201), f"create {name}: {r.status_code} {r.get_data(as_text=True)[:200]}")
    for c in models.get_candidates():
        if c["name"] == name:
            return c["id"]
    return None


admin_ids = [add(n, ADMIN) for n in ("SaitejaTest", "KarunTest", "MimiTest")]
pramod_id = add("PramodOwnTest", PRAMOD)
other_id = add("OtherOwnTest", OTHER)
# a legacy row with no owner (startup migrates these to the admin) must not leak either
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("INSERT INTO candidates (name, email, assigned_user_id) VALUES ('LegacyTest', 'legacy@example.invalid', NULL)")
conn.commit()
conn.close()


def visible(u):
    login(u)
    r = client.get("/api/consultants")
    data = r.get_json()
    rows = data if isinstance(data, list) else (data.get("consultants") or data.get("candidates") or [])
    return sorted(c["name"] for c in rows)


check(visible(PRAMOD) == ["PramodOwnTest"], f"Pramod should see only his own consultant: {visible(PRAMOD)}")
check(visible(OTHER) == ["OtherOwnTest"], f"other recruiter: {visible(OTHER)}")
all_admin = visible(ADMIN)
check(all(n in all_admin for n in ("SaitejaTest", "KarunTest", "MimiTest", "PramodOwnTest", "OtherOwnTest", "LegacyTest")),
      f"admin should see everyone: {all_admin}")

# admin filtering the bench by one recruiter sees only that recruiter's consultants
login(ADMIN)
r = client.get(f"/api/consultants?recruiter_id={PRAMOD['id']}")
data = r.get_json()
rows = data if isinstance(data, list) else (data.get("consultants") or data.get("candidates") or [])
check(sorted(c["name"] for c in rows) == ["PramodOwnTest"], f"admin filtered by Pramod: {[c['name'] for c in rows]}")

# dashboard page: Pramod gets no Admin & Settings menu / admin pane / admin's consultants
login(PRAMOD)
page = client.get("/dashboard").get_data(as_text=True)
check('data-tab="settings"' not in page and "Admin & Settings" not in page, "Admin & Settings menu shown to a recruiter")
check('id="tab-team"' not in page, "admin pane rendered for a recruiter")
check("SaitejaTest" not in page and "MimiTest" not in page, "admin's consultants rendered in the recruiter's dashboard")
check("switchTab('settings')" not in page, "recruiter's profile card should not open the admin page")
login(ADMIN)
page = client.get("/dashboard").get_data(as_text=True)
check('data-tab="settings"' in page and 'id="tab-team"' in page, "admin must still get Admin & Settings")

# acting on someone else's consultant by id is refused (404), own consultant passes the check
login(PRAMOD)
foreign = admin_ids[0]
attempts = {
    "get": client.get(f"/api/consultants/{foreign}"),
    "gmail-status": client.get(f"/api/consultants/{foreign}/gmail-status"),
    "connect-gmail": client.get(f"/api/consultants/{foreign}/connect-gmail"),
    "set-app-password": client.post(f"/api/consultants/{foreign}/set-app-password", json={"gmail_address": "x@example.invalid", "app_password": "abcd"}),
    "upload-resume": client.post(f"/api/consultants/{foreign}/upload-resume", data={}),
    "paste-and-draft": client.post("/api/outreach/paste-and-draft", json={"candidate_id": foreign, "raw_jd_text": "Java developer"}),
    "create-draft": client.post("/api/outreach/create-draft", json={"candidate_id": foreign, "job_id": 1}),
    "personalize-draft": client.post("/api/ai/personalize-draft", json={"candidate_id": foreign, "job_id": 1}),
}
for name, resp in attempts.items():
    check(resp.status_code in (403, 404), f"{name} on another owner's consultant should be refused, got {resp.status_code}")
own = client.get(f"/api/consultants/{pramod_id}/gmail-status")
check(own.status_code == 200, f"own consultant gmail-status should work, got {own.status_code}")

# logged out: the two routes that used to have no login check
with client.session_transaction() as s:
    s.clear()
check(client.get(f"/api/consultants/{pramod_id}/gmail-status").status_code == 401, "gmail-status without login")
check(client.get(f"/api/consultants/{pramod_id}/connect-gmail").status_code in (302, 401), "connect-gmail without login")

# ---- real browser (Microsoft Edge): log in as the recruiter through the login form
import socket  # noqa: E402
import threading  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors = []
shot = os.path.join(tmp_dir, "recruiter_view.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.route("**/api.apify.com/**", lambda route: route.abort())
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", "pramod-vis@example.invalid")
        page.fill("input[name=password]", "Vis-Test-2")
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        nav = page.inner_text("nav")
        check("Admin & Settings" not in nav, f"recruiter sidebar shows Admin & Settings: {nav!r}")
        page.click("a.nav-item[data-tab=candidates]")
        page.wait_for_timeout(1500)
        pane = page.inner_text("#tab-consultants")
        check("PramodOwnTest" in pane, "recruiter's own consultant missing from Consultants tab")
        for n in ("SaitejaTest", "KarunTest", "MimiTest", "OtherOwnTest", "LegacyTest"):
            check(n not in pane, f"{n} visible in the recruiter's Consultants tab")
        page.click(".sidebar-footer .user-profile-card")
        page.wait_for_timeout(300)
        check(not page.is_visible("#tab-team"), "clicking the profile card opened an admin page for a recruiter")
        page.screenshot(path=shot)
        browser.close()
finally:
    server.shutdown()
check(not js_errors, f"JavaScript errors for a recruiter: {js_errors}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"PASS: recruiters see/act only on their own consultants (API + Edge); admin sees all; no admin menu for recruiters. Screenshot: {shot}")
