"""People Data Labs was removed as a Sourcing data source. This checks:
- the PDL route / config / functions are gone,
- the people PDL already found (sourced_candidates.source='pdl') still show in the team pool,
  merged with Apify's and deduplicated on profile URL (they're real, already-paid-for people).
Standalone script against a throwaway SQLite DB. No network, no paid calls.
Run: python test_pdl_removed.py
"""
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_pdl.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = ""
import app as app_module  # noqa: E402
import linkedin_sourcing  # noqa: E402
import models  # noqa: E402
import sourcing_store  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---- code is gone
check(not hasattr(config, "PDL_API_KEY"), "config.PDL_API_KEY still exists")
for fn in ("pdl_search", "pdl_configured", "evaluate_pdl_person", "build_pdl_query"):
    check(not hasattr(linkedin_sourcing, fn), f"linkedin_sourcing.{fn} still exists")

# ---- pool keeps PDL's saved people, merged with Apify's, deduplicated
# Test rows use example.invalid URLs and obviously-test names - not real people.
conn = models.get_db_connection()
cur = conn.cursor()
rows = [
    ("pdl", "https://www.linkedin.com/in/test-only-a.example.invalid", "Test Person A", "2019"),
    ("pdl", "https://www.linkedin.com/in/test-only-shared.example.invalid", "Test Person Shared", "2019"),
    ("apify", "https://www.linkedin.com/in/test-only-shared.example.invalid", "Test Person Shared", "2019"),
    ("apify", "https://www.linkedin.com/in/test-only-b.example.invalid", "Test Person B", "2019"),
    ("apify", "https://www.linkedin.com/in/test-only-c.example.invalid", "Test Person C", "2021"),
]
for src, url, name, year in rows:
    cur.execute("INSERT INTO sourced_candidates (source, search_year, profile_url, name, bachelor_year) VALUES (?, ?, ?, ?, ?)",
                (src, year, url, name, year))
conn.commit()
conn.close()

m2019 = sourcing_store.get_cached_matches("apify", "2019")
names = sorted(m["name"] for m in m2019)
check(names == ["Test Person A", "Test Person B", "Test Person Shared"],
      f"2019 pool should hold PDL + Apify people, deduplicated: {names}")
all_years = sourcing_store.get_cached_matches("apify", None)
check(len(all_years) == 4, f"all-years pool should have 4 distinct people, got {len(all_years)}")
counts = sourcing_store.pool_counts("apify")
check(counts == {"2019": 3, "2021": 1}, f"pool_counts wrong (must count distinct people): {counts}")

# ---- HTTP: route gone, pool endpoint only takes apify, page has no PDL
client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {"id": 1, "name": "Test Admin", "role": "Admin", "email": "admin@example.invalid"}
r = client.post("/api/students/pdl-search", json={"bachelor_year": 2019})
check(r.status_code == 404, f"/api/students/pdl-search should be gone (404), got {r.status_code}")
r = client.get("/api/students/sourced-pool?source=pdl&bachelor_year=2019")
check(r.status_code == 400, f"sourced-pool?source=pdl should be rejected, got {r.status_code}")
r = client.get("/api/students/sourced-pool?source=apify&bachelor_year=2019")
check(r.status_code == 200 and len(r.get_json()["matches"]) == 3, f"sourced-pool apify 2019: {r.status_code} {r.get_json()}")
page = client.get("/dashboard").get_data(as_text=True)
check("People Data Labs" not in page and "pdl" not in page.lower(), "dashboard still mentions People Data Labs / pdl")
check("APIFY_API_TOKEN" in page, "with no Apify key the page should say to add APIFY_API_TOKEN")
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "js", "app.js"), encoding="utf-8") as f:
    js = f.read()
check("pdl" not in js.lower() and "People Data Labs" not in js, "app.js still references PDL")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: PDL removed; its saved people still served from the team pool (merged + deduplicated).")
