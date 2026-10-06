"""Draft emails: a professional HTML version next to the plain text.

The draft has text/plain (unchanged wording) AND text/html: table-based layout with inline CSS
only (Outlook / Microsoft 365), 760px max width, centred, Arial/Calibri 14px, line-height 1.6, a
signature block (name in bold, contact lines, clickable email/LinkedIn), everything HTML-escaped,
the resume still attached. The HTML is opened in Microsoft Edge at desktop and phone width: content
is at most 760px wide and never wider than the phone screen.
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

models.init_db()
from playwright.sync_api import sync_playwright  # noqa: E402

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
html = msg.get_body(preferencelist=("html",)).get_content()
check("Hi Hiring Team," in plain and "Best regards," in plain, "plain text version unchanged")
check(any(p.get_filename() == "Koushik_Mailtest.docx" for p in msg.iter_attachments()), "resume still attached")
check("max-width:760px" in html and 'width="760"' in html and "<!--[if mso]>" in html, "760px, with an Outlook (mso) fixed-width wrapper")
check("font-family:Arial, Calibri" in html and "font-size:14px" in html and "line-height:1.6" in html, "font + size + line height")
check("<style" not in html and "class=" not in html, "inline CSS only (Outlook ignores <style> classes)")
check('role="presentation"' in html, "table-based layout")
check("Koushik Mailtest &lt;b&gt;" in html and "Koushik Mailtest <b>" not in html, "name is HTML-escaped")
check('font-weight:bold; color:#111827;">Koushik Mailtest &lt;b&gt;</div>' in html, "signature: name in bold")
check('href="mailto:koushik-mailtest@example.invalid"' in html and 'href="https://www.linkedin.com/in/koushik-mailtest"' in html,
      "email + LinkedIn clickable")

# custom (AI Copilot) bodies get the same HTML treatment
gm.create_candidate_draft(cid, jid, custom_subject="Hello", custom_body="Hi Sam,\n\nShort note.\n\nThanks,\nKoushik")
h2 = captured[-1].get_body(preferencelist=("html",)).get_content()
check("Short note." in h2 and 'font-weight:bold; color:#111827;">Koushik</div>' in h2, "custom body rendered with signature")

# ---- render in Edge: desktop and phone
path = os.path.join(tmp_dir, "email.html")
open(path, "w", encoding="utf-8").write(html)
shots = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    for w in (1280, 390):
        page = browser.new_page(viewport={"width": w, "height": 900})
        page.goto("file:///" + path.replace("\\", "/"))
        content_w = page.evaluate("Math.max(...[...document.querySelectorAll('table[style*=\"max-width\"]')].map(t => t.getBoundingClientRect().width))")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(content_w <= 760.5 and overflow <= 1, f"{w}px wide: content {content_w}px, sideways scroll {overflow}px")
        if w == 1280:
            check(content_w > 700, f"desktop uses the full 760px: {content_w}")
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
print(f"PASS: draft email HTML - text+html, 760px table layout, inline CSS, signature, escaped, Edge desktop+phone. Screenshots: {shots}")
