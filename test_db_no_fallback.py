"""Production database: never fall back to SQLite.

Root cause this guards against: when Postgres (DATABASE_URL) couldn't be reached for a moment,
get_db_connection() silently switched to a local SQLite file on Render's disk. A vendor list
uploaded at that moment was "saved" there, shown once, and wiped by the next deploy. Now it retries,
then raises DatabaseUnavailable, and the API answers 503 "nothing was saved".
psycopg2 is replaced by a stub; no real database is contacted.
Run: python test_db_no_fallback.py
"""
import os
import sys
import tempfile
import types

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_nofallback.db")
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


uid = models.create_user("NoFallback Admin", "nofb@example.invalid", "NoFb-1", role="Admin")
uid = uid["id"] if isinstance(uid, dict) else uid
attempts = []
fake = types.ModuleType("psycopg2")


def failing_connect(*a, **k):
    attempts.append(k.get("connect_timeout"))
    raise OSError("could not connect to server (test)")


fake.connect = failing_connect
sys.modules["psycopg2"] = fake
import time  # noqa: E402

real_sleep = time.sleep
time.sleep = lambda s: None   # no real waiting in the test
config.DATABASE_URL = "postgresql://user:pw@db.example.invalid/ats"
sqlite_before = os.path.getmtime(config.DB_PATH)

try:
    models.get_db_connection()
    check(False, "must raise, not return a connection")
except models.DatabaseUnavailable as ex:
    check("nothing was saved" in str(ex), f"message: {ex}")
check(len(attempts) == 3 and attempts[0] == 10, f"3 attempts with a 10 s connect timeout: {attempts}")

client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {"id": uid, "name": "NoFallback Admin", "role": "Admin"}
r = client.post("/api/vendors", json={"company": "Vendor Test Co", "email": "a@vendor-test.example"})
check(r.status_code == 503 and "nothing was saved" in r.get_json()["error"], f"API says so: {r.status_code} {r.get_json()}")
check(os.path.getmtime(config.DB_PATH) == sqlite_before, "nothing was written to the SQLite file")

time.sleep = real_sleep
config.DATABASE_URL = ""
check(models.get_db_connection() is not None, "without DATABASE_URL (local), SQLite still works")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: production never falls back to SQLite - retries, then 503 'nothing was saved'; local SQLite unchanged.")
