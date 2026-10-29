"""Learning from the recruiter's edits (email_memory.learn_from_edit), per consultant.

When the recruiter changes the AI's email before saving the draft, the general habits that shows are saved as that
consultant's email rules (so the next email follows them); job facts are never learned (the AI is told so); a habit
already saved isn't added twice; a tiny edit asks the AI nothing; the AI sees the rules already saved. Learning runs
in the background after a draft is saved - on for the live site, off locally and in tests unless LEARN_FROM_EDITS=1.
The AI is a stub - no network, no cost. Labelled test fixtures.
Run: python test_learn_from_edits.py
"""
import os
import sys
import tempfile
import time

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_learn.db")
os.environ["DATABASE_URL"] = ""
os.environ.pop("LEARN_FROM_EDITS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import consultant_rules  # noqa: E402
import email_memory  # noqa: E402
import models  # noqa: E402
import reply_inbox  # noqa: E402

failures, prompts = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


models.init_db()
REC = models.create_user("Lena Learntest", "learn-rec@example.invalid", "Learn-1", role="Recruiter")
REC = REC["id"] if isinstance(REC, dict) else REC
cid = models.create_candidate("Arun Learntest", "arun.learntest@example.invalid", title="DevOps Engineer", assigned_user_id=REC)
other = models.create_candidate("Bina Learntest", "bina.learntest@example.invalid", title="Data Analyst", assigned_user_id=REC)
PREFS = ["Don't say the resume was updated for the role.", "Keep the email under 120 words."]


def fake_ai(system, prompt, max_tokens=3000):
    prompts.append(prompt)
    return {"preferences": PREFS}, "stub"


reply_inbox._ai = fake_ai
AI = ("Hi Mark,\n\nHope you are doing well. I came across your DevOps Engineer opening on Dice.\n\nI have 7 years in AWS and Terraform. "
      "My attached resume has been updated specifically for this role.\n\nArun Learntest")
FINAL = ("Hi Mark,\n\nI came across your DevOps Engineer opening on Dice. I have 7 years in AWS and Terraform.\n\n"
         "Please find my resume attached.\n\nArun Learntest")

# ---- 1. the habits are learned for THIS consultant
saved = email_memory.learn_from_edit(cid, AI, FINAL, REC)
conn = models.get_db_connection()
rules = [r["text"] for r in consultant_rules.list_for(conn, cid, "email")]
check(saved == PREFS and rules == PREFS, f"learned as this consultant's email rules: {saved} / {rules}")
check(not consultant_rules.list_for(conn, other, "email"), "another consultant learns nothing from it")
check("AI DRAFT:" in prompts[-1] and "WHAT THE RECRUITER SAVED INSTEAD:" in prompts[-1] and "updated specifically" in prompts[-1],
      "the AI compares the two versions")
check("about this one job" in email_memory.LEARN_SYSTEM and "any fact" in email_memory.LEARN_SYSTEM, "job facts are never learned")

# ---- 2. no duplicates; the AI sees what is already saved
again = email_memory.learn_from_edit(cid, AI, FINAL + "\nThanks", REC)
check(again == [] and len(consultant_rules.list_for(conn, cid, "email")) == 2, f"already-saved habits aren't added twice: {again}")
check("- Keep the email under 120 words." in prompts[-1], "the AI is shown the rules already saved")

# ---- 3. a tiny edit asks the AI nothing
n = len(prompts)
check(email_memory.learn_from_edit(cid, AI, AI + " ", REC) == [] and len(prompts) == n, "a tiny edit: no AI call")
conn.close()

# ---- 4. after a draft is saved: off in tests by default, on with LEARN_FROM_EDITS=1 (in the background)
check(not email_memory.learning_on(), "learning is off locally and in tests")
email_memory.remember_ai(other, 7, REC, "Data Analyst", AI.replace("Arun", "Bina"))
email_memory.remember_final(other, 7, REC, "Data Analyst", FINAL.replace("Arun", "Bina"))
time.sleep(0.5)
conn = models.get_db_connection()
check(not consultant_rules.list_for(conn, other, "email"), "nothing learned while learning is off")
conn.close()
os.environ["LEARN_FROM_EDITS"] = "1"
check(email_memory.learning_on(), "LEARN_FROM_EDITS=1 turns it on")
email_memory.remember_ai(other, 8, REC, "Data Analyst", AI.replace("Arun", "Bina"))
email_memory.remember_final(other, 8, REC, "Data Analyst", FINAL.replace("Arun", "Bina"))
for _ in range(50):
    conn = models.get_db_connection()
    got = [r["text"] for r in consultant_rules.list_for(conn, other, "email")]
    conn.close()
    if got:
        break
    time.sleep(0.1)
check(got == PREFS, f"saving the draft learns in the background: {got}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: learning from edits - habits saved as this consultant's email rules (never job facts), no duplicates, "
      "tiny edits skipped, the AI sees saved rules, runs in the background after a draft (on for the live site only).")
