"""Job Hunt Autofill (Chrome extension) end to end, with labelled fixtures and no paid calls (the AI is stubbed).

1. Connection key: only from a logged-in session; a bad key, or a key after a password change, is refused.
2. /api/ext/me lists only the consultants this recruiter may act on; another recruiter's consultant is 404 everywhere.
3. Autofill answers are saved on the consultant (unknown keys dropped, yes/no normalised) and feed the profile
   (names split, voluntary questions default to "Decline to self-identify", nothing invented).
4. Resume download, AI answers (an answer outside the given options is dropped), match (the Tailor analysis, or the
   free keyword check when the AI fails), Save -> Applications "Saved", "Yes, I applied" -> "Applied" in portal with
   the tailored resume used, "already on this job" status.
5. The extension zip: manifest + panel + filler, and config.js pointing at this server.
6. The real extension loaded in Edge: connect with the key, the panel reads the job page, Autofill fills the form
   (profile fields + resume + AI answer), "Yes, I applied" marks the application Applied. The tailored resume stays
   with its job across tab switches, an Apply that opens another site in a new tab, and reopening the panel.
7. Job Hunt in Edge: the consultant form's "Autofill answers" section saves; "Autofill extension" shows the key.
Run: python test_autofill_extension_browser.py
"""
import base64
import io
import json
import os
import re
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
check(base64.b64decode(res["base64"]) == RESUME_BYTES and res["filename"] == "Karun_Fixturetest_Resume.docx", "resume bytes, sent as <Name>_Resume.docx")

calls = []


def fake_ai(system, prompt, max_tokens):
    calls.append(system[:30])
    if "fill job application questions" in system:
        import re as _re
        why = _re.search(r'id "([\w-]+)": Why do you want', prompt)
        return json.dumps({"answers": {why.group(1) if why else "qwhy": "I build SQL and Power BI reports (fixture).", "qyrs": "Fifty", "qclear": ""}})
    raise AssertionError("only the form's questions use this AI call now: " + system[:60])


app_module._ext_ai = fake_ai
qs = [{"id": "qwhy", "label": "Why do you want to work here?"}, {"id": "qyrs", "label": "Years with SQL", "options": ["1-3", "4-6", "7+"]},
      {"id": "qclear", "label": "Security clearance?"}]
JOB = {"title": "Senior Data Analyst (Fixture)", "company": "Sample Fixture Co", "location": "Dallas, TX",
       "url": "https://boards.greenhouse.io/samplefixtureco/jobs/1", "description": "We need SQL, Power BI and Python. " * 5}
ans = anon.post("/api/ext/answers", headers=H, json={"candidate_id": K, "job": JOB, "questions": qs}).get_json()
check(ans["answers"] == {"qwhy": "I build SQL and Power BI reports (fixture)."}, f"answers parsed, invalid option + empty dropped: {ans}")

# ---- Tailor resume: the match + suggested changes, then only the ticked ones made, saved, returned for Autofill
import resume_bot as _rb  # noqa: E402
import resume_versions as _rv  # noqa: E402
tailor_calls = []
analyze_calls = []
TAILORED_TEXT = "KARUN FIXTURETEST\nData Analyst - SQL, Power BI dashboards (tailored fixture)"


def fake_analyze(resume_text, jd_text, custom_instructions="", docx_bytes=None):
    analyze_calls.append(jd_text[:20])
    return {"ai_powered": True, "initial_match_percentage": 70, "mandatory_matched_skills": ["SQL"], "partial_match_skills": ["Power BI"],
            "mandatory_missing_skills": ["Tableau"], "review": {"initial": 70, "target": 85, "items": [
                {"id": "align", "kind": "align", "skill": "", "label": "Reword the summary for this job", "sections": [], "evidence": "", "checked": True},
                {"id": "skill:Power BI", "kind": "safe", "skill": "Power BI", "label": "Show Power BI in the skills", "sections": ["skills"],
                 "evidence": "Power BI dashboards", "checked": True},
                {"id": "skill:Tableau", "kind": "missing", "skill": "Tableau", "label": "Add Tableau", "sections": ["skills"], "evidence": "", "checked": False}]}}


def fake_tailor(resume_text, jd_text, custom_instructions="", docx_bytes=None, approved=None, **_k):
    tailor_calls.append(approved)
    return {"optimized": True, "format_preserved": False, "updated_resume_text": TAILORED_TEXT, "initial_match_percentage": 70,
            "changes": [{"op": "replace", "before": "Data Analyst - SQL", "after": "Data Analyst - SQL, Power BI dashboards"}],
            "mandatory_matched_skills": ["SQL", "Power BI"]}


_rb.analyze_for_review = fake_analyze
_rb.optimize_resume_for_jd = fake_tailor

# Match = the analysis Tailor uses: one number, its suggestions with it; the free keyword check when the AI fails
m = anon.post("/api/ext/match", headers=H, json={"candidate_id": K, "job": JOB}).get_json()
check(m["score"] == 70 and m["method"] == "ai" and m["matched"] == ["SQL"] and m["missing"] == ["Tableau"] and len(m["review"]["items"]) == 3,
      f"match from the analysis: {m}")
check(len(analyze_calls) == 1, f"one analysis for the match: {analyze_calls}")
_rb.analyze_for_review = lambda *a, **k: {"error": "quota (fixture)"}
m2 = anon.post("/api/ext/match", headers=H, json={"candidate_id": K, "job": JOB}).get_json()
check(m2["method"] == "keywords" and m2["score"] == 75 and m2["missing"] == ["Tableau"], f"keyword fallback when the AI has no answer: {m2}")


def broken_analyze(*a, **k):
    raise RuntimeError("quota (fixture)")


_rb.analyze_for_review = broken_analyze
m3 = anon.post("/api/ext/match", headers=H, json={"candidate_id": K, "job": JOB}).get_json()
check(m3["method"] == "keywords", f"keyword fallback when the AI fails: {m3}")
_rb.analyze_for_review = fake_analyze
ta = anon.post("/api/ext/tailor", headers=H, json={"candidate_id": K, "job": JOB, "mode": "analyze"}).get_json()
check(ta["match"] == 70 and [i["id"] for i in ta["review"]["items"] if i["checked"]] == ["align", "skill:Power BI"], f"tailor: suggestions: {ta}")
tp = anon.post("/api/ext/tailor", headers=H, json={"candidate_id": K, "job": JOB, "mode": "apply", "approved": ["align", "skill:Power BI"]}).get_json()
check(tp.get("changed") and tailor_calls[-1] == ["align", "skill:Power BI"] and tp["filename"].endswith(".docx")
      and base64.b64decode(tp["base64"])[:2] == b"PK" and tp["changes"][0]["after"].endswith("Power BI dashboards"),
      f"tailor: only the ticked changes, a Word file back: {dict((k, v) for k, v in tp.items() if k != 'base64')}")
check(len(_rv.list_for(K)) == 1, "the tailored resume is saved as a version")
import autofill  # noqa: E402
check(autofill.clean_job({"title": "AI GENERALIST - Delhi - Indeed.com"})["title"] == "AI GENERALIST", "a job board's page title is cleaned")
check(autofill.clean_job({"title": "C# Developer - ASP.NET"})["title"] == "C# Developer - ASP.NET", "a real title keeps its parts")
check(anon.post("/api/ext/tailor", json={"candidate_id": K, "job": JOB}).status_code == 401, "tailor needs the connection key")

# ---- one tailored resume per job: the extension's version is found by Job Hunt (no second tailoring)
_v = _rv.get(tp["version_id"])
_cn = models.get_db_connection()
_cu = _cn.cursor()
_cu.execute("SELECT id FROM jobs WHERE url = ?", (JOB["url"],))
EXT_JOB = _cu.fetchone()[0]
_cn.close()
check(_v["job_id"] == EXT_JOB, f"the extension saves its tailored resume against the job right away: {_v['job_id']} / {EXT_JOB}")
SAME = models.save_or_update_scraped_job({"title": "senior data analyst (fixture) ", "company": "SAMPLE FIXTURE CO", "source": "Naukri",
                                          "url": "https://www.naukri.com/job-listings-same-fixture-1", "country": "United States",
                                          "description": "The same job found on another portal (fixture)."}, match_title_company=False)
SAME = SAME["id"] if isinstance(SAME, dict) else SAME
DIFF = models.save_or_update_scraped_job({"title": "Senior Data Analyst (Fixture)", "company": "Another Fixture Co", "source": "Dice",
                                          "url": "https://www.dice.com/job-detail/other-fixture-1", "country": "United States", "description": "Other."})
DIFF = DIFF["id"] if isinstance(DIFF, dict) else DIFF
fj = client.get(f"/api/optimized-resumes/for-job?candidate_id={K}&job_id={SAME}").get_json()
check(fj["version"] and fj["version"]["id"] == tp["version_id"], f"the same job on another portal: the tailor window opens the extension's version: {fj}")
check(client.get(f"/api/optimized-resumes/for-job?candidate_id={K}&job_id={DIFF}").get_json()["version"] is None, "another company's job: not this version")
pg_ = client.get(f"/api/jobs/{SAME}/page?candidate_id={K}").get_json()
check([v["id"] for v in pg_["versions"] if v["for_job"]] == [tp["version_id"]], f"job page: tailored for this job: {pg_['versions']}")
check(app_module._resume_version_for({"id": REC}, K, None, SAME)[2] == tp["version_id"], "email drafts for the job attach the extension's version")

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

# ---- already on this job?  + which resume went with "Yes, I applied"
status = lambda cid, job: anon.post("/api/ext/job-status", headers=H, json={"candidate_id": cid, "job": job})  # noqa: E731
check(status(K, JOB).get_json()["stage"] == "Saved", "status: saved job found by its link")
check(status(K, {**JOB, "url": "https://forms.example.invalid/apply/1"}).get_json()["stage"] == "Saved", "status: the apply form's link -> found by title + company")
check(status(K, {"title": "Never seen", "company": "Nobody", "url": "https://x.example/never"}).get_json() == {"found": False}, "status: a new job")
check(status(X, JOB).status_code == 404, "status: another recruiter's consultant hidden")
check(anon.post("/api/ext/job-status", json={"candidate_id": K, "job": JOB}).status_code == 401, "status needs the connection key")
JOB2 = {**JOB, "title": "Second Fixture Role", "url": "https://boards.greenhouse.io/samplefixtureco/jobs/2"}
foreign = _rv.save(X, None, "Other_Consultant.docx", b"PK fixture", "Java", "", "", OTHER)
ap = anon.post("/api/ext/applied", headers=H, json={"candidate_id": K, "job": JOB2, "version_id": foreign}).get_json()
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT resume_version_id FROM applications WHERE id = ?", (ap["application_id"],))
check(cur.fetchone()[0] is None, "applied: another consultant's resume version is never linked")
conn.close()
st2 = status(K, JOB2).get_json()
check(st2["stage"] == "Applied" and st2["label"] == "Applied" and len(st2["date"]) == 10, f"status: applied with its date: {st2}")
analyze_calls.clear()
tailor_calls.clear()

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
        wait_text(panel, '#match-box', '70%')
        panel.click("#btn-autofill")
        panel.wait_for_selector("#applied-card:not([hidden])", timeout=20000)
        result = panel.inner_text("#result-box")
        check("filled from Karun Fixturetest" in result and "Resume attached" in result and "1 answer written by AI" in result, f"result box: {result!r}")
        v = lambda sel: job_page.eval_on_selector(sel, "e => e.value")  # noqa: E731
        check(v("#fn") == "Karun" and v("#ln") == "Fixturetest" and v("#em") == "karun.fixture@example.invalid"
              and v("#li") == "https://www.linkedin.com/in/karun-fixturetest" and v("#au") == "Yes", "the real extension filled the form")
        check(job_page.eval_on_selector("#cv", "e => e.files.length && e.files[0].name") == "Karun_Fixturetest_Resume.docx", "resume attached by the extension, as <Name>_Resume.docx")
        check(v("#why") == "I build SQL and Power BI reports (fixture).", "AI answer filled by the extension")
        check(not job_page.evaluate("!!window.submitted"), "the extension never submits")
        shots["panel"] = os.path.join(tmp_dir, "panel.png")
        panel.set_viewport_size({"width": 400, "height": 900})
        panel.screenshot(path=shots["panel"], full_page=True)
        shots["form"] = os.path.join(tmp_dir, "form.png")
        job_page.screenshot(path=shots["form"], full_page=True)
        panel.click("#btn-applied")
        wait_text(panel, '#applied-card', 'Marked as Applied')
        wait_text(panel, '#job-status', 'already on this job')
        check("Applied" in panel.inner_text("#job-status"), f"the panel warns before a second application: {panel.inner_text('#job-status')!r}")

        # -- Tailor resume in the panel: ticks -> Apply -> what changed -> Autofill attaches the tailored file
        panel.click("#btn-tailor")
        wait_text(panel, '#tailor-box', 'Rewords the summary')
        check(len(analyze_calls) == 1, f"Tailor reuses the Match's analysis: {analyze_calls}")
        check(not re.search(r"\b(his|he|him)\b", panel.inner_text("#tailor-box"), re.I), f"no 'his' / 'he': {panel.inner_text('#tailor-box')!r}")
        ticks = panel.eval_on_selector_all("#tailor-box input[data-ch]", "els => els.map(e => e.dataset.ch + ':' + e.checked)")
        check(ticks == ["align:true", "skill:Power BI:true", "skill:Tableau:false"], f"panel: proof decides the ticks: {ticks}")
        check("Apply 2 changes" in panel.inner_text("#btn-tl-apply") and "about" in panel.inner_text("#tl-proj"), "panel: Apply count + expected match")
        panel.click("#btn-tl-apply")
        wait_text(panel, '#tailor-box', 'change made')
        tailored_name = panel.inner_text("#resume-name").replace(" (tailored for this job)", "")
        check(tailored_name == "Karun_Fixturetest_Resume.docx", f"the tailored file goes out as <Name>_Resume.docx, no job title: {tailored_name!r}")
        check("Power BI dashboards" in panel.inner_text("#tailor-box") and tailored_name.endswith(".docx") and "tailored" in panel.inner_text("#resume-name"),
              f"panel: what changed + the tailored resume is the one attached: {panel.inner_text('#resume-name')!r}")
        shots["tailor"] = os.path.join(tmp_dir, "panel_tailor.png")
        panel.screenshot(path=shots["tailor"], full_page=True)
        # preview: the real Word file in a tab, the changed lines highlighted; download: the same file
        with ctx.expect_page() as pv_info:
            panel.click("#btn-tl-preview")
        pv = pv_info.value
        pv.on("pageerror", lambda e: js_errors.append("preview: " + str(e)))
        wait_text(pv, '#pv-note', 'Tailored for this job')
        check(pv.inner_text("#pv-name") == tailored_name, f"preview names the tailored file: {pv.inner_text('#pv-name')!r}")
        hl = pv.eval_on_selector_all(".pv-changed", "els => els.map(e => e.textContent)")
        check(any("Power BI dashboards" in t for t in hl), f"preview highlights the changed line: {hl}")
        with pv.expect_download() as dl:
            pv.click("#pv-download")
        check(dl.value.suggested_filename == tailored_name, f"preview download: {dl.value.suggested_filename}")
        shots["preview"] = os.path.join(tmp_dir, "preview.png")
        pv.screenshot(path=shots["preview"], full_page=True)
        pv.close()
        with panel.expect_download() as dl:
            panel.click("#btn-tl-download")
        check(dl.value.suggested_filename == tailored_name and dl.value.path() and os.path.getsize(dl.value.path()) > 1000,
              f"panel download: {dl.value.suggested_filename}")
        # the same form (original already attached by Job Hunt): Autofill swaps in the tailored resume
        job_page.bring_to_front()
        panel.bring_to_front()
        panel.wait_for_timeout(1500)
        panel.click("#btn-autofill")
        for _ in range(100):
            if job_page.eval_on_selector("#cv", "e => e.files.length && e.files[0].size") not in (0, len(RESUME_BYTES)):
                break
            panel.wait_for_timeout(200)
        cv = job_page.eval_on_selector("#cv", "e => e.files.length ? [e.files[0].name, e.files[0].size] : null")
        check(cv and cv[0] == tailored_name and cv[1] != len(RESUME_BYTES), f"Autofill swapped in the tailored resume (same name, other file): {cv}")
        panel.wait_for_selector("#applied-card:not([hidden])", timeout=20000)
        check("Yes, I applied" in panel.inner_text("#applied-card"), f"the card asks again, it doesn't still say Marked: {panel.inner_text('#applied-card')!r}")
        panel.click("#btn-applied")
        wait_text(panel, '#applied-card', 'Marked as Applied')
        wait_text(panel, '#job-status', 'Senior_Data_Analyst')       # inside Job Hunt the version keeps its job name
        conn = models.get_db_connection()
        cur = conn.cursor()
        cur.execute("""SELECT r.filename, r.job_id, a.job_id FROM applications a JOIN jobs j ON j.id = a.job_id
                       JOIN optimized_resumes r ON r.id = a.resume_version_id WHERE a.candidate_id = ? AND j.url = ?""", (K, JOB["url"]))
        row = cur.fetchone()
        check(row is not None and row[0].startswith("Karun_Fixturetest_Senior_Data_Analyst") and row[1] == row[2], f"Applied records the tailored resume: {row and tuple(row)}")
        conn.close()

        # -- another tab, then back: the tailored resume and match are still with this job, with no new AI call
        tailor_used = len(tailor_calls)
        OTHER_JOB = """<html><head><title>Platform Engineer (Fixture)</title></head><body><h1>Platform Engineer (Fixture)</h1><main>""" + \
            "Labelled fixture job page for another job with Kubernetes and Terraform. " * 8 + """</main></body></html>"""
        ctx.route("https://other.fixture.example.invalid/job/5", lambda r: r.fulfill(status=200, content_type="text/html", body=OTHER_JOB))
        other = ctx.new_page()
        other.goto("https://other.fixture.example.invalid/job/5")
        other.bring_to_front()
        panel.bring_to_front()
        wait_text(panel, '#job-title', 'Platform Engineer')
        check(panel.is_hidden("#tailor-box") and panel.is_hidden("#match-box") and tailored_name not in panel.inner_text("#resume-name"),
              "another job doesn't show this job's resume")
        for _ in range(25):
            if panel.is_hidden("#job-status"):
                break
            panel.wait_for_timeout(200)
        check(panel.is_hidden("#job-status"), "another job has no 'already on this job' warning")
        job_page.bring_to_front()
        panel.bring_to_front()
        wait_text(panel, '#job-title', 'Senior Data Analyst')
        wait_text(panel, '#resume-name', tailored_name)
        check("change made" in panel.inner_text("#tailor-box") and "70%" in panel.inner_text("#match-box"), "back on the job: its tailored resume + match are back")
        other.close()

        # -- the panel closed and reopened: still there
        panel.reload()
        panel.wait_for_selector("#view-main:not([hidden])", timeout=10000)
        wait_text(panel, '#resume-name', tailored_name)
        check("Senior Data Analyst" in panel.inner_text("#job-title"), "panel reopened: the job is back")

        # -- Apply opens the form on another site, in a new tab: same job, same tailored resume
        FORM2 = """<html><head><title>Apply</title></head><body><h1>Apply</h1><label for="fn">First Name</label><input id="fn">
          <label for="cv2">Resume</label><input id="cv2" type="file"></body></html>"""
        ctx.route("https://forms.fixture.example.invalid/apply/77", lambda r: r.fulfill(status=200, content_type="text/html", body=FORM2))
        job_page.bring_to_front()
        with ctx.expect_page() as info:
            job_page.evaluate("window.open('https://forms.fixture.example.invalid/apply/77')")
        form_tab = info.value
        form_tab.wait_for_load_state()
        form_tab.bring_to_front()
        panel.bring_to_front()
        panel.wait_for_timeout(1500)
        check("Senior Data Analyst" in panel.inner_text("#job-title"), f"new tab on another site keeps the job: {panel.inner_text('#job-title')!r}")
        check(tailored_name in panel.inner_text("#resume-name"), f"new tab keeps the tailored resume: {panel.inner_text('#resume-name')!r}")
        panel.click("#btn-autofill")
        panel.wait_for_selector("#applied-card:not([hidden])", timeout=20000)
        check(form_tab.eval_on_selector("#cv2", "e => e.files.length && e.files[0].name") == tailored_name, "the tailored resume attached on the other site")
        check(len(tailor_calls) == tailor_used and len(analyze_calls) == 1, f"no new AI calls for any of that: {tailor_calls} {analyze_calls}")
        form_tab.close()

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
        check(not panel.is_hidden("#applied-card") and "Yes, I applied" in panel.inner_text("#applied-card"), "'Did you apply?' shown on the last step")

        # -- Indeed: the page title holds the city and site; the apply steps are on another site with another link
        INDEED = """<html><head><title>AI GENERALIST - Delhi - Indeed.com</title><meta property="og:title" content="AI GENERALIST - Delhi - Indeed.com">
          <meta property="og:site_name" content="Indeed"></head><body>
          <h2 data-testid="jobsearch-JobInfoHeader-title">AI GENERALIST<span> - job post</span></h2>
          <div data-testid="inlineHeader-companyName"><a href="#">Kviss Apparels Pvt Ltd.</a></div><div data-testid="inlineHeader-companyLocation">Delhi</div>
          <main>""" + "Labelled fixture Indeed posting with Python, SQL and Power BI work for reporting teams. " * 6 + """</main></body></html>"""
        ONLY_META = """<html><head><title>Data Lead - Pune - Indeed.com</title><meta property="og:title" content="Data Lead - Pune - Indeed.com"></head><body><main>""" + \
            "Labelled fixture Indeed posting. " * 20 + "</main></body></html>"
        RESUME_STEP = """<html><head><title>Add a resume | Indeed</title></head><body><h1>Add a resume for the employer</h1>
          <label for="cv3">Resume</label><input id="cv3" type="file"></body></html>"""
        REVIEW_STEP = """<html><head><title>Review your application</title></head><body><h1>Please review your application</h1></body></html>"""
        fulfil = lambda b: lambda r: r.fulfill(status=200, content_type="text/html", body=b)  # noqa: E731
        ctx.route(re.compile(r"https://in\.indeed\.com/viewjob\?jk=fixture1"), fulfil(INDEED))
        ctx.route(re.compile(r"https://in\.indeed\.com/viewjob\?jk=fixture2"), fulfil(ONLY_META))
        ctx.route("https://smartapply.indeed.com/beta/indeedapply/form/resume", fulfil(RESUME_STEP))
        ctx.route("https://smartapply.indeed.com/beta/indeedapply/form/review", fulfil(REVIEW_STEP))
        job_page.goto("https://in.indeed.com/viewjob?jk=fixture2")
        job_page.bring_to_front()
        panel.bring_to_front()
        wait_text(panel, '#job-title', 'Data Lead')
        check(panel.inner_text("#job-title") == "Data Lead", f"the page title alone: city and site removed: {panel.inner_text('#job-title')!r}")
        check(panel.inner_text("#job-company") == "", f"'Indeed' is never the company: {panel.inner_text('#job-company')!r}")
        job_page.goto("https://in.indeed.com/viewjob?jk=fixture1")
        job_page.bring_to_front()
        panel.bring_to_front()
        wait_text(panel, '#job-company', 'Kviss')
        check(panel.inner_text("#job-title") == "AI GENERALIST", f"Indeed title: {panel.inner_text('#job-title')!r}")
        check("Delhi" in panel.inner_text("#job-chips") and "in.indeed.com" in panel.inner_text("#job-chips"), f"location: {panel.inner_text('#job-chips')!r}")
        check("original resume" in panel.inner_text("#attach-note"), f"says the original will attach: {panel.inner_text('#attach-note')!r}")
        panel.click("#btn-match")
        wait_text(panel, '#match-box', '70%')
        panel.click("#btn-tailor")
        wait_text(panel, '#tailor-box', 'Rewords the summary')
        panel.click("#btn-tl-apply")
        wait_text(panel, '#tailor-box', 'change made')
        indeed_file = panel.inner_text("#resume-name").replace(" (tailored for this job)", "")
        check("Indeed" not in indeed_file and "Delhi" not in indeed_file, f"clean file name: {indeed_file!r}")
        check(indeed_file in panel.inner_text("#attach-note") and "tailored" in panel.inner_text("#attach-note"), f"says the tailored one will attach: {panel.inner_text('#attach-note')!r}")
        # the panel fits: a long file name and consultant name never make it scroll sideways
        panel.set_viewport_size({"width": 340, "height": 900})
        panel.evaluate("() => { const n = document.getElementById('resume-name'); n.dataset.was = n.textContent; n.textContent = 'Praveen_Valipireddy_AI_GENERALIST_Delhi_Indeed_com_and_more_words.docx (tailored for this job)'; }")
        over = panel.evaluate("() => [...document.querySelectorAll('#view-main *')].filter(e => e.offsetParent && e.getBoundingClientRect().right > document.documentElement.clientWidth + 1).map(e => e.id || e.tagName)")
        check(not over and panel.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), f"nothing wider than the panel: {over}")
        shots["indeed"] = os.path.join(tmp_dir, "panel_indeed.png")
        panel.screenshot(path=shots["indeed"], full_page=True)
        panel.evaluate("() => { const n = document.getElementById('resume-name'); n.textContent = n.dataset.was; }")
        # same tab, Indeed's own apply steps
        for step_url, label in (("https://smartapply.indeed.com/beta/indeedapply/form/resume", "resume step"), ("https://smartapply.indeed.com/beta/indeedapply/form/review", "review step")):
            job_page.goto(step_url)
            job_page.bring_to_front()
            panel.bring_to_front()
            panel.wait_for_timeout(1500)
            check(panel.inner_text("#job-title") == "AI GENERALIST" and indeed_file in panel.inner_text("#resume-name"), f"{label} keeps the job and tailored resume: {panel.inner_text('#job-title')!r} {panel.inner_text('#resume-name')!r}")
        job_page.goto("https://smartapply.indeed.com/beta/indeedapply/form/resume")
        job_page.bring_to_front()
        panel.bring_to_front()
        panel.wait_for_timeout(1200)
        panel.click("#btn-autofill")
        panel.wait_for_selector("#applied-card:not([hidden])", timeout=20000)
        check(job_page.eval_on_selector("#cv3", "e => e.files.length && e.files[0].name") == indeed_file, "the tailored resume attached on Indeed's step")
        panel.click("#btn-applied")
        wait_text(panel, '#applied-card', 'Marked as Applied')
        # back on the posting, in the same tab: all still there, and the warning shows
        job_page.goto("https://in.indeed.com/viewjob?jk=fixture1")
        job_page.bring_to_front()
        panel.bring_to_front()
        wait_text(panel, '#job-status', 'already on this job')
        check("AI_GENERALIST" in panel.inner_text("#job-status") and indeed_file in panel.inner_text("#resume-name"), f"back on the posting: {panel.inner_text('#job-status')!r} {panel.inner_text('#resume-name')!r}")
        # the same job opened in a new tab by another link
        again = ctx.new_page()
        ctx.route(re.compile(r"https://in\.indeed\.com/viewjob\?from=alert&jk=fixture1"), fulfil(INDEED))
        again.goto("https://in.indeed.com/viewjob?from=alert&jk=fixture1")
        again.bring_to_front()
        panel.bring_to_front()
        panel.wait_for_timeout(1500)
        check(indeed_file in panel.inner_text("#resume-name"), f"a new tab, another link: the tailored resume is still there: {panel.inner_text('#resume-name')!r}")
        again.close()
        ctx.close()

        conn = models.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT a.stage, a.apply_method FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.candidate_id = ? AND j.url = ?",
                    (K, "https://boards.greenhouse.io/samplefixtureco/jobs/1"))
        row = cur.fetchone()
        check(row is not None and (row[0], row[1]) == ("Applied", "portal"), f"'Yes, I applied' -> Applied in portal: {row and (row[0], row[1])}")
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
        check("app.js?v=5.99.19" in page.content(), "cache-buster not bumped to 5.99.19")
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
