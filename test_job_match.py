"""The skills match on job cards (job_match.py): of what the job asks for, how much the consultant has. Free, no AI.

1. Two listed skills no longer make 100%; a posting naming too few skills gets no percentage.
2. Skills deep in a whole posting count; the resume counts as well as the listed skills; "machine learning" is ML.
3. "react quickly" is not React; JavaScript is not Java.
4. /api/jobs/search: the match for the consultant asked for, using the stored posting; none for another recruiter's consultant.
5. Edge: the ring, the missing skills and the job page show it; best match first.
Run: python test_job_match.py
"""
import os
import socket
import sys
import tempfile
import threading
import time

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_match.db")
os.environ["DATABASE_URL"] = ""
os.environ["CAREER_SYNC_HOURS"] = "0"
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import job_match as jm  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---- 1-3. the score
two = jm.consultant_profile({"primary_skills": "AI, ML"})
m = jm.match(two, "Data Scientist. Work with AI and ML models.")
check(m["pct"] is None and sorted(m["hit"]) == ["AI", "ML"] and m["asked"] == 2, f"2 of 2 is not 100%: {m}")
m = jm.match(two, "Applied AI engineer. AI, ML, Python, Kubernetes, Spark and SAP.")
check(m["pct"] == 33 and m["asked"] == 6 and "Python" in m["missing"], f"counts what the job asks for: {m}")
m = jm.match(jm.consultant_profile({"primary_skills": "SQL", "resume_text": "Built pipelines in Python and Airflow."}), "SQL, Python and Airflow required.")
check(m["pct"] == 100 and m["hit"] and not m["missing"], f"the resume counts: {m}")
m = jm.match(jm.consultant_profile({"primary_skills": "ML, AWS, Python"}), "Machine learning on Amazon Web Services with Python.")
check(m["pct"] == 100 and m["asked"] == 3, f"different names, one skill: {m}")
m = jm.match(jm.consultant_profile({"primary_skills": "Python"}), "You react quickly. JavaScript and Python and TypeScript.")
check("React" not in m["missing"] + m["hit"] and "Java" not in m["missing"], f"no false skills: {m}")
m = jm.match(jm.consultant_profile({"primary_skills": "Excel, Python, SQL"}), "Advanced Excel, Python and SQL.")
check(m["pct"] == 100 and m["asked"] == 3, f"a listed skill outside the known list counts: {m}")
m = jm.match(jm.consultant_profile({"primary_skills": "Python"}), "Azure DevOps pipelines, Python, Spring Boot.")
check(m["asked"] == 3 and "Azure DevOps" in m["missing"] and "Azure" not in m["missing"], f"one skill, not two: {m}")
m = jm.match(jm.consultant_profile({}), "SQL, Python and Airflow required.")
check(m["no_profile"] and m["pct"] is None, f"no skills on file: {m}")

# ---- non-IT jobs (marketing, HR, finance) are read too, not just technology
MKT_JD = ("AI-Powered Digital Content & Social Media Marketing Specialist. Plan a monthly content calendar, content creation for "
          "Instagram and LinkedIn, SEO, Google Analytics, Meta Ads. Tools: Canva, CapCut, ChatGPT, Midjourney, HeyGen. Claude a plus.")
ai_eng = jm.consultant_profile({"primary_skills": "AI Automation, n8n, Python, Prompt Engineering",
                                "resume_text": "AI Automation Engineer. n8n workflows, Claude and Gemini, ChatGPT prompts, ElevenLabs voice, "
                                               "Instagram Reels automation, social media marketing posts generated with AI."})
m = jm.match(ai_eng, MKT_JD)
check(m["pct"] is not None and m["asked"] >= 9, f"a marketing job gets a percentage: {m}")
check({"Social Media Marketing", "ChatGPT", "Claude"} <= set(m["hit"]), f"what the resume shows counts: {m['hit']}")
check({"SEO", "Canva", "Content Calendar", "Google Analytics"} <= set(m["missing"]), f"the marketing skills it lacks are named: {m['missing']}")
check(sum(1 for n in m["hit"] + m["missing"] if n.lower() in ("social media marketing", "social media management")) == 1, "one skill, two names")
m = jm.match(jm.consultant_profile({"primary_skills": "Recruitment"}),
             "Talent Acquisition specialist: Boolean search, onboarding of new hires, payroll basics, HRIS. GST and Tally for invoices.")
check({"Talent Acquisition", "Boolean Search", "HRIS", "GST", "Tally"} <= set(m["missing"]) and m["pct"] is not None, f"HR + finance skills read: {m}")
m = jm.match(ai_eng, "We excel at onboarding clients; our seo-friendly team tallies results and shares CRM-free updates with the payroll of joy.")
check(not ({"Excel", "Onboarding", "Tally", "SEO", "Payroll"} & set(m["hit"] + m["missing"])), f"everyday words are not skills: {m}")

# ---- speed: a consultant search over many stored postings must not slow the Jobs list
prof = jm.consultant_profile({"primary_skills": "SQL, Power BI, Python", "resume_text": "Python Airflow Snowflake dbt " * 200})
posting = ("We are looking for someone to build reliable reporting with Python and SQL across several business teams. " * 60)
t0 = time.time()
for _ in range(jm.MAX_SCORED):
    jm.match(prof, posting)
secs = time.time() - t0
print(f"skills match: {jm.MAX_SCORED} postings of {len(posting)} chars in {secs:.2f}s")
check(secs < 10, f"too slow: {secs:.1f}s for the most a search scores")

stubs = [{"id": i, "title": "SQL, Python and Airflow"} for i in range(jm.MAX_SCORED + 5)]
jm.attach({"primary_skills": "SQL"}, stubs, lambda ids: {})
check("skill_match" in stubs[jm.MAX_SCORED - 1] and "skill_match" not in stubs[jm.MAX_SCORED], "a very wide search scores its most relevant jobs only")

# ---- fixtures
uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "match-rec@example.invalid", "Match-1"
REC = uid(models.create_user("Mia Matchtest", EMAIL, PW, role="Recruiter", region="USA"))
OTHER = uid(models.create_user("Oli Othertest", "match-other@example.invalid", "Match-2", role="Recruiter", region="USA"))
K = models.create_candidate("Kai Matchfixture", "kai.match@example.invalid", title="Data Analyst", primary_skills="SQL, Power BI",
                            experience_years=5, country="United States", assigned_user_id=REC,
                            resume_text="KAI MATCHFIXTURE\nData analyst. Python and Snowflake reporting for finance teams over five years.")
NOPE = models.create_candidate("Nia Otherfixture", "nia.match@example.invalid", title="Data Analyst", primary_skills="SQL",
                               country="United States", assigned_user_id=OTHER)


def job(n, title, description):
    j = models.save_or_update_scraped_job({"title": title, "company": f"Fixture Co {n}", "source": "Dice", "url": f"https://www.dice.com/job-detail/match-{n}",
                                           "location": "Dallas, TX", "job_type": "Contract", "country": "United States", "description": description})
    return j["id"] if isinstance(j, dict) else j


J_PART = job(1, "Data Analyst Partial (MF)", "Labelled fixture. SQL, Kafka, Terraform and Kubernetes.")
J_FEW = job(2, "Data Analyst Few (MF)", "Labelled fixture. SQL only.")
J_DEEP = job(3, "Data Analyst Deep (MF)", "Labelled fixture. See the full posting.")
J_NONE = job(4, "Data Analyst Vague (MF)", "Labelled fixture. Reporting for finance teams.")
_c = models.get_db_connection()
_c.cursor().execute("UPDATE jobs SET full_description = ? WHERE id = ?", ("Labelled fixture. " + "Long introduction. " * 300 + "Requires SQL, Power BI, Python and Snowflake.", J_DEEP))
_c.commit()
_c.close()
check(jm.job_text({"title": "T", "description": "short"}, "whole") == "T whole", "the stored posting is used over the summary")

client = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Mia", "role": "Recruiter", "email": EMAIL}
body = {"query": "Data Analyst", "country": "United States", "is_24h_only": True, "no_scrape": True, "source": "All", "type_filter": ""}
d = client.post("/api/jobs/search", json={**body, "candidate_id": K}).get_json()
by_id = {j["id"]: j for j in d["jobs"]}
deep = by_id[J_DEEP]["skill_match"]
check(deep["pct"] == 100 and deep["asked"] == 4, f"skills deep in the whole posting count: {deep}")
check("full_description" not in by_id[J_DEEP], "the list still doesn't send whole postings")
check(by_id[J_PART]["skill_match"]["pct"] == 25, f"partial: {by_id[J_PART]['skill_match']}")
check(by_id[J_FEW]["skill_match"]["pct"] is None, f"few: {by_id[J_FEW]['skill_match']}")
d = client.post("/api/jobs/search", json=body).get_json()
check(all("skill_match" not in j for j in d["jobs"]), "no consultant: no match")
d = client.post("/api/jobs/search", json={**body, "candidate_id": NOPE}).get_json()
check(all("skill_match" not in j for j in d["jobs"]), "another recruiter's consultant: no match")
page_d = client.get(f"/api/jobs/{J_DEEP}/page?candidate_id={K}").get_json()
check(page_d["skill_match"]["pct"] == 100, f"job page: {page_d.get('skill_match')}")

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()

js_errors, shot = [], os.path.join(tmp_dir, "match.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.20" in page.content(), "cache-buster not bumped to 5.99.20")
        page.locator(".cons-card:has-text('Kai Matchfixture')").first.click()
        page.wait_for_selector("#cons-jobs-list .cons-job", timeout=15000)
        cards = page.locator("#cons-jobs-list .cons-job")
        first = cards.first.inner_text()
        check("Deep (MF)" in first and "100%" in first and "STRONG" in first and "4 of 4 skills" in first, f"best match first: {first!r}")
        part = page.locator("#cons-jobs-list .cons-job:has-text('Partial (MF)')").inner_text()
        check("25%" in part and "STRETCH" in part and "✗" in part and "Kafka" in part, f"missing skills shown: {part!r}")
        few = page.locator("#cons-jobs-list .cons-job:has-text('Few (MF)')").inner_text()
        check("%" not in few and "1 of 1 skill" in few, f"too few skills: no percentage: {few!r}")
        vague = page.locator("#cons-jobs-list .cons-job:has-text('Vague (MF)')").inner_text()
        check("no skills named" in vague and "%" not in vague, f"vague posting: {vague!r}")
        check(cards.last.inner_text().find("Vague (MF)") >= 0, "a posting with no skills comes last")
        page.screenshot(path=shot)
        page.locator("#cons-jobs-list .cons-job:has-text('Partial (MF)') button.btn.cons-job-open").click()
        page.wait_for_function("document.querySelector('.jp-match') && document.querySelector('.jp-match').innerText.includes('25%')", timeout=15000)
        txt = page.inner_text(".jp-match")
        check("1 of 4" in txt and "Kafka" in txt, f"job page match: {txt!r}")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: skills match (Edge). Screenshot: {shot}")
