"""Browser test (Playwright + Microsoft Edge): message a sourced candidate on LinkedIn.

A. Job Hunt: the Sourcing card's "Message on LinkedIn" opens the draft window - template filled with the candidate's
   first name and the role, character counter (red + Open disabled over 300 in note mode, 1200 in message mode),
   AI draft (stub AI), Save as template, "Open in LinkedIn" opens the profile with the #jh-li draft, "I already sent
   it" marks the card (Messaged on LinkedIn, status Contacted), "Follow up in 3 days"; the window fits a phone.
B. The extension's LinkedIn helper (chrome_extension/linkedin.js) on labelled fixture pages - NOT linkedin.com:
   - message mode: clicks Message (opens the box only), types the draft, never clicks Send;
   - note mode: does NOT click Connect (outlines it); after the recruiter clicks Connect > Add a note, types the note;
   - a box limited to fewer characters than the draft: nothing typed, the limit is explained;
   - "I sent it" reports the candidate and the kind; "Didn't send" closes the box.
No network beyond the local app. People are labelled test fixtures.
Run: python test_linkedin_message_browser.py
"""
import json
import os
import socket
import sys
import tempfile
import threading
from datetime import date, timedelta
from urllib.parse import unquote

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_li_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import linkedin_ingest as li  # noqa: E402
import models  # noqa: E402
import resume_bot  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "chrome_extension", "linkedin.js")
EMAIL, PASSWORD = "li-ui@example.invalid", "LiUi-Test-1"
models.create_user("Priya Liuitest", EMAIL, PASSWORD, role="Recruiter")
li.ingest_profiles(li.ApifyProfileProvider().to_profiles([{
    "linkedinUrl": "https://www.linkedin.com/in/li-ui-fixture.example.invalid", "firstName": "Ravi", "lastName": "Liuitest",
    "headline": "Data Engineer", "location": {"linkedinText": "Dallas, Texas"},
    "currentPosition": [{"position": "Data Engineer", "companyName": "Test Co"}],
    "education": [{"schoolName": "CBIT", "degree": "B.Tech", "endDate": {"year": 2020}},
                  {"schoolName": "UNT", "degree": "MS", "endDate": {"year": 2023}}]}]), "apify", None)
resume_bot.generate_chat = lambda system, prompt, max_tokens=3000: (
    json.dumps({"message": "Hi Ravi, your Data Engineer work fits a Spark role I'm hiring for. Open to a quick chat?"}), "stub")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---- fixture pages for the helper (labelled; behave like a profile page's buttons and boxes)
STUB = """<script>
window.__sentMsgs = [];
window.chrome = { runtime: { sendMessage(msg, cb) {
  window.__sentMsgs.push(msg);
  if (msg.type === 'jh-li-get') setTimeout(() => cb && cb(window.__draft), 0);
  else if (msg.type === 'jh-li-sent') setTimeout(() => cb && cb({ ok: true }), 0);
  else if (cb) setTimeout(() => cb(true), 0);
} } };
</script>"""
MSG_PAGE = STUB + """<title>Fixture profile (not LinkedIn)</title><main><h1>Fixture Person</h1>
<button aria-label="Message Fixture Person" onclick="window.msgClicked=true; document.getElementById('dock').innerHTML =
 '<div class=msg-form><div class=msg-form__contenteditable contenteditable=true role=textbox style=min-height:40px></div>' +
 '<button onclick=window.sent=true>Send</button></div>'">Message</button>
<button aria-label="More actions">More</button></main><div id="dock"></div>
<aside><button aria-label="Message Someone Else">Message</button></aside>"""
NOTE_PAGE = STUB + """<title>Fixture profile (not LinkedIn)</title><main><h1>Fixture Person</h1>
<button id="conn" aria-label="Invite Fixture Person to connect" onclick="window.connectClicked=true; document.getElementById('dlg').innerHTML =
 '<div role=dialog><button id=addnote onclick=&quot;this.parentNode.innerHTML=\\'<textarea name=message maxlength=MAXLEN></textarea><button onclick=window.sent=true>Send</button>\\'&quot;>Add a note</button>' +
 '<button onclick=window.sent=true>Send without a note</button></div>'">Connect</button></main><div id="dlg"></div>"""

shot = os.path.join(tmp_dir, "li.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 950})
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{port}")
        page = ctx.new_page()
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.99.20" in page.content(), "cache-buster not bumped to 5.99.20")
        page.evaluate("localStorage.removeItem('jh-li-template'); localStorage.removeItem('jh-li-role')")

        # ---------------- A. Job Hunt
        page.click("a.nav-item[data-tab=sourcing]")
        page.click(".edu-tab[data-filter=A]")
        page.fill("#edu-inst-input", "CBIT")
        page.wait_for_selector(".edu-inst-option")
        page.click(".edu-inst-option")
        page.wait_for_function("document.querySelectorAll('#edu-results tr[data-pid]').length === 1")
        row = page.locator("#edu-results tr[data-pid]")
        pid = int(row.get_attribute("data-pid"))
        row.locator("td").nth(2).click()
        page.wait_for_selector("#trk-li-open")
        check(page.locator("#trk-li-sent").count() == 0, "no 'messaged' mark before sending")
        page.click("#trk-li-open")
        page.wait_for_selector("#li-modal")
        text = page.input_value("#li-text")
        check(text.startswith("Hi Ravi,") and "Data Engineer" in text and "[role]" in text and "Priya" in text, f"template filled: {text!r}")
        page.fill("#li-role", "Spark Developer")
        page.click("#li-tpl")
        text = page.input_value("#li-text")
        check("Spark Developer role" in text and "[role]" not in text, f"role in template: {text!r}")
        check(page.inner_text("#li-count") == f"{len(text)} / 300", f"counter: {page.inner_text('#li-count')}")
        page.fill("#li-text", "x" * 301)
        check(page.is_disabled("#li-go") and "301 / 300" in page.inner_text("#li-count"), "over 300 in note mode: Open disabled")
        page.check("input[name=li-mode][value=message]")
        check(not page.is_disabled("#li-go") and "/ 1200" in page.inner_text("#li-count"), "message mode allows 1200")
        page.check("input[name=li-mode][value=note]")

        page.screenshot(path=os.path.join(tmp_dir, "li_modal.png"))
        page.click("#li-ai")
        page.wait_for_function("document.getElementById('li-text').value.startsWith('Hi Ravi, your Data Engineer work fits a Spark role')")
        page.fill("#li-text", "Hi Ravi, quick question about a Spark Developer role. Free this week? - Priya")
        page.click("#li-tpl-save")
        saved = page.evaluate("localStorage.getItem('jh-li-template')")
        check(saved == "Hi {first_name}, quick question about a {role} role. Free this week? - Priya", f"template saved with words: {saved!r}")
        check(page.evaluate("localStorage.getItem('jh-li-role')") == "Spark Developer", "role remembered")

        page.evaluate("() => { window.__opened = []; window.open = (u) => { window.__opened.push(String(u)); return null; }; }")
        page.click("#li-go")
        opened = page.evaluate("window.__opened")
        check(len(opened) == 1 and opened[0].startswith("https://www.linkedin.com/in/li-ui-fixture.example.invalid#jh-li="), f"opened: {opened}")
        draft = json.loads(unquote(opened[0].split("#jh-li=", 1)[1])) if opened else {}
        check(draft.get("p") == pid and draft.get("k") == "note" and draft.get("n") == "Ravi Liuitest"
              and draft.get("m", "").startswith("Hi Ravi, quick question"), f"draft in the address: {draft}")
        check(page.locator("#li-modal").count() == 0, "window closes after Open in LinkedIn")

        page.click("#trk-li-open")
        page.wait_for_selector("#li-modal")
        check(page.input_value("#li-text").startswith("Hi Ravi, quick question about a Spark Developer role"), "saved template used next time")
        page.set_viewport_size({"width": 390, "height": 800})
        wide = page.evaluate("document.querySelector('#li-modal section').getBoundingClientRect().right <= window.innerWidth")
        check(wide, "draft window fits a phone screen")
        page.set_viewport_size({"width": 1440, "height": 950})
        page.click("#li-sent")
        page.wait_for_selector("#trk-li-sent")
        check("Messaged on LinkedIn" in page.inner_text("#trk-li-sent") and "Priya Liuitest" in page.inner_text("#trk-li-sent"), "card shows the mark")
        check(page.input_value("#trk-card-status") == "Contacted", "status moved to Contacted")
        check("Connection request + note sent on LinkedIn" in page.inner_text("#trk-activity"), "activity shows the send")
        check("Message again on LinkedIn" in page.inner_text("#trk-li-open"), "button says Message again")
        page.click(".trk-li-quick[data-act=followup]")
        in3 = (date.today() + timedelta(days=3)).isoformat()
        page.wait_for_function(f"document.getElementById('trk-followup').value === '{in3}'")
        page.click(".trk-li-quick[data-act=Interested]")
        page.wait_for_function("document.getElementById('trk-card-status').value === 'Interested'")
        page.screenshot(path=shot)

        # ---------------- B. the extension helper on fixture pages
        def helper_page(html, draft_obj):
            hp = ctx.new_page()
            hp.on("pageerror", lambda e: js_errors.append("helper: " + str(e)))
            hp.set_content(html)
            hp.evaluate("d => { window.__draft = d; }", draft_obj)
            hp.add_script_tag(path=HELPER)
            hp.wait_for_selector("#jobhunt-li-helper")
            return hp

        DRAFT = "Hi Fixture, your data work fits a Spark role. Open to a chat? - Priya"
        hp = helper_page(MSG_PAGE, {"p": 7, "k": "message", "m": DRAFT, "n": "Fixture Person"})
        hp.wait_for_function("document.querySelector('.msg-form__contenteditable') && document.querySelector('.msg-form__contenteditable').textContent.length > 0")
        check(hp.evaluate("document.querySelector('.msg-form__contenteditable').textContent") == DRAFT, "message typed into the box")
        check(hp.evaluate("window.msgClicked === true && !window.sent"), "Message clicked (box opened), Send NOT clicked")
        hp.screenshot(path=os.path.join(tmp_dir, "li_helper.png"))
        steps = hp.locator("#jobhunt-li-helper #steps").inner_text()
        check("Typed your message" in steps and "click Send on LinkedIn" in steps, f"helper steps: {steps}")
        hp.locator("#jobhunt-li-helper #sent").click()
        hp.wait_for_function("window.__sentMsgs.some(m => m.type === 'jh-li-sent')")
        sent = [m for m in hp.evaluate("window.__sentMsgs") if m["type"] == "jh-li-sent"][0]
        check(sent == {"type": "jh-li-sent", "p": 7, "k": "message"}, f"reported: {sent}")
        hp.wait_for_function("!document.getElementById('jobhunt-li-helper')", timeout=6000)
        check(not hp.evaluate("!!window.sent"), "still never sent by the helper")
        hp.close()

        NOTE = "Hi Fixture, your data work fits a Spark role. Open to a chat? - Priya"
        hp = helper_page(NOTE_PAGE.replace("MAXLEN", "300"), {"p": 8, "k": "note", "m": NOTE, "n": "Fixture Person"})
        hp.wait_for_timeout(1200)
        check(not hp.evaluate("!!window.connectClicked"), "helper does NOT click Connect")
        check("f59e0b" in (hp.evaluate("document.getElementById('conn').style.outline") or "").lower().replace("rgb(245, 158, 11)", "f59e0b"),
              "Connect outlined for the recruiter")
        check("then \"Add a note\"" in hp.locator("#jobhunt-li-helper #steps").inner_text(), "tells the recruiter what to click")
        hp.click("#conn")          # the recruiter's own clicks
        hp.click("#addnote")
        hp.wait_for_function("document.querySelector('textarea[name=message]') && document.querySelector('textarea[name=message]').value.length > 0")
        check(hp.input_value("textarea[name=message]") == NOTE and not hp.evaluate("!!window.sent"), "note typed, not sent")
        hp.locator("#jobhunt-li-helper #nosend").click()
        check(hp.locator("#jobhunt-li-helper").count() == 0 and any(m["type"] == "jh-li-done" for m in hp.evaluate("window.__sentMsgs")),
              "Didn't send closes and forgets the draft")
        hp.close()

        hp = helper_page(NOTE_PAGE.replace("MAXLEN", "200"), {"p": 9, "k": "note", "m": "y " * 125, "n": "Fixture Person"})
        hp.click("#conn")
        hp.click("#addnote")
        hp.wait_for_function("document.querySelector('#jobhunt-li-helper').shadowRoot.getElementById('steps').innerText.includes('allows 200 characters')")
        check(hp.input_value("textarea[name=message]") == "", "too-long note not typed into a 200-character box")
        hp.close()
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    print("screenshot:", shot)
    sys.exit(1)
print("PASS test_linkedin_message_browser", "(screenshots in " + tmp_dir + ")" if os.environ.get("SHOW_SHOTS") else "")
