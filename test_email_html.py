"""Draft emails look like a normal email typed in Gmail.

The draft has text/plain AND text/html. The HTML is what Gmail itself writes when someone types an
email: left-aligned, full width, the reader's normal font, a blank line between paragraphs, the
signature as plain lines - no centred fixed-width column, tables or styled blocks (the recruiter
asked for "realistic", not a newsletter look). Everything is HTML-escaped; email / LinkedIn links
are clickable; the resume is still attached. Rendered in Microsoft Edge: the text starts at the
left edge and uses the full width at desktop size, and never scrolls sideways on a phone.
Gmail is never contacted (imaplib stubbed). People are labelled test fixtures.
Run: python test_email_html.py
"""
import email
import email.policy
import io
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_email_html.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import docx  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

models.init_db()
failures, captured = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        captured.append(email.message_from_bytes(raw, policy=email.policy.default))

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP

d = docx.Document()
d.add_paragraph("KOUSHIK MAILTEST - test resume fixture")
buf = io.BytesIO()
d.save(buf)
cid = models.create_candidate("Koushik Mailtest <b>", "koushik-mailtest@example.invalid", title="Java Full Stack Developer",
                              experience_years=6, phone="+1 555 0100", primary_skills="Java, Spring Boot, React, AWS",
                              gmail_account="koushik-mailtest@example.invalid")
models.update_candidate(cid, gmail_app_password="test-app-password-not-real", location="Dallas, TX", country="United States",
                        resume_summary="Profile: https://www.linkedin.com/in/koushik-mailtest")
models.save_resume_file(cid, "Koushik_Mailtest.docx", buf.getvalue())
jid = models.save_or_update_scraped_job({"title": "Java Full Stack Developer", "company": "Mail Test Co", "source": "Dice",
                                         "url": "https://www.dice.com/job-detail/mailtest", "recruiter_email": "rec@example.invalid",
                                         "description": "Java, Spring Boot, React and AWS required."})
jid = jid["id"] if isinstance(jid, dict) else jid

res = gm.create_candidate_draft(cid, jid)
check(res.get("success") and res.get("resume_attached"), f"draft created with resume: {res}")
msg = captured[-1]
plain = msg.get_body(preferencelist=("plain",)).get_content()
html = msg.get_body(preferencelist=("html",)).get_content().strip()
check("Hi Hiring Team," in plain and "Best regards," in plain, "plain text version unchanged")
check(any(p.get_filename() == "Koushik_Mailtest.docx" for p in msg.iter_attachments()), "resume still attached")
check(html.startswith('<div dir="ltr"><div>Hi Hiring Team,</div><div><br></div>'), f"Gmail-style lines: {html[:80]!r}")
for banned in ("<table", "max-width", "width=", "font-family", "font-size", "text-align:center", "align=\"center\"", "<style", "<!--[if"):
    check(banned not in html, f"no newsletter layout: found {banned!r}")
check("Best regards,<br>Koushik Mailtest &lt;b&gt;<br>Java Full Stack Developer<br>Phone: +1 555 0100<br>Email: " in html,
      "signature = plain lines, name escaped")
check('<a href="mailto:koushik-mailtest@example.invalid">' in html and '<a href="https://www.linkedin.com/in/koushik-mailtest">' in html,
      "email + LinkedIn clickable")
check(html.count("<div><br></div>") == plain.strip().count("\n\n"), "one blank line between paragraphs, like the plain text")

# custom (AI Copilot) bodies get the same treatment
gm.create_candidate_draft(cid, jid, custom_subject="Hello", custom_body="Hi Sam,\n\nShort note.\n\nThanks,\nKoushik")
h2 = captured[-1].get_body(preferencelist=("html",)).get_content()
check("<div>Short note.</div>" in h2 and "<div>Thanks,<br>Koushik</div>" in h2, f"custom body: {h2!r}")

# ---- render in Edge: flush left, full width on desktop, no sideways scroll on a phone
path = os.path.join(tmp_dir, "email.html")
open(path, "w", encoding="utf-8").write("<!doctype html><meta charset='utf-8'><body style='margin:16px'>" + html + "</body>")
shots = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    for w in (1280, 390):
        page = browser.new_page(viewport={"width": w, "height": 900})
        page.goto("file:///" + path.replace("\\", "/"))
        box = page.eval_on_selector("div[dir=ltr]", "e => { const r = e.getBoundingClientRect(); return [r.left, r.width]; }")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(box[0] <= 17 and overflow <= 1, f"{w}px: starts at the left edge ({box[0]}px), sideways scroll {overflow}px")
        if w == 1280:
            check(box[1] > 1200, f"desktop: uses the full width, not a narrow column ({box[1]}px)")
        shots.append(os.path.join(tmp_dir, f"email_{w}.png"))
        page.screenshot(path=shots[-1], full_page=True)
        page.close()
    browser.close()

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshots:", shots)
    sys.exit(1)
print(f"PASS: draft email looks hand-typed - Gmail-style lines, left-aligned full width, plain signature, escaped, links. Screenshots: {shots}")
