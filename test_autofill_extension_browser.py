"""Job Hunt Autofill (Chrome extension) end to end, with labelled fixtures and no paid calls (the AI is stubbed).

1. Connection key: only from a logged-in session; a bad key, or a key after a password change, is refused.
2. /api/ext/me lists only the consultants this recruiter may act on; another recruiter's consultant is 404 everywhere.
3. Autofill answers are saved on the consultant (unknown keys dropped, yes/no normalised) and feed the profile
   (names split, voluntary questions default to "Decline to self-identify", nothing invented).
4. Resume download, AI answers (an answer outside the given options is dropped), match (AI, or the free keyword
   check when the AI fails), Save -> Applications "Saved", "Yes, I applied" -> "Applied" in portal.
5. The extension zip: manifest + panel + filler, and config.js pointing at this server.
6. The real extension loaded in Edge: connect with the key, the panel reads the job page, Autofill fills the form
   (profile fields + resume + AI answer), "Yes, I applied" marks the application Applied.
7. Job Hunt in Edge: the consultant form's "Autofill answers" section saves; "Autofill extension" shows the key.
Run: python test_autofill_extension_browser.py
"""
import base64
import io
import json
import os
import socket
import sys
import tempfile
import threading
import zipfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_autofill.db")
os.environ["DATABASE_URL"] = ""
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def wait_text(pg, sel, text, timeout=15):
    """Poll an element's text (the extension page's CSP blocks Playwright's string predicates)."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        try:
            if text in pg.inner_text(sel):
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {text!r} in {sel}: {pg.inner_text(sel)!r}")


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "autofill-rec@example.invalid", "Autofill-1"
REC = uid(models.create_user("Ava Autofilltest", EMAIL, PW, role="Recruiter", region="USA"))
OTHER = uid(models.create_user("Otto Otherrec", "autofill-other@example.invalid", "Other-1", role="Recruiter", region="USA"))
K = models.create_candidate("Karun Fixturetest", "karun.fixture@example.invalid", phone="+1 469 555 0100", title="Data Analyst",
                            primary_skills="SQL, Power BI, Python, Tableau", experience_years=6, target_rate="$60/hr (C2C)",
                            visa_status="OPT / STEM", location="Dallas, TX (Hybrid)", country="United States", assigned_user_id=REC,
                            resume_text="Karun Fixturetest - Data Analyst. 6 years of SQL, Power BI and Python reporting. (labelled test fixture)")
X = models.create_candidate("Other Consultant", "other.fixture@example.invalid", title="Java Dev", country="United States", assigned_user_id=OTHER)
RESUME_BYTES = b"PK fixture resume bytes (not a real docx)"
models.save_resume_file(K, "Karun_Fixturetest.docx", RESUME_BYTES)

client = app_module.app.test_client()
anon = app_module.app.test_client()

# ---- 1. key
check(anon.post("/api/extension/key").status_code == 401, "no key without login")
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Ava", "role": "Recruiter", "email": EMAIL}
KEY = client.post("/api/extension/key").get_json()["key"]
H = {"Authorization": "Bearer " + KEY}
check(anon.get("/api/ext/me").status_code == 401 and anon.get("/api/ext/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401,
      "missing / bad key refused")

# ---- 2. consultants
me = anon.get("/api/ext/me", headers=H).get_json()
check([c["name"] for c in me["consultants"]] == ["Karun Fixturetest"] and me["consultants"][0]["has_resume"], f"only my consultants: {me}")
for path in (f"/api/ext/consultants/{X}", f"/api/ext/consultants/{X}/resume"):
    check(anon.get(path, headers=H).status_code == 404, f"other recruiter's consultant hidden: {path}")
check(anon.post("/api/ext/applied", headers=H, json={"candidate_id": X, "job": {"title": "t", "url": "https://x.example/1"}}).status_code == 404,
      "can't log applications for another recruiter's consultant")

# ---- 3. answers saved + profile
r = client.put(f"/api/consultants/{K}", json={"autofill_answers": {"linkedin_url": "https://www.linkedin.com/in/karun-fixturetest",
               "work_authorized": "yes", "needs_sponsorship": "NO", "notice_period": "2 weeks", "evil_key": "x", "gender": ""}})
check(r.status_code == 200, f"answers saved: {r.get_json()}")
stored = json.loads(models.get_candidate_by_id(K)["autofill_answers"])
check(stored == {"linkedin_url": "https://www.linkedin.com/in/karun-fixturetest", "work_authorized": "Yes", "needs_sponsorship": "No",
                 "notice_period": "2 weeks"}, f"cleaned answers: {stored}")
prof = anon.get(f"/api/ext/consultants/{K}", headers=H).get_json()
p = prof["profile"]
check(p["first_name"] == "Karun" and p["last_name"] == "Fixturetest" and p["city"] == "Dallas" and p["expected_salary"] == "$60/hr (C2C)"
      and p["work_authorized"] == "Yes" and p["gender"] == "Decline to self-identify" and p["resume_filename"] == "Karun_Fixturetest.docx",
      f"profile: {p}")
check("university" not in p and "address_line" not in p, "unknown answers are left out, not invented")
check(prof["missing"] == [], f"nothing important missing: {prof['missing']}")

# ---- 4. resume, answers, match, save, applied
res = anon.get(f"/api/ext/consultants/{K}/resume", headers=H).get_json()
check(base64.b64decode(res["base64"]) == RESUME_BYTES and res["filename"] == "Karun_Fixturetest.docx", "resume bytes")

calls = []


def fake_ai(system, prompt, max_tokens):
    calls.append(system[:30])
    if "fill job application questions" in system:
        import re as _re
        why = _re.search(r'id "([\w-]+)": Why do you want', prompt)
        return json.dumps({"answers": {why.group(1) if why else "qwhy": "I build SQL and Power BI reports (fixture).", "qyrs": "Fifty", "qclear": ""}})
    return json.dumps({"score": 81, "strengths": ["SQL", "Power BI"], "missing": ["Snowflake"], "summary": "Strong reporting fit (fixture)."})


app_module._ext_ai = fake_ai
qs = [{"id": "qwhy", "label": "Why do you want to work here?"}, {"id": "qyrs", "label": "Years with SQL", "options": ["1-3", "4-6", "7+"]},
      {"id": "qclear", "label": "Security clearance?"}]
JOB = {"title": "Senior Data Analyst (Fixture)", "company": "Sample Fixture Co", "location": "Dallas, TX",
       "url": "https://boards.greenhouse.io/samplefixtureco/jobs/1", "description": "We need SQL, Power BI and Python. " * 5}
ans = anon.post("/api/ext/answers", headers=H, json={"candidate_id": K, "job": JOB, "questions": qs}).get_json()
check(ans["answers"] == {"qwhy": "I build SQL and Power BI reports (fixture)."}, f"answers parsed, invalid option + empty dropped: {ans}")
m = anon.post("/api/ext/match", headers=H, json={"candidate_id": K, "job": JOB}).get_json()
check(m["score"] == 81 and m["method"] == "ai" and m["missing"] == ["Snowflake"], f"AI match: {m}")


def broken_ai(*a, **k):
    raise RuntimeError("quota (fixture)")


app_module._ext_ai = broken_ai
m2 = anon.post("/api/ext/match", headers=H, json={"candidate_id": K, "job": JOB}).get_json()
check(m2["method"] == "keywords" and m2["score"] == 75 and m2["missing"] == ["Tableau"], f"keyword fallback when the AI fails: {m2}")
app_module._ext_ai = fake_ai

sv = anon.post("/api/ext/save-job", headers=H, json={"candidate_id": K, "job": JOB}).get_json()
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT stage FROM applications WHERE id = ?", (sv["application_id"],))
check(cur.fetchone()[0] == "Saved", "Save -> Applications Saved")
cur.execute("SELECT source, full_description, country FROM jobs WHERE id = ?", (sv["job_id"],))
row = cur.fetchone()
check(row[0] == "Company site (Greenhouse)" and row[1].startswith("We need SQL") and row[2] == "United States", f"job saved: {tuple(row)}")
conn.close()
check(anon.post("/api/ext/save-job", headers=H, json={"candidate_id": K, "job": {"title": "", "url": ""}}).status_code == 400, "no title -> refused")

# ---- 5. zip
z = client.get("/extension/jobhunt-autofill.zip")
names = zipfile.ZipFile(io.BytesIO(z.data)).namelist()
check(z.status_code == 200 and {"jobhunt-autofill/manifest.json", "jobhunt-autofill/panel.js", "jobhunt-autofill/filler.js",
                                "jobhunt-autofill/config.js", "jobhunt-autofill/icons/icon128.png"} <= set(names), f"zip contents: {names}")
check(b'"http://localhost"' in zipfile.ZipFile(io.BytesIO(z.data)).read("jobhunt-autofill/config.js"), "config.js points at this server")
check(anon.get("/extension/jobhunt-autofill.zip").status_code == 302, "zip needs login")

# ---- 6 + 7. browser
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"

ext_dir = os.path.join(tmp_dir, "ext")
with zipfile.ZipFile(io.BytesIO(z.data)) as zf:
    zf.extractall(ext_dir)
ext_dir = os.path.join(ext_dir, "jobhunt-autofill")
with open(os.path.join(ext_dir, "config.js"), "w") as f:      # the zip names the server it was downloaded from
    f.write(f"const JH_DEFAULT_SERVER = {json.dumps(BASE)};\n")

FORM = """<!doctype html><html><head><title>Senior Data Analyst (Fixture)</title>
<script type="application/ld+json">{"@type":"JobPosting","title":"Senior Data Analyst (Fixture)","hiringOrganization":{"name":"Sample Fixture Co"},
"jobLocation":{"address":{"addressLocality":"Dallas","addressRegion":"TX"}},"description":"We need SQL, Power BI and Python for client reporting dashboards and data models. Labelled test fixture page."}</script>
</head><body><h1>Senior Data Analyst (Fixture)</h1><form onsubmit="window.submitted=true;return false">
<label for="fn">First Name *</label><input id="fn" required>
<label for="ln">Last Name *</label><input id="ln" required>
<label for="em">Email *</label><input id="em" required>
<label for="li">LinkedIn Profile</label><input id="li">
<label for="cv">Resume/CV *</label><input id="cv" type="file">
<label for="au">Are you legally authorized to work in the United States? *</label><select id="au" required><option value="">Select...</option><option>Yes</option><option>No</option></select>
<label for="why">Why do you want to work here? *</label><textarea id="why" required></textarea>
<button id="go">Submit</button></form></body></html>"""

js_errors, shots = [], {}
try:
    with sync_playwright() as pw:
        # -- 6. the real extension
        ctx = pw.chromium.launch_persistent_context(os.path.join(tmp_dir, "profile"), channel="msedge", headless=True,
                                                    args=[f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = sw.url.split("/")[2]
        ctx.route("https://boards.greenhouse.io/samplefixtureco/jobs/1", lambda r: r.fulfill(status=200, content_type="text/html", body=FORM))
        job_page = ctx.pages[0] if ctx.pages else ctx.new_page()
        job_page.goto("https://boards.greenhouse.io/samplefixtureco/jobs/1")
        panel = ctx.new_page()
        panel.on("pageerror", lambda e: js_errors.append("panel: " + str(e)))
        panel.goto(f"chrome-extension://{ext_id}/panel.html")
        for _ in range(50):
            if panel.input_value("#in-server"):
                break
            panel.wait_for_timeout(100)
        check(panel.input_value("#in-server") == BASE, f"server prefilled from the zip: {panel.input_value('#in-server')}")
        panel.fill("#in-key", "not-a-key")
        panel.click("#btn-connect")
        wait_text(panel, '#connect-msg', 'not valid')
        check("not valid" in panel.inner_text("#connect-msg"), "bad key message")
        panel.fill("#in-key", KEY)
        panel.click("#btn-connect")
        panel.wait_for_selector("#view-main:not([hidden])", timeout=10000)
        wait_text(panel, '#job-title', 'Senior Data Analyst')
        check(panel.inner_text("#job-company") == "Sample Fixture Co", "panel shows the job's company")
        check("Karun Fixturetest" in panel.inner_text("#sel-consultant"), "consultant picker")
        panel.click("#row-info")
        wait_text(panel, '#info-box', 'karun-fixturetest')
        panel.click("#btn-match")
        wait_text(panel, '#match-box', '81%')
        panel.click("#btn-autofill")
        panel.wait_for_selector("#applied-card:not([hidden])", timeout=20000)
        result = panel.inner_text("#result-box")
        check("filled from Karun Fixturetest" in result and "Resume attached" in result and "1 answer written by AI" in result, f"result box: {result!r}")
        v = lambda sel: job_page.eval_on_selector(sel, "e => e.value")  # noqa: E731
        check(v("#fn") == "Karun" and v("#ln") == "Fixturetest" and v("#em") == "karun.fixture@example.invalid"
              and v("#li") == "https://www.linkedin.com/in/karun-fixturetest" and v("#au") == "Yes", "the real extension filled the form")
        check(job_page.eval_on_selector("#cv", "e => e.files.length && e.files[0].name") == "Karun_Fixturetest.docx", "resume attached by the extension")
        check(v("#why") == "I build SQL and Power BI reports (fixture).", "AI answer filled by the extension")
        check(not job_page.evaluate("!!window.submitted"), "the extension never submits")
        shots["panel"] = os.path.join(tmp_dir, "panel.png")
        panel.set_viewport_size({"width": 400, "height": 900})
        panel.screenshot(path=shots["panel"], full_page=True)
        shots["form"] = os.path.join(tmp_dir, "form.png")
        job_page.screenshot(path=shots["form"], full_page=True)
        panel.click("#btn-applied")
        wait_text(panel, '#applied-card', 'Marked as Applied')

        # -- a job page with no form: Autofill clicks the site's Apply, then fills; a 2-step form: "Next page" fills step 2
        STEP1 = """<html><head><title>AI Developer II (Fixture) | Careers</title></head><body><h1>AI Developer II (Fixture)</h1>
          <p>Labelled fixture job page for the extension test. SQL and Python reporting.</p>
          <a href="https://fixture.example.invalid/job/9/apply/section/1">Apply Now</a></body></html>"""
        SEC1 = """<html><head><title>AI Developer II (Fixture) | Careers</title></head><body><h1>Contact Information</h1>
          <label for="fn">First Name *</label><input id="fn" required><label for="em">Email *</label><input id="em" required>
          <a href="https://fixture.example.invalid/job/9/apply/section/2">Next</a></body></html>"""
        SEC2 = """<html><head><title>AI Developer II (Fixture) | Careers</title></head><body><h1>Work Summary</h1>
          <label for="li">LinkedIn Profile</label><input id="li"><button onclick="window.submitted=true">Submit</button></body></html>"""
        for path, body in (("/job/9", STEP1), ("/job/9/apply/section/1", SEC1), ("/job/9/apply/section/2", SEC2)):
            ctx.route("https://fixture.example.invalid" + path, (lambda b: lambda r: r.fulfill(status=200, content_type="text/html", body=b))(body))
        job_page.goto("https://fixture.example.invalid/job/9")
        job_page.bring_to_front()
        panel.bring_to_front()
        wait_text(panel, '#job-title', 'AI Developer II')
        panel.click("#btn-autofill")
        wait_text(panel, '#result-box', 'Next page', timeout=30)
        check("/apply/section/1" in job_page.url and job_page.eval_on_selector("#fn", "e => e.value") == "Karun"
              and job_page.eval_on_selector("#em", "e => e.value") == "karun.fixture@example.invalid", "Apply clicked, step 1 filled")
        check(panel.inner_text("#job-title") == "AI Developer II (Fixture)", f"job kept on the apply steps: {panel.inner_text('#job-title')!r}")
        panel.click("#btn-next-step")
        wait_text(panel, '#result-box', 'filled from', timeout=30)
        for _ in range(50):
            if "/section/2" in job_page.url and job_page.eval_on_selector("#li", "e => e.value"):
                break
            job_page.wait_for_timeout(200)
        check(job_page.eval_on_selector("#li", "e => e.value") == "https://www.linkedin.com/in/karun-fixturetest", "Next page -> step 2 filled")
        check(panel.inner_text("#job-title") != "Work Summary", "a step heading never becomes the job title")
        check(not job_page.evaluate("!!window.submitted"), "Submit never clicked on the last step")
        check(not panel.is_hidden("#applied-card"), "'Did you apply?' shown on the last step")
        ctx.close()

        conn = models.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT a.stage, a.apply_method FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.candidate_id = ? AND j.url = ?",
                    (K, "https://boards.greenhouse.io/samplefixtureco/jobs/1"))
        row = cur.fetchone()
        check(row is not None and tuple(row) == ("Applied", "portal"), f"'Yes, I applied' -> Applied in portal: {row and tuple(row)}")
        conn.close()

        # -- 7. Job Hunt screens
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        page.on("pageerror", lambda e: js_errors.append("dashboard: " + str(e)))
        page.goto(BASE + "/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.9" in page.content(), "cache-buster not bumped to 5.99.9")
        page.click("#nav-extension")
        page.wait_for_selector("#modal-extension", state="visible")
        page.click("#btn-ext-key")
        page.wait_for_selector("#ext-key-text", timeout=10000)
        check(len(page.input_value("#ext-key-text")) > 40, "the key is shown")
        page.click("#btn-close-extension")
        page.evaluate(f"openConsultantModal(state.consultants.find(c => c.id === {K}))")
        page.wait_for_selector("#modal-consultant", state="visible")
        if not page.locator("#af-fields [data-af]").count():
            failures.append("autofill fields not rendered in the consultant form")
        else:
            page.click("#af-section summary")
            check(page.input_value("[data-af=linkedin_url]") == "https://www.linkedin.com/in/karun-fixturetest", "saved answers shown")
            check(page.input_value("[data-af=work_authorized]") == "Yes", "yes/no shown")
            page.fill("[data-af=university]", "Fixture State University")
            shots["form_dash"] = os.path.join(tmp_dir, "consultant_form.png")
            page.screenshot(path=shots["form_dash"])
            page.click("#btn-save-consultant")
            page.wait_for_function("document.getElementById('modal-consultant').style.display === 'none'", timeout=10000)
            check(json.loads(models.get_candidate_by_id(K)["autofill_answers"]).get("university") == "Fixture State University",
                  "an answer edited in Job Hunt is saved")
        browser.close()
finally:
    server.shutdown()

# ---- 1b. password change revokes the key
models.update_user_password(REC, "Changed-2")
check(anon.get("/api/ext/me", headers=H).status_code == 401, "a password change revokes the key")

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshots:", shots)
    sys.exit(1)
print(f"PASS: Autofill extension - key + permissions, answers saved and cleaned, profile, resume, AI answers / match (+ free fallback), "
      f"Save / Applied in Applications, zip, the real extension in Edge filled the form and logged Applied, Job Hunt form + key. Screenshots: {shots}")
