"""Filter A / Filter B paid LinkedIn search for ONE chosen college (backend), plus the Postgres
cursor fix. Apify is never contacted: linkedin_sourcing.start_search / fetch_run / abort_run are
replaced by stubs that record what would have been sent. People are labelled test fixtures.
Run: python test_education_live_search.py
"""
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_live.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = "dummy-test-token-not-real"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = "dummy-test-token-not-real"
import app as app_module  # noqa: E402
import linkedin_sourcing as ls  # noqa: E402
import models  # noqa: E402
import sourcing_store  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def edu(school, degree, end):
    return {"schoolName": school, "degree": degree, "endDate": {"year": end}}


def person(slug, first, education, country="US"):
    return {"linkedinUrl": f"https://www.linkedin.com/in/{slug}.example.invalid", "firstName": first, "lastName": "Livetest",
            "headline": "Engineer", "location": {"linkedinText": "Dallas, Texas", "countryCode": country},
            "currentPosition": [{"position": "Engineer", "companyName": "Test Co"}], "education": education}


# ---- stubs (no network)
started, aborted = [], []
DATASETS = {}


def fake_start(bachelor_year, pages=1, location="United States", start_page=1, schools=None, experience_ids=None):
    run_id = f"RunTest{len(started):04d}AAAA"
    started.append({"start_page": start_page, "schools": schools, "experience_ids": experience_ids, "run_id": run_id})
    return {"runs": [{"run_id": run_id, "dataset_id": f"Data{run_id}", "start_page": start_page}],
            "pages_started": 1, "next_start_page": start_page + 1, "max_spend_usd": 0.31, "spent_today_usd": 0}


def fake_fetch(run_id, dataset_id, offset=0):
    items = DATASETS.get(dataset_id, [])[offset:]
    return {"run": {"usageTotalUsd": 0.2, "statusMessage": ""}, "status": "SUCCEEDED", "raw_items": items}


ls.start_search = fake_start
ls.fetch_run = fake_fetch
ls.abort_run = lambda run_id: aborted.append(run_id) or True

admin = models.create_user("Live Admin", "live-admin@example.invalid", "Live-Test-1", role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
client = app_module.app.test_client()
check(client.post("/api/education/live-start", json={"filter": "A", "institution_id": "group-jntu"}).status_code == 401,
      "live search requires login")
with client.session_transaction() as s:
    s["user"] = {"id": admin_id, "name": "Live Admin", "role": "Admin", "email": "a@example.invalid"}


def start(**body):
    r = client.post("/api/education/live-start", json=body)
    return r, (r.get_json() or {})


def poll(run, **body):
    r = client.post("/api/education/live-poll", json={**body, "run_id": run["run_id"], "dataset_id": run["dataset_id"],
                                                      "offset": 0, "matched_so_far": 0})
    return r, (r.get_json() or {})


def names(filter_, inst, **kw):
    r = client.get("/api/education/search", query_string={"filter": filter_, "institution_id": inst, **kw})
    return sorted(x["name"] for x in (r.get_json() or {}).get("results", []))


# ---- validation
check(start(filter="P", institution_id="group-jntu")[0].status_code == 400, "Passout has its own search")
check(start(filter="A", institution_id="us-ut-dallas")[0].status_code == 400, "Filter A needs an Indian college")
check(start(filter="B", institution_id="in-osmania")[0].status_code == 400, "Filter B needs a US university")
check(start(filter="A")[0].status_code == 400, "college required")
check(not started, "nothing may reach Apify for invalid requests")

# ---- Filter A start: the chosen college's LinkedIn school names (all campuses for a group), no year -> no experience facet
r, d = start(filter="A", institution_id="group-jntu")
check(r.status_code == 200 and d.get("runs"), f"A start: {r.status_code} {d}")
s0 = started[-1]
check(s0["schools"] == ["Jawaharlal Nehru Technological University Hyderabad", "Jawaharlal Nehru Technological University Kakinada",
                        "Jawaharlal Nehru Technological University Anantapur", "Jawaharlal Nehru Technological University"],
      f"JNTU group should search every campus by its real name: {s0['schools']}")
check(s0["experience_ids"] == [] and s0["start_page"] == 1, f"no year range -> no experience filter, page 1: {s0}")
# second click continues at the next page (team cursor), and it is separate from the Passout cursor
start(filter="A", institution_id="group-jntu")
check(started[-1]["start_page"] == 2, f"repeat search must continue on new pages: {started[-1]['start_page']}")
check(sourcing_store.get_cursor("apify", 2019)["next_page"] == 1, "Filter A cursor must not move the Passout cursor")
start(filter="A", institution_id="in-osmania", year_from=2018, year_to=2019)
check(started[-1]["schools"] == ["Osmania University"] and started[-1]["experience_ids"]
      and started[-1]["start_page"] == 1, f"single college + years: {started[-1]}")
start(filter="B", institution_id="us-ut-dallas", year_from=2020)
check(started[-1]["schools"] == ["The University of Texas at Dallas"] and started[-1]["experience_ids"] == [],
      f"Filter B: chosen university, no experience facet: {started[-1]}")

# ---- Filter A poll: strict verification
run = {"run_id": "RunPollAAAA0001", "dataset_id": "DataPollAAAA0001"}
DATASETS[run["dataset_id"]] = [
    # JNTUH Bachelor's + US Master's at a school NOT on the college list -> verified
    person("live-a1", "Anil", [edu("JNTUH College of Engineering Hyderabad", "B.Tech", 2019), edu("Example State University Testville", "MS", 2021)]),
    # JNTU Bachelor's but the Master's is Indian -> no
    person("live-a2", "Bala", [edu("JNTU Kakinada", "B.Tech", 2018), edu("Osmania University", "M.Tech", 2020)]),
    # Bachelor's somewhere else -> no
    person("live-a3", "Chan", [edu("Osmania University", "BE", 2017), edu("Example Tech University Northville", "MS", 2019)]),
    # not in the US -> no
    person("live-a4", "Dev", [edu("JNTUH", "B.Tech", 2016), edu("Example Tech University Northville", "MS", 2018)], country="IN"),
]
r, d = poll(run, filter="A", institution_id="group-jntu")
check(r.status_code == 200 and d.get("new_matches") == 1 and "raw_items" not in d, f"A poll: {r.status_code} {d}")
check(d.get("skipped", {}).get("no_us_master") == 1 and d.get("skipped", {}).get("no_bachelor_at_college") == 1
      and d.get("skipped", {}).get("not_in_us") == 1, f"skip reasons: {d.get('skipped')}")
check(names("A", "group-jntu") == ["Anil Livetest"], f"verified person appears in Filter A (US school not on list): {names('A', 'group-jntu')}")
check(names("A", "in-jntu-hyderabad") == ["Anil Livetest"], "...and under the single campus")
check(names("A", "group-jntu", year_from=2020) == [], "year range applies to the verified Bachelor's year")
row = client.get("/api/education/search", query_string={"filter": "A", "institution_id": "group-jntu"}).get_json()["results"][0]
check("Hyderabad (2019)" in row["indian_college"] and row["us_masters"].startswith("Example State University Testville - MS"),
      f"verified row columns: {row}")
lst = client.get("/api/education/institutions", query_string={"filter": "A"}).get_json()["institutions"]
grp = next(x for x in lst if x["id"] == "group-jntu")
check(grp["profiles"] == 1, f"dropdown count should include verified people: {grp['profiles']}")
check("Anil Livetest" in [x["name"] for x in client.get("/api/education/search", query_string={"filter": "P", "passout_year": 2019}).get_json()["results"]],
      "a Filter A match that also fits a passout year is banked for Passout too")
check("Chan Livetest" not in names("A", "group-jntu"), "non-JNTU person must not be shown")
# polling the same run again does not duplicate
poll(run, filter="A", institution_id="group-jntu")
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM profile_verifications")
check(cur.fetchone()[0] == 1, "verification stored once")
conn.close()

# ---- Filter B poll: UTD Master's + Indian Bachelor's (college not on the list, recognised by the India rule)
runb = {"run_id": "RunPollBBBB0001", "dataset_id": "DataPollBBBB0001"}
DATASETS[runb["dataset_id"]] = [
    person("live-b1", "Esha", [edu("Malla Reddy Engineering College", "B.Tech", 2017), edu("The University of Texas at Dallas", "Master of Science - MS", 2020)]),
    person("live-b2", "Frank", [edu("Texas Tech University", "BS", 2016), edu("UT Dallas", "MS", 2019)]),   # US Bachelor's -> no
    person("live-b3", "Gita", [edu("Osmania University", "BE", 2015), edu("UNT", "MS", 2018)]),            # other university -> no
]
r, d = poll(runb, filter="B", institution_id="us-ut-dallas")
check(r.status_code == 200 and d.get("new_matches") == 1, f"B poll: {d}")
check(names("B", "us-ut-dallas") == ["Esha Livetest"], f"Filter B verified: {names('B', 'us-ut-dallas')}")
check(names("B", "group-ut") == ["Esha Livetest"], "...and under 'University of Texas - any campus'")
check(names("B", "us-ut-dallas", year_from=2021) == [], "Filter B year range applies to the Master's year")
row = client.get("/api/education/search", query_string={"filter": "B", "institution_id": "us-ut-dallas"}).get_json()["results"][0]
check(row["indian_college"] == "Malla Reddy Engineering College (2017)", f"B columns: {row}")

# ---- early stop: once 15 verified matches exist the running run is aborted
runc = {"run_id": "RunPollCCCC0001", "dataset_id": "DataPollCCCC0001"}
DATASETS[runc["dataset_id"]] = [person(f"live-c{i}", f"Many{i}", [edu("JNTUH", "B.Tech", 2018), edu("UT Dallas", "MS", 2020)]) for i in range(16)]
ls.fetch_run = lambda rid, did, offset=0: {**fake_fetch(rid, did, offset), "status": "RUNNING"}
r, d = poll(runc, filter="A", institution_id="in-jntu-hyderabad")
check(d.get("new_matches") == 16 and runc["run_id"] in aborted and d.get("stopped_early"), f"early stop: {d} aborted={aborted}")
ls.fetch_run = fake_fetch

# ---- Postgres: the team cursor upsert must not get "RETURNING id" appended (sourcing_cursor has no id column)
import models as m  # noqa: E402

seen_sql = []


class FakePgCursor:
    description = [("next_page",)]

    def execute(self, sql, params=None):
        seen_sql.append(sql)
        if "RETURNING id" in sql:
            raise RuntimeError('column "id" does not exist')

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return []

    def close(self):
        pass


class FakePgConn:
    def cursor(self):
        return FakePgCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


real_get_conn = sourcing_store.get_db_connection
sourcing_store.get_db_connection = lambda: m.PgConnectionWrapper(FakePgConn())
try:
    sourcing_store.save_cursor("apify", "edu-A-group-jntu", next_page=7)
finally:
    sourcing_store.get_db_connection = real_get_conn
check(seen_sql and "RETURNING id" not in seen_sql[-1] and "ON CONFLICT" in seen_sql[-1],
      f"Postgres cursor SQL must not end in RETURNING id: {seen_sql[-1:]}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: Filter A/B LinkedIn search - chosen-college school names, team cursor, strict verification, early stop, Passout banking; Postgres cursor fix.")
