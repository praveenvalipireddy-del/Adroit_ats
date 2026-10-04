"""HarvestAPI direct as the sourcing provider (SOURCING_PROVIDER=harvestapi): a Passout search goes
lead-search -> full profile per result, the full profiles come back through the normal search-poll
in the Apify item shape (so matching, saving and the education filters work unchanged), the trial
page cap, the estimated cost in the daily budget, errors shown plainly, the key never leaked, and
switching back to Apify by removing the setting.
HarvestAPI and Apify are never contacted: harvest_direct.requests.get is a stub. Throwaway SQLite DB.
People are labelled test fixtures.
Run: python test_harvest_direct.py
"""
import os
import sys
import tempfile
import time

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_harvest.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
os.environ["SOURCING_PROVIDER"] = "harvestapi"
os.environ["HARVESTAPI_API_KEY"] = "test-harvest-key-not-real"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = ""
import app as app_module  # noqa: E402
import harvest_direct as hd  # noqa: E402
import linkedin_sourcing as ls  # noqa: E402
import models  # noqa: E402

failures, calls = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def edu(school, degree, end):
    return {"schoolName": school, "degree": degree, "fieldOfStudy": "Computer Science", "endDate": {"year": end}}


PROFILES = {
    "https://www.linkedin.com/in/match-harvtest": {
        "linkedinUrl": "https://www.linkedin.com/in/match-harvtest", "firstName": "Ravi", "lastName": "Harvtest",
        "headline": "Data Engineer", "location": {"linkedinText": "Dallas, Texas, United States", "parsed": {"countryCode": "US"}},
        "education": [edu("Osmania University", "Bachelor of Technology", 2019), edu("University of Texas at Dallas", "Master of Science", 2022)],
        "currentPosition": [{"position": "Data Engineer", "companyName": "Test Co"}]},
    "https://www.linkedin.com/in/india-harvtest": {
        "linkedinUrl": "https://www.linkedin.com/in/india-harvtest", "firstName": "Sita", "lastName": "Harvtest",
        "location": {"linkedinText": "Hyderabad, India", "parsed": {"countryCode": "IN"}},
        "education": [edu("Osmania University", "Bachelor of Technology", 2019)], "currentPosition": []},
}
MODE = {"search": "ok"}


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def fake_get(url, params=None, headers=None, timeout=None):
    calls.append((url, dict(params or {}), dict(headers or {})))
    if headers.get("X-API-Key") != "test-harvest-key-not-real":
        return Resp(401, {"error": "bad key"})
    if url.endswith("/linkedin/lead-search"):
        if MODE["search"] == "error":
            return Resp(402, {"error": "Insufficient credits for test-harvest-key-not-real"})
        urls = list(PROFILES) + ["https://www.linkedin.com/in/broken-harvtest"]
        return Resp(200, {"elements": [{"linkedinUrl": u, "firstName": "x"} for u in urls],
                          "pagination": {"totalElements": 1234, "pageNumber": 1}})
    if url.endswith("/linkedin/profile"):
        p = PROFILES.get(params["query"])
        return Resp(200, {"element": p}) if p else Resp(500, {"error": "profile unavailable"})
    return Resp(404, {})


hd.requests.get = fake_get
client = app_module.app.test_client()
uid = models.create_user("Harv Admin", "harv@example.invalid", "Harv-1", role="Admin")
with client.session_transaction() as s:
    s["user"] = {"id": uid["id"] if isinstance(uid, dict) else uid, "name": "Harv Admin", "role": "Admin"}


def run_search(year=2019, pages=3):
    r = client.post("/api/students/search-start", json={"bachelor_year": year, "pages": pages})
    d = r.get_json()
    if r.status_code != 200:
        return r.status_code, d, None
    run = d["runs"][0]
    for _ in range(100):
        p = client.post("/api/students/search-poll", json={"run_id": run["run_id"], "dataset_id": run["dataset_id"],
                                                          "bachelor_year": year, "offset": 0, "matched_so_far": 0}).get_json()
        if p.get("done"):
            return 200, d, p
        time.sleep(0.05)
    return 200, d, {"error": "never finished"}


# ---- a search on HarvestAPI
status, start, poll = run_search()
check(status == 200 and start.get("provider") == "harvestapi", f"started on HarvestAPI: {start}")
check(start["pages_started"] == 1, f"trial cap: 3 pages asked, 1 started ({start['pages_started']})")
check(start.get("pages_per_search") == 1, "the browser is told to stop after 1 page per Search click")
check(start["runs"][0]["run_id"].startswith("hv-"), "HarvestAPI run id")
search_calls = [c for c in calls if c[0].endswith("/lead-search")]
check(len(search_calls) == 1 and "Osmania University" in search_calls[0][1]["schools"] and search_calls[0][1]["locations"] == "United States",
      f"lead-search params: {search_calls[0][1] if search_calls else None}")
check("yearsOfExperienceIds" in search_calls[0][1], "experience filter passed through")
check(len([c for c in calls if c[0].endswith("/linkedin/profile")]) == 3, "one full-profile call per search result")
check(poll["scanned_new"] == 2 and len(poll["new_matches"]) == 1 and poll["new_matches"][0]["name"] == "Ravi Harvtest",
      f"matching works on HarvestAPI profiles: scanned {poll.get('scanned_new')}, matches {[m['name'] for m in poll.get('new_matches', [])]}")
check(poll["skipped"].get("not_in_us") == 1, f"non-US profile skipped: {poll.get('skipped')}")
check(poll["harvest"] == {"search_total": 1234, "search_found": 3, "profiles_ok": 2, "profile_calls": 3, "estimated_cost": True},
      f"trial numbers: {poll.get('harvest')}")
check(abs(poll["cost_usd"] - (0.10 + 3 * 0.004)) < 1e-6, f"estimated cost: {poll.get('cost_usd')}")
check(abs(ls.todays_spend_usd() - 0.112) < 1e-6, f"counted in today's budget: {ls.todays_spend_usd()}")
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM linkedin_profiles")
check(cur.fetchone()[0] == 2, "both scanned profiles stored for the education filters")
conn.close()

# ---- provider error is shown plainly, key redacted
MODE["search"] = "error"
status, start, poll = run_search()
check(poll["done"] and "Insufficient credits" in poll["provider_error"], f"provider error shown: {poll.get('provider_error')}")
check("test-harvest-key-not-real" not in str(poll), "the API key never appears in a response")
MODE["search"] = "ok"

# ---- daily budget applies
os.environ.pop("X", None)
ls.DAILY_BUDGET_USD = 0.15
status, start, _ = run_search()
check(status == 429 and "budget" in start["error"], f"daily budget stops the next search: {status} {start}")
ls.DAILY_BUDGET_USD = 5.0

# ---- missing key
os.environ["HARVESTAPI_API_KEY"] = ""
status, start, _ = run_search()
check(status == 503 and "HARVESTAPI_API_KEY" in start["error"], f"missing key: {start}")
os.environ["HARVESTAPI_API_KEY"] = "test-harvest-key-not-real"

# ---- switching back to Apify = removing the setting (no HarvestAPI call made)
os.environ["SOURCING_PROVIDER"] = ""
before = len(calls)
status, start, _ = run_search()
check(status == 503 and "APIFY_API_TOKEN" in start["error"] and len(calls) == before, f"back on Apify: {status} {start}")

# ---- run ids are validated
r = client.post("/api/students/search-poll", json={"run_id": "hv-1; drop", "dataset_id": "hv-1", "bachelor_year": 2019})
check(r.status_code == 400, "bad run id rejected")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: HarvestAPI direct - search + full profiles in the Apify shape, matching/storage unchanged, trial cap, estimated cost in budget, errors, key redacted, switch back to Apify.")
