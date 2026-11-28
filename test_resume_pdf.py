"""PDF upload, original download, reviewed export, and saved versions for an India Individual.

Uses a two-page fixture with two columns, a colored header, and a neighboring text run.
No external services. Run with python test_resume_pdf.py.
"""
import io
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import patch

tmp = Path(tempfile.mkdtemp(prefix="jobhunt-pdf-test-"))
os.environ["DATABASE_URL"] = ""
os.environ["DB_PATH"] = str(tmp / "pdf-test.db")
os.environ["CAREER_SYNC_HOURS"] = os.environ["REPLY_CHECK_MINUTES"] = "0"
import app
import individuals
import models
import pypdfium2 as pdf
import resume_bot
import resume_pdf
from PIL import ImageChops
from reportlab.pdfgen.canvas import Canvas
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

app.RESUMES_DIR = str(tmp)
client = app.app.test_client()
email, password = "pdf-ui@example.invalid", token_urlsafe(18)
user = models.create_user("PDF Fixture", email, password, role="Individual", region="India")
uid = user["id"] if isinstance(user, dict) else user
cid = models.create_candidate("PDF Fixture", email, assigned_user_id=uid, country="India")
token = token_urlsafe(8)
job = models.save_or_update_scraped_job({"title": "PDF fixture", "company": "Test fixture", "source": "LinkedIn",
                                         "url": "https://example.invalid/" + token, "country": "India"})
with client.session_transaction() as session:
    session["user"] = {"id": uid, "email": email, "name": "PDF Fixture", "role": "Individual"}

buf = io.BytesIO()
c = Canvas(buf, pagesize=(600, 760))
c.setFillColorRGB(0.08, 0.21, 0.34)
c.rect(0, 674, 600, 86, fill=1, stroke=0)
c.setFillColorRGB(1, 1, 1)
c.setFont("Helvetica-Bold", 22)
c.drawString(40, 704, "PDF Fixture")
c.setFillColorRGB(0.08, 0.14, 0.23)
c.setFont("Helvetica-Bold", 14)
c.drawString(40, 636, "EXPERIENCE")
c.drawString(385, 636, "SKILLS")
c.setFont("Helvetica", 12)
c.drawString(40, 605, "Built SQL reports.")
c.drawString(385, 605, "SQL and Python")
c.drawString(40, 578, "Reviewed requirements with the team.")
c.showPage()
c.drawString(40, 700, "Education and professional development")
c.save()
original = buf.getvalue()
text = resume_bot.extract_text_from_file_bytes(original, "original.pdf", strict=True)
edited = text.replace("Built SQL reports.", "Built SQL dashboards.")
assert "Built SQL dashboards." in edited

def rendered(doc_bytes, page_index):
    with pdf.PdfDocument(doc_bytes) as doc:
        return doc[page_index].render(scale=1).to_pil().copy().convert("RGB")


def check_edge():
    errors = []
    ai = {"optimized": True, "updated_resume_text": edited, "candidate_name": "PDF Fixture",
          "candidate_name_detected": True, "initial_match_percentage": 70, "target_match_percentage": 80}
    with patch.object(resume_bot, "optimize_resume_for_jd", return_value=ai), \
            patch.object(app.india_job_scrapers, "run_multi_source_india_scrape", return_value={"jobs": []}), \
            make_server("127.0.0.1", 0, app.app, threaded=True) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(accept_downloads=True)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/login")
                page.fill("input[name=email]", email)
                page.fill("input[name=password]", password)
                page.click("button[type=submit]")
                expect(page.locator("#filter-source")).to_have_value("LinkedIn")
                opts = page.locator("#filter-source option").evaluate_all("opts => opts.map(o => o.value)")
                assert set(opts) == {"LinkedIn", "Company site"}, opts   # paid portals are off until the admin turns them on
                page.click("a[data-tab=myprofile]")
                with page.expect_download() as dl:
                    page.get_by_role("link", name="Download original PDF").click()
                assert Path(dl.value.path()).read_bytes() == original
                page.click("a[data-tab=resumebot]")
                page.select_option("#resumebot-consultant-select", str(cid))
                expect(page.locator("#resumebot-resume-text")).to_have_value(text)
                page.fill("#resumebot-jd-text", "Fixture position requiring SQL and experience building dashboards.")
                page.click("#btn-run-resume-optimization")
                with page.expect_download() as dl:
                    page.get_by_role("button", name="Download PDF", exact=True).click()
                assert dl.value.suggested_filename.endswith(".pdf")
                assert "Built SQL dashboards." in resume_pdf._read_text(Path(dl.value.path()).read_bytes())
                page.click("#btn-save-optimized-version")
                expect(page.locator("#save-version-status")).to_contain_text("Saved as")
                expect(page.locator("#save-version-status")).to_contain_text(".pdf")
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()


def main():
    upload = client.post(f"/api/consultants/{cid}/upload-resume", data={"resume_file": (io.BytesIO(original), "layout.pdf")})
    assert upload.status_code == 200
    download = client.get(f"/api/consultants/{cid}/resume/download")
    assert download.data == original and download.mimetype == "application/pdf"
    route = "/api/resume-bot/download-pdf"
    fields = {"candidate_id": cid, "edited_text": edited}
    resp = client.post(route, data=fields)
    assert resp.status_code == 200, resp.get_json()
    assert "Built SQL dashboards." in resume_pdf._read_text(resp.data)
    before, after = rendered(original, 0), rendered(resp.data, 0)
    box = ImageChops.difference(before, after).getbbox()
    assert box and 38 <= box[0] < box[2] < 385 and 140 <= box[1] < box[3] <= 165, box
    assert rendered(original, 1).tobytes() == rendered(resp.data, 1).tobytes()
    if os.environ.get("JOBHUNT_PDF_QA_DIR"):
        dest = Path(os.environ["JOBHUNT_PDF_QA_DIR"])
        before.save(dest / "original.png")
        after.save(dest / "tailored.png")
    assert client.post(route, data={**fields, "edited_text": text}).data == original
    attached = client.post(route, data={"source_pdf": (io.BytesIO(original), "attached.pdf"), "edited_text": edited})
    assert attached.status_code == 200 and attached.mimetype == "application/pdf"
    other = models.create_user("Other Fixture", "other-pdf@example.invalid", token_urlsafe(), role="Recruiter")
    other_id = other["id"] if isinstance(other, dict) else other
    foreign = models.create_candidate("Other Fixture", "other-profile@example.invalid", assigned_user_id=other_id)
    assert client.get(f"/api/consultants/{foreign}/resume/download").status_code == 404
    assert client.post(route, data={**fields, "candidate_id": foreign}).status_code == 404
    assert app.app.test_client().get(f"/api/consultants/{cid}/resume/download").status_code == 401
    too_long = edited.replace("Built SQL dashboards.", "Developed many SQL reports for the team. " * 5)
    fail = client.post(route, data={**fields, "edited_text": too_long})
    assert fail.status_code == 409 and fail.json["can_reflow"]
    reflow = client.post(route, data={**fields, "edited_text": too_long, "pdf_layout": "reflow"})
    assert reflow.status_code == 200 and " ".join(resume_pdf._read_text(reflow.data).split()) == " ".join(too_long.split())
    with_bullets = "Experience\n\uf0b7 Reviewed cases.\n\uf0b7\nSecond item."
    bullets = client.post(route, data={**fields, "edited_text": with_bullets, "pdf_layout": "reflow"})
    assert bullets.status_code == 200
    assert resume_pdf._lines(resume_pdf._read_text(bullets.data)) == resume_pdf._lines(with_bullets.replace("\uf0b7", "\u2022"))
    if os.environ.get("JOBHUNT_PDF_QA_DIR"):
        rendered(bullets.data, 0).save(dest / "standard-bullets.png")
    # A private character inside a word has no known meaning. Identify it for review.
    unknown = client.post(route, data={**fields, "edited_text": "Experience\nCode A\uf0b7B", "pdf_layout": "reflow"})
    assert unknown.status_code == 400 and "U+F0B7 on text line 2" in unknown.json["error"]
    saved = client.post("/api/optimized-resumes", data={**fields, "format": "pdf", "job_id": job, "primary_skill": "SQL"})
    assert saved.status_code == 200 and saved.json["filename"].endswith(".pdf"), saved.json
    version = saved.json["id"]
    copy = client.get(f"/api/optimized-resumes/{version}/download")
    assert copy.mimetype == "application/pdf" and "Built SQL dashboards." in resume_pdf._read_text(copy.data)
    reopened = client.get(f"/api/optimized-resumes/for-job?candidate_id={cid}&job_id={job}").json["version"]
    assert reopened["id"] == version and "Built SQL dashboards." in reopened["text"] and not reopened["docx_base64"]
    edited_again = reopened["text"].replace("dashboards.", "queries.")
    repeat = client.post(route, data={**fields, "edited_text": edited_again, "source_version_id": version})
    assert repeat.mimetype == "application/pdf" and "Built SQL queries." in resume_pdf._read_text(repeat.data), repeat.json
    check_edge()
    # A saved version stays independent of a subsequent profile upload.
    models.save_resume_file(cid, "replacement.txt", b"Replacement original")
    assert client.get(f"/api/optimized-resumes/{version}/download").data == copy.data
    assert client.post(route, data={**fields, "source_version_id": version}).data == copy.data
    # Several clicks racing for the last search may spend only one slot.
    conn = models.get_db_connection()
    individuals.set_daily_limit(conn, other_id, 1)
    conn.close()
    def take(_):
        conn = models.get_db_connection()
        try:
            return individuals.reserve_search(conn, other_id)
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(take, range(4))) == 1
    print("PASS: original and saved PDFs, layout pixels, column overflow, download permissions and concurrent search limit.")

if __name__ == "__main__":
    main()
