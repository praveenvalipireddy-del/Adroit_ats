import os
import time
import random
import uuid
import re
import io
import json
import logging
from pathlib import Path
from flask import Flask, render_template, render_template_string, request, jsonify, session, redirect, url_for, send_file, Response, make_response
from werkzeug.utils import secure_filename
import msal

import config
import models
import resume_bot
import apify_service
import gmail_multi_manager
import us_job_scrapers
from templates_bundle import EMBEDDED_LOGIN_HTML, EMBEDDED_DASHBOARD_HTML, EMBEDDED_STYLE_CSS, EMBEDDED_APP_JS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Ensure required directories exist
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESUMES_DIR = os.path.join(BASE_DIR, "data", "resumes")
TOKENS_DIR = os.path.join(BASE_DIR, "data", "tokens")
os.makedirs(RESUMES_DIR, exist_ok=True)
os.makedirs(TOKENS_DIR, exist_ok=True)

# Initialize database
models.init_db()

def current_user():
    user = session.get("user")
    if not user:
        # In local dev environment, automatically ensure active logged-in session
        user = models.get_or_create_user(
            name="Praveen Valipireddy",
            email="praveen@adroit-ai.com",
            role="Lead Technical Recruiter & Admin",
            avatar_url="https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80"
        )
        session["user"] = user
    return user

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,PUT,POST,DELETE,OPTIONS"
    return response

# --- Static Asset Routing ---

@app.route("/static/css/style.css")
def serve_custom_css():
    css_file = os.path.join(BASE_DIR, "static", "css", "style.css")
    if os.path.exists(css_file):
        with open(css_file, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="text/css")
    return Response(EMBEDDED_STYLE_CSS, mimetype="text/css")

@app.route("/static/js/app.js")
def serve_custom_js():
    js_file = os.path.join(BASE_DIR, "static", "js", "app.js")
    if os.path.exists(js_file):
        with open(js_file, "r", encoding="utf-8") as f:
            resp = Response(f.read(), mimetype="application/javascript")
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            return resp
    resp = Response(EMBEDDED_APP_JS, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

# --- Authentication ---

@app.route("/")
def home():
    return redirect(url_for("dashboard"))

@app.route("/login")
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    try:
        return render_template("login.html", has_azure=config.HAS_AZURE_AUTH, env=config.ENV)
    except Exception:
        return render_template_string(EMBEDDED_LOGIN_HTML, has_azure=config.HAS_AZURE_AUTH, env=config.ENV)

@app.route("/dev-login", methods=["GET", "POST"])
def dev_login():
    user = models.get_or_create_user(
        name="Praveen Valipireddy",
        email="praveen@adroit-ai.com",
        role="Lead Technical Recruiter & Admin",
        avatar_url="https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80"
    )
    session["user"] = user
    models.log_activity(
        user["id"], 
        user["name"], 
        "User Logged In", 
        "Auth", 
        user["id"], 
        f"Logged in via Dev Test Mode (User: {user['name']})"
    )
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    user = current_user()
    if user:
        models.log_activity(user["id"], user["name"], "User Logged Out", "Auth", user["id"], f"User {user['name']} logged out")
    session.clear()
    return redirect(url_for("login"))

# --- Dashboard Main View ---

@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    
    stats = models.get_dashboard_stats()
    candidates = models.get_candidates()
    for c in candidates:
        c_status = gmail_multi_manager.is_candidate_connected(c["id"])
        c["gmail_connected"] = c_status.get("connected", False)

    try:
        return render_template(
            "dashboard.html",
            user=user,
            stats=stats,
            candidates=candidates,
            env=config.ENV,
            has_apify=bool(config.APIFY_API_TOKEN)
        )
    except Exception as e:
        logger.warning(f"Using embedded template fallback: {e}")
        return render_template_string(
            EMBEDDED_DASHBOARD_HTML,
            user=user,
            stats=stats,
            candidates=candidates,
            env=config.ENV,
            has_apify=bool(config.APIFY_API_TOKEN)
        )

# --- Stats & Activity API ---

@app.route("/api/stats")
def api_stats():
    if not current_user():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(models.get_dashboard_stats())

@app.route("/api/activity")
def api_activity():
    if not current_user():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(models.get_activity_logs(limit=25))

# --- Consultant / Candidate Management APIs ---

@app.route("/api/candidates", methods=["GET", "POST", "OPTIONS"])
@app.route("/api/consultants", methods=["GET", "POST", "OPTIONS"])
def api_consultants():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == "POST":
        # Check if multipart form with resume upload or JSON
        if request.content_type and "multipart/form-data" in request.content_type:
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            title = request.form.get("title", "Technical Consultant").strip()
            skills = request.form.get("skills", "").strip()
            exp = int(request.form.get("experience", 5))
            rate = request.form.get("rate", "$90/hr (C2C)").strip()
            visa = request.form.get("visa_status", "C2C Eligible").strip()
            location = request.form.get("location", "United States (Remote)").strip()
            summary = request.form.get("summary", "").strip()

            resume_filename = None
            resume_path = None
            resume_text = None

            if "resume_file" in request.files:
                file = request.files["resume_file"]
                if file.filename:
                    filename = secure_filename(file.filename)
                    dest_path = os.path.join(RESUMES_DIR, filename)
                    file.save(dest_path)
                    resume_filename = filename
                    resume_path = dest_path

                    # Extract text for ATS analysis
                    try:
                        with open(dest_path, "rb") as rf:
                            resume_text = resume_bot.extract_text_from_file_bytes(rf.read(), filename)
                    except Exception as e:
                        logger.error(f"Error extracting resume text: {e}")
        else:
            data = request.json or {}
            name = data.get("name", "").strip()
            email = data.get("email", "").strip()
            phone = data.get("phone", "").strip()
            title = data.get("title", "Technical Consultant").strip()
            skills = data.get("skills", "").strip()
            exp = int(data.get("experience", 5))
            rate = data.get("rate", "$90/hr (C2C)").strip()
            visa = data.get("visa_status", "C2C Eligible").strip()
            location = data.get("location", "United States (Remote)").strip()
            summary = data.get("summary", "").strip()
            resume_filename = data.get("resume_filename")
            resume_path = data.get("resume_path")
            resume_text = data.get("resume_text")

        if not name or not email:
            return jsonify({"error": "Consultant name and email are required"}), 400

        cand_id = models.create_candidate(
            name=name,
            email=email,
            phone=phone,
            title=title,
            primary_skills=skills,
            experience_years=exp,
            target_rate=rate,
            visa_status=visa,
            status="Available",
            location=location,
            resume_filename=resume_filename,
            resume_path=resume_path,
            resume_text=resume_text,
            resume_summary=summary,
            gmail_account=email
        )

        models.log_activity(
            user["id"],
            user["name"],
            "Added Consultant",
            "Candidate",
            cand_id,
            f"Added US bench consultant: {name} ({title}) - {rate}"
        )

        return jsonify({"success": True, "candidate_id": cand_id, "name": name})

    # GET request - return candidates with Gmail connection info
    candidates = models.get_candidates()
    for c in candidates:
        c_status = gmail_multi_manager.is_candidate_connected(c["id"])
        c["gmail_connected"] = c_status.get("connected", False)

    return jsonify(candidates)

@app.route("/api/consultants/<int:candidate_id>", methods=["GET", "PUT", "DELETE", "OPTIONS"])
def api_consultant_detail(candidate_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    cand = models.get_candidate_by_id(candidate_id)
    if not cand:
        return jsonify({"error": "Consultant not found"}), 404

    if request.method == "GET":
        cand_status = gmail_multi_manager.is_candidate_connected(candidate_id)
        cand["gmail_connected"] = cand_status.get("connected", False)
        return jsonify(cand)

    if request.method == "DELETE":
        models.delete_candidate(candidate_id)
        models.log_activity(
            user["id"],
            user["name"],
            "Deleted Consultant",
            "Candidate",
            candidate_id,
            f"Removed consultant profile #{candidate_id} ({cand['name']})"
        )
        return jsonify({"success": True, "deleted_id": candidate_id})

    if request.method == "PUT":
        data = request.json or {}
        update_fields = {}
        for key in ["name", "email", "phone", "title", "primary_skills", "experience_years", "target_rate", "visa_status", "status", "location", "resume_summary"]:
            if key in data:
                update_fields[key] = data[key]
        models.update_candidate(candidate_id, **update_fields)
        return jsonify({"success": True, "candidate_id": candidate_id})

@app.route("/api/consultants/<int:candidate_id>/upload-resume", methods=["POST", "OPTIONS"])
def api_consultant_upload_resume(candidate_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if "resume_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["resume_file"]
    if not file.filename:
        return jsonify({"error": "No selected file"}), 400

    filename = secure_filename(file.filename)
    dest_path = os.path.join(RESUMES_DIR, f"{candidate_id}_{filename}")
    file.save(dest_path)

    # Extract text
    resume_text = ""
    try:
        with open(dest_path, "rb") as rf:
            resume_text = resume_bot.extract_text_from_file_bytes(rf.read(), filename)
    except Exception as e:
        logger.error(f"Error extracting text from uploaded resume: {e}")

    models.update_candidate(
        candidate_id,
        resume_filename=filename,
        resume_path=dest_path,
        resume_text=resume_text
    )

    return jsonify({
        "success": True, 
        "filename": filename, 
        "path": dest_path,
        "text_preview": resume_text[:300] + "..." if resume_text else ""
    })

# --- Consultant Gmail OAuth Management ---

@app.route("/api/consultants/<int:candidate_id>/set-app-password", methods=["POST", "OPTIONS"])
def api_set_consultant_app_password(candidate_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    gmail_account = data.get("gmail_account", "").strip()
    app_password = data.get("app_password", "").replace(" ", "").strip()

    if not gmail_account or not app_password:
        return jsonify({"error": "Gmail address and 16-character App Password are required."}), 400

    # Verify credentials via live IMAP login test
    valid, msg = gmail_multi_manager.verify_gmail_app_password(gmail_account, app_password)
    if not valid:
        return jsonify({"error": f"Failed to authenticate with Gmail: {msg}. Please ensure 2-Step Verification is enabled and the 16-character App Password is correct."}), 400

    models.update_candidate(
        candidate_id,
        gmail_account=gmail_account,
        gmail_app_password=app_password
    )

    models.log_activity(
        user["id"],
        user["name"],
        "Connected Gmail (App Password)",
        "Candidate",
        candidate_id,
        f"Verified & connected Gmail App Password for consultant #{candidate_id} ({gmail_account})"
    )

    return jsonify({
        "success": True, 
        "candidate_id": candidate_id, 
        "gmail_account": gmail_account,
        "message": f"Gmail ({gmail_account}) verified and connected successfully via App Password!"
    })

@app.route("/api/consultants/<int:candidate_id>/connect-gmail")
def api_connect_candidate_gmail(candidate_id):
    redirect_uri = f"{request.host_url.rstrip('/')}/api/auth/google/callback"
    auth_url, verifier = gmail_multi_manager.get_auth_url(candidate_id, redirect_uri=redirect_uri)
    if not auth_url:
        return jsonify({"error": "Could not generate Google OAuth URL. Ensure oauth_credentials.json is present."}), 400
    if verifier:
        session[f"oauth_verifier_{candidate_id}"] = verifier
    return redirect(auth_url)

@app.route("/api/auth/google/callback")
def api_google_auth_callback():
    code = request.args.get("code")
    state = request.args.get("state") # candidate_id
    error = request.args.get("error")

    if error or not code or not state:
        return render_template_string("""
        <html><body style="font-family:sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:50px;">
            <h2 style="color:#ef4444;">❌ Gmail Authorization Failed</h2>
            <p>{{ error or 'Missing code/state' }}</p>
            <a href="/dashboard" style="display:inline-block; padding:10px 20px; background:#3b82f6; color:#fff; text-decoration:none; border-radius:6px; margin-top:20px;">Return to Dashboard</a>
        </body></html>
        """, error=error), 400

    candidate_id = int(state)
    redirect_uri = f"{request.host_url.rstrip('/')}/api/auth/google/callback"
    code_verifier = session.get(f"oauth_verifier_{candidate_id}")
    
    result = gmail_multi_manager.exchange_code_and_save_token(
        candidate_id, 
        code, 
        redirect_uri=redirect_uri,
        code_verifier=code_verifier
    )

    if result.get("success"):
        return redirect(f"/dashboard?auth_success=1&cand_id={candidate_id}")
    else:
        return render_template_string("""
        <html><body style="font-family:sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:50px;">
            <h2 style="color:#ef4444;">❌ Token Exchange Error</h2>
            <p>{{ error }}</p>
            <a href="/dashboard" style="display:inline-block; padding:10px 20px; background:#3b82f6; color:#fff; text-decoration:none; border-radius:6px; margin-top:20px;">Return to Dashboard</a>
        </body></html>
        """, error=result.get("error")), 400

@app.route("/api/consultants/<int:candidate_id>/gmail-status")
def api_candidate_gmail_status(candidate_id):
    status = gmail_multi_manager.is_candidate_connected(candidate_id)
    return jsonify(status)

# --- US IT Job Searching & Live Scrapers ---

@app.route("/api/jobs/search", methods=["POST", "OPTIONS"])
def api_jobs_search():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.json or {}
    query = data.get("query", "").strip()
    location = data.get("location", "").strip()
    source = data.get("source", "All")
    job_type = data.get("job_type", "Contract")
    contract_only = data.get("contract_only", False)
    is_24h_only = data.get("is_24h_only", False)
    live_scrape = data.get("live_scrape", False)

    results = models.get_jobs(
        query=query if query else None,
        location=location if location and location.lower() != "united states" else None,
        source=source if source != "All" else None,
        job_type=job_type if job_type != "All" else None,
        contract_only=contract_only,
        is_24h_only=is_24h_only
    )

    # If 0 results or live_scrape requested, trigger live US scrape automatically!
    if (len(results) == 0 or live_scrape) and query:
        try:
            scrape_res = us_job_scrapers.run_multi_source_us_scrape(
                keywords=[query],
                location=location or "United States",
                contract_only=True,
                save_to_db=True
            )
            results = models.get_jobs(
                query=query,
                location=location if location and location.lower() != "united states" else None,
                source=source if source != "All" else None,
                job_type=job_type if job_type != "All" else None,
                contract_only=False
            )
            if not results and scrape_res.get("jobs"):
                results = scrape_res["jobs"]
        except Exception as e:
            logger.error(f"Live scrape on-demand error: {e}")

    return jsonify({
        "jobs": results,
        "results": results,
        "count": len(results),
        "query": query,
        "contract_only": contract_only,
        "is_24h_only": is_24h_only
    })

@app.route("/api/jobs/scrape-us", methods=["POST", "OPTIONS"])
def api_trigger_us_scrape():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    keywords = data.get("keywords")
    location = data.get("location", "United States")
    contract_only = data.get("contract_only", True)

    scrape_res = us_job_scrapers.run_multi_source_us_scrape(
        keywords=keywords,
        location=location,
        contract_only=contract_only,
        save_to_db=True
    )

    models.log_activity(
        user["id"],
        user["name"],
        "Scraped 24h US Jobs",
        "Scraper",
        0,
        f"Scraped {scrape_res.get('count', 0)} US tech contract roles across LinkedIn, Dice, ZipRecruiter"
    )

    return jsonify({
        "success": True,
        "count": scrape_res.get("count", 0),
        "saved_to_db": scrape_res.get("saved_to_db", 0),
        "message": f"Successfully scraped {scrape_res.get('count', 0)} fresh US contract jobs."
    })

# --- In-Table Quick Email & Contact Update API ---

@app.route("/api/jobs/update-email", methods=["POST", "OPTIONS"])
def api_update_job_email():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    job_id = data.get("job_id")
    email = data.get("recruiter_email", "").strip()
    phone = data.get("recruiter_phone", "").strip()
    name = data.get("recruiter_name", "").strip()
    salary = data.get("salary", "").strip()

    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    models.update_job_recruiter_info(
        job_id=int(job_id),
        email=email if email else None,
        phone=phone if phone else None,
        name=name if name else None,
        salary=salary if salary else None
    )

    models.log_activity(
        user["id"],
        user["name"],
        "Updated Recruiter Email",
        "Job",
        job_id,
        f"Saved recruiter email '{email}' for Job #{job_id}"
    )

    return jsonify({
        "success": True, 
        "job_id": job_id, 
        "recruiter_email": email,
        "recruiter_name": name,
        "recruiter_phone": phone,
        "salary": salary
    })

# --- 1-Click Gmail Draft Creation Outreach API ---


# --- Instant Manual Requirement Paste & Draft API ---

def extract_details_from_raw_jd(text: str) -> dict:
    email_match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', text)
    email = email_match.group(0) if email_match else ""

    salary_match = re.search(r'\$[\d,]+(?:\.\d+)?(?:\s*[-–—to]+\s*\$?[\d,]+(?:\.\d+)?)?(?:\s*\/\s*(?:hr|hour|yr|year|mo|month|annum|day))?', text, re.IGNORECASE)
    salary = salary_match.group(0) if salary_match else "$90/hr (C2C)"

    title = ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        m = re.match(r'^(?:Role|Position|Job\s*Title|Title|Requirement)\s*[:\-=]\s*(.+)$', line, re.IGNORECASE)
        if m:
            clean_title = m.group(1).strip()
            if len(clean_title) > 2 and not clean_title.lower().startswith("for"):
                title = clean_title
                break

    if not title:
        for line in lines[:5]:
            if re.search(r'\b(engineer|developer|architect|lead|specialist|consultant|manager|analyst|administrator)\b', line, re.IGNORECASE):
                if len(line) <= 75 and not line.startswith("http") and not "@" in line:
                    title = line
                    break

    if not title:
        title = "Software Engineering Specialist"

    company = ""
    for line in lines:
        m = re.match(r'^(?:Client|End\s*Client|Company|Organization|Vendor)\s*[:\-=]\s*(.+)$', line, re.IGNORECASE)
        if m:
            clean_comp = m.group(1).strip()
            if 2 <= len(clean_comp) <= 60:
                company = clean_comp
                break

    if not company and email and "@" in email:
        domain = email.split("@")[1].split(".")[0]
        if domain not in ["gmail", "yahoo", "hotmail", "outlook", "icloud"]:
            company = domain.capitalize()

    if not company:
        company = "Direct Client"

    return {
        "title": title,
        "company": company,
        "recruiter_email": email,
        "salary": salary
    }

@app.route("/api/outreach/parse-jd", methods=["POST", "OPTIONS"])
def api_parse_jd():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.json or {}
    text = data.get("raw_jd_text", "").strip()
    if not text:
        return jsonify({"error": "No requirement text provided"}), 400

    extracted = extract_details_from_raw_jd(text)
    return jsonify(extracted)

@app.route("/api/outreach/paste-and-draft", methods=["POST", "OPTIONS"])
def api_paste_and_draft():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    candidate_id = data.get("candidate_id")
    raw_jd_text = data.get("raw_jd_text", "").strip()
    job_title = data.get("job_title", "").strip()
    company = data.get("company", "").strip()
    recruiter_email = data.get("recruiter_email", "").strip()
    salary = data.get("salary", "").strip()
    custom_notes = data.get("custom_notes", "").strip()

    if not candidate_id:
        return jsonify({"error": "Candidate ID is required"}), 400
    if not raw_jd_text and not job_title:
        return jsonify({"error": "Please provide requirement text or job title"}), 400

    # Auto-extract missing fields from raw text
    if raw_jd_text:
        extracted = extract_details_from_raw_jd(raw_jd_text)
        if not job_title:
            job_title = extracted["title"]
        if not company:
            company = extracted["company"]
        if not recruiter_email:
            recruiter_email = extracted["recruiter_email"]
        if not salary:
            salary = extracted["salary"]

    if not recruiter_email or "@" not in recruiter_email:
        return jsonify({"error": "Recruiter email is required. Please type recruiter email."}), 400

    # Save to SQLite database as a job record
    job_id = models.save_or_update_scraped_job({
        "title": job_title or "Technical Role",
        "company": company or "Direct Client",
        "location": "United States (Remote / Onsite)",
        "job_type": "Contract (C2C)",
        "salary": salary or "$90/hr (C2C)",
        "source": "Manual Paste",
        "url": "",
        "recruiter_email": recruiter_email,
        "description": raw_jd_text or f"Manual Requirement for {job_title} at {company}",
        "matched_skills": job_title,
        "match_score": 95
    })

    # Call multi-manager to draft in Gmail with attached .docx resume
    result = gmail_multi_manager.create_candidate_draft(
        candidate_id=int(candidate_id),
        job_id=job_id,
        custom_to_email=recruiter_email,
        custom_notes=custom_notes or raw_jd_text
    )

    if not result.get("success"):
        return jsonify(result), 400

    result["job_id"] = job_id
    return jsonify(result)

@app.route("/api/outreach/create-draft", methods=["POST", "OPTIONS"])
def api_create_outreach_draft():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    candidate_id = data.get("candidate_id")
    job_id = data.get("job_id")
    custom_to_email = data.get("custom_to_email")
    custom_notes = data.get("custom_notes", "")

    if not candidate_id or not job_id:
        return jsonify({"error": "candidate_id and job_id are required"}), 400

    result = gmail_multi_manager.create_candidate_draft(
        candidate_id=int(candidate_id),
        job_id=int(job_id),
        custom_to_email=custom_to_email,
        custom_notes=custom_notes
    )

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)

# --- Pipeline APIs ---

@app.route("/api/pipeline")
def api_pipeline():
    if not current_user():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(models.get_pipeline_by_stages())

@app.route("/api/pipeline/update-stage", methods=["POST"])
def api_update_pipeline_stage():
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    app_id = data.get("application_id")
    new_stage = data.get("stage")
    notes = data.get("notes", "")

    if not app_id or not new_stage:
        return jsonify({"error": "application_id and stage are required"}), 400

    models.update_application_stage(app_id, new_stage, notes)
    return jsonify({"success": True, "stage": new_stage})

# --- Master Resume Bot APIs ---

@app.route("/api/resume-bot/optimize", methods=["POST", "OPTIONS"])
def api_resume_bot_optimize():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.json or {}
    resume_text = data.get("resume_text", "").strip()
    jd_text = data.get("jd_text", "").strip()
    custom_instructions = data.get("custom_instructions", "").strip()
    candidate_id = data.get("candidate_id")

    if candidate_id and not resume_text:
        cand = models.get_candidate_by_id(candidate_id)
        if cand:
            resume_text = cand.get("resume_text") or f"{cand['name']}\n{cand['title']}\nSkills: {cand['primary_skills']}\nRate: {cand['target_rate']}"

    if not resume_text:
        return jsonify({"error": "Resume text or uploaded file is required."}), 400
    if not jd_text:
        return jsonify({"error": "Job Description (JD) is required."}), 400

    result = resume_bot.optimize_resume_for_jd(
        resume_text=resume_text,
        jd_text=jd_text,
        custom_instructions=custom_instructions
    )

    return jsonify(result)

@app.route("/api/resume-bot/download-docx", methods=["POST", "OPTIONS"])
def api_resume_bot_download_docx():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.json or {}
    resume_text = data.get("resume_text", "")
    candidate_name = data.get("candidate_name", "Consultant")

    if not resume_text:
        return jsonify({"error": "No resume text provided"}), 400

    docx_io = resume_bot.create_docx_resume(resume_text, candidate_name)
    safe_filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', candidate_name)}_Optimized_Resume.docx"

    return send_file(
        docx_io,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=safe_filename
    )


# =========================================================================
# US IT Staffing - Bench Candidates & Student Sourcing (2018 - 2026)
# =========================================================================

@app.route("/api/students/search", methods=["GET", "POST", "OPTIONS"])
def api_search_students():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json(silent=True) or request.args.to_dict()
    keyword = data.get("keyword") or data.get("query") or data.get("category") or "Computer Science"
    category = data.get("category", "all")
    intent = data.get("intent", "ready_to_market")
    try:
        start_year = int(data.get("from_year") or data.get("start_year") or data.get("startYear") or 2018)
        end_year = int(data.get("to_year") or data.get("end_year") or data.get("endYear") or 2026)
    except (ValueError, TypeError):
        start_year, end_year = 2018, 2026

    location = data.get("location", "United States")
    max_items = int(data.get("max_items") or data.get("limit") or 30)
    force_live = bool(data.get("scrape") or data.get("live") or data.get("force_live"))

    candidates = apify_service.scrape_bench_candidates(
        category=category,
        keyword=keyword,
        intent=intent,
        start_year=start_year,
        end_year=end_year,
        location=location,
        max_items=max_items,
        force_live=force_live
    )

    return jsonify({
        "status": "success",
        "count": len(candidates),
        "query": {
            "keyword": keyword,
            "category": category,
            "intent": intent,
            "start_year": start_year,
            "end_year": end_year,
            "location": location
        },
        "students": candidates,
        "candidates": candidates,
        "results": candidates
    })

@app.route("/api/students/add-to-bench", methods=["POST", "OPTIONS"])
def api_add_student_to_bench():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json() or {}
    name = data.get("name", "Consultant").strip()
    if not name or name == "Candidate":
        name = "US Tech Consultant"

    clean_name = re.sub(r'[^a-zA-Z0-9]', '', name.lower())
    if not clean_name:
        clean_name = "consultant"
    unique_suffix = f"{int(time.time())}_{random.randint(100, 999)}"
    email = data.get("email") or f"{clean_name}.{unique_suffix}@talent.internal"
    phone = data.get("phone", "")
    title = data.get("headline") or f"{data.get('degree', 'MS in Tech')} Consultant"
    grad_year = str(data.get("grad_year", "2024"))
    university = data.get("university", "US University")
    location = data.get("location", "United States (Open to Relocate)")
    summary = data.get("summary", "")
    skills = data.get("skills") or f"{data.get('degree', '')}, {data.get('category', 'Tech')}, US Master's"

    try:
        exp_years = max(1, 2026 - int(grad_year))
    except:
        exp_years = 3

    # Add candidate to ATS database
    try:
        cid = models.create_candidate(
            name=name,
            email=email,
            phone=phone,
            title=title,
            primary_skills=skills,
            experience_years=exp_years,
            target_rate="$85/hr (C2C)",
            visa_status="OPT / STEM OPT / C2C",
            status="Bench - Ready to Market",
            location=location,
            resume_summary=f"Education: {data.get('degree', 'MS')} ({university}, Class of {grad_year}). LinkedIn: {data.get('profile_url', '')}. Summary: {summary}"
        )
    except Exception as ex:
        # Fallback with timestamped email if unique constraint hit
        email = f"{clean_name}.{uuid.uuid4().hex[:6]}@talent.internal"
        cid = models.create_candidate(
            name=name,
            email=email,
            phone=phone,
            title=title,
            primary_skills=skills,
            experience_years=exp_years,
            target_rate="$85/hr (C2C)",
            visa_status="OPT / STEM OPT / C2C",
            status="Bench - Ready to Market",
            location=location,
            resume_summary=f"Education: {data.get('degree', 'MS')} ({university}, Class of {grad_year}). LinkedIn: {data.get('profile_url', '')}. Summary: {summary}"
        )

    user = current_user() or {"id": 1, "name": "Valipireddy Praveen"}
    models.log_activity(
        user["id"],
        user["name"],
        "Added to Bench",
        "Candidate",
        cid,
        f"Added {name} ({data.get('degree', 'MS')} - {university}, {grad_year}) to active Bench Consultants."
    )

    return jsonify({
        "status": "success",
        "message": f"Successfully onboarded {name} to Bench Hotlist!",
        "candidate_id": cid
    })

@app.route("/api/students/export-csv", methods=["GET", "POST", "OPTIONS"])
def api_export_students_csv():
    if request.method == "OPTIONS":
        return make_response("", 200)

    import io
    import csv

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        candidates = data.get("candidates", [])
    else:
        candidates = apify_service.scrape_bench_candidates()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Full Name", "Degree & Major", "Graduation Year", "University", 
        "Current Location", "Status & Intent", "Headline", "LinkedIn Profile URL", "Summary"
    ])

    for c in candidates:
        writer.writerow([
            c.get("name", ""),
            c.get("degree", ""),
            c.get("grad_year", ""),
            c.get("university", ""),
            c.get("location", ""),
            c.get("status_badge", ""),
            c.get("headline", ""),
            c.get("profile_url", ""),
            c.get("summary", "")
        ])

    csv_data = output.getvalue()
    response = make_response(csv_data)
    response.headers["Content-Disposition"] = "attachment; filename=LinkedIn_US_Candidates_2018_2026.csv"
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    return response

@app.route("/api/students/generate-pitch", methods=["POST", "OPTIONS"])
def api_generate_student_pitch():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json() or {}
    name = data.get("name", "Candidate")
    degree = data.get("degree", "Master's")
    university = data.get("university", "your University")
    grad_year = data.get("grad_year", "2024")
    category = data.get("category", "Software Engineering")

    subject = f"US IT Bench Placement & Direct C2C Project Opportunities for {name} ({degree})"
    pitch_body = f"""Hi {name},

Hope you are having a productive week!

I came across your profile and noticed your strong profile with {degree} from {university} (Class of {grad_year}).

At our US Staffing & Consulting practice, we represent active bench consultants and recent Master's / STEM OPT graduates for top-tier C2C and W2 contract projects with Fortune 500 direct clients and Prime Vendors across the United States.

We have active client requisitions in {category.replace('_', ' ').title()} / Cloud / Tech domains with competitive rates ($75 - $95+/hr C2C).

Our bench support includes:
• Direct marketing to Tier-1 Vendors & Direct Implementation Partners
• Targeted resume packaging & technical interview coaching
• Dedicated marketing team with fast placement turnaround (2-3 weeks)

Are you currently open to exploring new project opportunities or bench marketing support? 

Let's connect for a quick 5-minute call this week.

Best regards,
US IT Staffing & Talent Placement Team
"""

    return jsonify({
        "status": "success",
        "subject": subject,
        "pitch_body": pitch_body
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\n=======================================================")
    print(f">> ADROIT MULTI-CONSULTANT ATS RUNNING AT http://localhost:{port}")
    print(f">> 1-Click Recruiter Login: http://localhost:{port}/dev-login")
    print(f"=======================================================\n")
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
