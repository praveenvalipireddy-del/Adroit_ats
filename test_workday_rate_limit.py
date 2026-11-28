"""Workday career sites answer 429 Too Many Requests when asked quickly (career_boards.py):
1. a 429 waits (Retry-After when given, else 5 s / 15 s) and tries again; other errors fail at once; three 429s give up;
2. Workday requests are spaced out (WORKDAY_GAP_S) and its job details are read one at a time;
3. the daily sync never reads two Workday companies at the same time, while other portals still run alongside.
No network: http_json, sleep and fetch_board are stubs. Run: python test_workday_rate_limit.py
"""
import os
import sys
import tempfile
import threading
import time

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_workday.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
import requests  # noqa: E402

import career_boards as cb  # noqa: E402
import models  # noqa: E402

failures = []


def check(ok, msg):
    if not ok:
        failures.append(msg)


def http_error(code, retry_after=None):
    r = requests.Response()
    r.status_code = code
    if retry_after is not None:
        r.headers["Retry-After"] = str(retry_after)
    return requests.HTTPError(f"{code} Client Error", response=r)


slept = []
cb.sleep = lambda s: slept.append(round(s, 2))

# ---- 1. retries on 429
answers = [http_error(429), http_error(429, retry_after=2), {"ok": 1}]


def stub(url, body=None):
    a = answers.pop(0)
    if isinstance(a, Exception):
        raise a
    return a


cb.http_json = stub
check(cb.workday_json("https://x.wd5.myworkdayjobs.com/a") == {"ok": 1}, "a 429 is retried until it works")
check(5 in slept and 2.0 in slept, f"waits 5 s, then the Retry-After seconds: {slept}")
answers[:] = [http_error(429)] * 4
try:
    cb.workday_json("https://x.wd5.myworkdayjobs.com/a")
    check(False, "three 429s should give up")
except requests.HTTPError:
    check(len(answers) == 1, f"gave up after 3 tries (of the 4 queued 429s, 1 is left): {len(answers)} left")
answers[:] = [http_error(404), {"ok": 1}]
try:
    cb.workday_json("https://x.wd5.myworkdayjobs.com/a")
    check(False, "a 404 is not retried")
except requests.HTTPError:
    check(len(answers) == 1, "a 404 fails at once")
answers[:] = [http_error(429, retry_after=999), {"ok": 1}]
slept.clear()
cb.workday_json("https://x.wd5.myworkdayjobs.com/a")
check(cb.WORKDAY_MAX_RETRY_AFTER in slept, f"a huge Retry-After is capped: {slept}")

# ---- 2. spacing + one-at-a-time details through fetch_board
calls, in_flight, max_flight = [], [0], [0]
lock = threading.Lock()
today = time.strftime("%Y-%m-%d")


def wd_stub(url, body=None):
    with lock:
        in_flight[0] += 1
        max_flight[0] = max(max_flight[0], in_flight[0])
    try:
        calls.append(url)
        if url.endswith("/jobs"):
            return {"jobPostings": [{"title": f"Cloud Engineer {i}", "externalPath": f"/job/Austin/C_{i}", "postedOn": "Posted Today",
                                     "locationsText": "Austin, TX"} for i in range(4)]}
        return {"jobPostingInfo": {"title": "Cloud Engineer", "location": "Austin, TX", "country": {"alpha2Code": "US"},
                                   "externalUrl": "https://fixture.wd5.myworkdayjobs.com" + url.split("Ext")[-1],
                                   "startDate": today, "timeType": "Full time", "jobDescription": "<p>AWS. Labelled fixture.</p>"}}
    finally:
        with lock:
            in_flight[0] -= 1


cb.http_json = wd_stub
slept.clear()
cb.WORKDAY_GAP_S = 0.05
real_sleep = time.sleep
cb.sleep = lambda s: (slept.append(s), real_sleep(s))
jobs = cb.fetch_board("workday", "fixture|wd5|Ext", "Fixture WD")
check(len(jobs) == 4, f"four labelled fixture jobs read: {len(jobs)}")
check(max_flight[0] == 1, f"Workday requests go one at a time: {max_flight[0]}")
check(len(slept) >= 3, f"requests are spaced out: {len(slept)} pauses")

# ---- 3. the sync: never two Workday companies at once, other portals alongside
models.init_db() if hasattr(models, "init_db") else None
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("DELETE FROM career_boards")
for i, ats in enumerate(["workday", "workday", "greenhouse", "workday", "lever", "greenhouse"]):
    cur.execute("INSERT INTO career_boards (ats, slug, company, active) VALUES (?, ?, ?, 1)", (ats, f"fx{i}", f"Fixture {i}"))
conn.commit()
running, peak_wd, overlap = {"workday": 0, "other": 0}, [0], [False]


def fake_fetch(ats, slug, company, now=None):
    k = "workday" if ats == "workday" else "other"
    with lock:
        running[k] += 1
        peak_wd[0] = max(peak_wd[0], running["workday"])
        if running["workday"] and running["other"]:
            overlap[0] = True
    real_sleep(0.05)
    with lock:
        running[k] -= 1
    return []


cb.fetch_board = fake_fetch
cb.discover = lambda c: None
res = cb.sync_all(conn, time_budget_s=60)
check(res["boards"] == 6 and res["errors"] == 0, f"all six fixture boards read: {res}")
check(peak_wd[0] == 1, f"never two Workday companies at once: {peak_wd[0]}")
check(overlap[0], "a non-Workday company runs alongside a Workday one")
conn.close()

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: Workday 429 retry (Retry-After, capped), spaced one-at-a-time requests, one Workday company at a time in the sync.")
