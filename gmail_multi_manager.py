import os
import sys
import json
import base64
import mimetypes
import imaplib
import time
from pathlib import Path
from email.message import EmailMessage
from typing import Dict, Optional, Any, Tuple

import models
import config

BASE_DIR = Path(config.BASE_DIR)
DATA_DIR = BASE_DIR / "data"
TOKENS_DIR = DATA_DIR / "tokens"
RESUMES_DIR = DATA_DIR / "resumes"
OAUTH_CREDS_FILE = BASE_DIR / "oauth_credentials.json"

TOKENS_DIR.mkdir(parents=True, exist_ok=True)
RESUMES_DIR.mkdir(parents=True, exist_ok=True)

GMAIL_SCOPES = [
    "https://mail.google.com/"
]

def verify_gmail_app_password(gmail_address: str, app_password: str) -> Tuple[bool, str]:
    """Test login to Gmail via IMAP with App Password."""
    clean_pass = app_password.replace(" ", "").strip()
    clean_user = gmail_address.strip()
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", port=993)
        mail.login(clean_user, clean_pass)
        mail.logout()
        return True, "Login successful"
    except Exception as e:
        return False, str(e)

def is_candidate_connected(candidate_id: int) -> Dict[str, Any]:
    cand = models.get_candidate_by_id(candidate_id)
    if not cand:
        return {"connected": False, "message": "Candidate not found"}

    # 1. Check App Password first
    if cand.get("gmail_app_password") and cand.get("gmail_account"):
        return {
            "connected": True,
            "method": "app_password",
            "email": cand.get("gmail_account")
        }

    # 2. Check OAuth token
    token_path = TOKENS_DIR / f"token_{candidate_id}.json"
    if not token_path.exists() and cand.get("gmail_token_path") and Path(cand["gmail_token_path"]).exists():
        token_path = Path(cand["gmail_token_path"])

    if token_path.exists():
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
            is_valid = creds.valid or (creds.expired and bool(creds.refresh_token))
            if is_valid:
                return {
                    "connected": True,
                    "method": "oauth",
                    "email": cand.get("gmail_account") or cand.get("email"),
                    "token_path": str(token_path)
                }
        except Exception:
            pass

    return {
        "connected": False,
        "email": cand.get("gmail_account") or cand.get("email")
    }

def get_auth_url(candidate_id: int, redirect_uri: str = "http://localhost:5000/api/auth/google/callback") -> Tuple[Optional[str], Optional[str]]:
    creds_file = OAUTH_CREDS_FILE if OAUTH_CREDS_FILE.exists() else Path(r"F:\AI_Job_Hunter\oauth_credentials.json")
    if not creds_file.exists():
        return None, None

    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            str(creds_file),
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=str(candidate_id)
        )
        verifier = getattr(flow, "code_verifier", None)
        if verifier:
            verifier_file = TOKENS_DIR / f"verifier_{candidate_id}.txt"
            with open(verifier_file, "w", encoding="utf-8") as vf:
                vf.write(verifier)

        return auth_url, verifier
    except Exception as e:
        print(f"[-] Error generating OAuth URL for candidate {candidate_id}: {e}")
        return None, None

def exchange_code_and_save_token(candidate_id: int, auth_code: str, redirect_uri: str = "http://localhost:5000/api/auth/google/callback", code_verifier: Optional[str] = None) -> Dict[str, Any]:
    creds_file = OAUTH_CREDS_FILE if OAUTH_CREDS_FILE.exists() else Path(r"F:\AI_Job_Hunter\oauth_credentials.json")
    if not creds_file.exists():
        return {"success": False, "error": "OAuth credentials file not found"}

    try:
        from google_auth_oauthlib.flow import Flow
        from googleapiclient.discovery import build

        flow = Flow.from_client_secrets_file(
            str(creds_file),
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri
        )

        if not code_verifier:
            verifier_file = TOKENS_DIR / f"verifier_{candidate_id}.txt"
            if verifier_file.exists():
                with open(verifier_file, "r", encoding="utf-8") as vf:
                    code_verifier = vf.read().strip()

        if code_verifier:
            flow.code_verifier = code_verifier
            flow.fetch_token(code=auth_code, code_verifier=code_verifier)
        else:
            flow.fetch_token(code=auth_code)

        creds = flow.credentials
        token_path = TOKENS_DIR / f"token_{candidate_id}.json"
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        gmail_address = ""
        try:
            service = build("gmail", "v1", credentials=creds)
            profile = service.users().getProfile(userId="me").execute()
            gmail_address = profile.get("emailAddress", "")
        except Exception:
            pass

        models.update_candidate(
            candidate_id,
            gmail_account=gmail_address if gmail_address else None,
            gmail_token_path=str(token_path)
        )

        return {
            "success": True,
            "candidate_id": candidate_id,
            "gmail_account": gmail_address,
            "token_path": str(token_path)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def generate_consultant_pitch(candidate: Dict, job: Dict, custom_notes: str = "") -> str:
    """
    Generates a concise, short & sweet 1st-person job application email written
    directly from the consultant's perspective to the hiring recruiter.
    """
    cand_name = candidate.get("name", "Consultant")
    cand_title = candidate.get("title", "Technical Consultant")
    cand_skills = candidate.get("primary_skills", "")
    cand_exp = candidate.get("experience_years") or 6
    cand_visa = candidate.get("visa_status") or "H1B"
    cand_phone = candidate.get("phone", "")
    cand_email = candidate.get("gmail_account") or candidate.get("email", "")
    cand_linkedin = candidate.get("linkedin_url") or candidate.get("profile_url") or ""

    job_title = job.get("title") or "Technical Position"
    source = job.get("source") or "LinkedIn"
    recruiter_name = job.get("recruiter_name") or "Hiring Team"
    if not recruiter_name or recruiter_name.lower() in ["none", "hiring manager", "null"]:
        recruiter_name = "Hiring Team"

    # Extract 3-4 top concise skills
    skills_list = [s.strip() for s in cand_skills.split(",") if s.strip()]
    top_skills = ", ".join(skills_list[:4]) if skills_list else cand_skills

    # Suitability summary matching requirement
    if custom_notes and len(custom_notes.strip()) > 10:
        clean_note = " ".join(custom_notes.strip().split())
        if len(clean_note) > 120:
            clean_note = clean_note[:117] + "..."
        why_suit = f"My hands-on experience in {top_skills} directly aligns with your requirement for {clean_note}."
    else:
        why_suit = f"My hands-on experience in {top_skills} directly aligns with your project requirements."

    # First-person direct candidate application
    body = f"""Hi {recruiter_name},

I came across your job posting for {job_title} posted on {source}. I am very interested in this position.

I am {cand_name} with {cand_exp} years of experience as a {cand_title} on {cand_visa} visa. {why_suit}

Please find my resume attached for your review. I am available immediately for an interview and can join right away on contract / C2C terms.

Looking forward to hearing from you.

Best regards,
{cand_name}
{cand_title}
Phone: {cand_phone}
Email: {cand_email}
LinkedIn: {cand_linkedin}
"""
    return body

def create_candidate_draft(candidate_id: int, job_id: int, custom_to_email: Optional[str] = None, custom_notes: str = "", custom_subject: Optional[str] = None, custom_body: Optional[str] = None) -> Dict[str, Any]:
    cand = models.get_candidate_by_id(candidate_id)
    if not cand:
        return {"success": False, "error": f"Candidate #{candidate_id} not found."}

    job = models.get_job_by_id(job_id)
    if not job:
        return {"success": False, "error": f"Job #{job_id} not found."}

    to_email = (custom_to_email or job.get("recruiter_email") or "").strip()
    if not to_email or "@" not in to_email:
        return {
            "success": False, 
            "needs_email": True, 
            "error": "Recruiter email is missing. Please type recruiter email directly in the table cell."
        }

    if custom_to_email and custom_to_email != job.get("recruiter_email"):
        models.update_job_recruiter_info(job_id, email=custom_to_email)

    cand_name = cand.get("name", "Consultant")
    cand_title = cand.get("title", "Specialist")
    cand_exp = cand.get("experience_years") or 6
    cand_visa = cand.get("visa_status") or "H1B"
    job_title = job.get("title", "Technical Role")
    company = job.get("company", "Company")
    sender_email = cand.get("gmail_account") or cand.get("email")

    subject = (custom_subject.strip() if custom_subject and custom_subject.strip() else f"Job Application: {job_title} - {cand_name} ({cand_exp} Yrs Exp | {cand_visa})")
    body_text = (custom_body.strip() if custom_body and custom_body.strip() else generate_consultant_pitch(cand, job, custom_notes))

    msg = EmailMessage()
    msg["To"] = to_email
    msg["From"] = sender_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    # Attach Resume
    resume_attached = False
    resume_path = cand.get("resume_path")
    if resume_path and os.path.exists(resume_path):
        with open(resume_path, "rb") as f:
            file_data = f.read()
            file_name = os.path.basename(resume_path)
        msg.add_attachment(
            file_data,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=file_name
        )
        resume_attached = True

    # 1. METHOD A: Using Gmail App Password (IMAP Append to [Gmail]/Drafts)
    app_password = cand.get("gmail_app_password")
    if app_password and sender_email:
        try:
            clean_pass = app_password.replace(" ", "").strip()
            mail = imaplib.IMAP4_SSL("imap.gmail.com", port=993)
            mail.login(sender_email, clean_pass)
            
            # Select or append to Drafts folder
            draft_folder = '"[Gmail]/Drafts"'
            mail.append(draft_folder, "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            mail.logout()

            draft_id = f"imap-draft-{int(time.time())}"
            models.mark_job_drafted(
                job_id=job_id,
                candidate_id=candidate_id,
                draft_id=draft_id,
                user_id=1,
                notes=f"Created Gmail Draft via App Password for {cand_name} -> {to_email}"
            )
            models.log_activity(
                user_id=1,
                user_name="Recruiter",
                action="Gmail Draft Created (App Password)",
                target_type="Job Application",
                target_id=job_id,
                details=f"Drafted application in {sender_email} for {job_title} at {company} ({to_email})"
            )
            return {
                "success": True,
                "method": "app_password",
                "draft_id": draft_id,
                "to_email": to_email,
                "subject": subject,
                "candidate_name": cand_name,
                "company": company,
                "resume_attached": resume_attached,
                "preview": body_text[:280] + "..."
            }
        except Exception as e:
            return {"success": False, "error": f"IMAP App Password Error: {str(e)}"}

    # 2. METHOD B: Using OAuth Token
    token_path = TOKENS_DIR / f"token_{candidate_id}.json"
    if not token_path.exists() and cand.get("gmail_token_path") and Path(cand["gmail_token_path"]).exists():
        token_path = Path(cand["gmail_token_path"])

    if token_path.exists():
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())

            if creds and creds.valid:
                service = build("gmail", "v1", credentials=creds)
                raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
                draft_body = {"message": {"raw": raw_msg}}
                draft = service.users().drafts().create(userId="me", body=draft_body).execute()
                draft_id = draft.get("id")

                models.mark_job_drafted(
                    job_id=job_id,
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                    user_id=1,
                    notes=f"Created 1-Click Gmail Draft for {cand_name} -> {to_email}"
                )
                models.log_activity(
                    user_id=1,
                    user_name="Recruiter",
                    action="Gmail Draft Created (OAuth)",
                    target_type="Job Application",
                    target_id=job_id,
                    details=f"Drafted application for {cand_name} -> {job_title} at {company} ({to_email})"
                )
                return {
                    "success": True,
                    "method": "oauth",
                    "draft_id": draft_id,
                    "to_email": to_email,
                    "subject": subject,
                    "candidate_name": cand_name,
                    "company": company,
                    "resume_attached": resume_attached,
                    "preview": body_text[:280] + "..."
                }
        except Exception as e:
            print(f"[-] OAuth draft error: {e}")

    # Neither connected
    auth_url, _ = get_auth_url(candidate_id)
    return {
        "success": False,
        "needs_auth": True,
        "auth_url": auth_url,
        "error": f"Gmail not connected for {cand['name']}. Connect via App Password or Google OAuth."
    }
