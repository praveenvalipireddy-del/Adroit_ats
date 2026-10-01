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
import sourcing_store
import linkedin_ingest
import education_filters
import education_match
import gmail_multi_manager
import us_job_scrapers
import india_job_scrapers
import job_description_fetch
from templates_bundle import EMBEDDED_LOGIN_HTML, EMBEDDED_DASHBOARD_HTML, EMBEDDED_STYLE_CSS, EMBEDDED_APP_JS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
if config.USING_DEFAULT_SECRET_KEY and config.ENV == "production":
    logger.warning("SECURITY: SECRET_KEY is not set, so sessions are signed with the publicly known default key. "
                   "Set SECRET_KEY to a long random value in the server environment.")
if config.ALLOW_DEV_LOGIN and config.ENV == "production":
    logger.warning("SECURITY: ALLOW_DEV_LOGIN is enabled in production - /dev-login logs in as admin without a password.")

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


def can_access_candidate(user, candidate_id) -> bool:
    """Admins can act on any consultant; a recruiter only on consultants assigned to them."""
    try:
        cid = int(candidate_id)
    except (TypeError, ValueError):
        return False
    is_admin = "Admin" in (user or {}).get("role", "")
    return bool(user) and models.get_candidate_by_id(cid, user_id=user["id"], is_admin=is_admin) is not None

@app.before_request
def require_login_for_student_api():
    """Every /api/students/* route (search, paid LinkedIn search, add-to-bench,
    pitch, CSV export) needs a logged-in user. Several of them previously had no
    check, so anyone could create bench candidates or trigger paid searches."""
    if request.method != "OPTIONS" and request.path.startswith("/api/students/") and not current_user():
        return jsonify({"error": "Login required"}), 401


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
        email = request.form.get("email") or (request.get_json(silent=True) or {}).get("email", "")
        password = request.form.get("password") or (request.get_json(silent=True) or {}).get("password", "")
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
        return render_template("login.html", error=error_msg, has_azure=config.HAS_AZURE_AUTH, allow_dev_login=config.ALLOW_DEV_LOGIN, env=config.ENV)
    except Exception:
        return render_template_string(EMBEDDED_LOGIN_HTML, error=error_msg, has_azure=config.HAS_AZURE_AUTH, allow_dev_login=config.ALLOW_DEV_LOGIN, env=config.ENV)

@app.route("/dev-login", methods=["GET", "POST"])
def dev_login():
    # This route logs in as the admin WITHOUT checking any password (it falls back to
    # get_or_create_user, which returns the existing admin account as-is), so it is
    # disabled unless ALLOW_DEV_LOGIN=1 is set on the server.
    if not config.ALLOW_DEV_LOGIN:
        return make_response("Not found", 404)
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
            has_apify=bool(config.APIFY_API_TOKEN),
            has_google_oauth=gmail_multi_manager.oauth_configured(),
            has_gemini=resume_bot.gemini_configured(),
            has_grok=resume_bot.xai_configured(),
            has_openrouter=resume_bot.openrouter_configured()
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
            has_apify=bool(config.APIFY_API_TOKEN),
            has_google_oauth=gmail_multi_manager.oauth_configured(),
            has_gemini=resume_bot.gemini_configured(),
            has_grok=resume_bot.xai_configured(),
            has_openrouter=resume_bot.openrouter_configured()
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
            country = request.form.get("country", "United States").strip()
            summary = request.form.get("summary", "").strip()

            resume_filename = None
            resume_path = None
            resume_text = None
            resume_raw = None

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
                            resume_raw = rf.read()
                        resume_text = resume_bot.extract_text_from_file_bytes(resume_raw, filename)
                    except Exception as e:
                        logger.error(f"Error extracting resume text: {e}")
        else:
            resume_raw = None
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
            country = data.get("country", "United States").strip()
            summary = data.get("summary", "").strip()
            resume_filename = data.get("resume_filename")
            resume_path = data.get("resume_path")
            resume_text = data.get("resume_text")

        if not name or not email:
            return jsonify({"error": "Consultant name and email are required"}), 400

        is_admin = ("Admin" in user.get("role", ""))
        assigned_user_id = user["id"]
        if is_admin:
            req_assigned = (request.form.get("assigned_user_id") if request.form else None) or (request.get_json(silent=True) or {}).get("assigned_user_id")
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
            country=country,
            resume_filename=resume_filename,
            resume_path=resume_path,
            resume_text=resume_text,
            resume_summary=summary,
            gmail_account=email,
            assigned_user_id=assigned_user_id
        )
        if resume_raw and resume_filename:
            try:
                models.save_resume_file(cand_id, resume_filename, resume_raw)
            except Exception as e:
                logger.error(f"Could not store the original resume file for consultant {cand_id}: {e}")

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
        _ensure_resume_text(cand)
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
        for key in ["name", "email", "phone", "title", "primary_skills", "experience_years", "target_rate", "visa_status", "status", "location", "country", "resume_summary"]:
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
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404

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
            resume_raw = rf.read()
        resume_text = resume_bot.extract_text_from_file_bytes(resume_raw, filename)
        # Keep the original file itself in the database so the optimizer can edit it in place.
        models.save_resume_file(candidate_id, filename, resume_raw)
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
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404

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
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404
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
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404
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
    country = (data.get("country") or "United States").strip()

    results = models.get_jobs(
        query=query if query else None,
        location=location if location and location.lower() != "united states" else None,
        source=source if source != "All" else None,
        job_type=job_type if job_type != "All" else None,
        contract_only=contract_only,
        is_24h_only=is_24h_only,
        country=country
    )

    # If 0 results or live_scrape requested, trigger a live scrape of the selected market. This no
    # longer requires a typed query: since is_24h_only now genuinely hides stale/example data, an
    # empty-query tab load with nothing fresh in the DB must still go get real jobs rather than
    # show an empty table - "Software Engineer" is the same generic default already used elsewhere
    # (triggerUsScrape) when no keyword is given.
    if len(results) == 0 or live_scrape:
        scrape_query = query or "Software Engineer"
        try:
            if country.lower() == "india":
                scrape_res = india_job_scrapers.run_multi_source_india_scrape(
                    keywords=[scrape_query], location=location or "India", save_to_db=True
                )
            else:
                scrape_res = us_job_scrapers.run_multi_source_us_scrape(
                    keywords=[scrape_query],
                    location=location or "United States",
                    contract_only=True,
                    save_to_db=True
                )
            results = models.get_jobs(
                query=query if query else None,
                location=location if location and location.lower() != "united states" else None,
                source=source if source != "All" else None,
                job_type=job_type if job_type != "All" else None,
                contract_only=False,
                is_24h_only=is_24h_only,
                country=country
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
        f"Scraped {scrape_res.get('count', 0)} US tech contract roles from Dice and LinkedIn"
    )

    return jsonify({
        "success": True,
        "count": scrape_res.get("count", 0),
        "saved_to_db": scrape_res.get("saved_to_db", 0),
        "message": f"Successfully scraped {scrape_res.get('count', 0)} fresh US contract jobs."
    })

@app.route("/api/jobs/scrape-india", methods=["POST", "OPTIONS"])
def api_trigger_india_scrape():
    """Scrapes real live India job postings (Naukri, Foundit/Monster India, LinkedIn) via
    Apify actors - each is pay-per-result (roughly $0.001/job), and an empty search costs
    nothing. Requires APIFY_API_TOKEN on the server, same as the LinkedIn Sourcing feature."""
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if not (config.APIFY_API_TOKEN or "").strip():
        return jsonify({"error": "APIFY_API_TOKEN is not configured on the server. India sourcing (Naukri, Foundit) needs it."}), 503

    data = request.json or {}
    keywords = data.get("keywords")
    location = data.get("location", "India")

    scrape_res = india_job_scrapers.run_multi_source_india_scrape(
        keywords=keywords,
        location=location,
        save_to_db=True
    )

    models.log_activity(
        user["id"],
        user["name"],
        "Scraped India Jobs",
        "Scraper",
        0,
        f"Scraped {scrape_res.get('count', 0)} India roles from Naukri, Foundit, and LinkedIn"
    )

    return jsonify({
        "success": True,
        "count": scrape_res.get("count", 0),
        "saved_to_db": scrape_res.get("saved_to_db", 0),
        "message": f"Successfully scraped {scrape_res.get('count', 0)} fresh India jobs."
    })

# --- In-Table Quick Email & Contact Update API ---

@app.route("/api/jobs/<int:job_id>/full-description", methods=["POST", "OPTIONS"])
def api_job_full_description(job_id):
    """Full job description for the Resume Optimizer. Pasted requirements already have it; for a
    LinkedIn/Dice posting it is read from that one public posting (free, on demand) and saved, so
    the next recruiter gets it instantly. Never invented: an unreadable posting returns 422."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    job = models.get_job_by_id(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if (job.get("full_description") or "").strip():
        return jsonify({"description": job["full_description"], "cached": True})
    if job.get("source") == "Manual Paste" and (job.get("description") or "").strip():
        return jsonify({"description": job["description"], "cached": True})
    text, reason = job_description_fetch.fetch_full_description(job.get("url"))
    if not text:
        return jsonify({"error": reason}), 422
    models.save_job_full_description(job_id, text)
    return jsonify({"description": text, "cached": False})


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
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404
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
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404

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
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404

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

    # Login required: this returns the (rewritten) resume text, and used to accept any
    # candidate_id from anyone - so a stranger could enumerate ids and read other people's resumes.
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    # Three ways in:
    #  - multipart with an attached .docx  -> edited IN PLACE (original formatting preserved)
    #  - JSON + use_stored_file + candidate_id -> the consultant's stored original .docx, same
    #  - JSON with resume_text (pasted / PDF / txt / edited) -> text path, Word file regenerated
    docx_bytes = None
    format_note = ""
    if request.content_type and "multipart/form-data" in request.content_type:
        jd_text = (request.form.get("jd_text") or "").strip()
        custom_instructions = (request.form.get("custom_instructions") or "").strip()
        resume_text = ""
        f = request.files.get("resume_file")
        if not f or not f.filename:
            return jsonify({"error": "No resume to optimize. Attach a .docx file or paste the resume text first."}), 400
        if not secure_filename(f.filename).lower().endswith(".docx"):
            return jsonify({"error": "Editing in the original format needs a .docx file. For other formats paste the text instead."}), 400
        docx_bytes = f.read()
        if len(docx_bytes) > 5 * 1024 * 1024:
            return jsonify({"error": "That file is over 5 MB - attach a smaller resume."}), 413
    else:
        data = request.get_json(silent=True) or {}
        resume_text = (data.get("resume_text") or "").strip()
        jd_text = (data.get("jd_text") or "").strip()
        custom_instructions = (data.get("custom_instructions") or "").strip()
        candidate_id = data.get("candidate_id")
        use_stored_file = bool(data.get("use_stored_file"))

        cand = None
        if candidate_id and (use_stored_file or not resume_text):
            # Only a consultant this user may see. Never invent a stand-in resume from their
            # name/title/skills - an optimization of made-up text is worse than an error.
            try:
                cand = models.get_candidate_by_id(int(candidate_id), user_id=user["id"], is_admin=("Admin" in user.get("role", "")))
            except (TypeError, ValueError):
                cand = None
        if cand and use_stored_file:
            docx_bytes = _stored_docx_bytes(cand)
            if docx_bytes is None:
                format_note = ("This consultant's original Word file is no longer stored (or was uploaded as a PDF/text file), so a clean "
                               "Word file was built from the resume text. Attach the original .docx to keep its exact formatting.")
        if cand and not resume_text:
            resume_text = (_ensure_resume_text(cand).get("resume_text") or "").strip()

        if not resume_text and docx_bytes is None:
            return jsonify({"error": "No resume to optimize. Attach a .docx / .pdf / .txt file or paste the resume text first."}), 400

    if not jd_text:
        return jsonify({"error": "Paste the client's Job Description (JD) first."}), 400

    result = resume_bot.optimize_resume_for_jd(
        resume_text=resume_text,
        jd_text=jd_text,
        custom_instructions=custom_instructions,
        docx_bytes=docx_bytes,
    )
    if result.get("error"):
        return jsonify(result), 400

    if docx_bytes is None and not format_note:
        format_note = ("Original formatting is only kept when the resume is a .docx file (attached or stored). This download is a clean "
                       "Word file built from the resume text.")
    result["format_note"] = "" if result.get("format_preserved") else format_note
    return jsonify(result)


def _stored_resume_file(cand):
    """(filename, bytes) of a consultant's original resume file (.docx/.pdf/.txt): from the
    database, or - for consultants created before that table existed - from the resumes folder.
    Only files inside RESUMES_DIR are read. None when there isn't one."""
    rec = models.get_resume_file(cand["id"])
    if rec and rec.get("data"):
        return rec.get("filename") or "resume", rec["data"]
    root = os.path.realpath(RESUMES_DIR)
    candidates = []
    if cand.get("resume_path"):
        candidates.append(cand["resume_path"])
    # The stored absolute path is from whichever server saved it (it changes between deploys /
    # Docker vs native), so also look for the same file name in this server's resumes folder.
    for name in (cand.get("resume_path"), cand.get("resume_filename")):
        base = secure_filename(os.path.basename(name or ""))
        if base:
            candidates.append(os.path.join(RESUMES_DIR, base))
    for path in candidates:
        if os.path.splitext(path.lower())[1] not in (".docx", ".pdf", ".txt"):
            continue
        real = os.path.realpath(path)
        if real.startswith(root + os.sep) and os.path.isfile(real):
            try:
                with open(real, "rb") as fh:
                    return os.path.basename(real), fh.read()
            except OSError:
                continue
    return None


def _stored_docx_bytes(cand):
    """The original .docx of a consultant the caller may see, or None."""
    found = _stored_resume_file(cand)
    if found and found[0].lower().endswith(".docx"):
        return found[1]
    return None


def _ensure_resume_text(cand):
    """A consultant can have a resume FILE but no extracted text - e.g. those created by the old
    startup seeding, which saved resume_path/resume_filename only. The Resume Optimizer reads the
    text, so it reported "No resume is on file" although the file was there. Extract it once,
    store it (and keep the file in the database so it survives deploys). Mutates and returns cand."""
    if (cand.get("resume_text") or "").strip():
        return cand
    found = _stored_resume_file(cand)
    if not found:
        return cand
    filename, data = found
    text = (resume_bot.extract_text_from_file_bytes(data, filename) or "").strip()
    if len(text) < 30:
        return cand
    models.update_candidate(cand["id"], resume_text=text,
                            **({} if cand.get("resume_filename") else {"resume_filename": filename}))
    if not models.get_resume_file(cand["id"]):
        models.save_resume_file(cand["id"], filename, data)
    cand["resume_text"] = text
    cand["resume_filename"] = cand.get("resume_filename") or filename
    logger.info(f"Extracted stored resume text for consultant #{cand['id']} ({filename}, {len(text)} chars)")
    return cand


@app.route("/api/resume-bot/extract-text", methods=["POST", "OPTIONS"])
def api_resume_bot_extract_text():
    """Reads an attached resume file and returns its text so the page can show exactly what will
    be optimized. Nothing is stored - the file is read in memory and discarded."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    if not current_user():
        return jsonify({"error": "Login required"}), 401

    f = request.files.get("resume_file")
    if not f or not f.filename:
        return jsonify({"error": "Choose a resume file (.docx, .pdf or .txt)."}), 400
    filename = secure_filename(f.filename) or "resume"
    if os.path.splitext(filename.lower())[1] not in (".docx", ".pdf", ".txt"):
        return jsonify({"error": "Unsupported file type. Attach a .docx, .pdf or .txt resume."}), 400
    raw = f.read()
    if len(raw) > 5 * 1024 * 1024:
        return jsonify({"error": "That file is over 5 MB - attach a smaller resume."}), 413

    text = resume_bot.extract_text_from_file_bytes(raw, filename, strict=True)
    if len(text.strip()) < 30:
        return jsonify({"error": "No readable text was found in that file (a scanned-image PDF can't be read). Paste the resume text instead."}), 422
    return jsonify({"text": text, "filename": filename, "chars": len(text)})

@app.route("/api/resume-bot/download-docx", methods=["POST", "OPTIONS"])
def api_resume_bot_download_docx():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not current_user():
        return jsonify({"error": "Login required"}), 401

    data = request.get_json(silent=True) or {}
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
    """Selected Bachelor's passout year (India). None means 'All years'. Clamped to the range
    Sourcing actually supports (linkedin_sourcing.MIN/MAX_BACHELOR_YEAR) - not a hardcoded 2020,
    which used to silently downgrade a request for e.g. 2023 to 2020 with no explanation."""
    raw_by = str((data or {}).get("bachelor_year") or (data or {}).get("year") or "").strip()
    year_match = re.search(r'\b(19\d\d|20\d\d)\b', raw_by)
    if not year_match:
        return None
    year = int(year_match.group(1))
    return max(linkedin_sourcing.MIN_BACHELOR_YEAR, min(linkedin_sourcing.MAX_BACHELOR_YEAR, year))


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


@app.route("/api/students/sourced-pool", methods=["GET"])
def api_students_sourced_pool():
    """Every verified match ANY recruiter has already found for this (source, year) -
    read from the shared database, costs nothing. The Sourcing tab calls this first so
    the whole team sees each other's results instantly instead of re-paying to re-find them."""
    if not current_user():
        return jsonify({"error": "Login required"}), 401
    source = (request.args.get("source") or "").strip()
    if source != "apify":
        return jsonify({"error": "source must be 'apify'"}), 400
    bachelor_year = _parse_bachelor_year({"bachelor_year": request.args.get("bachelor_year")})
    matches = sourcing_store.get_cached_matches(source, bachelor_year)
    cursor = sourcing_store.get_cursor(source, bachelor_year)
    return jsonify({"matches": matches, "exhausted": cursor["exhausted"],
                    "pool_counts": sourcing_store.pool_counts(source)})


@app.route("/api/students/search-start", methods=["POST", "OPTIONS"])
def api_students_search_start():
    """Start a live LinkedIn search (costs Apify credits, capped per search and
    per day). Returns the Apify run/dataset ids; the client then polls
    /api/students/search-poll. Login required - this spends real money.

    start_page comes from the TEAM'S shared cursor, not the browser: once one
    recruiter has scanned pages 1-5 for a year, the next recruiter's search (on
    any device) continues at page 6 instead of re-paying to re-scan 1-5."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    data = request.get_json(silent=True) or {}
    bachelor_year = _parse_bachelor_year(data)
    cursor = sourcing_store.get_cursor("apify", bachelor_year)
    start_page = cursor["next_page"]
    pages = data.get("pages") or 1
    # Re-trying ONE page whose run failed at the data provider: same page again, cursor untouched
    # (that page was already reserved when it was first started).
    retry_page = None
    if data.get("retry_start_page"):
        try:
            retry_page = max(1, min(int(data["retry_start_page"]), linkedin_sourcing.MAX_START_PAGE))
            start_page, pages = retry_page, 1
        except (TypeError, ValueError):
            retry_page = None
    result = linkedin_sourcing.start_search(
        bachelor_year,
        pages=pages,   # number of parallel one-page runs to start (max 5)
        location=data.get("location") or "United States",
        start_page=start_page,
    )
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    # Reserve this page range immediately so a second recruiter clicking Search in the
    # same moment gets the NEXT range, not an overlapping (double-paid) one.
    if not retry_page:
        sourcing_store.save_cursor("apify", bachelor_year, next_page=result["next_start_page"])
    result["bachelor_year"] = bachelor_year
    return jsonify(result)


@app.route("/api/students/search-poll", methods=["POST", "OPTIONS"])
def api_students_search_poll():
    """Poll a running LinkedIn search: returns run status plus ONLY the newly
    scanned profiles that pass the strict India-Bachelor's + US-Master's check."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
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

    bachelor_year = _parse_bachelor_year(data)
    result = linkedin_sourcing.poll_search(
        run_id, dataset_id, bachelor_year, offset, matched_so_far,
        target=data.get("target") or linkedin_sourcing.TARGET_MATCHES,
    )
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    # Every recruiter's find goes straight into the shared pool - the next person who
    # searches this year sees it for free instead of Apify re-scanning the same profile.
    sourcing_store.save_matches("apify", bachelor_year, result.get("new_matches") or [], user["id"])
    # Profiles that were fully verified but graduated in a DIFFERENT year are banked under their own
    # year (their year's search will then show them for free). Not sent to the browser for this year.
    other = result.pop("other_year_matches", None) or []
    result["banked_other_years"] = sourcing_store.save_matches("apify", None, other, user["id"]) if other else 0
    # EVERY scanned profile (not just this year's matches) has a full, already-paid-for education
    # list: store it for the education filters (Filter A / B). No extra API call.
    raw_items = result.pop("raw_items", None) or []
    if raw_items:
        try:
            linkedin_ingest.ingest_profiles(linkedin_ingest.ApifyProfileProvider().to_profiles(raw_items), "apify", user["id"])
            vconn = models.get_db_connection()
            try:
                linkedin_ingest.mark_verified(vconn, (result.get("new_matches") or []) + other)
                vconn.commit()
            finally:
                vconn.close()
        except Exception as ex:
            logger.warning(f"Education filters: could not store scanned profiles: {ex}")
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


# --- Education filters (Sourcing: Filter A = Indian college -> US Master's, Filter B = US university
# -> Indian undergrad). Reads the team-wide education tables only - no Apify / paid calls. ---

def _edu_user():
    user = current_user()
    if not user:
        return None, (jsonify({"error": "Login required"}), 401)
    return user, None


def _edu_admin():
    user = current_user()
    if not user:
        return None, (jsonify({"error": "Login required"}), 401)
    if "Admin" not in user.get("role", ""):
        return None, (jsonify({"error": "Admin permission required."}), 403)
    return user, None


@app.route("/api/education/institutions", methods=["GET"])
def api_education_institutions():
    """Autocomplete list for one filter: A -> Indian colleges, B -> US universities."""
    user, err = _edu_user()
    if err:
        return err
    f = (request.args.get("filter") or "").strip().upper()
    if f not in ("A", "B"):
        return jsonify({"error": "filter must be 'A' or 'B'"}), 400
    conn = models.get_db_connection()
    try:
        insts = education_filters.institutions_for(conn, f)
    finally:
        conn.close()
    return jsonify({"institutions": insts})


@app.route("/api/education/search", methods=["GET"])
def api_education_search():
    user, err = _edu_user()
    if err:
        return err
    try:
        params = education_filters.parse_params(request.args)
    except education_filters.FilterError as ex:
        return jsonify({"error": str(ex)}), 400
    conn = models.get_db_connection()
    try:
        return jsonify(education_filters.search(conn, params))
    finally:
        conn.close()


@app.route("/api/education/export-xlsx", methods=["GET"])
def api_education_export_xlsx():
    """Excel download of every row the current filter matches (up to 10,000). Logged with the recruiter id."""
    user, err = _edu_user()
    if err:
        return err
    try:
        params = education_filters.parse_params(request.args)
    except education_filters.FilterError as ex:
        return jsonify({"error": str(ex)}), 400
    conn = models.get_db_connection()
    try:
        rows = education_filters.export_rows(conn, params)
        cur = conn.cursor()
        inst_name = education_filters.display_name(conn, params["institution_id"])
        if params["filter"] == "P":
            span = (str(params["year_from"]) if params["year_from"] == params["year_to"]
                    else f"{params['year_from']}-{params['year_to']}")
            label = f"Passout {span}: Indian Bachelor's + US Master's"
            inst_name = f"Passout_{span}"
        else:
            label = ("Filter A: Indian college -> US Master's" if params["filter"] == "A"
                     else "Filter B: US university -> Indian undergrad") + f" | {inst_name}"
        if params["filter"] != "P" and (params["year_from"] or params["year_to"]):
            label += f" | years {params['year_from'] or '...'}-{params['year_to'] or '...'}"
        if params["location"]:
            label += f" | location: {params['location']}"
        if params["keyword"]:
            label += f" | keyword: {params['keyword']}"
        cur.execute("INSERT INTO capture_log (user_id, action, source, profile_id, details) VALUES (?, ?, ?, ?, ?)",
                    (user["id"], "export_xlsx", "education_filter", None, f"{len(rows)} rows | {label}"))
        conn.commit()
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Exported Excel", "Sourcing", 0, f"{len(rows)} candidates - {label}")
    data = education_filters.build_xlsx(rows, label)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", inst_name if params["filter"] == "P" else f"Filter_{params['filter']}_{inst_name}").strip("_")[:80]
    return send_file(io.BytesIO(data), as_attachment=True, download_name=f"{slug}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _edu_live_params(data):
    """Validated (filter, member ids, year_from, year_to, school names) for a Filter A/B LinkedIn search."""
    f = str(data.get("filter") or "").strip().upper()
    if f not in ("A", "B"):
        raise education_filters.FilterError("filter must be 'A' or 'B'")
    params = education_filters.parse_params({"filter": f, "institution_id": data.get("institution_id"),
                                             "year_from": data.get("year_from"), "year_to": data.get("year_to")})
    members = education_filters.member_ids(params["institution_id"])
    want_country = education_filters.FILTERS[f]["chosen_country"]
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        names = []
        for m in members:
            cur.execute("SELECT MIN(canonical_name), MIN(country) FROM institution_aliases WHERE canonical_id = ?", (m,))
            row = cur.fetchone()
            if not row or not row[0] or row[1] != want_country:
                raise education_filters.FilterError("Choose a college from the list.")
            # LinkedIn's school filter needs a real school name, not our "(campus not stated)" label.
            names.append(re.sub(r"\s*\(campus not stated\)\s*$", "", row[0]))
    finally:
        conn.close()
    return f, params["institution_id"], members, params["year_from"], params["year_to"], names


@app.route("/api/education/live-start", methods=["POST", "OPTIONS"])
def api_education_live_start():
    """Start a paid LinkedIn search (Apify) for ONE chosen college/university (Filter A/B). Same
    caps as the Passout search: per-run spend cap, daily budget, Apify account headroom. The team's
    shared cursor per (filter, institution) makes the next search continue on new profiles."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    try:
        f, inst_id, members, y_from, y_to, schools = _edu_live_params(request.get_json(silent=True) or {})
    except education_filters.FilterError as ex:
        return jsonify({"error": str(ex)}), 400
    exp_ids = []
    if f == "A" and (y_from or y_to):
        lo, hi = (y_from or y_to), (y_to or y_from)
        for y in range(lo, min(hi, lo + 20) + 1):
            for e in linkedin_sourcing.experience_ids_for_year(y):
                if e not in exp_ids:
                    exp_ids.append(e)
    cursor_key = f"edu-{f}-{inst_id}"
    cursor = sourcing_store.get_cursor("apify", cursor_key)
    result = linkedin_sourcing.start_search(None, pages=1, start_page=cursor["next_page"],
                                            schools=schools, experience_ids=exp_ids)
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    sourcing_store.save_cursor("apify", cursor_key, next_page=result["next_start_page"])
    models.log_activity(user["id"], user["name"], "LinkedIn Search", "Sourcing", 0,
                        f"Filter {f} live search: {', '.join(schools)} (page {cursor['next_page']})")
    return jsonify(result)


@app.route("/api/education/live-poll", methods=["POST", "OPTIONS"])
def api_education_live_poll():
    """Poll a Filter A/B LinkedIn search run: stores every scanned profile, records the people
    verified for the chosen institution, banks Passout-year matches, returns counts (no raw data)."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    try:
        f, inst_id, members, y_from, y_to, _ = _edu_live_params(data)
    except education_filters.FilterError as ex:
        return jsonify({"error": str(ex)}), 400
    run_id, dataset_id = str(data.get("run_id") or ""), str(data.get("dataset_id") or "")
    if not _APIFY_ID_RE.match(run_id) or not _APIFY_ID_RE.match(dataset_id):
        return jsonify({"error": "Invalid run reference"}), 400
    try:
        offset = max(0, int(data.get("offset") or 0))
        matched_so_far = max(0, int(data.get("matched_so_far") or 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid offset"}), 400
    result = linkedin_sourcing.poll_filter_search(run_id, dataset_id, f, members, y_from, y_to,
                                                  offset=offset, matched_so_far=matched_so_far)
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    raw_items = result.pop("raw_items", None) or []
    matches = result.pop("new_matches", None) or []
    passout = result.pop("passout_matches", None) or []
    try:
        if raw_items:
            linkedin_ingest.ingest_profiles(linkedin_ingest.ApifyProfileProvider().to_profiles(raw_items), "apify", user["id"])
        if passout:
            sourcing_store.save_matches("apify", None, passout, user["id"])
        conn = models.get_db_connection()
        try:
            linkedin_ingest.mark_filter_verified(conn, matches)
            linkedin_ingest.mark_verified(conn, passout)
            conn.commit()
        finally:
            conn.close()
    except Exception as ex:
        logger.warning(f"Education live search: could not store results: {ex}")
    result["new_matches"] = len(matches)
    result["passout_banked"] = len(passout)
    return jsonify(result)


def _tracker_profile(profile_id):
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, tracking_status FROM linkedin_profiles WHERE id = ?", (profile_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _tracker_thread(profile_id):
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT c.id, c.kind, c.comment, c.created_at, c.user_id, u.name AS author
                       FROM sourcing_comments c LEFT JOIN users u ON u.id = c.user_id
                       WHERE c.profile_id = ? ORDER BY c.id DESC""", (profile_id,))
        return [{"id": r["id"], "kind": r["kind"], "text": r["comment"], "author": r["author"] or "",
                 "user_id": r["user_id"], "at": str(r["created_at"] or "")[:16]} for r in cur.fetchall()]
    finally:
        conn.close()


@app.route("/api/sourcing/profiles/<int:profile_id>/comments", methods=["GET", "POST", "OPTIONS"])
def api_sourcing_comments(profile_id):
    """Sourcing tracker: a sourced candidate's status history + comments (team-wide). POST adds a comment."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    prof = _tracker_profile(profile_id)
    if not prof:
        return jsonify({"error": "Candidate not found"}), 404
    if request.method == "POST":
        text = ((request.get_json(silent=True) or {}).get("comment") or "").strip()
        if not text:
            return jsonify({"error": "Write a comment first."}), 400
        if len(text) > 2000:
            return jsonify({"error": "Comments are limited to 2,000 characters."}), 400
        conn = models.get_db_connection()
        try:
            conn.cursor().execute("INSERT INTO sourcing_comments (profile_id, user_id, kind, comment) VALUES (?, ?, 'comment', ?)",
                                  (profile_id, user["id"], text))
            conn.commit()
        finally:
            conn.close()
    return jsonify({"status": prof.get("tracking_status") or "New", "thread": _tracker_thread(profile_id),
                    "statuses": education_filters.STATUSES})


@app.route("/api/sourcing/comments/<int:comment_id>", methods=["DELETE", "OPTIONS"])
def api_sourcing_comment_delete(comment_id):
    """Delete a comment - only its author or an admin. Status-change records can't be deleted."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, kind, profile_id FROM sourcing_comments WHERE id = ?", (comment_id,))
        row = cur.fetchone()
        if not row or row["kind"] != "comment":
            return jsonify({"error": "Comment not found"}), 404
        if row["user_id"] != user["id"] and "Admin" not in user.get("role", ""):
            return jsonify({"error": "Only the person who wrote it (or an admin) can delete a comment."}), 403
        cur.execute("DELETE FROM sourcing_comments WHERE id = ?", (comment_id,))
        conn.commit()
        profile_id = row["profile_id"]
    finally:
        conn.close()
    return jsonify({"success": True, "thread": _tracker_thread(profile_id)})


@app.route("/api/sourcing/profiles/<int:profile_id>/status", methods=["POST", "OPTIONS"])
def api_sourcing_status(profile_id):
    """Set a sourced candidate's tracking status; the change is recorded in its thread."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    status = ((request.get_json(silent=True) or {}).get("status") or "").strip()
    if status not in education_filters.STATUSES:
        return jsonify({"error": "Unknown status."}), 400
    prof = _tracker_profile(profile_id)
    if not prof:
        return jsonify({"error": "Candidate not found"}), 404
    old = prof.get("tracking_status") or "New"
    if old != status:
        conn = models.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE linkedin_profiles SET tracking_status = ? WHERE id = ?", (status, profile_id))
            cur.execute("INSERT INTO sourcing_comments (profile_id, user_id, kind, comment) VALUES (?, ?, 'status', ?)",
                        (profile_id, user["id"], f"Status: {old} \u2192 {status}"))
            conn.commit()
        finally:
            conn.close()
    return jsonify({"status": status, "thread": _tracker_thread(profile_id)})


@app.route("/api/education/unmapped", methods=["GET"])
def api_education_unmapped():
    """Admin list: school names from LinkedIn profiles that didn't match the institution list."""
    user, err = _edu_admin()
    if err:
        return err
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT u.id, u.name, u.name_norm, u.status, u.suggested_canonical_id, u.seen_count,
                              (SELECT MIN(a.canonical_name) FROM institution_aliases a
                               WHERE a.canonical_id = u.suggested_canonical_id) AS suggested_name,
                              (SELECT COUNT(DISTINCT e.profile_id) FROM profile_education e
                               WHERE e.institution_norm = u.name_norm) AS profiles
                       FROM unmapped_institutions u ORDER BY u.seen_count DESC, u.id DESC LIMIT 500""")
        items = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT canonical_id, MIN(canonical_name) AS name, MIN(country) AS country
                       FROM institution_aliases GROUP BY canonical_id ORDER BY MIN(country), MIN(canonical_name)""")
        institutions = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return jsonify({"items": items, "institutions": institutions})


@app.route("/api/education/unmapped/<int:item_id>/map", methods=["POST", "OPTIONS"])
def api_education_unmapped_map(item_id):
    """Map an unmatched school name to an existing institution, or create a new institution for it.
    The name becomes an alias, and every stored education entry with that name is updated."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user, err = _edu_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, name_norm FROM unmapped_institutions WHERE id = ?", (item_id,))
        item = cur.fetchone()
        if not item:
            return jsonify({"error": "Not found"}), 404
        item = dict(item)
        canonical_id = (data.get("canonical_id") or "").strip()
        if canonical_id:
            cur.execute("SELECT canonical_name, country, city FROM institution_aliases WHERE canonical_id = ? LIMIT 1",
                        (canonical_id,))
            inst = cur.fetchone()
            if not inst:
                return jsonify({"error": "Unknown institution"}), 400
            canonical_name, country, city = inst["canonical_name"], inst["country"], inst["city"]
        else:
            canonical_name = (data.get("new_name") or "").strip()
            country = (data.get("country") or "").strip()
            city = (data.get("city") or "").strip()
            if not canonical_name or country not in ("India", "USA", "Other"):
                return jsonify({"error": "A new institution needs a name and a country (India, USA or Other)."}), 400
            canonical_id = "custom-" + re.sub(r"[^a-z0-9]+", "-", canonical_name.lower()).strip("-")[:60]
            cur.execute("SELECT 1 FROM institution_aliases WHERE canonical_id = ?", (canonical_id,))
            if cur.fetchone():
                return jsonify({"error": "That institution already exists - pick it from the list instead."}), 400
            name_norm = education_match.normalize(canonical_name)
            cur.execute("SELECT 1 FROM institution_aliases WHERE alias_norm = ?", (name_norm,))
            if cur.fetchone():
                return jsonify({"error": "That name already belongs to an institution - pick it from the list instead."}), 400
            if name_norm != item["name_norm"]:
                cur.execute("""INSERT INTO institution_aliases (canonical_id, canonical_name, alias, alias_norm, country, city)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (canonical_id, canonical_name, canonical_name, name_norm, country, city))
        cur.execute("SELECT canonical_id FROM institution_aliases WHERE alias_norm = ?", (item["name_norm"],))
        existing = cur.fetchone()
        if existing and existing[0] != canonical_id:
            conn.rollback()
            return jsonify({"error": "That name is already an alias of another institution."}), 400
        if not existing:
            cur.execute("""INSERT INTO institution_aliases (canonical_id, canonical_name, alias, alias_norm, country, city)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (canonical_id, canonical_name, item["name"], item["name_norm"], country, city))
        cur.execute("DELETE FROM unmapped_institutions WHERE id = ?", (item_id,))
        conn.commit()
        education_match.invalidate_cache()
        updated = linkedin_ingest.rematch_institution(conn, item["name_norm"])
        conn.commit()
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Mapped Institution", "Sourcing", item_id,
                        f"'{item['name']}' -> {canonical_name} ({country}); {updated} education entries updated")
    return jsonify({"success": True, "canonical_id": canonical_id, "canonical_name": canonical_name,
                    "entries_updated": updated})
