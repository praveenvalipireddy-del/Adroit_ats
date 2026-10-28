"""Indeed (US + India) and LinkedIn recruiter posts in the Jobs tab.

1. Indeed: the actor is asked the right way (us/in, 24h/3 days, "contract" for a Contract search) and its
   nested fields are read (employer, location, salary, job types, full description, email in the text).
2. Recruiter posts: the LinkedIn search query, 24h / 50 posts; job seekers dropped without AI; the AI reads
   the rest in full; only real requirements of this market are kept; an email / phone only counts if it is
   written in the post; two posts with the same title stay two jobs; the cost is reported.
3. Paid portals only when picked: "All US portals" never calls Apify; India "All" never runs recruiter posts;
   an automatic search (no_scrape) never starts a paid scrape.
4. Edge: both markets list Indeed + recruiter posts, the cost hint, the post badge and the per-portal note.
Apify and the AI are stubs - no network, no cost. Labelled test fixtures. Run: python test_new_portals_browser.py
"""
import json
import os
import re
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_new_portals.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = "test-token-not-real"
import app as app_module  # noqa: E402
import india_job_scrapers as ij  # noqa: E402
import indeed_jobs  # noqa: E402
import linkedin_posts  # noqa: E402
import models  # noqa: E402
import resume_bot  # noqa: E402
import us_job_scrapers as us  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures, posts_calls, ai_calls = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


INDEED_ITEM = {
    "key": "abc123portaltest", "url": "https://www.indeed.com/viewjob?jk=abc123portaltest", "title": "Senior DevOps Engineer Portaltest",
    "datePublished": "2026-10-26T05:00:00.000Z",
    "location": {"countryName": "United States", "countryCode": "US", "admin1Code": "TX", "city": "Dallas"},
    "employer": {"name": "Acme Portaltest Inc"},
    "jobTypes": {"NJXCK": "Contract"},
    "baseSalary": {"min": 70, "max": 80, "unitOfWork": "HOUR", "currencyCode": "USD"},
    "description": {"text": "AWS, Terraform, EKS. 8+ years. Send resumes to jobs@acme-portaltest.example", "html": "<p>...</p>"},
}
INDEED_IN = dict(INDEED_ITEM, key="in1portaltest", url="https://in.indeed.com/viewjob?jk=in1portaltest", title="DevOps Engineer Indeedin Portaltest",
                 location={"countryName": "India", "countryCode": "IN", "city": "Hyderabad"}, baseSalary={}, jobTypes={"X": "Full-time"},
                 description={"text": "Kubernetes, Jenkins. 5 years."})


def post(n, content, name="Riya Recruiter", headline="Talent Acquisition at CloudHire"):
    return {"type": "post", "id": str(n), "linkedinUrl": f"https://www.linkedin.com/posts/test-portaltest-{n}", "content": content,
            "author": {"name": name, "info": headline, "linkedinUrl": f"https://www.linkedin.com/in/rec{n}?miniProfileUrn=x"},
            "postedAt": {"postedAgoShort": "3h", "date": "2026-10-26T08:00:00Z"}}


POSTS = [
    post(1, "We are hiring! DevOps Engineer (C2C, Remote US). 8+ yrs AWS, Terraform. Share resumes to riya@cloudhire-portaltest.example or call +1 469 555 0199"),
    post(2, "#OpenToWork I am looking for a new DevOps role. My resume is attached.", name="Job Seeker"),
    post(3, "Hiring tip: always tailor your resume! Training batch starts Monday - enroll now.", name="Trainer"),
    post(4, "Urgent requirement: DevOps Engineer, Bangalore, 5+ years, full-time. Send CV to hr@blr-portaltest.example", name="Blr Recruiter"),
    post(5, "Looking for a DevOps Engineer on W2 in Austin TX - DM me.", name="Sam Vendor", headline="Sr Recruiter"),
    post(6, "Hiring DevOps Engineer (contract) in Chicago. Apply via DM.", name="Pat Hiring"),
]
VERDICTS = {
    1: {"is_requirement": True, "title": "DevOps Engineer", "company": "CloudHire", "location": "Remote, US", "market": "USA", "employment_type": "C2C",
        "skills": ["AWS", "Terraform"], "recruiter_email": "riya@cloudhire-portaltest.example", "recruiter_phone": "+1 469 555 0199"},
    3: {"is_requirement": False, "why": "training ad"},
    4: {"is_requirement": True, "title": "DevOps Engineer", "location": "Bangalore", "market": "India", "employment_type": "Full-time"},
    5: {"is_requirement": True, "title": "DevOps Engineer", "location": "Austin, TX", "market": "USA", "employment_type": "W2",
        "recruiter_email": "sam@invented-portaltest.example"},        # not in the post - must be dropped
    6: {"is_requirement": True, "title": "DevOps Engineer", "location": "Chicago", "market": "USA", "employment_type": "Contract"},
}


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload, text or json.dumps(payload)

    def json(self):
        return self._p


def fake_post(url, params=None, json=None, timeout=None):
    if indeed_jobs.ACTOR in url:
        posts_calls.append(("indeed", dict(json)))
        return Resp(200, [INDEED_IN] if json.get("country") == "in" else [INDEED_ITEM])
    if linkedin_posts.ACTOR in url:
        posts_calls.append(("posts", dict(json)))
        return Resp(200, POSTS)
    posts_calls.append(("other", url))
    return Resp(200, [])


def fake_generate(client, types, system, prompt, want_json, max_tokens, **k):
    nums = [int(n) for n in re.findall(r"=== POST (\d+)", prompt)]
    ai_calls.append(nums)
    return json.dumps({"posts": [dict(VERDICTS.get(n, {"is_requirement": False}), n=n) for n in nums]}), False, "stub"


ij.requests.post = fake_post
resume_bot._generate = fake_generate
us.requests.get = lambda *a, **k: Resp(200, None, "<html></html>")

# ---- 1. Indeed
jobs = indeed_jobs.scrape_indeed("DevOps Engineer", location="United States", country="United States", job_type="Contract")
call = posts_calls[-1][1]
check(call == {"country": "us", "title": "DevOps Engineer contract", "location": "", "limit": 25, "datePosted": "1"}, f"Indeed US request: {call}")
j = jobs[0] if jobs else {}
check(j.get("company") == "Acme Portaltest Inc" and j.get("location") == "Dallas, TX" and j.get("salary") == "$70 - $80/hr"
      and j.get("job_type") == "Contract" and j.get("recruiter_email") == "jobs@acme-portaltest.example" and "Terraform, EKS" in j.get("description", "")
      and j.get("source") == "Indeed (Live)" and j.get("country") == "United States", f"Indeed fields read: {j}")
jobs = indeed_jobs.scrape_indeed("DevOps Engineer", location="Hyderabad", country="India")
check(posts_calls[-1][1]["country"] == "in" and posts_calls[-1][1]["datePosted"] == "3" and posts_calls[-1][1]["location"] == "Hyderabad", "Indeed India request")
check(jobs and jobs[0]["salary"] == "" and jobs[0]["country"] == "India" and jobs[0]["location"] == "Hyderabad", f"no invented salary, India: {jobs[:1]}")

# ---- 2. recruiter posts
posts_calls.clear()
res = linkedin_posts.search("DevOps Engineer", market="United States")
call = posts_calls[-1][1]
check(call["searchQueries"] == ["hiring DevOps Engineer"] and call["maxPosts"] == 50 and call["postedLimit"] == "24h" and call["sortBy"] == "date",
      f"LinkedIn post search: {call}")
read = sorted(n for c in ai_calls for n in c)
check(2 not in read and read == [1, 3, 4, 5, 6], f"the job seeker is dropped without AI, every other post is read: {read}")
titles = [(x["recruiter_name"], x["recruiter_email"], x["recruiter_phone"]) for x in res["jobs"]]
check(res["read"] == 6 and res["requirements"] == 3 and res["other_market"] == 1 and res["not_requirements"] == 2, f"counts: {res}")
check(("Riya Recruiter", "riya@cloudhire-portaltest.example", "+1 469 555 0199") in titles, f"email + phone written in the post kept: {titles}")
sam = [x for x in res["jobs"] if x["recruiter_name"] == "Sam Vendor"]
check(sam and sam[0]["recruiter_email"] == "", "an email the post doesn't contain is dropped")
check(abs(res["cost_usd"] - 0.012) < 1e-9, f"cost reported ($0.002 x 6 posts): {res['cost_usd']}")

# nothing in 24 hours -> one more search over the last week; nothing there -> said so, $0.002 for two empty searches
real_fake_post = ij.requests.post
week = []
def _empty_then_week(url, params=None, json=None, timeout=None):
    week.append(json.get("postedLimit"))
    return Resp(200, [] if json.get("postedLimit") == "24h" else POSTS[:1])
ij.requests.post = _empty_then_week
r_w = linkedin_posts.search("DevOps Engineer", market="United States")
check(week == ["24h", "week"] and r_w["widened"] and r_w["read"] == 1, f"widened to the last week: {week} {r_w['read']}")
ij.requests.post = lambda url, params=None, json=None, timeout=None: Resp(200, [])
r_e = linkedin_posts.search("DevOps Engineer", market="United States")
check(r_e["read"] == 0 and r_e["cost_usd"] == 0.002 and not r_e["error"], f"two empty searches: {r_e}")
ij.requests.post = lambda url, params=None, json=None, timeout=None: Resp(200, [{"type": "post", "id": "9", "unknownField": "x"}])
r_f = linkedin_posts.search("DevOps Engineer", market="United States")
check("format this app doesn't read yet" in r_f["error"], f"a renamed field is reported, not shown as 0 posts: {r_f['error']}")
ij.requests.post = real_fake_post
riya = [x for x in res["jobs"] if x["recruiter_name"] == "Riya Recruiter"][0]
check("Profile: https://www.linkedin.com/in/rec1" in riya["description"] and "Talent Acquisition at CloudHire" in riya["description"]
      and riya["url"].endswith("test-portaltest-1") and riya["source"] == "Recruiter Post", f"recruiter + post links: {riya['description'][:200]}")
res_in = linkedin_posts.search("DevOps Engineer", market="India")
check([x["location"] for x in res_in["jobs"]] == ["Bangalore"] and res_in["jobs"][0]["country"] == "India", "India search keeps the India post only")

# ---- 3. paid portals only when picked; posts saved as separate jobs
posts_calls.clear()
r = us.run_multi_source_us_scrape(["DevOps Engineer"], save_to_db=False)
check(not posts_calls and set(r["sources"]) == {"Dice", "LinkedIn"}, f"All US portals = free only, no Apify: {posts_calls} {r['sources']}")
r = us.run_multi_source_us_scrape(["DevOps Engineer"], save_to_db=True, sources=["Recruiter Post"], job_type="")
check([c[0] for c in posts_calls] == ["posts"] and r["sources"]["Recruiter Post"]["count"] == 3
      and "read 6 posts, 3 real requirements" in r["sources"]["Recruiter Post"]["note"] and "$0.012" in r["sources"]["Recruiter Post"]["note"],
      f"recruiter posts only, with the note: {r['sources']}")
saved = [x for x in models.get_jobs(source="Recruiter Post", country="United States")]
check(len(saved) == 3 and all(x["title"] == "DevOps Engineer" for x in saved), f"three posts with the same title stay three jobs: {len(saved)}")
check(not models.get_jobs(source="LinkedIn", query="DevOps Engineer", country="United States") or
      all(x["source"] != "Recruiter Post" for x in models.get_jobs(source="LinkedIn", country="United States")), "the LinkedIn pick doesn't show recruiter posts")
posts_calls.clear()
r = ij.run_multi_source_india_scrape(["DevOps Engineer"], save_to_db=False)
check("posts" not in [c[0] for c in posts_calls] and "indeed" in [c[0] for c in posts_calls] and "Recruiter Post" not in r["sources"],
      f"India All: Indeed yes, recruiter posts no: {[c[0] for c in posts_calls]}")

client = app_module.app.test_client()
EMAIL, PW = "portals-admin@example.invalid", "Portals-1"
models.create_user("Pia Portaltest", EMAIL, PW, role="Admin")
with client.session_transaction() as s_:
    s_["user"] = {"id": 1, "name": "Pia", "role": "Admin", "email": EMAIL}
posts_calls.clear()
d = client.post("/api/jobs/search", json={"query": "Nothingmatches Portaltest", "source": "Indeed", "country": "United States", "no_scrape": True,
                                          "type_filter": ""}).get_json()
check(not posts_calls, f"an automatic search never starts a paid Indeed scrape: {posts_calls}")
d = client.post("/api/jobs/search", json={"query": "Senior DevOps Engineer Portaltest", "source": "Indeed", "country": "United States", "no_scrape": False,
                                          "type_filter": ""}).get_json()
check([c[0] for c in posts_calls] == ["indeed"] and d["scrape_sources"] and d["scrape_sources"]["Indeed"]["count"] == 1
      and any(x["title"] == "Senior DevOps Engineer Portaltest" for x in d["jobs"]), f"Search Jobs with Indeed picked: {d.get('scrape_sources')}")

# ---- 4. the screen
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "new_portals.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.add_init_script("try { localStorage.setItem('jobsView', 'table') } catch (e) {}")   # these checks use the Jobs table view
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.7" in page.content(), "cache-buster not bumped to 5.99.7")
        page.click("a.nav-item[data-tab=jobs]")
        opts = page.eval_on_selector("#filter-source", "s => [...s.options].map(o => o.value)")
        check("Indeed" in opts and "Recruiter Post" in opts, f"US portals: {opts}")
        page.select_option("#filter-source", "Recruiter Post")
        check("about $0.10 per search" in page.inner_text("#jobs-portal-cost"), "cost hint before a recruiter-post search")
        page.fill("#filter-query", "DevOps Engineer")
        page.select_option("#filter-job-type", "")
        page.click("#btn-search-jobs")
        page.wait_for_selector(".post-badge", timeout=15000)
        check("Post by Riya Recruiter" in page.inner_text("#jobs-table-body"), "recruiter post badge in the table")
        page.screenshot(path=shot)
        page.select_option("#filter-country", "India")
        page.wait_for_timeout(400)
        opts = page.eval_on_selector("#filter-source", "s => [...s.options].map(o => o.value)")
        check("Indeed" in opts and "Recruiter Post" in opts and "Naukri" in opts, f"India portals: {opts}")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: Indeed (US + India) and LinkedIn recruiter posts - requests, fields, seekers dropped without AI, AI reads every post, written emails only, market, cost note, paid only when picked, Edge. Screenshot: {shot}")
