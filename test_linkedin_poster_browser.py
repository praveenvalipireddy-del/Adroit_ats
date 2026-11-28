"""Extension 1.6.0: "Message the poster" on LinkedIn jobs and recruiter posts. Labelled fixtures, no network, no AI.
1. Server: /api/ext/linkedin-messaged saves the job to the consultant's Applications and logs "Messaged <name> on
   LinkedIn"; job-status then reports it (the panel warns before a second message); only the user's own consultants.
2. The poster: on a job page from "Meet the hiring team" - never "People you can reach out to" (alumni / connections);
   on a single post, its author.
3. The real extension in Edge: the card shows the poster and the fixed message (first name, job title, company);
   "Type it on LinkedIn" opens their profile and types it (never clicks Send); "I sent it" logs it in Job Hunt.
Run: python test_linkedin_poster_browser.py
"""
import io
import json
import os
import socket
import sys
import tempfile
import threading
import zipfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_li_poster.db")
os.environ["DATABASE_URL"] = ""
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN", "APOLLO_API_KEY"):
    os.environ[k] = ""
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
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


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL = "li-poster@example.invalid"
REC = uid(models.create_user("Lia Postertest", EMAIL, "Poster-1", role="Recruiter", region="USA,India,Canada"))
OTHER = uid(models.create_user("Otto Otherrec", "li-other@example.invalid", "Other-1", role="Recruiter", region="USA"))
C = models.create_candidate("Vera Fixturetest", "vera.fixture@example.invalid", title="Cyber Security", country="Canada", assigned_user_id=REC)
X = models.create_candidate("Other Consultant", "other.li@example.invalid", title="Java Dev", country="United States", assigned_user_id=OTHER)

client = app_module.app.test_client()
anon = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Lia", "role": "Recruiter", "email": EMAIL}
KEY = client.post("/api/extension/key").get_json()["key"]
H = {"Authorization": "Bearer " + KEY}

# ---- 1. server
JOB_URL = "https://www.linkedin.com/jobs/view/4478800001/"
JOB = {"title": "Senior Security Engineer (Fixture)", "company": "Fixture Dental Co", "url": JOB_URL}
st = anon.post("/api/ext/job-status", headers=H, json={"candidate_id": C, "job": JOB}).get_json()
check(st.get("messaged") is None, f"nothing messaged yet: {st}")
r = anon.post("/api/ext/linkedin-messaged", headers=H, json={"candidate_id": C, "job": JOB, "mode": "message",
                                                              "person": {"name": "Nora Poster", "url": "https://www.linkedin.com/in/nora-poster-fixture/"}})
check(r.status_code == 200 and r.get_json()["application_id"], f"logged: {r.status_code} {r.get_json()}")
st = anon.post("/api/ext/job-status", headers=H, json={"candidate_id": C, "job": JOB}).get_json()
check(st["messaged"] and "Messaged Nora Poster on LinkedIn (a message)" in st["messaged"]["detail"] and len(st["messaged"]["date"]) == 10,
      f"job-status reports the message: {st.get('messaged')}")
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT stage, recruiter_name FROM applications WHERE id = ?", (r.get_json()["application_id"],))
row = cur.fetchone()
conn.close()
check(row["stage"] == "Saved" and row["recruiter_name"] == "Nora Poster", f"application: saved, poster as the recruiter: {dict(row)}")
check(anon.post("/api/ext/linkedin-messaged", headers=H, json={"candidate_id": X, "job": JOB}).status_code == 404, "another recruiter's consultant refused")
check(anon.post("/api/ext/linkedin-messaged", headers=H, json={"candidate_id": C, "job": {"title": ""}}).status_code == 400, "no job -> 400")
check(anon.post("/api/ext/linkedin-messaged", json={"candidate_id": C, "job": JOB}).status_code == 401, "needs the key")

# ---- 2 + 3. browser
z = client.get("/extension/jobhunt-autofill.zip")
check(json.loads(zipfile.ZipFile(io.BytesIO(z.data)).read("jobhunt-autofill/manifest.json"))["version"] == "1.7.0", "extension version 1.7.0")
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

JOB2_URL = "https://www.linkedin.com/jobs/view/4478800002/"
JOB_PAGE = """<!doctype html><html><head><title>Cloud Security Analyst (Fixture) | Fixture Dental Co | LinkedIn</title>
<script type="application/ld+json">{"@type":"JobPosting","title":"Cloud Security Analyst (Fixture)","hiringOrganization":{"name":"Fixture Dental Co"},
"description":"Labelled fixture LinkedIn-style job page for the extension test. Cloud security, SIEM and IAM work for a dental technology company."}</script>
</head><body><main><h1>Cloud Security Analyst (Fixture)</h1>
<section class="job-details-people-who-can-help__section"><h2>Meet the hiring team</h2>
  <div class="hirer-card"><a href="https://www.linkedin.com/in/hana-hirer-fixture/?trk=job"><strong>Hana Hirer</strong></a>
  <div>• 3rd</div><div>Talent Acquisition Partner at Fixture Dental Co</div><div>Job poster</div><button>Message</button></div></section>
<section><h2>People you can reach out to</h2>
  <div><a href="https://www.linkedin.com/in/alum-fixture/"><strong>Al Alumnus</strong></a><div>Key Account Manager at Fixture Dental Co</div></div></section>
</main></body></html>"""
NO_POSTER_PAGE = JOB_PAGE.replace('<section class="job-details-people-who-can-help__section"><h2>Meet the hiring team</h2>', '<section><h2>About</h2>') \
                         .replace("hana-hirer-fixture", "not-a-poster").replace("<div>Job poster</div>", "")
POST_PAGE = """<!doctype html><html><head><title>Post | LinkedIn</title></head><body><main>
<div class="update-components-actor"><a href="https://www.linkedin.com/in/rex-recruiter-fixture/"><span aria-hidden="true">Rex Recruiter</span></a>
<span>Technical Recruiter at Fixture Staffing</span></div><p>Hiring a Cyber Security Analyst (fixture post).</p></main></body></html>"""
REC_URL = "https://www.linkedin.com/in/rita-recruiter-fixture/"
# a recruiter's profile, not connected: Connect is under More; Connect opens a dialog with "Add a note" (all fixtures)
REC_PROFILE = """<!doctype html><html><head><title>Rita Recruiter (fixture profile)</title></head><body><main><h1>Rita Recruiter</h1>
<div class="text-body-medium">Technical Recruiter at Fixture Dental Co</div>
<button aria-label="More actions" onclick="document.getElementById('menu').hidden = false">More</button>
<div id="menu" role="menu" hidden><div role="menuitem" aria-label="Invite Rita Recruiter to connect" onclick="openInvite()">Connect</div></div>
</main><div id="dlg"></div>
<script>
function openInvite() {
  window.connectClicked = true;
  document.getElementById('dlg').innerHTML = '<div role="dialog"><button id="addnote">Add a note</button><button id="nonote">Send without a note</button></div>';
  document.getElementById('nonote').onclick = () => { window.sent = true; };
  document.getElementById('addnote').onclick = () => {
    window.addClicked = true;
    document.querySelector('#dlg [role=dialog]').innerHTML = '<textarea name="message" maxlength="300"></textarea><button id="send">Send</button>';
    document.getElementById('send').onclick = () => { window.sent = true; };
  };
}
</script></body></html>"""
NEW_LAYOUT_URL = "https://www.linkedin.com/jobs/view/4478800004/"
NEW_LAYOUT = """<!doctype html><html><head><title>(4) Senior Incident Response Analyst (Fixture) | Check Point Fixture | LinkedIn</title></head>
<body><main><h1>Senior Incident Response Analyst (Fixture)</h1><p>Labelled fixture page in LinkedIn's newer layout: no JSON-LD, the company only in the tab title.</p></main></body></html>"""
PROFILE = """<!doctype html><html><head><title>Hana Hirer (fixture profile)</title></head><body><main><h1>Hana Hirer</h1>
<button aria-label="Message Hana Hirer" onclick="window.msgClicked=true; document.getElementById('dock').innerHTML =
 '<div class=msg-form><div class=msg-form__contenteditable contenteditable=true role=textbox style=min-height:40px></div><button onclick=window.sent=true>Send</button></div>'">Message</button>
</main><div id="dock"></div></body></html>"""

js_errors, shot = [], os.path.join(tmp_dir, "li_poster_panel.png")
try:
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(os.path.join(tmp_dir, "profile"), channel="msedge", headless=True,
                                                    args=[f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = sw.url.split("/")[2]
        html = lambda body: (lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=body))  # noqa: E731
        ctx.route(JOB2_URL, html(JOB_PAGE))
        ctx.route("https://www.linkedin.com/jobs/view/4478800003/", html(NO_POSTER_PAGE))
        ctx.route("https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/", html(POST_PAGE))
        ctx.route("https://www.linkedin.com/in/hana-hirer-fixture/", html(PROFILE))
        ctx.route(REC_URL, html(REC_PROFILE))
        ctx.route(NEW_LAYOUT_URL, html(NEW_LAYOUT))

        # -- 2. the poster reader (filler.js) on fixture pages
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: js_errors.append("page: " + str(e)))
        filler = os.path.join(ext_dir, "filler.js")
        pg.goto(JOB2_URL)
        pg.add_script_tag(path=filler)
        p = pg.evaluate("window.JH.readPoster()")
        check(p == {"name": "Hana Hirer", "title": "Talent Acquisition Partner at Fixture Dental Co", "url": "https://www.linkedin.com/in/hana-hirer-fixture/"},
              f"job page: the hiring team's poster, not the alumnus: {p}")
        pg.goto("https://www.linkedin.com/jobs/view/4478800003/")
        pg.add_script_tag(path=filler)
        check(pg.evaluate("window.JH.readPoster()") is None, "no hiring team -> no poster (never 'People you can reach out to')")
        pg.goto("https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/")
        pg.add_script_tag(path=filler)
        p = pg.evaluate("window.JH.readPoster()")
        check(p and p["name"] == "Rex Recruiter" and p["url"] == "https://www.linkedin.com/in/rex-recruiter-fixture/", f"post: its author: {p}")
        pg.goto(NEW_LAYOUT_URL)
        pg.add_script_tag(path=filler)
        j = pg.evaluate("window.JH.readJob()")
        check(j["company"] == "Check Point Fixture" and j["title"] == "Senior Incident Response Analyst (Fixture)",
              f"newer LinkedIn layout: company from the tab title: {j['title']!r} / {j['company']!r}")
        pg.goto(JOB2_URL)
        pg.add_script_tag(path=filler)
        people = pg.evaluate("window.JH.readLinkedInPeople()")
        check([r["name"] for r in people["reach"]] == ["Al Alumnus"] and people["poster"]["name"] == "Hana Hirer", f"reach-out people apart from the poster: {people}")
        pg.close()

        # -- 3. the real extension
        job_page = ctx.pages[0] if ctx.pages else ctx.new_page()
        job_page.goto(JOB2_URL)
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
        panel.wait_for_selector("#poster-card:not([hidden])", timeout=15000)
        for _ in range(100):                      # the extension page's CSP forbids wait_for_function
            if "Hana Hirer" in panel.inner_text("#li-people"):
                break
            panel.wait_for_timeout(100)
        people_txt = panel.inner_text("#li-people")
        check("Hana Hirer" in people_txt and "Job poster" in people_txt and "Al Alumnus" in people_txt and "Ask for a referral" in people_txt,
              f"card: the poster, and the alumnus as a referral: {people_txt!r}")
        check(panel.input_value("#li-kind") == "poster", "the poster is picked first")
        msg = panel.input_value("#poster-msg")
        want = "Hi Hana, I saw your posting for the Cloud Security Analyst (Fixture) role at Fixture Dental Co. I'm interested in this position and would love to connect. Thank you!"
        check(msg == want, f"fixed message: {msg!r}")
        check(f"{len(want)} / 300 characters" in panel.inner_text("#poster-count"), f"counter: {panel.inner_text('#poster-count')!r}")
        panel.set_viewport_size({"width": 400, "height": 1000})
        panel.screenshot(path=shot, full_page=True)
        # A tab the extension opens itself is not routed by Playwright (it would load the real linkedin.com): the panel's
        # tabs.create is captured, and the same link is opened in a routed page - the extension's background sees it alike.
        panel.evaluate("() => { chrome.tabs.create = (o) => { window.__opened = o.url; return Promise.resolve({}); }; }")
        panel.click("#btn-poster-type")
        opened = panel.evaluate("() => window.__opened || ''")
        check(opened.startswith("https://www.linkedin.com/in/hana-hirer-fixture/#jh-li="), f"profile opened with the draft: {opened[:80]}")
        prof = ctx.new_page()
        prof.on("pageerror", lambda e: js_errors.append("profile: " + str(e)))
        prof.goto(opened)
        prof.wait_for_selector("#jobhunt-li-helper", timeout=15000)
        for _ in range(100):
            if prof.evaluate("!!document.querySelector('.msg-form__contenteditable') && document.querySelector('.msg-form__contenteditable').textContent.length > 0"):
                break
            prof.wait_for_timeout(100)
        typed = prof.evaluate("(document.querySelector('.msg-form__contenteditable') || {}).textContent || ''")
        check(typed == want, f"message typed into LinkedIn's box: {typed!r} | helper: {prof.locator('#jobhunt-li-helper #steps').inner_text()!r}")
        check(prof.evaluate("window.msgClicked === true && !window.sent"), "Message opened, Send NOT clicked")
        prof.locator("#jobhunt-li-helper #sent").click()
        for _ in range(100):
            if "Saved in Job Hunt" in prof.locator("#jobhunt-li-helper #steps").inner_text():
                break
            prof.wait_for_timeout(100)
        check("application shows" in prof.locator("#jobhunt-li-helper #steps").inner_text(), "helper confirms the log")
        st = anon.post("/api/ext/job-status", headers=H, json={"candidate_id": C, "job": {"title": "Cloud Security Analyst (Fixture)", "company": "Fixture Dental Co", "url": JOB2_URL}}).get_json()
        check(st.get("messaged") and "Messaged Hana Hirer on LinkedIn (a message) - https://www.linkedin.com/in/hana-hirer-fixture/" in st["messaged"]["detail"],
              f"logged in Job Hunt with the profile link: {st.get('messaged')}")
        check(not prof.evaluate("!!window.sent"), "never sent by Job Hunt")
        prof.close()

        # referral: picking the alumnus switches to the referral message
        panel.click("input[name=li-person][value='1']")
        ref = panel.input_value("#poster-msg")
        check(panel.input_value("#li-kind") == "referral" and ref == "Hi Al, I'm applying for the Cloud Security Analyst (Fixture) role at Fixture Dental Co. "
              "Would you be open to referring me? Thank you!", f"referral message: {ref!r}")
        # Find recruiters: a LinkedIn people search for the company
        panel.evaluate("() => { window.__opened = ''; }")
        panel.click("#btn-li-recruiters")
        opened = panel.evaluate("() => window.__opened || ''")
        check(opened.startswith("https://www.linkedin.com/search/results/people/?keywords=Fixture%20Dental%20Co%20recruiter"), f"recruiter search: {opened}")

        # a recruiter's profile opened afterwards: offered with the recruiter message; Connect -> Add a note opened, typed, not sent
        job_page.goto(REC_URL)
        job_page.bring_to_front()
        for _ in range(100):
            if "Rita Recruiter" in panel.inner_text("#li-people"):
                break
            panel.wait_for_timeout(100)
        check("This profile" in panel.inner_text("#li-people") and panel.input_value("#li-kind") == "recruiter", f"profile card: {panel.inner_text('#li-people')!r}")
        rec_msg = panel.input_value("#poster-msg")
        check(rec_msg == "Hi Rita, I saw the opening for the Cloud Security Analyst (Fixture) role at Fixture Dental Co. "
              "I'm interested in this position and would love to connect. Thank you!", f"recruiter message about the last job: {rec_msg!r}")
        panel.click("#btn-poster-type")
        job_page.wait_for_selector("#jobhunt-li-helper", timeout=15000)
        for _ in range(100):
            if job_page.evaluate("!!document.querySelector('textarea[name=message]') && document.querySelector('textarea[name=message]').value.length > 0"):
                break
            job_page.wait_for_timeout(100)
        note = job_page.evaluate("(document.querySelector('textarea[name=message]') || {}).value || ''")
        check(note == rec_msg, f"note typed: {note!r} | helper: {job_page.locator('#jobhunt-li-helper #steps').inner_text()!r}")
        check(job_page.evaluate("window.connectClicked === true && window.addClicked === true && !window.sent"),
              "Connect (under More) and Add a note opened by Job Hunt - Send NOT clicked")
        job_page.locator("#jobhunt-li-helper #sent").click()
        for _ in range(100):
            if "Saved in Job Hunt" in job_page.locator("#jobhunt-li-helper #steps").inner_text():
                break
            job_page.wait_for_timeout(100)
        st = anon.post("/api/ext/job-status", headers=H, json={"candidate_id": C, "job": {"title": "Cloud Security Analyst (Fixture)",
                                                                                          "company": "Fixture Dental Co", "url": JOB2_URL}}).get_json()
        check(st.get("messaged") and "Messaged Rita Recruiter (recruiter) on LinkedIn (a connection note)" in st["messaged"]["detail"],
              f"recruiter note logged: {st.get('messaged')}")
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
print(f"PASS: LinkedIn outreach - poster (never alumni) / post author / referral contacts / any profile; poster, recruiter and "
      f"referral messages; recruiter search; Message or Connect -> Add a note opened and typed (Send never clicked); logged. Screenshot: {shot}")
