"""Emails that think: plan -> write -> check, rules read as intent, and the rule check before saving.

1. Rules are described to every writer as intentions ("never copy a rule's words, except quoted text").
2. The first email runs PLAN -> WRITE -> REVIEW: the plan goes into the writer's prompt; the review may polish the
   email (its text is used) but never add a fact (a review that adds a rate keeps the first draft); the notes and
   rule conflicts come back to the screen.
3. Rule check: a clean wording, duplicates and conflicts with the saved rules; only for the consultant's recruiter.
4. Edge: adding a rough rule shows the suggested wording; "Save suggested wording" saves the clean rule.
The AI is a stub - no network, no cost. Labelled test fixtures. Run: python test_email_thinking.py
"""
import json
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_email_thinking.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.GEMINI_API_KEY = "test-not-real"
import app as app_module  # noqa: E402
import application_brief  # noqa: E402
import consultant_rules  # noqa: E402
import models  # noqa: E402
import resume_bot as rb  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures, calls = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "think-rec@example.invalid", "Think-1"
REC = uid(models.create_user("Tara Thinktest", EMAIL, PW, role="Recruiter", region="India"))
OTHER = uid(models.create_user("Otto Thinktest", "think-other@example.invalid", "Think-2", role="Recruiter", region="India"))
cid = models.create_candidate("Visu Thinktest", "visu-think@example.invalid", title="Senior Cloud DevOps Engineer", primary_skills="AWS, Terraform, Docker",
                              experience_years=10, phone="+91 90000 00001", assigned_user_id=REC, country="India",
                              resume_text="VISU THINKTEST - Senior Cloud DevOps Engineer. SunTec Business Solutions: Terraform, Docker, Jenkins on OCI.")
conn = models.get_db_connection()
consultant_rules.add(conn, cid, "email", "Hi First name in email, Hope you are doing well!", REC)
consultant_rules.add(conn, cid, "email", "I came across on you job req posted on linkedin. iam visakh and short line related to jd and resume", REC)
conn.close()
JD = "DevOps Engineer at Chinmay Finlease. Must have: OCI, Docker, Jenkins, Terraform. Send resumes to tushar.soni@chinmayfinlease.example"
DRAFT = ("Hi Tushar,\n\nI came across your DevOps Engineer posting on LinkedIn. I'm Visu, a Senior Cloud DevOps Engineer.\n\n"
         "At SunTec Business Solutions I built OCI infrastructure with Terraform, Docker and Jenkins.\n\nMy resume is attached.\n\n"
         "Visu Thinktest\nSenior Cloud DevOps Engineer\n+91 90000 00001")
POLISHED = DRAFT.replace("I built OCI infrastructure", "I built and ran OCI infrastructure")
review_adds_rate = {"on": False}


def fake_generate(client, types, system, prompt, want_json, max_tokens, **_k):
    if system == application_brief.BRIEF_SYSTEM:
        calls.append("brief")
        return json.dumps({"apply_to_email": "tushar.soni@chinmayfinlease.example", "must_have_skills": ["OCI", "Docker"]}), False, "stub"
    if system == application_brief.PLAN_SYSTEM:
        calls.append("plan")
        return json.dumps({"needs": ["OCI", "Terraform"], "rules": [{"rule": "2", "how_applied": "say where we found the job, introduce Visu"}],
                           "rule_conflicts": ["Rules 1 and 3 both set the greeting"], "points": ["OCI + Terraform at SunTec"]}), False, "stub"
    if system == application_brief.WRITE_SYSTEM:
        calls.append("write")
        check("PLAN:" in prompt and "OCI + Terraform at SunTec" in prompt, "the writer gets the plan")
        return json.dumps({"body": DRAFT}), False, "stub"
    if system == application_brief.REVIEW_SYSTEM:
        calls.append("review")
        check("DRAFT:" in prompt and DRAFT in prompt, "the reviewer gets the draft")
        body = POLISHED + ("\nMy rate is $95/hr." if review_adds_rate["on"] else "")
        return json.dumps({"body": body, "notes": ["Opened with where we found the job (rule 2)", "Led with OCI and Terraform - the JD's must-haves"]}), False, "stub"
    if system.startswith("A recruiter is adding a standing rule"):
        calls.append("rulecheck")
        return json.dumps({"clean": "Mention that we found the job on LinkedIn, introduce the consultant, and add one line linking the resume to the job.",
                           "duplicates": [2], "conflicts": [{"n": 1, "why": "both set the opening"}], "advice": "Rule 2 says the same - replace it."}), False, "stub"
    return json.dumps({}), False, "stub"


rb._generate = fake_generate
rb._gemini_client = lambda: (None, None)
rb.gemini_configured = lambda: False
rb.ai_configured = lambda: True

# ---- 1. rules as intent
block = consultant_rules.prompt_block(cid, "email")
check("NEVER copy a rule's own words" in block and "quotation marks" in block and "iam visakh" in block, "rules described as intentions")

# ---- 2. plan -> write -> review
cand = models.get_candidate_by_id(cid)
job = {"id": None, "title": "DevOps Engineer", "company": "Chinmay Finlease", "recruiter_name": "Tushar Soni",
       "source": "LinkedIn (Live 24h)"}
out = application_brief.build(cand, job, JD, resume_text=cand["resume_text"], tailored=False)
check(calls == ["brief", "plan", "write", "review"], f"plan -> write -> check: {calls}")
check(out["body"] == POLISHED, f"the review's polish is used: {out['body'][:200]!r}")
check(out["notes"][0].startswith("Opened with where we found the job") and out["rule_conflicts"] == ["Rules 1 and 3 both set the greeting"], f"notes: {out}")
calls.clear()
review_adds_rate["on"] = True
out = application_brief.build(cand, job, JD, resume_text=cand["resume_text"], tailored=False)
check(out["body"] == DRAFT and "$95" not in out["body"], "a review that adds a fact (a rate) is not used - the first draft stays")
review_adds_rate["on"] = False
# the rule says LinkedIn, but this job came from Dice: the true source wins (asked again, then corrected in code)
calls.clear()
out_d = application_brief.build(cand, dict(job, source="Dice (Live 24h)"), JD, resume_text=cand["resume_text"], tailored=False)
check("on Dice" in out_d["body"] and "on LinkedIn" not in out_d["body"], f"a rule's example site never overrides the true source: {out_d['body'][:160]!r}")

# ---- 2b. the job link: right after the greeting, only for a real web link, never in a reply to their own email
import email_memory  # noqa: E402
LINK = "https://www.linkedin.com/jobs/view/4300000001"
check(application_brief.job_link_line({"title": "DevOps Engineer", "url": LINK}) == f"Position: DevOps Engineer - {LINK}", "job link line")
check(application_brief.job_link_line({"title": "DevOps Engineer", "url": "email:5:<m1@x>"}) == "", "no link for an email requirement")
check("your LinkedIn post" in application_brief.job_link_line({"title": "DevOps Engineer", "url": LINK, "source": "Recruiter Post"}), "post link wording")
job_l = dict(job, url=LINK)
out = application_brief.build(cand, job_l, JD, resume_text=cand["resume_text"], tailored=False)
check(out["body"].startswith(f"Hi Tushar,\n\nPosition: DevOps Engineer - {LINK}\n\nI came across"), f"link right after the greeting: {out['body'][:160]!r}")
out_r = application_brief.build(cand, job_l, JD, resume_text=cand["resume_text"], tailored=False, reply=True)
check(LINK not in out_r["body"], "no job link in a reply to the vendor's own requirement email")

# ---- 2c. memory: the AI's version + what the recruiter saved; the next email learns from it
check(email_memory.prompt_block(cid) == "", "no memory yet")
email_memory.remember_ai(cid, 77, REC, "DevOps Engineer", "Hi Tushar,\n\nHope you are doing well!\n\nAI text.")
email_memory.remember_final(cid, 77, REC, "DevOps Engineer", "Hi Tushar,\n\nRecruiter's own shorter text.\n\nVisu")
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT ai_body, final_body, drafted_at FROM email_examples WHERE candidate_id = ?", (cid,))
rows = [dict(r) for r in cur.fetchall()]
conn.close()
check(len(rows) == 1 and "AI text" in rows[0]["ai_body"] and "shorter text" in rows[0]["final_body"] and rows[0]["drafted_at"], f"one row, both versions: {rows}")
mem = email_memory.prompt_block(cid)
check("AI DRAFT:" in mem and "WHAT THE RECRUITER SENT INSTEAD:" in mem and "Never copy job-specific facts" in mem, "memory shows the change the recruiter made")
email_memory.remember_final(cid, None, REC, "SRE", "Hi,\n\nTyped by hand.", pasted=True)
check("SENT:\nHi,\n\nTyped by hand." in email_memory.prompt_block(cid), "an email the AI didn't write is remembered too")
for i in range(4):
    email_memory.remember_final(cid, 100 + i, REC, f"Job {i}", f"Hi,\n\nEmail {i}.")
check(len(email_memory.examples(cid)) == 3 and email_memory.examples(cid)[0]["final_body"].endswith("Email 3."), "the last 3, newest first")
calls.clear()
seen = {}
real_gen = rb._generate
def _spy(client, types, system, prompt, want_json, max_tokens, **k):
    if system == application_brief.WRITE_SYSTEM:
        seen["write"] = prompt
    return real_gen(client, types, system, prompt, want_json, max_tokens, **k)
rb._generate = _spy
out = application_brief.build(cand, job, JD, resume_text=cand["resume_text"], tailored=False)
rb._generate = real_gen
check("EMAILS THE RECRUITER APPROVED FOR THIS CONSULTANT" in seen.get("write", "") and "Email 3." in seen.get("write", ""), "the writer gets the approved emails")
check(any("style of your last 3 approved email" in n for n in out["notes"]), f"the note says so: {out['notes']}")
other = models.create_candidate("Other Memtest", "other-mem@example.invalid", title="QA", assigned_user_id=OTHER, country="India")
check(email_memory.prompt_block(other) == "", "memory is per consultant")

# ---- 3. rule check + API privacy
client = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Tara", "role": "Recruiter", "email": EMAIL}
d = client.post(f"/api/consultants/{cid}/rules/check", json={"kind": "email", "text": "came across linkedin iam visu short line jd resume"}).get_json()
check(d["clean"].startswith("Mention that we found the job on LinkedIn") and d["duplicates"][0]["n"] == 2 and d["conflicts"][0]["n"] == 1
      and "I came across on you job req" in d["duplicates"][0]["text"], f"rule check: {d}")
conn = models.get_db_connection()
check(len(consultant_rules.list_for(conn, cid, "email")) == 2, "the check saves nothing")
conn.close()
with client.session_transaction() as s_:
    s_["user"] = {"id": OTHER, "name": "Otto", "role": "Recruiter", "email": "think-other@example.invalid"}
check(client.post(f"/api/consultants/{cid}/rules/check", json={"kind": "email", "text": "x y z"}).status_code == 404, "another recruiter's consultant: 404")

# the preview route keeps the AI's version for the memory (the draft routes add the final one)
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Tara", "role": "Recruiter", "email": EMAIL}
jid = models.save_or_update_scraped_job({"title": "Platform Engineer Memtest", "company": "Memco", "source": "LinkedIn (Live 24h)", "url": LINK + "9",
                                         "description": JD, "country": "India"})
jid = jid["id"] if isinstance(jid, dict) else jid
pv = client.post("/api/outreach/application-preview", json={"candidate_id": cid, "job_id": jid, "jd_text": JD, "resume_text": cand["resume_text"]}).get_json()
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT ai_body FROM email_examples WHERE candidate_id = ? AND job_id = ? AND final_body IS NULL", (cid, jid))
kept = cur.fetchone()
conn.close()
check(kept and kept[0] == pv.get("body") and (LINK + "9") in pv.get("body", ""), f"preview kept the AI version (with the job link): {pv.get('body', '')[:120]!r}")

# ---- 4. the Rules window
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "email_thinking.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.11" in page.content(), "cache-buster not bumped to 5.99.11")
        page.wait_for_function("state.consultants && state.consultants.length", timeout=15000)
        page.evaluate(f"openRules({cid}, 'email')")
        page.wait_for_function("document.getElementById('modal-rules').style.display === 'flex'", timeout=5000)
        page.fill("#rules-text", "came across linkedin iam visu short line jd resume")
        page.click("#rules-add")
        page.wait_for_selector("#rules-use-clean", timeout=10000)
        txt = page.inner_text("#rules-check")
        check("Suggested wording" in txt and "Already said by rule 2" in txt and "Conflicts with rule 1" in txt, f"suggestion + warnings shown: {txt!r}")
        page.screenshot(path=shot)
        page.click("#rules-use-clean")
        page.wait_for_function("document.getElementById('rules-email').innerText.includes('Mention that we found the job on LinkedIn')", timeout=5000)
        check(not page.is_visible("#rules-check"), "the suggestion box closes after saving")
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
print(f"PASS: emails that think - rules as intent, plan -> write -> check (polish used, new facts refused), notes + rule conflicts, rule check (clean wording, duplicates, conflicts, privacy), Rules window in Edge. Screenshot: {shot}")
