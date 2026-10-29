"""Inbox replies that think: Gemini reasons first, sees the whole conversation, and an unverified detail is
marked "[please confirm: ...]" instead of the AI's reply being thrown away.

1. Gemini gets a thinking level and room for it on top of the answer; a model without thinking levels is
   asked again without one.
2. mask(): a rate / years / work authorization / email / phone that isn't on file becomes a placeholder;
   the details that ARE on file stay.
3. A reply draft: the AI sees the application email we sent, the earlier messages in the thread and the
   resume; a first draft with an invented rate is retried; a second one is kept with the rate marked.
4. A colleague at the recruiter's company counts as a reply only in a "Re:" thread about the same job.
5. Requirement emails: a role in the subject ("Payments Engineer") is never skipped for a receipt word.
The AI and Gmail are stubs - no network, no cost. Labelled test fixtures.
Run: python test_inbox_thinking.py
"""
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_inbox_thinking.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import inbox_requirements as ir  # noqa: E402
import models  # noqa: E402
import outreach_ai  # noqa: E402
import reply_inbox as ri  # noqa: E402
import resume_bot  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


models.init_db()

# ---- 1. Gemini thinking level + room for it
from google.genai import types  # noqa: E402

seen = []


class FakeModels:
    def __init__(self, reject_thinking=False):
        self.reject_thinking = reject_thinking

    def generate_content(self, model, contents, config):
        seen.append(config)
        if self.reject_thinking and config.thinking_config is not None:
            raise RuntimeError("400 INVALID_ARGUMENT: thinking_level is not supported for this model (fixture)")

        class R:
            text = '{"ok": true}'
            candidates = []
        return R()


class FakeClient:
    def __init__(self, reject=False):
        self.models = FakeModels(reject)


resume_bot.GEMINI_MODEL = "gemini-fixture"
os.environ["GEMINI_FALLBACK_MODELS"] = ""
text, _, model = resume_bot._generate_gemini(FakeClient(), types, "sys", "prompt", True, 3000, thinking="high")
cfg = seen[-1]
check(text == '{"ok": true}' and cfg.thinking_config is not None and cfg.thinking_config.thinking_level.lower() == "high"
      and cfg.max_output_tokens == 3000 + resume_bot.GEMINI_THINKING_ROOM, f"thinking level + room: {cfg}")
seen.clear()
resume_bot._generate_gemini(FakeClient(), types, "sys", "prompt", True, 3000)
check(seen[-1].thinking_config is None and seen[-1].max_output_tokens == 3000, "no thinking asked -> plain request")
seen.clear()
text, _, _ = resume_bot._generate_gemini(FakeClient(reject=True), types, "sys", "prompt", True, 3000, thinking="high")
check(text == '{"ok": true}' and len(seen) == 2 and seen[1].thinking_config is None, f"model without thinking levels: asked again without ({len(seen)} calls)")

# ---- 2. mask
f = {"consultant_name": "Arun Fixture", "experience_years": 8, "us_based": True, "visa_status": "H1B", "target_rate": "$70/hr",
     "phone": "+1 469 555 0100", "email": "arun.fixture@example.com", "job_description": "Budget $90/hr"}
body = ("Hi,\nArun has 8 years (12 years in Java), rate $70/hr or $85/hr, H1B, also open to C2C.\n"
        "Reach him at arun.fixture@example.com or other.fixture@example.com, +1 469 555 0100 / +1 214 555 0199.\nThanks")
m = outreach_ai.mask(f, "", body, "")
check("8 years" in m and "12 years" not in m and "[please confirm: years of experience]" in m, f"years: {m}")
check("$70/hr" in m and "$85" not in m and "[please confirm: rate]" in m, f"rate (the JD budget is not the consultant's): {m}")
check("H1B" in m and "C2C" not in m and "[please confirm: work authorization]" in m, f"work authorization: {m}")
check("arun.fixture@example.com" in m and "other.fixture" not in m and "+1 469 555 0100" in m and "214" not in m, f"contacts: {m}")
check(not outreach_ai.guard(f, "", m, ""), f"a masked email passes the fact guard: {outreach_ai.guard(f, '', m, '')}")

# ---- 3. reply draft: whole conversation, retry, then mark
cand_id = models.create_candidate("Arun Fixture", "arun.fixture@example.com", phone="+1 469 555 0100", title="Java Developer",
                                  primary_skills="Java, Spring Boot, Kafka", experience_years=8, target_rate="$70/hr", visa_status="H1B")
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("INSERT INTO jobs (title, company, source, url, status) VALUES ('Java Developer (fixture)', 'Fixture Corp', 'Manual Paste', 'fixture:ithink:1', 'Open')")
job_id = cur.lastrowid
cur.execute("INSERT INTO applications (job_id, candidate_id, stage) VALUES (?, ?, 'Applied')", (job_id, cand_id))
app_id = cur.lastrowid
cur.execute("INSERT INTO email_examples (candidate_id, job_id, job_title, final_body) VALUES (?, ?, 'Java Developer', ?)",
            (cand_id, job_id, "Hi Priya, sharing Arun for the Java Developer role - Spring Boot and Kafka at Fixture Bank. (fixture)"))
cur.execute("""INSERT INTO application_replies (application_id, candidate_id, message_id, from_email, subject, received_at, body)
               VALUES (?, ?, '<m1@fixture>', 'priya@fixturevendor.example', 'Re: Java Developer', '2026-10-28 10:00:00', ?)""",
            (app_id, cand_id, "Thanks, is he on H1B? (earlier fixture message)"))
cur.execute("""INSERT INTO application_replies (application_id, candidate_id, message_id, from_email, subject, received_at, body)
               VALUES (?, ?, '<m2@fixture>', 'priya@fixturevendor.example', 'Re: Java Developer', '2026-10-29 09:00:00', ?)""",
            (app_id, cand_id, "What is his rate and when can he talk? (latest fixture message)"))
conn.commit()
latest = cur.lastrowid
reply = ri.get(conn, latest)
conn.close()

prompts, answers = [], [
    {"body": "Hi Priya,\nHis rate is $95/hr and he can talk Tuesday.\nArun Fixture", "needs_info": []},
    {"body": "Hi Priya,\nHis rate is $92/hr, free Tuesday.\nArun Fixture", "needs_info": ["interview time"]},
]


def fake_ai(system, prompt, max_tokens=3000):
    prompts.append(prompt)
    return answers[min(len(prompts), len(answers)) - 1], "stub-thinking-model"


ri._ai = fake_ai
resume_bot.ai_configured = lambda: True
d = ri.draft_reply(models.get_candidate_by_id(cand_id), models.get_job_by_id(job_id), reply)
p0 = prompts[0] if prompts else ""
check("OUR APPLICATION EMAIL" in p0 and "Fixture Bank" in p0, "the AI sees the application email we sent")
check("earlier fixture message" in p0 and p0.count("latest fixture message") == 1, "the AI sees the earlier message in the thread (once each)")
check(len(prompts) == 2 and "REJECTED" in prompts[1] and "$95/hr" in prompts[1], f"an invented rate is sent back for a retry: {len(prompts)} calls")
check(d["applied"] and "$92" not in d["body"] and "[please confirm: rate]" in d["body"] and "Tuesday" in d["body"]
      and "[please confirm: rate]" in d["needs_info"] and "interview time" in d["needs_info"], f"kept with the rate marked: {d}")
prompts.clear()
answers[:] = [{"body": "Hi Priya,\nHis rate is $70/hr. Free Tuesday.\nArun Fixture", "needs_info": [], "reply": "Answered rate and time."}]
d = ri.draft_reply(models.get_candidate_by_id(cand_id), models.get_job_by_id(job_id), reply)
check(d["applied"] and "$70/hr" in d["body"] and len(prompts) == 1 and not d["needs_info"], f"a rate on file is used as is, one call: {d}")

# ---- 3b. the requirement / application email: same - a detail that isn't on file is marked, not the template
import application_brief  # noqa: E402

calls_ab = []


def fake_ab(system, prompt, thinking="low"):
    calls_ab.append((system[:30], thinking))
    if system == application_brief.WRITE_SYSTEM:
        return {"body": "Hi Priya,\n\nArun built Kafka pipelines at Fixture Bank. His rate is $95/hr.\n\nArun Fixture", "answered": []}, "stub"
    return {}, "stub"


application_brief._ai = fake_ab
w = application_brief.write(models.get_candidate_by_id(cand_id), models.get_job_by_id(job_id), "Java + Kafka developer (fixture JD)",
                            "Arun Fixture - Java, Kafka, Spring Boot at Fixture Bank (fixture resume)", {"questions": [], "required_details": []},
                            reply=True)
check(not w["fallback"] and "Kafka pipelines" in w["body"] and "$95" not in w["body"] and "[please confirm: rate]" in w["body"]
      and "[please confirm: rate]" in w["needs_input"], f"application email kept with the rate marked: {w}")
check([t for s, t in calls_ab if s == application_brief.WRITE_SYSTEM[:30]] == [ri.REPLY_THINKING] * 2,
      f"the email is written with thinking ({ri.REPLY_THINKING}), twice before marking: {calls_ab}")

# ---- 4. colleague replies
row = {"job_title": "Senior Java Developer - Kafka"}
check(ri.colleague_reply({"subject": "RE: Senior Java Developer - Kafka (Dallas)"}, row), "colleague answering the thread")
check(not ri.colleague_reply({"subject": "Need Python Developer - C2C"}, row), "colleague's new requirement is not a reply")
check(not ri.colleague_reply({"subject": "Re: Python Developer"}, row), "a reply about another job is not this application's")
check("gmail.com" in ri.FREE_MAIL, "free mail domains never match a whole company")

# ---- 5. requirement subjects
for s, want in [("Payments Engineer - C2C - Dallas", False), ("Order Management Lead (Remote)", False),
                ("Your order has shipped", True), ("Payment receipt #4421 (fixture)", True), ("OTP for login", True)]:
    check(ir._subject_skip(s) == want, f"subject skip {s!r} -> {not want}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for x in failures:
        print("  -", x)
    sys.exit(1)
print("PASS: inbox that thinks - Gemini thinking level + room (and a retry without it), unverified rate / years / visa / contacts "
      "marked [please confirm], reply drafts see our application email + the thread + resume, retried then marked (never "
      "thrown away), colleague replies only in the same job's thread, role subjects never skipped.")
