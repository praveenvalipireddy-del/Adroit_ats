"""Gmail drafts must attach the consultant's resume even when it is only on disk under an OLD
server path (consultants created before resumes were stored in the database).

Root cause this guards against: draft creation looked in the database, then at the exact stored
resume_path. That absolute path is from whichever server saved it; after a Render redeploy it no
longer exists, so the draft was created WITHOUT the resume (reported for Praveen's consultant). The
Resume Optimizer already had the better lookup; both now share models.find_resume_file.

Gmail is never contacted: imaplib.IMAP4_SSL is replaced by a stub that captures the message.
The test resume file is generated, labelled, and deleted afterwards.
Run: python test_draft_resume_attachment.py
"""
import email
import io
import os
import sys
import tempfile
import uuid

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_draft.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import docx  # noqa: E402

import gmail_multi_manager as gm  # noqa: E402
import models  # noqa: E402

models.init_db()
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


captured = []


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

d = docx.Document()
d.add_paragraph("PRAVEEN TEST RESUME (fixture)")
buf = io.BytesIO()
d.save(buf)
fname = f"test_only_{uuid.uuid4().hex[:8]}_resume.docx"
local = os.path.join(models.RESUMES_DIR, fname)
os.makedirs(models.RESUMES_DIR, exist_ok=True)
with open(local, "wb") as fh:
    fh.write(buf.getvalue())

try:
    cid = models.create_candidate("Praveen Drafttest", "praveen-draft@example.invalid", title="AI Engineer",
                                  resume_filename=fname, resume_path=f"/opt/render/project/src/data/resumes/{fname}",
                                  gmail_account="praveen-draft@example.invalid")
    models.update_candidate(cid, gmail_app_password="test-app-password-not-real")
    job = models.save_or_update_scraped_job({"title": "AI Engineer Draft Test", "company": "Draft Co", "source": "Manual Paste",
                                             "url": "", "recruiter_email": "rec@example.invalid",
                                             "description": "Pasted test requirement for an AI engineer."})
    job_id = job["id"] if isinstance(job, dict) else job

    res = gm.create_candidate_draft(cid, job_id, custom_to_email="rec@example.invalid")
    check(res.get("success") and res.get("resume_attached") is True, f"draft should attach the resume: {res}")
    check(captured, "a draft should have been appended to Gmail (stub)")
    if captured:
        msg = email.message_from_bytes(captured[-1])
        names = [part.get_filename() for part in msg.walk() if part.get_filename()]
        check(names == [fname], f"attachment in the draft: {names}")
    check(models.get_resume_file(cid) is not None, "file found on disk should be copied into the database (survives deploys)")

    # no resume anywhere -> honest note, still a draft
    cid2 = models.create_candidate("No Resume Drafttest", "nores-draft@example.invalid", gmail_account="nores-draft@example.invalid")
    models.update_candidate(cid2, gmail_app_password="test-app-password-not-real")
    res2 = gm.create_candidate_draft(cid2, job_id, custom_to_email="rec@example.invalid")
    check(res2.get("success") and res2.get("resume_attached") is False and "No resume file" in res2.get("resume_note", ""),
          f"no-resume consultant: {res2}")
finally:
    try:
        os.remove(local)
    except OSError:
        pass

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: Gmail drafts attach the resume found by the shared lookup (old server path -> this server's data/resumes); honest note when none.")
