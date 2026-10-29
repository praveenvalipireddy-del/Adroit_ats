"""Browser test (Playwright + Microsoft Edge): the Optimize window's steps.

1 Match check: the match %, the parts of the score, "The job asks / The resume shows" (title, experience, ...) and the
  skills (matched / under other words / missing) - from the analysis only: nothing is rewritten yet.
2 Choose changes: a skill the resume really shows (a quote that is in the resume) is ticked with that quote as proof;
  one it doesn't show in words is not ticked; a missing must-have is not ticked ("only if he really has it");
  the expected match moves with the ticks; nothing ticked = nothing to apply.
3 Apply: the rewrite is told ONLY the ticked skills (a ticked missing one goes in Technical Skills only), then the
  resume is shown; 4 Email: the email, then Create Draft.
"Send resume as is": straight to the email; the draft attaches the original resume and no version is saved.
The AI and Gmail are stubs - no network, no cost. Labelled test fixtures.
Run: python test_optimize_steps_browser.py
"""
import email
import email.policy
import io
import json
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_opt_steps.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = ""
config.GEMINI_API_KEY = "test-key-not-real"
import app as app_module  # noqa: E402
import docx  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import models  # noqa: E402
import resume_bot as rb  # noqa: E402
import resume_versions  # noqa: E402
import _optimize_steps  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

failures, js_errors, ai_calls, rewrite_prompts, captured = [], [], [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


RESUME = ["ARUN STEPTEST", "Senior DevOps Engineer",
          "DevOps engineer with 7 years building cloud infrastructure on AWS with Terraform.",
          "Sample Bank Co. (fixture): built event streaming between the payment services with Kafka topics.",
          "Sample Retail (fixture): packaged the services as Helm charts for EKS."]
ANALYSIS = {"initial_match_percentage": 62,
            "match_breakdown": {"mandatory_skills": 25, "recent_project_relevance": 16, "domain_experience": 11,
                                "tools_frameworks_cloud": 6, "certifications_education": 2, "location_work_authorization": 2},
            "domain_detected": "Payments", "strong_match_skills": ["AWS", "Terraform"], "partial_match_skills": ["Kafka"],
            "mandatory_missing_skills": ["ISO 8583"], "preferred_missing_skills": [], "risky_skills_avoided": ["ISO 8583 - not in the resume"],
            "skills_to_add": {"summary": ["Kafka"], "technical_skills": ["Kafka", "Helm", "Prometheus"], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 84,
            "skill_evidence": {"Kafka": "built event streaming between the payment services with Kafka topics",
                               "Helm": "packaged the services as Helm charts for EKS",
                               "Prometheus": "monitored everything with Prometheus dashboards"}}   # not in the resume -> no proof


def fake_generate(client, types, system, prompt, want_json, max_tokens, **_k):
    if want_json and rb._ANALYSIS_CONTRACT in system:
        ai_calls.append("analysis")
        return json.dumps(ANALYSIS), False, "stub"
    if not want_json:
        ai_calls.append("rewrite")
        rewrite_prompts.append(prompt)
        return "[2] DevOps engineer for payments platforms with 7 years building cloud infrastructure on AWS with Terraform and Kafka.", False, "stub"
    ai_calls.append("other")
    return json.dumps({}), False, "stub"


rb._generate = fake_generate
rb.gemini_configured = lambda: True
rb.ai_configured = lambda: True


class _FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        captured.append(email.message_from_bytes(raw, policy=email.policy.default))

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = _FakeIMAP

# ---- backend: nothing chosen = nothing changed
r0 = rb.optimize_resume_for_jd("\n".join(RESUME), "Senior DevOps Engineer (fixture JD). " * 10, approved=[])
check(not r0.get("optimized") and "No changes were chosen" in (r0.get("not_optimized_reason") or ""), f"nothing ticked -> unchanged: {r0.get('not_optimized_reason')}")

ai_calls.clear()
EMAIL, PW = "steps-rec@example.invalid", "Steps-Test-1"
user = models.create_user("Sara Stepstest", EMAIL, PW, role="Admin")
uid = user["id"] if isinstance(user, dict) else user
doc = docx.Document()
for line in RESUME:
    doc.add_paragraph(line)
buf = io.BytesIO()
doc.save(buf)
cid = models.create_candidate("Arun Steptest", "arun.steptest@example.invalid", title="Senior DevOps Engineer", experience_years=7,
                              primary_skills="AWS, Terraform", assigned_user_id=uid, resume_filename="arun_steptest.docx")
models.save_resume_file(cid, "arun_steptest.docx", buf.getvalue())
models.update_candidate(cid, gmail_account="arun.steptest@example.invalid", gmail_app_password="test-app-password-not-real",
                        location="Dallas, TX", resume_text="\n".join(RESUME))
JD = ("Senior DevOps Engineer (fixture) - Sample Bank, Dallas, TX. 5-8 years of experience. Must have: AWS, Terraform, Kafka, Helm, "
      "ISO 8583 payment messages. Build and run the payment platform's infrastructure. " * 3)


def _job(d):
    j = models.save_or_update_scraped_job(d)
    return j["id"] if isinstance(j, dict) else j


j1 = _job({"title": "Senior DevOps Engineer Steptest", "company": "Sample Bank", "location": "Dallas, TX", "source": "Manual Paste", "url": "",
           "recruiter_email": "mark@samplebank-vendor.example", "description": JD})
j2 = _job({"title": "Platform Engineer Steptest", "company": "Sample Retail", "location": "Remote", "source": "Manual Paste", "url": "",
           "recruiter_email": "nina@sampleretail-vendor.example", "description": JD.replace("Senior DevOps", "Platform")})

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
shots = {k: os.path.join(tmp_dir, f"steps_{k}.png") for k in ("match", "choose", "email")}
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        page.wait_for_function("typeof openOptimizePanel === 'function' && state.consultants && state.consultants.length", timeout=15000)

        # ---- 1. Match check
        page.evaluate(f"openOptimizePanel({{ mode: 'job', candId: {cid}, jobId: {j1}, title: 'Senior DevOps Engineer Steptest', jdText: {json.dumps(JD)} }})")
        page.wait_for_function(_optimize_steps.READY, timeout=20000)
        s1 = page.inner_text("#do-step1")
        check("62%" in page.inner_text("#do-step1 .mc-pct") and "Fair match" in s1, f"match % and band: {s1[:120]!r}")
        check("Must-have skills" in s1 and "25 / 40" in s1, "the parts of the score")
        check("Experience" in s1 and "5-8 years" in s1 and "7 years" in s1 and "Job title" in s1, f"job vs resume rows: {s1!r}")
        check("✗ ISO 8583" in s1 and "~ Kafka" in s1 and "✓ AWS" in s1, "skills: matched / other words / missing")
        check(ai_calls == ["analysis"] and page.input_value("#do-optimized") == "", f"nothing rewritten yet: {ai_calls}")
        check(page.is_visible("#btn-do-asis") and not page.is_visible("#btn-do-save"), "Send as is offered; no Save before the email step")
        page.screenshot(path=shots["match"])

        # ---- 2. Choose changes
        page.click("#btn-do-next")
        ticked = page.eval_on_selector_all("#do-step2 input[data-ch]", "els => Object.fromEntries(els.map(e => [e.dataset.ch, e.checked]))")
        check(ticked == {"align": True, "skill:Kafka": True, "skill:Helm": True, "skill:Prometheus": False, "skill:ISO 8583": False},
              f"proof decides what is ticked: {ticked}")
        s2 = page.inner_text("#do-step2")
        check('Found in his resume: "built event streaming between the payment services with Kafka topics"' in s2, "the proof is shown")
        check("add only if he really has it" in s2.lower(), "a missing must-have is marked 'only if true'")
        check(page.inner_text("#btn-do-next") == "Apply 3 changes →" and "about" in page.inner_text("#do-proj"), "Apply count + expected match")
        page.evaluate("document.querySelectorAll('#do-step2 input[data-ch]').forEach(i => { i.checked = false; i.dispatchEvent(new Event('change')); })")
        check(page.is_disabled("#btn-do-next") and page.inner_text("#do-proj") == "no change", "nothing ticked: nothing to apply")
        for cb in ("align", "skill:Kafka", "skill:Helm", "skill:ISO 8583"):
            page.check(f"#do-step2 input[data-ch='{cb}']")
        page.screenshot(path=shots["choose"])

        # ---- 3. Apply: only the ticked changes
        page.click("#btn-do-next")
        page.wait_for_function("optPanel.step === 3", timeout=20000)
        prompt = rewrite_prompts[-1] if rewrite_prompts else ""
        tech = next((l for l in prompt.splitlines() if l.strip().startswith("Technical Skills:")), "")
        check("Kafka" in tech and "Helm" in tech and "ISO 8583" in tech and "Prometheus" not in prompt, f"only the ticked skills go to the rewrite: {tech!r}")
        check(page.is_visible("#do-step3") and "change" in page.inner_text("#do-status") and "Kafka" in page.input_value("#do-optimized"),
              f"the changed resume is shown: {page.inner_text('#do-status')[:120]!r}")

        # ---- 4. Email, then the draft
        page.wait_for_function("document.getElementById('do-email-body') && document.getElementById('do-email-body').value.length > 20", timeout=15000)
        _optimize_steps.to_email(page)
        check(page.is_visible("#do-email-box") and page.is_visible("#btn-do-save") and page.input_value("#do-email-to") == "mark@samplebank-vendor.example",
              "step 4: the email with Create Draft")
        page.screenshot(path=shots["email"])
        page.click("#btn-close-draft-optimize")

        # ---- "Send resume as is": the original goes, nothing saved
        before = len(captured)
        page.evaluate(f"openOptimizePanel({{ mode: 'job', candId: {cid}, jobId: {j2}, title: 'Platform Engineer Steptest', jdText: {json.dumps(JD.replace('Senior DevOps', 'Platform'))} }})")
        page.wait_for_function(_optimize_steps.READY, timeout=20000)
        page.click("#btn-do-asis")
        page.wait_for_function("optPanel.step === 4 && document.getElementById('do-email-body').value.length > 20", timeout=15000)
        check("as it is" in page.inner_text("#do-status") and "original resume" in page.inner_text("#do-filename"), "sending as is is said plainly")
        page.click("#btn-do-save")
        page.wait_for_function(f"document.querySelector('.toast-container') && document.querySelector('.toast-container').innerText.includes('Gmail Draft Created')", timeout=15000)
        check(len(captured) == before + 1, "a draft was created")
        if len(captured) > before:
            att = [a.get_filename() for a in captured[-1].iter_attachments()]
            check(att == ["arun_steptest.docx"], f"the original resume is attached: {att}")
        check(resume_versions.list_for(cid) == [], "no tailored version saved for 'as is'")
        browser.close()
finally:
    server.shutdown()
check(not js_errors, f"JavaScript errors: {js_errors}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshots:", shots)
    sys.exit(1)
print(f"PASS: Optimize steps - match check (score parts, job vs resume, skills) with nothing rewritten, changes ticked by proof, "
      f"only ticked changes rewritten (a ticked missing skill -> Skills only), email step, 'send as is' attaches the original. Screenshots: {shots}")
