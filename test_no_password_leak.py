"""Consultant Gmail App Passwords never reach the browser.

Root cause this guards against: /api/consultants and /api/consultants/<id> returned `SELECT c.*`
rows as JSON, including gmail_app_password (and gmail_token_path) - visible to anyone who could see
the consultant in the dashboard. The page never used them.
Throwaway SQLite DB; no network. Labelled test fixtures.
Run: python test_no_password_leak.py
"""
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_leak.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import models  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


SECRET = "leaktestsecretpw"
EMAIL, PASSWORD = "leak@example.invalid", "Leak-Test-1"
u = models.create_user("Leak Admin", EMAIL, PASSWORD, role="Admin")
uid = u["id"] if isinstance(u, dict) else u
cid = models.create_candidate("Leak Consultanttest", "leak-c@example.invalid", gmail_account="leak-c@example.invalid", assigned_user_id=uid)
models.update_candidate(cid, gmail_app_password=SECRET, gmail_token_path="/secret/token_path_test.json")

client = app_module.app.test_client()
client.post("/login", data={"email": EMAIL, "password": PASSWORD})
lst = client.get("/api/consultants")
det = client.get(f"/api/consultants/{cid}")
dash = client.get("/dashboard")
for name, r in (("list", lst), ("detail", det), ("dashboard page", dash)):
    body = r.get_data(as_text=True)
    check(r.status_code == 200, f"{name}: HTTP {r.status_code}")
    check(SECRET not in body and "token_path_test" not in body, f"{name} must not contain the App Password / token path")
check(det.get_json().get("gmail_connected") is True and det.get_json().get("gmail_account") == "leak-c@example.invalid",
      "still shows Gmail connected + the address")
check(models.get_candidate_by_id(cid)["gmail_app_password"] == SECRET, "the password is still stored for drafting")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: Gmail App Password / token path never in consultant API responses or the dashboard page; drafting still has it.")
