"""A consultant whose resume FILE exists but whose text was never extracted must still work in the
Resume Optimizer.

Root cause this guards against: consultants created by the old startup seeding (e.g. Karun) have
resume_path/resume_filename but resume_text = NULL. The optimizer reads resume_text only, so it said
"No resume is on file for Karun" although the file was there. Also, the stored resume_path is an
absolute path from whichever server saved it, which changes between deploys.

Standalone script on a throwaway SQLite DB. Test resume files are generated, clearly labelled, and
deleted afterwards. No AI keys are set, so the optimizer uses its local keyword scan - no network.
Run: python test_resume_on_file.py
"""
import io
import os
import sys
import tempfile
import uuid

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_resume.db")
os.environ["DATABASE_URL"] = ""
for k in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.GEMINI_API_KEY = config.OPENROUTER_API_KEY = config.XAI_API_KEY = config.APIFY_API_TOKEN = ""
import docx  # noqa: E402

import app as app_module  # noqa: E402
import models  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def make_docx(text):
    d = docx.Document()
    for line in text.split("\n"):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


RESUME = "KARUN TEST RESUME (fixture)\nData Analyst - SQL, Python, Power BI, Tableau, Excel.\nBuilt reporting dashboards."
admin = models.create_user("Resume Test Admin", "resume-admin@example.invalid", "Resume-Test-1", role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {"id": admin_id, "name": "Resume Test Admin", "role": "Admin", "email": "a@example.invalid"}

created_files = []
try:
    # 1. Karun's case: file in this server's resumes folder, but resume_path from ANOTHER server, no text
    fname = f"test_only_{uuid.uuid4().hex[:8]}_resume.docx"
    local = os.path.join(app_module.RESUMES_DIR, fname)
    with open(local, "wb") as fh:
        fh.write(make_docx(RESUME))
    created_files.append(local)
    c1 = models.create_candidate("Karun Fileonly", "k1@example.invalid", title="Data Analyst", assigned_user_id=admin_id,
                                 resume_filename=fname, resume_path=f"/opt/render/project/src/data/resumes/{fname}")
    r = client.get(f"/api/consultants/{c1}")
    body = r.get_json() or {}
    check(r.status_code == 200 and "KARUN TEST RESUME" in (body.get("resume_text") or ""),
          f"file-only consultant should get resume text: {r.status_code} {str(body.get('resume_text'))[:80]!r}")
    stored = models.get_candidate_by_id(c1)
    check("KARUN TEST RESUME" in (stored.get("resume_text") or ""), "extracted text should be saved for next time")
    check(models.get_resume_file(c1) is not None, "the file should be copied into the database (survives deploys)")

    # 2. file only in the database (resume_files), no text
    c2 = models.create_candidate("Blob Onlytest", "k2@example.invalid", assigned_user_id=admin_id, resume_filename="blob.docx")
    models.save_resume_file(c2, "blob.docx", make_docx(RESUME.replace("KARUN", "BLOB")))
    r = client.get(f"/api/consultants/{c2}")
    check("BLOB TEST RESUME" in ((r.get_json() or {}).get("resume_text") or ""), "DB-stored file should give resume text")

    # 3. no resume at all -> still empty (the honest "No resume on file" message stays), no error
    c3 = models.create_candidate("No Resumetest", "k3@example.invalid", assigned_user_id=admin_id)
    r = client.get(f"/api/consultants/{c3}")
    check(r.status_code == 200 and not (r.get_json() or {}).get("resume_text"), f"no-resume consultant: {r.status_code}")

    # 4. a path outside the resumes folder is never read
    outside = os.path.join(tmp_dir, "outside_secret.txt")
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("OUTSIDE FILE MUST NOT BE READ " * 5)
    c4 = models.create_candidate("Outside Pathtest", "k4@example.invalid", assigned_user_id=admin_id,
                                 resume_filename="", resume_path=outside)
    r = client.get(f"/api/consultants/{c4}")
    check("OUTSIDE FILE" not in ((r.get_json() or {}).get("resume_text") or ""), "a resume_path outside RESUMES_DIR was read")

    # 5. the optimizer itself accepts the file-only consultant (no AI keys -> local keyword scan only)
    c5 = models.create_candidate("Karun Optimizetest", "k5@example.invalid", assigned_user_id=admin_id,
                                 resume_filename=fname, resume_path=f"/somewhere/else/{fname}")
    r = client.post("/api/resume-bot/optimize", json={"candidate_id": c5, "jd_text": "Data Analyst with SQL, Python and Power BI."})
    check(r.status_code == 200, f"optimize for a file-only consultant should not fail with 'No resume': {r.status_code} {r.get_data(as_text=True)[:200]}")
finally:
    for f in created_files:
        try:
            os.remove(f)
        except OSError:
            pass

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: resume text is extracted from a stored file (old path / DB) once and saved; outside paths ignored; optimizer works.")
