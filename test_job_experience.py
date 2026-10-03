"""Jobs experience filter: reading the experience a posting asks for, the filter rule (1 year of
stretch, senior titles, "not stated"), the /api/jobs/search parameters, and LinkedIn's own level
filter (f_E) on live scrapes. Standalone; throwaway SQLite DB; LinkedIn is stubbed (no network).
Job rows are labelled test fixtures.
Run: python test_job_experience.py
"""
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_exp.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import india_job_scrapers  # noqa: E402
import job_experience as je  # noqa: E402
import models  # noqa: E402
import us_job_scrapers  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---- parsing
cases = {
    "Experience: 3-6 Yrs. Skills: Java": (3, 6), "5+ years of experience with Java; 3+ years AWS": (5, None),
    "Minimum of 7 years in data engineering": (7, None), "8 years of hands-on experience in Python": (8, None),
    "3 to 5 years experience": (3, 5), "10 + Yrs": (10, None), "at least 4 yrs": (4, None),
    "Founded in 2010, 200 employees": None, "Experience: Not specified.": None, "": None,
}
for text, want in cases.items():
    got = je.parse_years(text)
    got_t = (got["min"], got["max"]) if got else None
    check(got_t == want, f"parse_years({text!r}) = {got_t}, want {want}")

c = je.classify({"title": "Java Developer", "description": "Posted on Naukri: Java Developer. Experience: 2-5 Yrs. Skills: Java."})
check(c["source"] == "posting" and c["label"] == "2-5 yrs", f"Naukri label: {c}")
c = je.classify({"title": "Data Engineer", "description": "Short summary", "full_description": "Requirements: 6+ years of experience."})
check(c["source"] == "text" and c["label"] == "6+ yrs", f"full description used: {c}")
c = je.classify({"title": "Sr. Java Architect", "description": "Dice US Contract Requisition: Sr. Java Architect"})
check(c["source"] == "title" and c["label"] == "Senior (from title)", f"title fallback: {c}")
c = je.classify({"title": "Data Analyst", "description": "Dice US Contract Requisition: Data Analyst"})
check(c["source"] == "" and c["label"] == "Not stated", f"nothing stated: {c}")

# ---- rule (consultant with 4 years)
F = je.fits
check(F({"min": 3, "max": 6}, 4) and F({"min": 5}, 4) and not F({"min": 6}, 4), "min <= years + 1 (one year of stretch)")
check(not F({"min": None, "level": "senior"}, 4) and F({"min": None, "level": "senior"}, 7), "senior title hidden under 6 years")
check(F({"min": None, "level": None}, 4) and not F({"min": None, "level": None}, 4, include_unstated=False), "not stated follows the checkbox")
check(F({"min": 12}, None), "no experience set = no filter")
check(je.linkedin_levels(4) == "2,3,4" and je.linkedin_levels(None) == "" and je.linkedin_levels(9) == "4,5", "LinkedIn levels")

# ---- /api/jobs/search
for title, desc in [("Java Developer Exp36 Test", "Posted on Naukri: x. Experience: 3-6 Yrs."),
                    ("Java Developer Exp5plus Test", "Need 5+ years of experience in Java."),
                    ("Java Developer Exp8plus Test", "Need 8+ years of experience in Java."),
                    ("Senior Java Architect Expnone Test", "Dice US Contract Requisition: Senior Java Architect"),
                    ("Java Developer Unstated Test", "Dice US Contract Requisition: Java Developer")]:
    models.save_or_update_scraped_job({"title": title, "company": "Test Co", "source": "Dice", "url": f"https://www.dice.com/job-detail/{title[-12:]}",
                                       "description": desc, "job_type": "Contract", "country": "United States"})
us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}   # never scrape here
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}

client = app_module.app.test_client()


def titles(**body):
    r = client.post("/api/jobs/search", json={"query": "java", "country": "United States", "is_24h_only": True, **body})
    jobs = r.get_json()["jobs"]
    return sorted(j["title"] for j in jobs), jobs


all_titles, jobs = titles()
check(len(all_titles) == 5, f"no experience set -> all 5: {all_titles}")
check(all("experience" in j and j["experience"]["label"] for j in jobs), "every job carries its experience")
four, _ = titles(my_experience=4)
check(four == ["Java Developer Exp36 Test", "Java Developer Exp5plus Test", "Java Developer Unstated Test"],
      f"4 years: 3-6 and 5+ shown, 8+ and senior-title hidden, unstated kept: {four}")
four_strict, _ = titles(my_experience=4, include_unstated=False)
check(four_strict == ["Java Developer Exp36 Test", "Java Developer Exp5plus Test"], f"4 years, stated only: {four_strict}")
ten, _ = titles(my_experience=10)
check(len(ten) == 5, f"10 years -> everything: {ten}")

# ---- LinkedIn level filter on a live scrape (requests stubbed)
seen = []


class Resp:
    status_code = 200
    text = "<html></html>"


def fake_get(url, *a, **k):
    seen.append(url)
    return Resp()


import importlib  # noqa: E402
us = importlib.reload(us_job_scrapers)   # undo the stub above for this part
us.requests.get = fake_get
us.scrape_linkedin_us("Java", experience_years=4)
check(seen and "f_E=2%2C3%2C4" in seen[-1], f"LinkedIn asked for Entry/Associate/Mid-Senior: {seen[-1:]}")
us.scrape_linkedin_us("Java")
check("f_E=" not in seen[-1], "no experience -> no LinkedIn level filter")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: jobs experience - parsing, posting/text/title sources, 1-year stretch, senior titles, not-stated toggle, API filter, LinkedIn f_E.")
