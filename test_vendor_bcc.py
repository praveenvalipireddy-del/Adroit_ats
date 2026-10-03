"""Paste Requirement & Draft with vendor BCC: the ticked vendor contacts go in the draft's Bcc header,
re-checked on the server (only the recruiter's own, active contacts, never the To address, at most
10); last-emailed is recorded; the match route lists emails not in the recruiter's list (for
"Save to my vendors"); the pasted job is saved without invented rate / title / company.
Gmail is never contacted: imaplib.IMAP4_SSL is replaced by a stub that captures the message.
Standalone; throwaway SQLite DB. People / companies are labelled test fixtures.
Run: python test_vendor_bcc.py
"""
import email
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_vendor_bcc.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import models  # noqa: E402
import vendors  # noqa: E402

failures, captured = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        captured.append(raw)

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP


def uid(u):
    return u["id"] if isinstance(u, dict) else u


ASHA = {"id": uid(models.create_user("Asha Bcctest", "bcc-asha@example.invalid", "Bcc-1", role="Recruiter")), "name": "Asha Bcctest", "role": "Recruiter"}
RAVI = {"id": uid(models.create_user("Ravi Bcctest", "bcc-ravi@example.invalid", "Bcc-2", role="Recruiter")), "name": "Ravi Bcctest", "role": "Recruiter"}
cid = models.create_candidate("Kumar Bcctest", "kumar-bcc@example.invalid", title="Java Developer", gmail_account="kumar-bcc@example.invalid",
                              assigned_user_id=ASHA["id"])
models.update_candidate(cid, gmail_app_password="test-app-password-not-real")

conn = models.get_db_connection()
rows = [{"company": "Vendor One Test Inc", "name": f"Contact {i:02d}", "email": f"c{i:02d}@vendor-one.example"} for i in range(1, 13)]
rows.append({"company": "Vendor One Test Inc", "name": "The Poster", "email": "poster@vendor-one.example"})
vendors.import_rows(conn, ASHA["id"], rows)
vendors.import_rows(conn, RAVI["id"], [{"company": "Vendor One Test Inc", "name": "Ravi's", "email": "ravis@vendor-one.example"}])
cur = conn.cursor()
cur.execute("SELECT id, email FROM vendor_contacts")
ID = {r["email"]: r["id"] for r in cur.fetchall()}
cur.execute("UPDATE vendor_contacts SET status = 'unsubscribed' WHERE email = 'c12@vendor-one.example'")
conn.commit()
conn.close()

client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = ASHA

REQ = """Job Title: Java Developer
Location: Dallas, TX (Hybrid)
Experience: 5+ years
Please send resumes to poster@vendor-one.example, cc newperson@vendor-one.example
"""

# ---- match: domain, To excluded client-side but listed; unknown emails
m = client.post("/api/vendors/match", json={"emails": ["poster@vendor-one.example", "newperson@vendor-one.example"]}).get_json()
check(m["matched_by"] == "domain" and len(m["contacts"]) == 12, f"12 active own contacts (c12 unsubscribed, Ravi's excluded): {len(m['contacts'])}")
check(m["unknown_emails"] == ["newperson@vendor-one.example"], f"unknown emails: {m['unknown_emails']}")
check(m["max_bcc"] == 10, "max_bcc")

# ---- draft: tick 11 incl. the poster, one of Ravi's, the unsubscribed one, and junk
ticked = [ID["poster@vendor-one.example"], ID["ravis@vendor-one.example"], ID["c12@vendor-one.example"], "x"] + \
         [ID[f"c{i:02d}@vendor-one.example"] for i in range(1, 12)]
r = client.post("/api/outreach/paste-and-draft", json={"candidate_id": cid, "raw_jd_text": REQ, "recruiter_email": "poster@vendor-one.example",
                                                         "job_title": "Java Developer", "company": "", "bcc_contact_ids": ticked})
d = r.get_json()
check(r.status_code == 200 and d.get("success"), f"draft created: {d}")
check(len(captured) == 1, "one draft appended")
msg = email.message_from_bytes(captured[-1])
bcc = [a.strip() for a in (msg["Bcc"] or "").split(",") if a.strip()]
want = [f"c{i:02d}@vendor-one.example" for i in range(1, 11)]
check(bcc == want, f"Bcc = first 10 of own active contacts, poster (To) / Ravi's / unsubscribed dropped: {bcc}")
check(msg["To"] == "poster@vendor-one.example", f"To: {msg['To']}")
check(d.get("bcc") == want, f"response lists the BCC: {d.get('bcc')}")
check("ravis@vendor-one.example" not in captured[-1].decode("utf-8", "replace"), "Ravi's contact appears nowhere in the draft")

conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT email, last_emailed_at, last_emailed_note FROM vendor_contacts WHERE last_emailed_at IS NOT NULL ORDER BY email")
emailed = cur.fetchall()
check([e["email"] for e in emailed] == want and "Java Developer - Kumar Bcctest" == emailed[0]["last_emailed_note"],
      f"last emailed recorded for the 10: {[e['email'] for e in emailed]}")
cur.execute("SELECT salary, location, job_type, company FROM jobs WHERE id = ?", (d["job_id"] if not isinstance(d["job_id"], dict) else d["job_id"]["id"],))
job = dict(cur.fetchone())
conn.close()
check(job["salary"] in ("", None) and job["location"] in ("", None) and "C2C" not in (job["job_type"] or ""), f"no invented rate/location/type: {job}")
check(job["company"] in ("Vendor-one", "", None), f"company only from the text/email: {job}")

# ---- no BCC when nothing ticked; old callers (no bcc_contact_ids) unchanged
r = client.post("/api/outreach/paste-and-draft", json={"candidate_id": cid, "raw_jd_text": REQ, "recruiter_email": "poster@vendor-one.example",
                                                         "job_title": "Java Developer", "bcc_contact_ids": []})
check(r.status_code == 200 and email.message_from_bytes(captured[-1])["Bcc"] is None, "nothing ticked -> no Bcc header")
r = client.post("/api/outreach/paste-and-draft", json={"candidate_id": cid, "raw_jd_text": REQ, "recruiter_email": "poster@vendor-one.example",
                                                         "job_title": "Java Developer"})
check(r.status_code == 200 and email.message_from_bytes(captured[-1])["Bcc"] is None, "old request shape -> no Bcc header")

# ---- title is required (no invented "Software Engineering Specialist")
r = client.post("/api/outreach/paste-and-draft", json={"candidate_id": cid, "raw_jd_text": "please send resumes to a@vendor-one.example",
                                                         "recruiter_email": "a@vendor-one.example", "job_title": ""})
check(r.status_code == 400 and "title" in r.get_json()["error"].lower(), f"missing title: {r.get_json()}")
ex = app_module.extract_details_from_raw_jd("hello a@gmail.com")
check(ex["salary"] == "" and ex["title"] == "" and ex["company"] == "", f"extractor invents nothing: {ex}")

# ---- the draft builder itself never puts the To address in Bcc and de-duplicates
captured.clear()
job_id = models.save_or_update_scraped_job({"title": "Bcc Unit Test", "company": "X", "source": "Manual Paste", "url": "",
                                            "recruiter_email": "to@vendor-one.example", "description": "x"})
job_id = job_id["id"] if isinstance(job_id, dict) else job_id
res = gm.create_candidate_draft(cid, job_id, bcc=["TO@vendor-one.example", "a@vendor-one.example", "a@vendor-one.example", "bad"])
check(res["success"] and email.message_from_bytes(captured[-1])["Bcc"] == "a@vendor-one.example", f"builder bcc cleanup: {res.get('bcc')}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: vendor BCC on Paste & Draft - own active contacts only, To excluded, cap 10, last-emailed, unknown emails, no invented job data.")
