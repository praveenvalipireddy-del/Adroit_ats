"""Adding colleges for the education filters: the Srinidhi spellings of SNIST (seed v2), and an
admin adding a college that isn't on the list - admin-only, no duplicates of names already on the
list, it shows in the Filter A / B autocomplete straight away, and saved profiles that list any of
its names count for it. Standalone; throwaway SQLite DB; no network. Labelled test fixtures.
Run: python test_add_college.py
"""
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_add_college.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import education_match  # noqa: E402
import linkedin_ingest  # noqa: E402
import models  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def uid(u):
    return u["id"] if isinstance(u, dict) else u


ADMIN = {"id": uid(models.create_user("College Admin", "col-admin@example.invalid", "Col-1", role="Admin")), "name": "College Admin", "role": "Admin"}
REC = {"id": uid(models.create_user("College Rec", "col-rec@example.invalid", "Col-2", role="Recruiter")), "name": "College Rec", "role": "Recruiter"}
client = app_module.app.test_client()


def login(u):
    with client.session_transaction() as s:
        s["user"] = u


# ---- Srinidhi spellings belong to SNIST (seed v2)
m = education_match.load_matcher(force=True)
for spelling in ("Srinidhi Institute of Science and Technology", "Sreenidhi Institute of Science & Technology", "SNIST"):
    r = m.match(spelling)
    check(r.get("status") == "matched" and r.get("canonical_id") == "in-snist", f"{spelling!r} -> {r}")

# ---- a saved profile from a college that isn't on the list yet (Indian Bachelor's + US Master's)
linkedin_ingest.ingest_profiles([{
    "linkedin_url": "https://www.linkedin.com/in/new-college-coltest", "name": "Meena Coltest", "headline": "QA Engineer",
    "location": "Austin, Texas, United States", "current_company": "", "current_title": "",
    "education": [{"institution_name": "Example Rural College of Engineering Testpur", "degree": "B.Tech", "field_of_study": "CSE", "start_year": 2015, "end_year": 2019},
                  {"institution_name": "The University of Texas at Dallas", "degree": "MS", "field_of_study": "CS", "start_year": 2020, "end_year": 2022}],
    "education_complete": True}], "apify", ADMIN["id"])


def filter_a(inst_id, year=None):
    q = {"filter": "A", "institution_id": inst_id}
    if year:
        q.update(year_from=year, year_to=year)
    return [x["name"] for x in client.get("/api/education/search", query_string=q).get_json().get("results", [])]


login(ADMIN)
names = [i["name"] for i in client.get("/api/education/institutions", query_string={"filter": "A"}).get_json()["institutions"]]
check("Example Rural College of Engineering Testpur" not in names, "not on the list before")

# ---- only admins can add
login(REC)
r = client.post("/api/education/institutions/add", json={"name": "Example Rural College of Engineering Testpur", "country": "India"})
check(r.status_code == 403, f"recruiter cannot add: {r.status_code}")

# ---- validation and duplicates
login(ADMIN)
check(client.post("/api/education/institutions/add", json={"name": "Abc", "country": "India"}).status_code == 400, "name too short")
check(client.post("/api/education/institutions/add", json={"name": "Example Rural College", "country": "Mars"}).status_code == 400, "country must be India/USA")
r = client.post("/api/education/institutions/add", json={"name": "Some New College Testpur", "country": "India", "aliases": "Osmania University"})
check(r.status_code == 400 and "already on the list as Osmania University" in r.get_json()["error"], f"existing name refused: {r.get_json()}")

# ---- add it
r = client.post("/api/education/institutions/add", json={"name": "Example Rural College of Engineering Testpur", "country": "India",
                                                          "city": "Testpur", "aliases": "ERCET, Example Rural Engg College"})
d = r.get_json()
check(r.status_code == 200 and d["canonical_id"] == "custom-example-rural-college-of-engineering-testpur", f"added: {d}")
check(d["entries_linked"] == 1, f"the saved profile's education entry is linked: {d}")
lst = client.get("/api/education/institutions", query_string={"filter": "A"}).get_json()["institutions"]
new = next((i for i in lst if i["id"] == d["canonical_id"]), None)
check(new and new["city"] == "Testpur" and set(new["aliases"]) == {"ERCET", "Example Rural Engg College"} and new["profiles"] == 1,
      f"in the Filter A list with aliases + 1 profile: {new}")
check(filter_a(d["canonical_id"]) == ["Meena Coltest"], "the saved person now shows for the new college")
check(filter_a(d["canonical_id"], 2019) == ["Meena Coltest"] and filter_a(d["canonical_id"], 2020) == [], "exact passout year applies")
check(education_match.load_matcher(force=True).match("ERCET").get("canonical_id") == d["canonical_id"], "short name matches future profiles")
r = client.post("/api/education/institutions/add", json={"name": "Example Rural College of Engineering Testpur", "country": "India"})
check(r.status_code == 400, "adding the same college twice is refused")
lst_b = [i["id"] for i in client.get("/api/education/institutions", query_string={"filter": "B"}).get_json()["institutions"]]
check(d["canonical_id"] not in lst_b, "an Indian college is not in the US (Filter B) list")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: colleges - Srinidhi spellings for SNIST, admin add (admin-only, no duplicates, in the list, saved profiles linked, exact year).")
