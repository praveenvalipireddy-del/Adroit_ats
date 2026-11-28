"""Extension gaps between Job Hunt and the Autofill extension (1.5.3), with labelled fixtures and no paid calls:
1. Server: the profile gives the phone country / dial code; City is never guessed from a label-filled Location;
   the "not filled yet" list includes the address and the employer; a resume tailored for the job on the website is
   found by job-status and served to the extension (only for the user's own consultants).
2. The real extension on a Workday-style fixture page (Country button list, Country Phone Code search list preset to
   India (+91), Province list, plain inputs): the country and province are picked, the wrong phone code is replaced
   by Canada (+1), and the website's tailored resume is attached instead of the original.
Run: python test_extension_workday_browser.py
"""
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import zipfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_ext_workday.db")
os.environ["DATABASE_URL"] = ""
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import autofill  # noqa: E402
import models  # noqa: E402
import resume_versions  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL = "wd-rec@example.invalid"
REC = uid(models.create_user("Wanda Workdaytest", EMAIL, "Workday-1", role="Recruiter", region="USA,India,Canada"))
OTHER = uid(models.create_user("Otto Otherrec", "wd-other@example.invalid", "Other-1", role="Recruiter", region="Canada"))
C = models.create_candidate("Cora Canadatest", "cora.fixture@example.invalid", phone="4165550100", title="Security Consultant",
                            location="City, Scarborough, Ontario, M1P 0A0. Province,", country="Canada", assigned_user_id=REC,
                            resume_text="Cora Canadatest - Security Consultant (labelled test fixture)")
X = models.create_candidate("Other Consultant", "other.wd@example.invalid", title="Java Dev", country="Canada", assigned_user_id=OTHER)
models.save_resume_file(C, "Cora_Canadatest.docx", b"PK original fixture resume")

# ---- 1. server
cand = models.get_candidate_by_id(C)
p = autofill.profile(cand, "Cora_Canadatest.docx")
check(p.get("phone_country") == "Canada" and p.get("phone_dial") == "+1", f"phone country + dial code from the country: {p}")
check("city" not in p, f"City is not guessed from a label-filled Location: {p.get('city')!r}")
check(autofill.city_from_location("Dallas, TX (Hybrid)") == "Dallas", "a normal Location still gives the city")
miss = autofill.missing(cand)
for label in ("City", "ZIP / postal code", "Street address", "Current / last employer"):
    check(label in miss, f"missing list names {label}: {miss}")

client = app_module.app.test_client()
anon = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Wanda", "role": "Recruiter", "email": EMAIL}
client.put(f"/api/consultants/{C}", json={"autofill_answers": {"city": "Scarborough", "state": "Ontario", "postal_code": "M1P 0A0"}})
KEY = client.post("/api/extension/key").get_json()["key"]
H = {"Authorization": "Bearer " + KEY}

JOB_URL = "https://fixtureco.wd3.myworkdayjobs.com/en-US/External/job/Toronto/Security-Consultant_1/apply"
JOB = {"title": "Security Consultant (Fixture)", "company": "Fixtureco", "url": JOB_URL}
job_id = models.save_or_update_scraped_job({"title": JOB["title"], "company": JOB["company"], "source": "Company site", "url": JOB_URL,
                                           "country": "Canada", "description": "Labelled fixture job."})
job_id = job_id["id"] if isinstance(job_id, dict) else job_id
TAILORED = b"PK tailored fixture resume"
vid = resume_versions.save(C, job_id, "Cora_Canadatest_Security.docx", TAILORED, "Security", "", "", REC)
foreign = resume_versions.save(X, None, "Other_Consultant.docx", b"PK other", "Java", "", "", OTHER)

st = anon.post("/api/ext/job-status", headers=H, json={"candidate_id": C, "job": JOB}).get_json()
check(st.get("tailored") and st["tailored"]["version_id"] == vid, f"job-status finds the website's tailored resume: {st}")
v = anon.get(f"/api/ext/versions/{vid}", headers=H).get_json()
check(v.get("base64") and v["filename"] == "Cora_Canadatest_Resume.docx", f"version served as <Name>_Resume.docx: {v.get('filename')}")
check(anon.get(f"/api/ext/versions/{foreign}", headers=H).status_code == 404, "another recruiter's version is refused")
check(anon.get(f"/api/ext/versions/{vid}").status_code == 401, "a version needs the key")
ext_cons = anon.get(f"/api/ext/consultants/{C}", headers=H).get_json()
check("City" not in ext_cons["missing"] and "ZIP / postal code" not in ext_cons["missing"], f"saved answers leave the missing list: {ext_cons['missing']}")

# ---- 2. the real extension on a Workday-style page
z = client.get("/extension/jobhunt-autofill.zip")
check(json.loads(zipfile.ZipFile(io.BytesIO(z.data)).read("jobhunt-autofill/manifest.json"))["version"] == "1.6.0", "extension version 1.6.0")
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"
ext_dir = os.path.join(tmp_dir, "ext")
zipfile.ZipFile(io.BytesIO(z.data)).extractall(ext_dir)
ext_dir = os.path.join(ext_dir, "jobhunt-autofill")
with open(os.path.join(ext_dir, "config.js"), "w") as f:
    f.write(f"const JH_DEFAULT_SERVER = {json.dumps(BASE)};\n")

# Workday-style widgets: a button that opens a listbox, and a search box with a preset "pill" (India (+91)).
WORKDAY = """<!doctype html><html><head><title>Security Consultant (Fixture)</title>
<script type="application/ld+json">{"@type":"JobPosting","title":"Security Consultant (Fixture)","hiringOrganization":{"name":"Fixtureco"},
"description":"Labelled fixture Workday-style application page for the extension test. Security consulting role in Toronto."}</script>
<style>[role=listbox]{position:absolute;background:#fff;border:1px solid #999} [role=option],[data-automation-id=promptOption]{padding:4px;cursor:pointer}</style>
</head><body><h1>Security Consultant (Fixture)</h1>
<div data-automation-id="formField-country"><label>Country*</label><button type="button" aria-haspopup="listbox" id="country">Select One</button></div>
<div data-automation-id="formField-addressLine1"><label for="addr">Address Line 1</label><input id="addr"></div>
<div data-automation-id="formField-city"><label for="city">City</label><input id="city"></div>
<div data-automation-id="formField-countryRegion"><label>Province or Territory*</label><button type="button" aria-haspopup="listbox" id="prov">Select One</button></div>
<div data-automation-id="formField-postalCode"><label for="zip">Postal Code</label><input id="zip"></div>
<div data-automation-id="formField-countryPhoneCode"><label>Country Phone Code*</label>
  <div id="chips"><div data-automation-id="selectedItem">India (+91)<span data-automation-id="DELETE_charm">x</span></div></div>
  <input data-automation-id="searchBox" id="code-search"></div>
<div data-automation-id="formField-phoneNumber"><label for="phone">Phone Number*</label><input id="phone"></div>
<div><label for="cv">Resume</label><input type="file" id="cv"></div>
<script>
const LISTS = {country: ['India', 'United States Minor Outlying Islands', 'United States of America', 'Canada'], prov: ['Alberta', 'Ontario', 'Quebec']};
const CODES = ['India (+91)', 'Canada (+1)', 'United States of America (+1)'];
function closeLists(){ document.querySelectorAll('[role=listbox]').forEach(l => l.remove()); }
for (const id of Object.keys(LISTS)) {
  const b = document.getElementById(id);
  b.addEventListener('click', () => { closeLists(); const ul = document.createElement('ul'); ul.setAttribute('role','listbox');
    for (const t of LISTS[id]) { const li = document.createElement('li'); li.setAttribute('role','option'); li.textContent = t;
      li.addEventListener('click', () => { b.textContent = t; closeLists(); }); ul.appendChild(li); }
    document.body.appendChild(ul); });
}
document.getElementById('chips').addEventListener('click', e => { if (e.target.dataset.automationId === 'DELETE_charm') e.target.parentElement.remove(); });
const search = document.getElementById('code-search');
search.addEventListener('keydown', e => { if (e.key !== 'Enter') return; closeLists(); const ul = document.createElement('ul'); ul.setAttribute('role','listbox');
  for (const t of CODES.filter(c => c.toLowerCase().includes(search.value.toLowerCase()))) { const d = document.createElement('div');
    d.setAttribute('data-automation-id','promptOption'); d.textContent = t;
    d.addEventListener('click', () => { const chip = document.createElement('div'); chip.setAttribute('data-automation-id','selectedItem'); chip.textContent = t;
      document.getElementById('chips').appendChild(chip); search.value = ''; closeLists(); }); ul.appendChild(d); }
  document.body.appendChild(ul); });
</script></body></html>"""

js_errors = []
shot = os.path.join(tmp_dir, "workday_form.png")
try:
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(os.path.join(tmp_dir, "profile"), channel="msedge", headless=True,
                                                    args=[f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = sw.url.split("/")[2]
        ctx.route(JOB_URL, lambda r: r.fulfill(status=200, content_type="text/html", body=WORKDAY))
        job_page = ctx.pages[0] if ctx.pages else ctx.new_page()
        job_page.on("pageerror", lambda e: js_errors.append("page: " + str(e)))
        job_page.goto(JOB_URL)
        panel = ctx.new_page()
        panel.on("pageerror", lambda e: js_errors.append("panel: " + str(e)))
        panel.goto(f"chrome-extension://{ext_id}/panel.html")
        for _ in range(50):
            if panel.input_value("#in-server"):
                break
            panel.wait_for_timeout(100)
        panel.fill("#in-key", KEY)
        panel.click("#btn-connect")
        panel.wait_for_selector("#view-main:not([hidden])", timeout=10000)
        job_page.bring_to_front()
        for _ in range(150):                      # the extension page's CSP forbids wait_for_function
            if "tailored" in panel.inner_text("#attach-note"):
                break
            panel.wait_for_timeout(100)
        check("Cora_Canadatest_Resume.docx" in panel.inner_text("#attach-note"), f"attach note: {panel.inner_text('#attach-note')!r}")
        check("Tailored for this job in Job Hunt" in panel.inner_text("#tailor-box"), f"tailor box: {panel.inner_text('#tailor-box')!r}")
        panel.click("#btn-autofill")
        panel.wait_for_selector("#applied-card:not([hidden])", timeout=30000)
        t = lambda sel: job_page.inner_text(sel).strip()  # noqa: E731
        val = lambda sel: job_page.eval_on_selector(sel, "e => e.value")  # noqa: E731
        check(t("#country") == "Canada", f"Workday Country list picked: {t('#country')!r}")
        check(t("#prov") == "Ontario", f"Workday Province list picked: {t('#prov')!r}")
        check(t("#chips") == "Canada (+1)", f"India (+91) replaced by Canada (+1): {t('#chips')!r}")
        check(val("#code-search") == "", "the phone number was not typed into the code search box")
        check(val("#phone") == "4165550100" and val("#city") == "Scarborough" and val("#zip") == "M1P 0A0",
              f"plain fields: phone {val('#phone')!r}, city {val('#city')!r}, zip {val('#zip')!r}")
        check(job_page.eval_on_selector("#cv", "e => e.files.length && e.files[0].name") == "Cora_Canadatest_Resume.docx",
              "the website's tailored resume was attached")
        size = job_page.eval_on_selector("#cv", "e => e.files[0].size")
        check(size == len(TAILORED), f"attached file is the tailored one ({size} bytes)")
        job_page.screenshot(path=shot, full_page=True)
        ctx.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f_ in failures:
        print("  -", f_)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: extension gaps - phone code / City / missing list; website tailored resume used; Workday Country, Province and "
      f"Phone Code lists filled (India (+91) replaced). Screenshot: {shot}")
