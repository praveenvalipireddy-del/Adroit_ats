"""Vendor recruiters saved automatically from job requirements, with the real company name.

1. Company name: the vendor company the AI read wins; else the posting's company when it belongs to the email's
   domain; else a readable domain marked as a guess (and the posting's company kept as the end client).
2. Never saved: personal addresses (gmail ...) and job-board relays (Dice private email, Indeed, Naukri).
3. An existing contact is only enriched: empty fields filled, a guessed company replaced by a real one, what the
   recruiter typed never overwritten; a new job raises the requirement count and the last job.
4. Old contacts saved with a domain as the company ('pyramidci.com') get the real name from a matching job.
5. A recruiter's LinkedIn post gives their headline (title) and profile link.
6. The Jobs-tab email box saves the vendor (USA / India list from the job's market); Edge: the Vendors tab shows
   where they were found, the last job, "check name", and the "Added automatically" filter.
Labelled test fixtures. Run: python test_vendor_capture_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_vendor_capture.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import models  # noqa: E402
import vendors  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "capture-rec@example.invalid", "Capture-1"
REC = uid(models.create_user("Cara Capturetest", EMAIL, PW, role="Recruiter", region="Both"))

# ---- 1. company name
rc = vendors.resolve_company
check(rc("priya@pyramidci-capt.com", posting_company="Pyramid Consulting") == ("Pyramid Consulting", False, ""), "posting company that matches the email")
check(rc("priya@pyramidci-capt.com", posting_company="Bank of America") == ("Pyramidci-capt", True, "Bank of America"), "end client -> note, domain name as a guess")
check(rc("priya@pyramidci-capt.com", vendor_company="Pyramid Consulting Inc", posting_company="Bank of America")[0:3:2] == ("Pyramid Consulting Inc", "Bank of America"),
      "the vendor company the AI read wins; the posting's company is the client")
check(vendors.company_matches_email("Tata Consultancy Services", "a@tcs.com") and not vendors.company_matches_email("Wells Fargo", "a@tcs.com"), "initials match")
check(rc("ravi@techdigitalcorp.com", posting_company="(see post)")[1], "a placeholder company is not a company")

# ---- 2. never saved
conn = models.get_db_connection()
for e, why in (("someone@gmail.com", "personal"), ("abc123@user.dice.com", "relay"), ("x9@indeedemail.com", "relay"), ("r.k=@naukri.com", "relay")):
    r = vendors.capture(conn, REC, "USA", e, posting_company="Acme")
    check(r["status"] == "skipped", f"{e} not saved ({why}): {r}")

# ---- 3. create, then enrich without overwriting
r1 = vendors.capture(conn, REC, "USA", "Priya@PyramidCI-capt.com", posting_company="Bank of America", name="Priya S", job_title="DevOps Engineer",
                     job_url="https://www.dice.com/job-detail/capt-1", found_via="Dice job")
check(r1["status"] == "created" and r1["company"] == "Pyramidci-capt" and r1["company_guess"], f"created with a guessed name: {r1}")
cur = conn.cursor()
cur.execute("SELECT * FROM vendor_contacts WHERE id = ?", (r1["id"],))
row = dict(cur.fetchone())
check(row["source"] == "auto" and row["notes"] == "Client: Bank of America" and row["req_count"] == 1 and row["last_job_url"].endswith("capt-1")
      and row["email"] == "priya@pyramidci-capt.com" and row["found_via"] == "Dice job", f"saved fields: {row}")
cur.execute("UPDATE vendor_contacts SET contact_name = 'Priya Sharma (typed)' WHERE id = ?", (r1["id"],))
conn.commit()
r2 = vendors.capture(conn, REC, "USA", "priya@pyramidci-capt.com", vendor_company="Pyramid Consulting", name="P", phone="+1 469 555 0101",
                     job_title="SRE", job_url="https://www.dice.com/job-detail/capt-2", found_via="Dice job")
cur.execute("SELECT * FROM vendor_contacts WHERE id = ?", (r1["id"],))
row = dict(cur.fetchone())
check(r2["status"] == "updated" and row["company_name"] == "Pyramid Consulting" and not row["company_guess"] and row["phone"] == "+1 469 555 0101"
      and row["contact_name"] == "Priya Sharma (typed)" and row["req_count"] == 2 and row["last_job_title"] == "SRE", f"enriched, nothing typed overwritten: {row}")
r3 = vendors.capture(conn, REC, "USA", "priya@pyramidci-capt.com", job_title="SRE", job_url="https://www.dice.com/job-detail/capt-2")
check(r3["status"] == "unchanged", f"the same job again changes nothing: {r3}")
cur.execute("UPDATE vendor_contacts SET company_name = 'My Name For Them', company_guess = 0 WHERE id = ?", (r1["id"],))
conn.commit()
vendors.capture(conn, REC, "USA", "priya@pyramidci-capt.com", vendor_company="Another Name", job_url="https://www.dice.com/job-detail/capt-3")
cur.execute("SELECT company_name FROM vendor_contacts WHERE id = ?", (r1["id"],))
check(cur.fetchone()[0] == "My Name For Them", "a company the recruiter set is never replaced")
check(vendors.capture(conn, REC, "India", "priya@pyramidci-capt.com", posting_company="Pyramid Consulting")["status"] == "created", "India list is separate")

# ---- 4. old domain-only company names
cur.execute("""INSERT INTO vendor_contacts (owner_user_id, company_name, company_norm, email, email_domain, status, source, region)
               VALUES (?, 'apexcapt.com', 'apexcapt com', 'sam@apexcapt.com', 'apexcapt.com', 'active', 'paste', 'USA')""", (REC,))
conn.commit()
models.save_or_update_scraped_job({"title": "Java Captest", "company": "Apex Capt Systems", "source": "Dice", "url": "https://www.dice.com/job-detail/capt-9",
                                   "recruiter_email": "sam@apexcapt.com"})
check(vendors.fix_domain_company_names(conn) >= 1, "old domain company names fixed")
cur.execute("SELECT company_name, company_guess FROM vendor_contacts WHERE email = 'sam@apexcapt.com'")
check(tuple(cur.fetchone()) == ("Apex Capt Systems", 0), "real name from the job with the same recruiter email")

# ---- 5. recruiter post details
post_job = {"title": "DevOps Engineer", "company": "(see post)", "source": "Recruiter Post", "url": "https://www.linkedin.com/posts/riya-capt-1",
            "recruiter_name": "Riya Capt", "description": "Recruiter post by Riya Capt (Talent Acquisition at CloudHire)\nProfile: https://www.linkedin.com/in/riya-capt\nPosted: 3h on LinkedIn\n\nHiring..."}
r5 = vendors.capture_from_job(conn, REC, "USA", post_job, email="riya@cloudhire-capt.com")
cur.execute("SELECT title, linkedin_url, found_via, contact_name FROM vendor_contacts WHERE id = ?", (r5["id"],))
check(tuple(cur.fetchone()) == ("Talent Acquisition at CloudHire", "https://www.linkedin.com/in/riya-capt", "LinkedIn recruiter post", "Riya Capt"), "post details saved")
check(r5["company"] == "CloudHire" and not r5["company_guess"], f"company from the recruiter's headline: {r5}")
conn.close()

# ---- 6. the Jobs-tab email box + the Vendors tab
client = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Cara", "role": "Recruiter", "email": EMAIL}
jid = models.save_or_update_scraped_job({"title": "Data Engineer Captest", "company": "Infosys Capt", "source": "Naukri (Live)",
                                         "url": "https://www.naukri.com/job-listings-capt-5", "country": "India", "recruiter_name": "Anil K"})
jid = jid["id"] if isinstance(jid, dict) else jid
d = client.post("/api/jobs/update-email", json={"job_id": jid, "recruiter_email": "anil@infosyscapt.com"}).get_json()
check(d["vendor"] and d["vendor"]["status"] == "created" and d["vendor"]["region"] == "India" and d["vendor"]["company"] == "Infosys Capt",
      f"email box saves the vendor in the India list: {d.get('vendor')}")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "vendor_capture.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.13" in page.content(), "cache-buster not bumped to 5.99.13")
        page.click("a.nav-item[data-tab=vendors]")
        page.click("#vendors-tab-usa")
        page.wait_for_function("document.getElementById('vendors-body').innerText.includes('CloudHire')", timeout=10000)
        txt = page.inner_text("#vendors-body")
        check("Found via LinkedIn recruiter post" in txt and "DevOps Engineer ↗" in txt and "in ↗" in txt, f"where found + last job + LinkedIn: {txt[:400]!r}")
        check("Apex Capt Systems" in txt and "apexcapt.com\n" not in txt, "fixed company name shown")
        page.check("#vendors-auto")
        page.wait_for_timeout(300)
        auto = page.inner_text("#vendors-body")
        check("Apex Capt Systems" not in auto and "CloudHire" in auto, f"Added automatically filter: {auto[:300]!r}")
        check(page.locator("#vendors-body .vendor-guess").count() == 0, "no 'check name' when the company is known")
        page.screenshot(path=shot)
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: vendors captured from requirements - real company names, personal / relay emails skipped, enrich without overwriting, requirement count, old domain names fixed, recruiter post details, email box route, Vendors tab in Edge. Screenshot: {shot}")
