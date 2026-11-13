"""The email step finds the recruiter: "🔍 Find recruiter (Apollo)" next to To (Apollo stubbed: no network, no credits).

1. The button shows for a saved job only (the tailor window), not for a pasted requirement with no job.
2. Search (free): the hiring team at the job's company, recruiters first.
3. "Get email · 1 credit" asks first; a cancelled confirm calls nothing. A verified email fills To and is saved on the job.
4. A guessed / unverified email never fills To.
5. Searching again: the person revealed before shows "Use" - fills To with no new credit.
6. Apollo's Free plan: a clear message with the Apollo / LinkedIn links to find the person yourself.
Run: python test_email_find_recruiter_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_email_find.db")
os.environ["DATABASE_URL"] = ""
os.environ["CAREER_SYNC_HOURS"] = "0"
os.environ["APOLLO_API_KEY"] = "fixture-not-a-real-key"
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "APIFY_API_TOKEN"):
    os.environ[k] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import apollo_api  # noqa: E402
import app as app_module  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


P1, P2 = "a" * 24, "b" * 24
calls = []


def fake_post(path, body):
    calls.append(path)
    if path == "/mixed_people/api_search":
        return {"people": [
            {"id": P2, "first_name": "Hari", "last_name_obfuscated": "H***", "title": "HR Business Partner", "organization": {"name": "Crystree Fixture"}},
            {"id": P1, "first_name": "Rita", "last_name_obfuscated": "Re***r", "title": "Talent Acquisition Lead", "organization": {"name": "Crystree Fixture Pvt Ltd"},
             "has_email": True}]}
    if path == "/people/match":
        if body["id"] == P1:
            return {"person": {"name": "Rita Recruiter", "email": "rita@crystree.example", "email_status": "verified", "organization": {"name": "Crystree Fixture"}}}
        return {"person": {"name": "Hari H", "email": "hari@crystree.example", "email_status": "guessed", "organization": {"name": "Crystree Fixture"}}}
    raise AssertionError(path)


apollo_api.http_post = fake_post

uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "find-rec@example.invalid", "FindRec-1"
REC = uid(models.create_user("Fia Findtest", EMAIL, PW, role="Recruiter", region="India"))
K = models.create_candidate("Ravi Findfixture", "ravi.find@example.invalid", title="Digital Marketing Executive", experience_years=3,
                            country="India", assigned_user_id=REC)
JOB = models.save_or_update_scraped_job({"title": "Social Media Specialist (Find fixture)", "company": "Crystree Fixture", "source": "Indeed",
                                         "url": "https://in.indeed.com/viewjob?jk=findfixture1", "location": "Hyderabad", "country": "India",
                                         "description": "Labelled test fixture. Social media content planning."})
JOB = JOB["id"] if isinstance(JOB, dict) else JOB

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()

SETUP = """([cid, jid]) => {
    const host = document.createElement('div'); host.id = 'zz-email-box'; host.style.display = 'none';
    document.body.prepend(host);
    window.__zzJob = jid;
    window.__zz = emailBox('zz', () => ({ candidate_id: cid, job_id: window.__zzJob }));
    window.__zz.set({ to: '', subject: 'Application', body: 'Hi Hiring Team,' });
}"""
js_errors, shot = [], os.path.join(tmp_dir, "find_recruiter.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1300, "height": 900})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.20" in page.content(), "cache-buster not bumped to 5.99.20")
        page.wait_for_function("typeof emailBox === 'function'", timeout=15000)
        page.evaluate(SETUP, [K, JOB])

        # 1. shown for a saved job
        check(page.is_visible("#btn-zz-email-find"), "Find recruiter shown for a saved job")

        # 2. search: recruiters first, free
        page.click("#btn-zz-email-find")
        page.wait_for_selector("#zz-email-team .em-person", timeout=10000)
        names = page.eval_on_selector_all("#zz-email-team .em-person b", "els => els.map(e => e.innerText)")
        check(names and names[0].startswith("Rita"), f"recruiters first: {names}")
        check(calls == ["/mixed_people/api_search"], f"search only, no credit: {calls}")

        # 3. cancelled confirm -> nothing; accepted -> verified email fills To
        page.once("dialog", lambda d: d.dismiss())
        page.locator("#zz-email-team .em-person:has-text('Rita') .em-reveal").click()
        page.wait_for_timeout(400)
        check("/people/match" not in calls and page.input_value("#zz-email-to") == "", "cancelled: no credit, To empty")
        msg = []
        page.once("dialog", lambda d: (msg.append(d.message), d.accept()))
        page.locator("#zz-email-team .em-person:has-text('Rita') .em-reveal").click()
        page.wait_for_function("document.getElementById('zz-email-to').value === 'rita@crystree.example'", timeout=10000)
        check(msg and "1 Apollo credit" in msg[0], f"credit asked first: {msg}")
        check(calls.count("/people/match") == 1, f"one credit: {calls}")
        check(page.locator("#zz-email-team .em-person.picked").count() == 1, "the picked person is marked")
        check((models.get_job_by_id(JOB) or {}).get("recruiter_email") == "rita@crystree.example", "the email is saved on the job")
        page.screenshot(path=shot, full_page=True)

        # 4. guessed email never fills To
        page.once("dialog", lambda d: d.accept())
        page.locator("#zz-email-team .em-person:has-text('Hari') .em-reveal").click()
        page.wait_for_function("document.querySelector('#zz-email-team .em-person:nth-child(3) .sa-warn, #zz-email-team .sa-warn')", timeout=10000)
        check(page.input_value("#zz-email-to") == "rita@crystree.example", "a guessed email doesn't replace To")

        # 5. search again: Rita saved -> Use, no new credit
        page.fill("#zz-email-to", "")
        page.click("#btn-zz-email-find")
        page.wait_for_selector("#zz-email-team .em-person .em-use", timeout=10000)
        before = calls.count("/people/match")
        page.locator("#zz-email-team .em-person:has-text('rita@crystree.example') .em-use").click()
        check(page.input_value("#zz-email-to") == "rita@crystree.example" and calls.count("/people/match") == before, "saved person: Use fills To, no credit")

        # 6. Free plan: the find-it-yourself links
        def free_plan(path, body):
            raise apollo_api.ApolloError("Your Apollo account is on the Free plan (fixture). Use the free way below.", plan_blocked=True)
        apollo_api.http_post = free_plan
        page.click("#btn-zz-email-find")
        page.wait_for_selector("#zz-email-team a[href*='apollo.io']", timeout=10000)
        check("Free plan" in page.inner_text("#zz-email-team") and page.locator("#zz-email-team a[href*='linkedin.com']").count() == 1, "Free plan: links to find it yourself")
        apollo_api.http_post = fake_post

        # 1b. no saved job (pasted requirement): no button
        page.evaluate("() => { window.__zzJob = null; window.__zz.reset(); window.__zz.set({ to: '', subject: 's', body: 'b' }); }")
        check(page.is_hidden("#btn-zz-email-find") and page.is_hidden("#zz-email-team"), "no job: no Find button, list closed")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: find the recruiter from the email step (Edge). Screenshot: {shot}")
