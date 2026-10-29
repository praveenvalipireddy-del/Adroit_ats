"""The Chrome extension's form filler (chrome_extension/filler.js) on a labelled sample application form, in Edge.

1. Reads the job: JSON-LD JobPosting (title, company, location, description); company from the ATS address otherwise.
2. Fills standard fields from the consultant profile: names, email, phone, LinkedIn, Yes/No dropdowns (work authorization,
   sponsorship), a Yes/No radio group (relocation), "Decline" for voluntary questions, a range dropdown (years).
3. Attaches the resume to the resume upload (not to the cover-letter upload).
4. Never overwrites a field that already has a value, never ticks consent boxes, never submits.
5. Questions it can't answer are returned for the AI; fillAnswers fills them (amber); required ones left get red.
6. React-style forms see the input/change events.
Run: python test_autofill_filler_browser.py
"""
import base64
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
FILLER = os.path.join(HERE, "chrome_extension", "filler.js")
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


FORM = """<!doctype html><html><head><title>Apply - Sample Fixture Co</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Senior Data Analyst (Fixture)",
"hiringOrganization":{"@type":"Organization","name":"Sample Fixture Co"},
"jobLocation":{"@type":"Place","address":{"addressLocality":"Dallas","addressRegion":"TX","addressCountry":"US"}},
"description":"<p>We need SQL, Power BI and Python. 5+ years of analytics experience. This is a labelled test fixture.</p>"}</script>
</head><body>
<h1>Senior Data Analyst (Fixture)</h1>
<form id="f" onsubmit="window.submitted=true; return false;">
 <div class="field"><label for="first_name">First Name *</label><input id="first_name" required></div>
 <div class="field"><label for="last_name">Last Name *</label><input id="last_name" required></div>
 <div class="field"><label for="email">Email *</label><input id="email" type="email" required></div>
 <div class="field"><label for="phone">Phone</label><input id="phone" type="tel"></div>
 <div class="field"><label for="q1">LinkedIn Profile</label><input id="q1"></div>
 <div class="field"><label for="city">City</label><input id="city" value="Already typed"></div>
 <div class="field"><label for="resume">Resume/CV *</label><input id="resume" type="file"></div>
 <div class="field"><label for="cover">Cover Letter</label><input id="cover" type="file"></div>
 <div class="field"><label for="auth">Are you legally authorized to work in the United States? *</label>
   <select id="auth" required><option value="">Select...</option><option value="1">Yes</option><option value="0">No</option></select></div>
 <div class="field"><label for="spon">Will you now or in the future require sponsorship for employment visa status? *</label>
   <select id="spon" required><option value="">Select...</option><option value="1">Yes</option><option value="0">No</option></select></div>
 <div class="field"><label for="yrs">Years of experience</label>
   <select id="yrs"><option value="">--</option><option>0-2 years</option><option>3-5 years</option><option>6-9 years</option><option>10+ years</option></select></div>
 <fieldset><legend>Are you willing to relocate?</legend>
   <label><input type="radio" name="reloc" value="y"> Yes</label><label><input type="radio" name="reloc" value="n"> No</label></fieldset>
 <div class="field"><label for="cctc">Current CTC</label><input id="cctc"></div>
 <div class="field"><label for="ectc">Expected CTC</label><input id="ectc"></div>
 <div class="field"><label for="f2f">Are you available for a face-to-face interview?</label>
   <select id="f2f"><option value="">Select...</option><option>Yes</option><option>No</option></select></div>
 <div class="field"><label for="gender">Gender</label>
   <select id="gender"><option value="">Please select</option><option>Male</option><option>Female</option><option>Decline To Self Identify</option></select></div>
 <div class="field"><label for="why">Why do you want to work at Sample Fixture Co? *</label><textarea id="why" required></textarea></div>
 <div class="field"><label for="clear">Do you hold an active security clearance? *</label><input id="clear" required></div>
 <div class="field"><label><input type="checkbox" id="consent"> I agree to the privacy policy</label></div>
 <input type="hidden" id="hid" value="keep">
 <button type="submit" id="submit">Submit application</button>
</form>
<script>
  window.reactSaw = {};
  document.getElementById('first_name').addEventListener('input', e => { window.reactSaw.first = e.target.value; });
  document.getElementById('auth').addEventListener('change', e => { window.reactSaw.auth = e.target.value; });
</script>
</body></html>"""

PROFILE = {"full_name": "Karun Fixture", "first_name": "Karun", "last_name": "Fixture", "email": "karun.fixture@example.invalid",
           "phone": "+1 469 555 0100", "linkedin_url": "https://www.linkedin.com/in/karun-fixture", "city": "Dallas",
           "work_authorized": "Yes", "needs_sponsorship": "No", "willing_to_relocate": "Yes", "years_experience": "6",
           "current_ctc": "18 LPA", "expected_salary": "25 LPA", "f2f_ready": "Yes",
           "gender": "Decline to self-identify", "race": "Decline to self-identify", "resume_filename": "Karun_Fixture.docx"}
RESUME = {"filename": "Karun_Fixture.docx", "mime": "application/octet-stream", "base64": base64.b64encode(b"fixture resume bytes").decode()}

js_errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page()
    page.on("pageerror", lambda e: js_errors.append(str(e)))
    page.route("https://boards.greenhouse.io/samplefixtureco/jobs/1", lambda r: r.fulfill(status=200, content_type="text/html", body=FORM))
    page.goto("https://boards.greenhouse.io/samplefixtureco/jobs/1")
    page.add_script_tag(path=FILLER)

    # ---- 1. job
    job = page.evaluate("window.JH.readJob()")
    check(job["title"] == "Senior Data Analyst (Fixture)" and job["company"] == "Sample Fixture Co" and job["location"] == "Dallas, TX, US",
          f"job from JSON-LD: {job}")
    check("Power BI" in job["description"] and "<p>" not in job["description"], f"description is text: {job['description'][:120]!r}")
    page.evaluate("document.querySelector('script[type=\"application/ld+json\"]').remove()")
    check(page.evaluate("window.JH.readJob().company") == "Samplefixtureco", "company from the Greenhouse address when there is no JSON-LD")

    # ---- 2-4. fill
    r = page.evaluate("([p, r]) => window.JH.fill(p, r)", [PROFILE, RESUME])
    v = lambda sel: page.eval_on_selector(sel, "e => e.value")  # noqa: E731
    check(v("#first_name") == "Karun" and v("#last_name") == "Fixture" and v("#email") == PROFILE["email"] and v("#phone") == PROFILE["phone"],
          "names / email / phone filled")
    check(v("#q1") == PROFILE["linkedin_url"], "LinkedIn filled")
    check(v("#auth") == "1" and v("#spon") == "0", f"work authorization Yes, sponsorship No: {v('#auth')} {v('#spon')}")
    check(page.eval_on_selector("#yrs", "e => e.selectedOptions[0].text") == "6-9 years", "years range picked")
    check(page.eval_on_selector("input[name=reloc][value=y]", "e => e.checked"), "relocation radio Yes")
    check(page.eval_on_selector("#gender", "e => e.selectedOptions[0].text") == "Decline To Self Identify", "voluntary question declined")
    check(r["resumeAttached"] and page.eval_on_selector("#resume", "e => e.files.length && e.files[0].name") == "Karun_Fixture.docx",
          "resume attached to the resume upload")
    check(page.eval_on_selector("#cover", "e => e.files.length") == 0, "cover letter upload left alone")
    check(v("#cctc") == "18 LPA" and v("#ectc") == "25 LPA", f"current CTC and expected CTC go to their own fields: {v('#cctc')} / {v('#ectc')}")
    check(page.eval_on_selector("#f2f", "e => e.selectedOptions[0].text") == "Yes", "F2F interview question answered")
    check(v("#city") == "Already typed", "a field with a value is never overwritten")
    check(not page.eval_on_selector("#consent", "e => e.checked"), "consent box never ticked")
    check(v("#hid") == "keep", "hidden field untouched")
    check(not page.evaluate("!!window.submitted"), "the form is never submitted")
    check(page.evaluate("window.reactSaw") == {"first": "Karun", "auth": "1"}, f"input/change events reach the page: {page.evaluate('window.reactSaw')}")
    check(page.eval_on_selector("#first_name", "e => e.getAttribute('data-jh-mark')") == "1", "filled fields are marked")
    labels = {q["label"]: q for q in r["questions"]}
    check(set(labels) == {"Why do you want to work at Sample Fixture Co? *", "Do you hold an active security clearance? *"},
          f"only the non-standard questions go to the AI: {list(labels)}")
    check(all(q["required"] for q in r["questions"]), "required flag read")

    # ---- 5. AI answers
    why = labels["Why do you want to work at Sample Fixture Co? *"]["id"]
    clr = labels["Do you hold an active security clearance? *"]["id"]
    out = page.evaluate("a => window.JH.fillAnswers(a)", {why: "I build SQL and Power BI reporting (fixture answer)."})
    check(out["filled"] == [why] and v("#why").startswith("I build SQL"), "AI answer filled")
    check("245, 158, 11" in page.eval_on_selector("#why", "e => getComputedStyle(e).outlineColor"), "AI answer marked amber")
    page.evaluate("ids => window.JH.markUnanswered(ids)", [clr])
    check("220, 38, 38" in page.eval_on_selector("#clear", "e => getComputedStyle(e).outlineColor"), "required question left -> red")
    check(v("#clear") == "", "the unanswerable question stays empty (nothing invented)")
    shot = os.path.join(os.environ.get("TEMP", HERE), "autofill_filler.png")
    page.screenshot(path=shot, full_page=True)

    # ---- second run doesn't overwrite what the first run (or the recruiter) filled
    page.fill("#first_name", "Typed By Recruiter")
    page.evaluate("([p, r]) => window.JH.fill(p, r)", [PROFILE, RESUME])
    check(v("#first_name") == "Typed By Recruiter", "a second Autofill keeps what the recruiter typed")
    check(page.evaluate("window.JH.clickApply().clicked") is False, "Apply is never clicked when a form is already there")
    check(page.evaluate("window.JH.hasNext()") == "", "'Submit application' is not a Next button")
    check(not page.evaluate("!!window.submitted"), "still never submitted")

    # ---- 7. a job page with no form: Apply opens it; an application step: section heading is not the title
    page.route("https://fixture.example.invalid/hcmUI/jobs/job/1", lambda r: r.fulfill(status=200, content_type="text/html", body="""
      <html><head><title>AI Developer II (Fixture) | Careers</title></head><body><h1>AI Developer II (Fixture)</h1>
      <div class="job-location">Location: Chandigarh, India</div><p>Labelled fixture job page.</p>
      <a href="#" id="apply" onclick="document.getElementById('f').hidden=false;window.applyClicked=true;return false;">Apply Now</a>
      <form id="f" hidden><label for="e">Email *</label><input id="e"><button type="submit" onclick="window.submitted=true;return false;">Submit</button></form>
      </body></html>"""))
    page.goto("https://fixture.example.invalid/hcmUI/jobs/job/1")
    page.add_script_tag(path=FILLER)
    j = page.evaluate("window.JH.readJob()")
    check(j["title"] == "AI Developer II (Fixture)" and j["location"] == "Chandigarh, India", f"title + location without 'Location:': {j}")
    c = page.evaluate("window.JH.clickApply()")
    check(c["clicked"] and page.evaluate("!!window.applyClicked") and page.evaluate("window.JH.formFieldCount()") == 1, f"Apply opened the form: {c}")
    check(not page.evaluate("!!window.submitted"), "opening the form submits nothing")

    page.route("https://fixture.example.invalid/hcmUI/jobs/job/1/apply/section/2", lambda r: r.fulfill(status=200, content_type="text/html", body="""
      <html><head><title>AI Developer II (Fixture) | Careers</title></head><body><h1>Work Summary</h1>
      <label for="co">Current Company</label><input id="co">
      <button id="back">Back</button><button id="nx" onclick="window.nextClicked=true">Next</button>
      <button onclick="window.submitted=true">Submit</button></body></html>"""))
    page.goto("https://fixture.example.invalid/hcmUI/jobs/job/1/apply/section/2")
    page.add_script_tag(path=FILLER)
    j = page.evaluate("window.JH.readJob()")
    check(j["title"] == "AI Developer II (Fixture)" and j["isApplyStep"], f"'Work Summary' is a step heading, not the title: {j}")
    check(page.evaluate("window.JH.hasNext()") == "Next", "Next button found")
    page.evaluate("window.JH.clickNext()")
    check(page.evaluate("!!window.nextClicked") and not page.evaluate("!!window.submitted"), "Next clicked, Submit never")
    browser.close()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"PASS: extension form filler - job read, standard fields + Yes/No + radio + ranges + decline, resume attached, nothing overwritten / ticked / submitted, AI questions filled amber, unanswered red. Screenshot: {shot}")
