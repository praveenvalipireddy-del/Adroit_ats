"""Full job description fetch for the Resume Optimizer (job_description_fetch.py + the
/api/jobs/<id>/full-description endpoint).

Network is never used: requests.get inside the module is replaced by a stub serving pages shaped
like the real ones (checked live 2026-10-01: LinkedIn's guest jobPosting page puts the description
in div.show-more-less-html__markup; Dice job-detail pages carry a schema.org JobPosting JSON-LD
block). Text below is clearly-labelled test content, not a real posting.
Run: python test_job_description_fetch.py
"""
import json
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_jd.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import job_description_fetch as jdf  # noqa: E402
import models  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


BODY = ("<strong>Job Title:</strong><br>Test Data Analyst — Hybrid<br><br><p>Test-only posting used by the "
        "automated tests. Responsibilities:</p><ul><li>Build Power BI dashboards</li><li>Write complex SQL</li>"
        "<li>Automate reporting in Python</li></ul><p>Requirements: 5+ years of SQL, Power BI and Python; strong "
        "communication with business stakeholders; pharma domain experience is a plus.</p>")
LINKEDIN_PAGE = (f'<html><body><h2 class="top-card-layout__title">Test</h2><section class="description">'
                 f'<div class="description__text"><div class="show-more-less-html__markup">{BODY}</div></div>'
                 f'</section></body></html>')
DICE_PAGE = ('<html><head><script type="application/ld+json">'
             + json.dumps({"@context": "https://schema.org", "@type": "JobPosting", "title": "Test", "description": BODY})
             + '</script></head><body>...</body></html>')


class FakeResp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.content = text.encode("utf-8")


calls = []
routes = {}


def fake_get(url, headers=None, timeout=None):
    calls.append(url)
    status, text = routes.get(url, (404, ""))
    return FakeResp(status, text)


jdf.requests.get = fake_get

LI_URL = "https://www.linkedin.com/jobs/view/data-analyst-test-at-test-co-4472303911?trk=x"
LI_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4472303911"
DICE_UUID = "0ef62fcc-a0db-40e1-8c8d-c16f972eab97"
DICE_URL = f"https://www.dice.com/job-detail/{DICE_UUID}"
routes[LI_API] = (200, LINKEDIN_PAGE)
routes[DICE_URL] = (200, DICE_PAGE)

# ---- id extraction / allowed hosts
check(jdf.linkedin_job_id(LI_URL) == "4472303911", "LinkedIn slug-id URL")
check(jdf.linkedin_job_id("https://www.linkedin.com/jobs/view/4472303911/") == "4472303911", "LinkedIn bare id URL")
check(jdf.linkedin_job_id("https://www.linkedin.com/jobs/search/?currentJobId=4472303911&keywords=x") == "4472303911", "currentJobId URL")
check(jdf.linkedin_job_id("https://evil.example.com/jobs/view/4472303911") is None, "non-LinkedIn host must be rejected")
check(jdf.linkedin_job_id("https://linkedin.com.evil.example/jobs/view/4472303911") is None, "look-alike host must be rejected")
check(jdf.dice_job_id(DICE_URL) == DICE_UUID, "Dice id")
check(jdf.dice_job_id("https://www.naukri.com/job-listings-123") is None, "Naukri not supported")

# ---- reading
text, err = jdf.fetch_full_description(LI_URL)
check(text and not err and calls[-1] == LI_API, f"LinkedIn fetch: err={err!r} url={calls[-1:]}")
check(text and "Test Data Analyst — Hybrid" in text and "- Build Power BI dashboards" in text and "<" not in text,
      f"LinkedIn text conversion (dash kept, bullets, no tags): {text[:200] if text else text!r}")
text, err = jdf.fetch_full_description("https://www.dice.com/job-detail/" + DICE_UUID + "?src=x")
check(text and "- Write complex SQL" in text and calls[-1] == DICE_URL, f"Dice fetch: {err!r} {calls[-1:]}")

n = len(calls)
text, err = jdf.fetch_full_description("https://www.naukri.com/job-listings-test-123")
check(text is None and "LinkedIn and Dice" in err and len(calls) == n, "unsupported site: no request, clear reason")
text, err = jdf.fetch_full_description(None)
check(text is None and err, "no URL")
routes["https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1111111111"] = (404, "")
text, err = jdf.fetch_full_description("https://www.linkedin.com/jobs/view/1111111111")
check(text is None and "no longer available" in err, f"404: {err!r}")
routes["https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/2222222222"] = (
    200, '<div class="show-more-less-html__markup">Too short.</div>')
text, err = jdf.fetch_full_description("https://www.linkedin.com/jobs/view/2222222222")
check(text is None and "too short" in err, f"short description must not count as full: {err!r}")
routes["https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/3333333333"] = (200, "<html><body>No description</body></html>")
text, err = jdf.fetch_full_description("https://www.linkedin.com/jobs/view/3333333333")
check(text is None and "no job description" in err, f"layout change is reported, not guessed: {err!r}")


def boom(*a, **k):
    raise jdf.requests.ConnectionError("down")


jdf.requests.get = boom
text, err = jdf.fetch_full_description(LI_URL)
check(text is None and "Could not reach" in err, f"network error: {err!r}")
jdf.requests.get = fake_get

# ---- endpoint: fetch once, save, serve from the DB afterwards; summary untouched
SUMMARY = "Posted 11 hours ago on LinkedIn: Test Data Analyst at Test Co."
li_job = models.save_or_update_scraped_job({"title": "Test Data Analyst", "company": "Test Co", "source": "LinkedIn (Live 24h)",
                                            "url": LI_URL, "description": SUMMARY})
li_job = li_job["id"] if isinstance(li_job, dict) else li_job
paste_job = models.save_or_update_scraped_job({"title": "Pasted Test", "company": "Test Client", "source": "Manual Paste",
                                               "url": "", "description": "Full pasted requirement text (test)."})
paste_job = paste_job["id"] if isinstance(paste_job, dict) else paste_job
naukri_job = models.save_or_update_scraped_job({"title": "Naukri Test", "company": "Test In", "source": "Naukri",
                                                "url": "https://www.naukri.com/job-listings-test-1", "description": "Short."})
naukri_job = naukri_job["id"] if isinstance(naukri_job, dict) else naukri_job

client = app_module.app.test_client()
check(client.post(f"/api/jobs/{li_job}/full-description").status_code == 401, "endpoint requires login")
with client.session_transaction() as s:
    s["user"] = {"id": 1, "name": "T", "role": "Recruiter", "email": "t@example.invalid"}
n = len(calls)
r1 = client.post(f"/api/jobs/{li_job}/full-description")
d1 = r1.get_json() or {}
check(r1.status_code == 200 and "Build Power BI dashboards" in d1.get("description", "") and d1.get("cached") is False,
      f"first call fetches: {r1.status_code} {d1}")
r2 = client.post(f"/api/jobs/{li_job}/full-description")
check(r2.status_code == 200 and r2.get_json().get("cached") is True and len(calls) == n + 1, "second call must come from the DB (no refetch)")
job = models.get_job_by_id(li_job)
check(job["description"] == SUMMARY and "Build Power BI" in (job.get("full_description") or ""),
      "full text goes to full_description; the summary used elsewhere is unchanged")
n = len(calls)
r = client.post(f"/api/jobs/{paste_job}/full-description")
check(r.status_code == 200 and r.get_json()["description"] == "Full pasted requirement text (test)." and len(calls) == n,
      "pasted requirement returns its own text without fetching")
r = client.post(f"/api/jobs/{naukri_job}/full-description")
check(r.status_code == 422 and "LinkedIn and Dice" in r.get_json().get("error", ""), f"unsupported site -> 422 with reason: {r.get_json()}")
check(client.post("/api/jobs/999999/full-description").status_code == 404, "unknown job")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: full job description - LinkedIn/Dice reading, allowed hosts only, honest failures, saved once and reused.")
