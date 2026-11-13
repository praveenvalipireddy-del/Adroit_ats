"""Message a sourced candidate on LinkedIn (server side): the AI draft and "messaged on LinkedIn" on the tracker card.

1. trim() fits LinkedIn's limits at a sentence / word end.
2. AI draft: role required; the AI's JSON message is fitted to the note limit; an AI failure is a clear 502
   (the recruiter still has the template). The AI is a stub - no paid call.
3. Marked sent (from Job Hunt, and from the extension with its connection key): logged as 'linkedin' activity,
   a New candidate becomes Contacted, the recruiter becomes the owner, the card carries linkedin_sent.
   A second mark by someone else keeps the owner and the status. Bad mode / unknown candidate / no key refused.
Standalone script on a throwaway SQLite DB; no network. People are labelled test fixtures.
Run: python test_linkedin_message.py
"""
import json
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_li_msg.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import linkedin_ingest as li  # noqa: E402
import linkedin_outreach  # noqa: E402
import models  # noqa: E402
import resume_bot  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def uid(u):
    return u["id"] if isinstance(u, dict) else u


REC = {"id": uid(models.create_user("Lima Recruiter", "li-rec@example.invalid", "LiMsg-1", role="Recruiter")), "name": "Lima Recruiter", "role": "Recruiter"}
OTHER = {"id": uid(models.create_user("Oscar Other", "li-other@example.invalid", "LiMsg-2", role="Recruiter")), "name": "Oscar Other", "role": "Recruiter"}

li.ingest_profiles(li.ApifyProfileProvider().to_profiles([{
    "linkedinUrl": "https://www.linkedin.com/in/li-msg-fixture.example.invalid", "firstName": "Asha", "lastName": "Limsgtest",
    "headline": "Senior Java Developer", "location": {"linkedinText": "Hyderabad, India"},
    "currentPosition": [{"position": "Senior Java Developer", "companyName": "Fixture Co"}],
    "education": [{"schoolName": "JNTUH", "degree": "B.Tech", "endDate": {"year": 2018}}]}]), "apify", None)
conn = models.get_db_connection()
PID = conn.cursor().execute("SELECT id FROM linkedin_profiles WHERE name LIKE 'Asha%'").fetchone()["id"]
conn.close()

client = app_module.app.test_client()
anon = app_module.app.test_client()


def login(u):
    with client.session_transaction() as s:
        s["user"] = {**u, "email": "x@example.invalid"}


# ---- 1. trim
check(linkedin_outreach.trim("Short one.", 300) == "Short one.", "short text unchanged")
long_text = "First sentence is here. " + "word " * 80
t = linkedin_outreach.trim(long_text, 100)
check(len(t) <= 100 and t == "First sentence is here.", f"cut at sentence end: {t!r}")
t = linkedin_outreach.trim("alpha " * 60, 50)
check(len(t) <= 50 and not t.endswith(" ") and t.split()[-1] == "alpha", f"cut at word end: {t!r}")
check(linkedin_outreach.first_name("Asha Limsgtest") == "Asha", "first name")

# ---- 2. AI draft (stubbed AI)
check(anon.post(f"/api/sourcing/profiles/{PID}/linkedin-draft", json={"role": "Java"}).status_code == 401, "draft needs login")
login(REC)
r = client.post(f"/api/sourcing/profiles/{PID}/linkedin-draft", json={"role": "  "})
check(r.status_code == 400 and "role" in r.get_json()["error"], "role required")

seen = {}


def fake_chat(system, prompt, max_tokens=3000):
    seen["prompt"] = prompt
    return json.dumps({"message": "Hi Asha, your Senior Java Developer work at Fixture Co fits a Java Lead role I'm hiring for. "
                                  "Open to a quick chat this week? " + "Extra words " * 40 + "- Lima"}), "stub"


resume_bot.generate_chat = fake_chat
r = client.post(f"/api/sourcing/profiles/{PID}/linkedin-draft", json={"role": "Java Lead", "mode": "note", "company": "Fixture Staffing"})
d = r.get_json()
check(r.status_code == 200 and d["limit"] == 300 and len(d["message"]) <= 300, f"note fitted to 300: {d}")
check(d["message"].startswith("Hi Asha") and d["message"].endswith("?"), f"cut at a sentence end: {d['message']!r}")
facts = json.loads(seen["prompt"].split("FACTS:\n", 1)[1])
check(facts["role"] == "Java Lead" and facts["candidate_first_name"] == "Asha" and facts["recruiter_name"] == "Lima Recruiter"
      and facts["recruiter_company"] == "Fixture Staffing" and facts["candidate_company"] == "Fixture Co", f"facts sent to the AI: {facts}")
r = client.post(f"/api/sourcing/profiles/{PID}/linkedin-draft", json={"role": "Java Lead", "mode": "message"})
check(r.status_code == 200 and r.get_json()["limit"] == 1200, "message mode limit 1200")


def broken_chat(*a, **k):
    raise RuntimeError("provider down")


resume_bot.generate_chat = broken_chat
r = client.post(f"/api/sourcing/profiles/{PID}/linkedin-draft", json={"role": "Java Lead"})
check(r.status_code == 502 and "template" in r.get_json()["error"], "AI failure -> 502 with a way forward")
check(client.post("/api/sourcing/profiles/999999/linkedin-draft", json={"role": "x"}).status_code == 404, "unknown candidate 404")

# ---- 3. mark sent
card = client.get(f"/api/sourcing/profiles/{PID}/card").get_json()
check(card["linkedin_sent"] is None and card["status"] == "New", "starts unmessaged")
check(client.post(f"/api/sourcing/profiles/{PID}/linkedin-sent", json={"mode": "shout"}).status_code == 400, "bad mode refused")
check(client.post("/api/sourcing/profiles/999999/linkedin-sent", json={"mode": "note"}).status_code == 404, "unknown candidate 404")
r = client.post(f"/api/sourcing/profiles/{PID}/linkedin-sent", json={"mode": "note"})
c = r.get_json()
check(r.status_code == 200 and c["status"] == "Contacted" and c["owner_user_id"] == REC["id"], f"New -> Contacted, owner claimed: {c.get('status')}")
check(c["linkedin_sent"] and c["linkedin_sent"]["author"] == "Lima Recruiter"
      and c["linkedin_sent"]["text"] == "Connection request + note sent on LinkedIn", f"linkedin_sent: {c['linkedin_sent']}")
kinds = [t["kind"] for t in c["thread"]]
check("linkedin" in kinds and "owner" in kinds and "status" in kinds, f"activity kinds: {kinds}")

# extension key (Bearer)
KEY = client.post("/api/extension/key").get_json()["key"]
check(anon.post(f"/api/ext/sourcing/{PID}/linkedin-sent", json={"mode": "message"}).status_code == 401, "extension needs a key")
check(anon.post(f"/api/ext/sourcing/{PID}/linkedin-sent", json={"mode": "message"},
                headers={"Authorization": "Bearer nonsense"}).status_code == 401, "bad key refused")
login(OTHER)
KEY_OTHER = client.post("/api/extension/key").get_json()["key"]
r = anon.post(f"/api/ext/sourcing/{PID}/linkedin-sent", json={"mode": "message"}, headers={"Authorization": "Bearer " + KEY_OTHER})
c = r.get_json()
check(r.status_code == 200 and c["owner_user_id"] == REC["id"] and c["status"] == "Contacted", "second sender keeps owner and status")
check(c["linkedin_sent"]["author"] == "Oscar Other" and c["linkedin_sent"]["text"] == "Message sent on LinkedIn", "latest send shown")
check([t["kind"] for t in c["thread"]].count("linkedin") == 2, "both sends in the history")
check(KEY != KEY_OTHER, "keys differ per user")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS test_linkedin_message")
