"""Tests for education_match (institution alias matching + degree-level mapping) and the
institution_aliases seed. Standalone script against a throwaway SQLite DB - never the real DB,
no network, no paid calls.
Run: python test_education_match.py
"""
import os
import sys
import tempfile
from collections import defaultdict

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_edu.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import models  # noqa: E402
import education_match as em  # noqa: E402
import institutions_seed  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---------------------------------------------------------------- normalize
check(em.normalize("The University of Texas at Dallas") == "university of texas at dallas", "normalize: drop 'the'")
check(em.normalize("  Texas A&M University-Commerce ") == "texas a and m university commerce", "normalize: & and dash")
check(em.normalize("Vignan's  Institute") == "vignans institute", "normalize: apostrophe, spaces")
check(em.normalize(None) == "", "normalize: None")

# ---------------------------------------------------------------- degree level
cases = {
    # Bachelors (spec list + LinkedIn's usual long forms)
    "B.Tech": "Bachelors", "BTech": "Bachelors", "Bachelor of Technology - BTech": "Bachelors",
    "BE": "Bachelors", "B.E.": "Bachelors", "Bachelor of Engineering - BE": "Bachelors",
    "BSc": "Bachelors", "B.Sc": "Bachelors", "BCA": "Bachelors", "BCom": "Bachelors", "B.Com": "Bachelors",
    "Bachelor's degree": "Bachelors", "Bachelor of Science - BS": "Bachelors",
    # Masters
    "MS": "Masters", "M.S.": "Masters", "Master of Science - MS": "Masters", "MSc": "Masters",
    "MEng": "Masters", "MBA": "Masters", "Master of Business Administration - MBA": "Masters",
    "MCA": "Masters", "Master's degree": "Masters", "M.Tech": "Masters", "Master of Computer Applications": "Masters",
    # PhD / Other
    "PhD": "PhD", "Ph.D.": "PhD", "Doctor of Philosophy - PhD": "PhD",
    "Diploma": "Other", "High School": "Other", "": "Other", None: "Other",
    "Integrated B.Tech + M.Tech (Dual Degree)": "Other",
}
for deg, want in cases.items():
    got = em.degree_level(deg)
    check(got == want, f"degree_level({deg!r}) = {got}, want {want}")

# ---------------------------------------------------------------- seed data sanity
norm_to_ids = defaultdict(set)
for cid, name, country, city, aliases in institutions_seed.INSTITUTIONS:
    check(country in ("India", "USA"), f"seed {cid}: country {country!r}")
    for a in [name] + list(aliases):
        norm_to_ids[em.normalize(a)].add(cid)
ambiguous = {k: v for k, v in norm_to_ids.items() if len(v) > 1}
check(not ambiguous, f"seed has aliases pointing at 2+ institutions: {ambiguous}")
for banned in ("iit", "nit", "usc", "ou", "ub", "asu", "vit"):
    check(banned not in norm_to_ids, f"ambiguous bare alias {banned!r} must not be seeded")

# ---------------------------------------------------------------- DB seed
models.init_db()
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM institution_aliases")
n_seeded = cur.fetchone()[0]
check(n_seeded == len(norm_to_ids), f"seeded {n_seeded} alias rows, expected {len(norm_to_ids)}")

# Admin deletes an alias and adds their own -> a restart (same seed version) must keep both edits.
cur.execute("DELETE FROM institution_aliases WHERE alias_norm = ?", ("utd",))
cur.execute("""INSERT INTO institution_aliases (canonical_id, canonical_name, alias, alias_norm, country, city)
               VALUES ('us-ut-dallas', 'The University of Texas at Dallas', 'UT-D Richardson', 'ut d richardson', 'USA', 'Richardson')""")
conn.commit()
conn.close()
models.init_db()
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM institution_aliases WHERE alias_norm = 'utd'")
check(cur.fetchone()[0] == 0, "restart re-added an alias the admin deleted")
cur.execute("SELECT COUNT(*) FROM institution_aliases WHERE alias_norm = 'ut d richardson'")
check(cur.fetchone()[0] == 1, "restart lost an admin-added alias")
# put UTD back for the matching tests below
cur.execute("""INSERT INTO institution_aliases (canonical_id, canonical_name, alias, alias_norm, country, city)
               VALUES ('us-ut-dallas', 'The University of Texas at Dallas', 'UTD', 'utd', 'USA', 'Richardson')""")
conn.commit()

m = em.load_matcher(conn, force=True)


def expect(name, canonical_id, method=None):
    r = m.match(name)
    ok = r.get("status") == "matched" and r.get("canonical_id") == canonical_id and (method is None or r.get("method") == method)
    check(ok, f"match({name!r}) -> {r}, want {canonical_id} ({method or 'any'})")
    return r


def expect_not_matched(name, status=None):
    r = m.match(name)
    ok = r.get("status") != "matched" and r.get("country") is None and (status is None or r.get("status") == status)
    check(ok, f"match({name!r}) should NOT match (want {status or 'review/unmapped'}), got {r}")
    return r


# exact aliases from the spec
expect("UTD", "us-ut-dallas", "exact")
expect("The University of Texas at Dallas", "us-ut-dallas", "exact")
expect("JNTUH", "in-jntu-hyderabad", "exact")
expect("IIT-M", "in-iit-madras", "exact")
expect("IIT Madras", "in-iit-madras", "exact")
expect("Texas A&M University - Commerce", "us-tamu-commerce", "exact")
expect("University of North Texas", "us-unt", "exact")
check(m.match("UTD")["country"] == "USA" and m.match("JNTUH")["country"] == "India", "country inferred from alias")

# location suffix after a comma
expect("Osmania University, Hyderabad, Telangana", "in-osmania", "exact_location_suffix")
expect("Anna University, Chennai", "in-anna-university")
# ...but NOT when the suffix is another campus / not a location
expect_not_matched("University of Maryland, Baltimore County Campus")
expect("Northeastern University Boston MA", "us-northeastern", "exact_location_suffix")
# a DIFFERENT city after the name is a different institution (UNT Dallas is not UNT in Denton)
expect_not_matched("University of North Texas, Dallas")
expect_not_matched("University of North Texas at Dallas")

# fuzzy: typos are accepted
expect("Jawaharlal Nehru Technolgical University Hyderbad", "in-jntu-hyderabad", "fuzzy")
expect("Stevens Institute of Tecnology", "us-stevens", "fuzzy")
expect("Univ of Texas at Dallas", "us-ut-dallas")            # "Univ" expanded
expect("Osmania Univ", "in-osmania", "exact")

# fuzzy: a different campus of the same family must never be matched
for other_campus in ("National Institute of Technology Silchar", "Indian Institute of Technology Mandi",
                     "Jawaharlal Nehru Technological University Gurajada Vizianagaram",
                     "The University of Texas at Austin", "University of North Carolina at Chapel Hill"):
    expect_not_matched(other_campus)

# US vs India look-alikes
expect("Illinois Institute of Technology", "us-illinois-tech")
expect_not_matched("Indiana University Bloomington")        # not in the list -> unmapped, and never "India"

# JNTU with no campus: country known, campus not guessed
r = expect("JNTU", "in-jntu-unspecified")
check(r["country"] == "India", "bare JNTU is India")

# unmatched names go to the admin list (review vs unmapped), counted on repeat
res1 = em.match_and_record(conn, "Some Totally Unknown College of Arts")
res2 = em.match_and_record(conn, "Some Totally Unknown College of Arts")
conn.commit()
check(res1["status"] == "unmapped", f"unknown college status {res1}")
cur.execute("SELECT status, seen_count FROM unmapped_institutions WHERE name_norm = ?",
            (em.normalize("Some Totally Unknown College of Arts"),))
row = cur.fetchone()
check(row is not None and row[0] == "unmapped" and row[1] == 2, f"unmapped list entry wrong: {row and tuple(row)}")

# a matched name is NOT added to the admin list
em.match_and_record(conn, "UT Dallas")
conn.commit()
cur.execute("SELECT COUNT(*) FROM unmapped_institutions WHERE name_norm = 'ut dallas'")
check(cur.fetchone()[0] == 0, "matched institution was logged as unmapped")

# review band: close but under the accept threshold -> suggestion recorded, not matched
review = None
for probe in ("Sreenidhi Institute of Science Technology Hyderabad", "Vellore Institute Technology Univ"):
    rr = m.match(probe)
    if rr["status"] == "review":
        review = (probe, rr)
        break
check(review is not None, "expected at least one probe in the 85-92 review band")
if review:
    check(review[1]["suggestion"]["canonical_id"] and review[1]["country"] is None,
          f"review result must carry a suggestion and no country: {review}")

conn.close()

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"PASS: education_match - {len(cases)} degree strings, {n_seeded} seeded aliases, matching/fuzzy/campus-guard/admin-list checks.")
