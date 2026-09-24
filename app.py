import urllib.parse
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
import linkedin_sourcing
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
    return session.get("user")

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

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))

    error_msg = None
    if request.method == "POST":
        email = request.form.get("email") or (request.json or {}).get("email", "")
        password = request.form.get("password") or (request.json or {}).get("password", "")
        email = email.strip()

        user = models.authenticate_user(email, password)
        if user:
            session["user"] = user
            models.log_activity(user["id"], user["name"], "User Logged In", "Auth", user["id"], f"User {user['name']} signed in")
            if request.is_json:
                return jsonify({"success": True, "redirect": url_for("dashboard")})
            return redirect(url_for("dashboard"))
        else:
            error_msg = "Invalid email or password. Please verify credentials."
            if request.is_json:
                return jsonify({"error": error_msg}), 401

    try:
        return render_template("login.html", error=error_msg, has_azure=config.HAS_AZURE_AUTH, env=config.ENV)
    except Exception:
        return render_template_string(EMBEDDED_LOGIN_HTML, error=error_msg, has_azure=config.HAS_AZURE_AUTH, env=config.ENV)

@app.route("/dev-login", methods=["GET", "POST"])
def dev_login():
    user = models.authenticate_user("praveen@adroit-ai.com", "Admin@2026")
    if not user:
        user = models.get_or_create_user(
            name="Praveen Valipireddy",
            email="praveen@adroit-ai.com",
            role="Admin",
            avatar_url="https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80",
            password="Admin@2026"
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

    is_admin = ("Admin" in user.get("role", ""))
    selected_recruiter_id = request.args.get("recruiter_id", type=int) if is_admin else user["id"]
    stats = models.get_dashboard_stats(user_id=selected_recruiter_id, is_admin=(is_admin and not request.args.get("recruiter_id")))
    candidates = models.get_candidates(user_id=selected_recruiter_id, is_admin=(is_admin and not request.args.get("recruiter_id")))
    for c in candidates:
        c_status = gmail_multi_manager.is_candidate_connected(c["id"])
        c["gmail_connected"] = c_status.get("connected", False)

    recruiters = models.get_users() if is_admin else []

    try:
        return render_template(
            "dashboard.html",
            user=user,
            stats=stats,
            candidates=candidates,
            recruiters=recruiters,
            selected_recruiter_id=selected_recruiter_id if is_admin else None,
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
            recruiters=recruiters,
            selected_recruiter_id=selected_recruiter_id if is_admin else None,
            env=config.ENV,
            has_apify=bool(config.APIFY_API_TOKEN)
        )

# --- Stats & Activity API ---

@app.route("/api/stats")
def api_stats():
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    is_admin = ("Admin" in user.get("role", ""))
    recruiter_id = request.args.get("recruiter_id", type=int)
    return jsonify(models.get_dashboard_stats(user_id=recruiter_id if is_admin else user["id"], is_admin=(is_admin and not recruiter_id)))

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

        is_admin = ("Admin" in user.get("role", ""))
        assigned_user_id = user["id"]
        if is_admin:
            req_assigned = (request.form.get("assigned_user_id") if request.form else None) or (request.json or {}).get("assigned_user_id")
            if req_assigned:
                try: assigned_user_id = int(req_assigned)
                except: pass

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
            gmail_account=email,
            assigned_user_id=assigned_user_id
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

    # GET request - return candidates scoped to current recruiter
    is_admin = ("Admin" in user.get("role", ""))
    recruiter_id = request.args.get("recruiter_id", type=int)
    candidates = models.get_candidates(user_id=recruiter_id if is_admin else user["id"], is_admin=(is_admin and not recruiter_id))
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

    is_admin = ("Admin" in user.get("role", ""))
    cand = models.get_candidate_by_id(candidate_id, user_id=user["id"], is_admin=is_admin)
    if not cand:
        return jsonify({"error": "Consultant not found or unauthorized"}), 404

    if request.method == "GET":
        cand_status = gmail_multi_manager.is_candidate_connected(candidate_id)
        cand["gmail_connected"] = cand_status.get("connected", False)
        return jsonify(cand)

    if request.method == "DELETE":
        models.delete_candidate(candidate_id, user_id=user["id"], is_admin=is_admin)
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

    custom_subject = data.get("custom_subject")
    custom_body = data.get("custom_body")

    result = gmail_multi_manager.create_candidate_draft(
        candidate_id=int(candidate_id),
        job_id=int(job_id),
        custom_to_email=custom_to_email,
        custom_notes=custom_notes,
        custom_subject=custom_subject,
        custom_body=custom_body
    )

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)


def personalize_pitch_with_prompt(cand, job, instruction: str, current_subject: str, current_body: str):
    """
    Intelligently adjusts pitch subject and body based on recruiter prompt and instructions.
    Works dynamically with or without external API keys.
    """
    cand_name = cand.get("name", "Consultant")
    cand_title = cand.get("title", "Technical Consultant")
    cand_exp = cand.get("experience_years") or 6
    cand_visa = cand.get("visa_status") or "H1B"
    cand_rate = cand.get("target_rate") or "$90/hr C2C"
    cand_email = cand.get("gmail_account") or cand.get("email", "")
    cand_phone = cand.get("phone", "")
    job_title = job.get("title") or "Technical Position"
    company = job.get("company") or "Hiring Team"
    recruiter_name = job.get("recruiter_name") or "Hiring Team"
    if not recruiter_name or recruiter_name.lower() in ["none", "null", "hiring manager"]:
        recruiter_name = "Hiring Team"

    inst_lower = instruction.lower().strip()

    subject = current_subject or f"Job Application: {job_title} - {cand_name} ({cand_exp} Yrs Exp | {cand_visa})"
    body = current_body or ""

    if not body:
        body = gmail_multi_manager.generate_consultant_pitch(cand, job, "")

    reply_parts = []

    # Check for rate adjustment
    import re
    rate_match = re.search(r"\$\d+(?:\/hr)?(?:\s*c2c)?", inst_lower)
    target_rate = rate_match.group(0).upper() if rate_match else cand_rate
    if rate_match:
        reply_parts.append(f"Updated rate to {target_rate}")

    # Check for skills to highlight
    skills_found = []
    for sk in ["aws", "azure", "gcp", "python", "java", "spring boot", "microservices", "kafka", "kubernetes", "docker", "terraform", "react", "angular", "devops", "sql", "snowflake", "spark", "c2c"]:
        if sk in inst_lower:
            skills_found.append(sk.title() if len(sk) > 3 else sk.upper())

    if skills_found:
        reply_parts.append(f"Highlighted key skills: {', '.join(skills_found)}")

    # Check for tone / length requests
    is_short = any(w in inst_lower for w in ["short", "brief", "concise", "quick", "3 sentences", "4 sentences", "minimal"])
    is_bullets = any(w in inst_lower for w in ["bullet", "bullets", "points", "structured", "bullet points"])
    is_urgent = any(w in inst_lower for w in ["urgent", "immediate", "asap", "ready to join", "interview ready"])

    if is_short:
        reply_parts.append("Condensed draft into a punchy, high-impact note")
    if is_bullets:
        reply_parts.append("Formatted strengths into executive bullet points")
    if is_urgent:
        reply_parts.append("Emphasized immediate availability for C2C interviews")

    if not reply_parts and instruction:
        reply_parts.append(f"Tailored pitch with note: '{instruction}'")

    # Generate personalized subject if requested
    if "subject" in inst_lower:
        if skills_found:
            subject = f"Top {skills_found[0]} Consultant: {cand_name} ({cand_exp} Yrs Exp | {cand_visa}) for {job_title}"
        elif is_urgent:
            subject = f"Immediate C2C Candidate: {cand_name} ({cand_exp} Yrs Exp) - {job_title}"

    # Build personalized body
    if is_short:
        body = f"""Hi {recruiter_name},

I am applying for the {job_title} role at {company}. I bring over {cand_exp} years of hands-on expertise specializing in {', '.join(skills_found) if skills_found else cand.get('primary_skills', 'enterprise solutions')}.

I am authorized to work on {cand_visa} (C2C open at {target_rate}) and available for an immediate technical interview. My resume is attached for your review.

Looking forward to connecting!

Best regards,
{cand_name}
{cand_phone} | {cand_email}"""
    elif is_bullets:
        body = f"""Hi {recruiter_name},

I am writing to express my interest in the {job_title} opening at {company}. With {cand_exp}+ years of experience in technical architecture and implementation, I am a great fit for your team.

Key Highlights:
• Core Expertise: {', '.join(skills_found) if skills_found else cand.get('primary_skills', 'Full-stack development')}
• Work Authorization: {cand_visa} (Open for C2C at {target_rate})
• Availability: Immediate for interviews and project onboarding
{f'• Recruiter Note: {instruction}' if instruction and not skills_found else ''}

My resume is attached for your review. Please let me know a convenient time for a brief discussion.

Best regards,
{cand_name}
{cand_phone} | {cand_email}"""
    else:
        # Standard personalized refinement
        skill_str = f"with deep hands-on expertise in {', '.join(skills_found)}" if skills_found else f"specializing in {cand.get('primary_skills', 'software engineering')}"
        body = f"""Hi {recruiter_name},

I hope this note finds you well.

I came across your opening for the {job_title} position at {company} and wanted to reach out directly. I bring over {cand_exp} years of enterprise experience {skill_str}.

I am authorized to work in the US on {cand_visa} and available on C2C ({target_rate}). I am open to discussing how my background aligns with your project goals.

Please find my updated resume attached. I look forward to hearing from you.

Best regards,
{cand_name}
{cand_phone} | {cand_email}"""

    reply_msg = f"Done! {', '.join(reply_parts) if reply_parts else 'Personalized the email draft based on your preferences.'} You can review the draft below and click 'Save to Gmail Draft' when ready."
    return reply_msg, subject, body


@app.route("/api/ai/personalize-draft", methods=["POST", "OPTIONS"])
def api_ai_personalize_draft():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    candidate_id = data.get("candidate_id")
    job_id = data.get("job_id")
    user_instruction = data.get("instruction", "").strip()
    current_subject = data.get("current_subject", "")
    current_body = data.get("current_body", "")

    if not candidate_id or not job_id:
        return jsonify({"error": "candidate_id and job_id are required"}), 400

    cand = models.get_candidate_by_id(int(candidate_id))
    job = models.get_job_by_id(int(job_id))
    if not cand or not job:
        return jsonify({"error": "Candidate or Job not found"}), 404

    reply_msg, new_subject, new_body = personalize_pitch_with_prompt(
        cand, job, user_instruction, current_subject, current_body
    )

    return jsonify({
        "success": True,
        "reply": reply_msg,
        "subject": new_subject,
        "body": new_body,
        "to_email": job.get("recruiter_email") or "",
        "consultant_name": cand.get("name"),
        "resume_path": cand.get("resume_path") or cand.get("resume_filename") or "",
        "job_title": job.get("title"),
        "company": job.get("company")
    })

# --- Pipeline APIs ---

@app.route("/api/pipeline")
def api_pipeline():
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    is_admin = ("Admin" in user.get("role", ""))
    recruiter_id = request.args.get("recruiter_id", type=int)
    return jsonify(models.get_pipeline_by_stages(user_id=recruiter_id if is_admin else user["id"], is_admin=(is_admin and not recruiter_id)))

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

def _parse_bachelor_year(data):
    """Selected Bachelor's passout year (India). None means 'All years'."""
    raw_by = str((data or {}).get("bachelor_year") or (data or {}).get("year") or "").strip()
    year_match = re.search(r'\b(19\d\d|20\d\d)\b', raw_by)
    return min(2020, int(year_match.group(1))) if year_match else None


@app.route("/api/students/search", methods=["GET", "POST", "OPTIONS"])
def api_search_students():
    if request.method == "OPTIONS":
        return make_response("", 200)

    if not current_user():
        return jsonify({"error": "Login required"}), 401

    data = request.get_json(silent=True) or request.args.to_dict()
    keyword = data.get("keyword") or data.get("query") or data.get("category") or "Computer Science"
    category = data.get("category", "all")
    intent = data.get("intent", "ready_to_market")

    # Bachelor's passout year in India — the one filter this workflow uses today
    # (STRICT EXACT MATCH). More filters (technology/role, visa pathway, US
    # university, US region) can be reintroduced later without changing this shape.
    bachelor_year = _parse_bachelor_year(data)
    if bachelor_year:
        start_year = end_year = bachelor_year
    else:
        start_year, end_year = 2012, 2020
    location = data.get("location", "United States")

    # This endpoint is now FREE and instant: it only returns candidates the
    # recruiter has already added to the ATS. The live LinkedIn search (which
    # costs Apify credits) is a separate, explicit action: /api/students/search-start
    # + /api/students/search-poll.
    candidates = []

    # Prepend any candidates the recruiter has already added to the database
    # that genuinely match the requested Bachelor's year.
    try:
        db_cands = models.get_candidates()
        bench_imported = []
        for db_c in db_cands:
            c_summary = str(db_c.get("resume_summary") or "")
            c_skills = str(db_c.get("primary_skills") or "")
            c_title = str(db_c.get("title") or "")
            # Only include a candidate if their resume summary/skills/title
            # actually specifies the target year
            y_match = re.search(r'\b(19\d\d|20\d\d)\b', c_summary + " " + c_skills + " " + c_title)
            if not y_match:
                continue
            c_year = y_match.group(1)

            if bachelor_year is not None and int(c_year) != int(bachelor_year):
                continue

            # HONEST labelling: we only know this person is on the recruiter's
            # own bench roster and that their resume text mentions this year
            # somewhere. We do NOT know their degree, college or Master's, so
            # none is claimed - the recruiter confirms from the resume.
            bench_imported.append({
                "id": db_c.get("id"),
                "name": db_c.get("name"),
                "headline": db_c.get("title") or "Technical Consultant",
                "bachelor_year": c_year,
                "grad_year": c_year,
                "bachelor_degree": "Degree: see resume",
                "bachelor_college": "Recruiter-added (details not verified)",
                "master_degree": "Master's: see resume",
                "master_university": "See resume",
                "location": db_c.get("location") or "United States",
                "status_badge": "⭐ Bench - Added by Recruiter",
                "status_tag": "⭐ Bench - Added by Recruiter",
                "settlement_badge": "⭐ On your bench",
                "settlement_sub": "Year taken from resume text - unverified",
                "year_verified": False,
                "quality": "[BENCH] Recruiter-added candidate - education not verified",
                "degree": f"Resume mentions {c_year} (education not verified)",
                "profile_url": db_c.get("resume_filename") if (db_c.get("resume_filename") or "").startswith("http") else f"https://www.google.com/search?q=site:linkedin.com/in/+%22{urllib.parse.quote_plus(db_c.get('name', ''))}%22+USA",
                "linkedin_url": db_c.get("resume_filename") if (db_c.get("resume_filename") or "").startswith("http") else f"https://www.google.com/search?q=site:linkedin.com/in/+%22{urllib.parse.quote_plus(db_c.get('name', ''))}%22+USA",
                "is_imported": True
            })
        # Prepend imported candidates (deduped by name against live results)
        seen_names = set(c.get("name", "").lower() for c in bench_imported)
        for cand in candidates:
            if cand.get("name", "").lower() not in seen_names:
                bench_imported.append(cand)
        candidates = bench_imported
    except Exception as ex:
        logger.warning(f"Error fetching imported candidates: {ex}")

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

_APIFY_ID_RE = re.compile(r"^[A-Za-z0-9]{8,40}$")


@app.route("/api/students/search-start", methods=["POST", "OPTIONS"])
def api_students_search_start():
    """Start a live LinkedIn search (costs Apify credits, capped per search and
    per day). Returns the Apify run/dataset ids; the client then polls
    /api/students/search-poll. Login required - this spends real money."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    if not current_user():
        return jsonify({"error": "Login required"}), 401

    data = request.get_json(silent=True) or {}
    bachelor_year = _parse_bachelor_year(data)
    result = linkedin_sourcing.start_search(
        bachelor_year,
        pages=data.get("pages") or linkedin_sourcing.DEFAULT_PAGES,
        location=data.get("location") or "United States",
        start_page=data.get("start_page") or 1,
    )
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    result["bachelor_year"] = bachelor_year
    return jsonify(result)


@app.route("/api/students/search-poll", methods=["POST", "OPTIONS"])
def api_students_search_poll():
    """Poll a running LinkedIn search: returns run status plus ONLY the newly
    scanned profiles that pass the strict India-Bachelor's + US-Master's check."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    if not current_user():
        return jsonify({"error": "Login required"}), 401

    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or "")
    dataset_id = str(data.get("dataset_id") or "")
    if not _APIFY_ID_RE.match(run_id) or not _APIFY_ID_RE.match(dataset_id):
        return jsonify({"error": "Invalid run reference"}), 400
    try:
        offset = max(0, int(data.get("offset") or 0))
        matched_so_far = max(0, int(data.get("matched_so_far") or 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid offset"}), 400

    result = linkedin_sourcing.poll_search(run_id, dataset_id, _parse_bachelor_year(data), offset, matched_so_far)
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    return jsonify(result)


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
    models.assign_candidate_to_recruiter(cid, user["id"])
    models.log_activity(
        user["id"],
        user["name"],
        "Added to Bench",
        "Candidate",
        cid,
        f"Added {name} ({data.get('degree', 'MS')} - {university}, {grad_year}) to {user['name']}'s Bench Consultants."
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
        if not candidates:
            kw = data.get("keyword", "Computer Science")
            by = data.get("bachelor_year")
            col = data.get("college")
            loc = data.get("location", "United States")
            candidates = apify_service.scrape_bench_candidates(keyword=kw, bachelor_year=by, college=col, location=loc)
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


# --- Recruiter & Team Management APIs (Admin Only) ---

@app.route("/api/admin/recruiters", methods=["GET", "POST", "OPTIONS"])
def api_admin_recruiters():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if "Admin" not in user.get("role", ""):
        return jsonify({"error": "Admin permission required."}), 403

    if request.method == "POST":
        data = request.json or {}
        name = data.get("name", "").strip()
        email = data.get("email", "").strip()
        password = data.get("password", "").strip()
        role = data.get("role", "Recruiter").strip()

        if not name or not email or not password:
            return jsonify({"error": "Name, email, and password are required."}), 400

        try:
            new_user = models.create_user(name, email, password, role=role)
            models.log_activity(
                user["id"], user["name"], "Created Recruiter", "User", new_user["id"],
                f"Admin created recruiter account for {name} ({email}) with role '{role}'"
            )
            return jsonify({"success": True, "user": new_user})
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400
        except Exception as e:
            return jsonify({"error": f"Failed to create recruiter: {str(e)}"}), 500

    return jsonify(models.get_users())

@app.route("/api/admin/recruiters/<int:recruiter_id>", methods=["DELETE", "OPTIONS"])
def api_admin_delete_recruiter(recruiter_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user or "Admin" not in user.get("role", ""):
        return jsonify({"error": "Forbidden"}), 403

    if recruiter_id == user["id"]:
        return jsonify({"error": "Cannot delete your own admin account."}), 400

    target = models.get_user_by_id(recruiter_id)
    if not target:
        return jsonify({"error": "Recruiter not found."}), 404

    models.delete_user(recruiter_id)
    models.log_activity(
        user["id"], user["name"], "Deleted Recruiter", "User", recruiter_id,
        f"Admin deleted recruiter account {target['name']} ({target['email']}). Candidates reassigned to Admin."
    )
    return jsonify({"success": True, "message": f"Recruiter {target['name']} deleted."})

@app.route("/api/admin/recruiters/<int:recruiter_id>/reset-password", methods=["POST", "OPTIONS"])
def api_admin_reset_password(recruiter_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user or "Admin" not in user.get("role", ""):
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    new_password = data.get("new_password", "").strip()
    if not new_password or len(new_password) < 4:
        return jsonify({"error": "New password must be at least 4 characters."}), 400

    models.update_user_password(recruiter_id, new_password)
    return jsonify({"success": True, "message": "Password updated successfully."})

@app.route("/api/admin/candidates/<int:candidate_id>/reassign", methods=["POST", "OPTIONS"])
def api_admin_reassign_candidate(candidate_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user or "Admin" not in user.get("role", ""):
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    new_user_id = data.get("assigned_user_id")
    if not new_user_id:
        return jsonify({"error": "assigned_user_id is required"}), 400

    target_user = models.get_user_by_id(new_user_id)
    if not target_user:
        return jsonify({"error": "Target recruiter not found"}), 404

    cand = models.get_candidate_by_id(candidate_id, is_admin=True)
    if not cand:
        return jsonify({"error": "Candidate not found"}), 404

    models.assign_candidate_to_recruiter(candidate_id, new_user_id)
    models.log_activity(
        user["id"], user["name"], "Reassigned Candidate", "Candidate", candidate_id,
        f"Reassigned candidate '{cand['name']}' to recruiter '{target_user['name']}'"
    )
    return jsonify({"success": True, "message": f"Candidate reassigned to {target_user['name']}."})
