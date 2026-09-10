import os
import re
import sqlite3
import urllib.parse
import urllib.parse
import sqlite3
import os
import json
from datetime import datetime
import config

def get_db_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Create Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        role TEXT DEFAULT 'Recruiter',
        avatar_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Create Candidates / Consultants table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT,
        title TEXT,
        primary_skills TEXT,
        experience_years INTEGER DEFAULT 5,
        target_rate TEXT DEFAULT '$90/hr (C2C)',
        visa_status TEXT DEFAULT 'C2C Eligible',
        status TEXT DEFAULT 'Available',
        location TEXT DEFAULT 'United States (Remote)',
        resume_filename TEXT,
        resume_path TEXT,
        resume_text TEXT,
        resume_summary TEXT,
        gmail_account TEXT,
        gmail_token_path TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. Create Jobs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        company TEXT NOT NULL,
        location TEXT DEFAULT 'United States',
        job_type TEXT DEFAULT 'Contract (C2C)',
        salary TEXT,
        source TEXT DEFAULT 'Dice',
        url TEXT,
        recruiter_email TEXT,
        recruiter_phone TEXT,
        recruiter_name TEXT,
        description TEXT,
        matched_skills TEXT,
        match_score INTEGER DEFAULT 88,
        status TEXT DEFAULT 'Open',
        is_24h INTEGER DEFAULT 1,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 4. Create Applications / Pipeline table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        candidate_id INTEGER NOT NULL,
        user_id INTEGER,
        stage TEXT DEFAULT 'Saved',
        notes TEXT,
        draft_id TEXT,
        drafted_at TIMESTAMP,
        applied_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE,
        FOREIGN KEY (candidate_id) REFERENCES candidates (id) ON DELETE CASCADE
    );
    """)

    # 5. Create Activity Logs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        action TEXT NOT NULL,
        target_type TEXT,
        target_id INTEGER,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()

    # Apply schema migrations for missing columns in existing databases
    migrate_db(conn)

    # Seed initial data if tables are empty
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        seed_initial_data(conn)

    conn.close()

def migrate_db(conn):
    """Automatically adds missing columns to existing SQLite tables."""
    cursor = conn.cursor()

    # Candidate table columns
    cursor.execute("PRAGMA table_info(candidates)")
    c_cols = [row[1] for row in cursor.fetchall()]
    candidate_new_cols = {
        "visa_status": "TEXT DEFAULT 'C2C Eligible'",
        "resume_filename": "TEXT",
        "resume_path": "TEXT",
        "resume_text": "TEXT",
        "gmail_account": "TEXT",
        "gmail_token_path": "TEXT",
        "gmail_app_password": "TEXT"
    }
    for col, c_type in candidate_new_cols.items():
        if col not in c_cols:
            cursor.execute(f"ALTER TABLE candidates ADD COLUMN {col} {c_type}")

    # Jobs table columns
    cursor.execute("PRAGMA table_info(jobs)")
    j_cols = [row[1] for row in cursor.fetchall()]
    job_new_cols = {
        "recruiter_email": "TEXT",
        "recruiter_phone": "TEXT",
        "recruiter_name": "TEXT",
        "matched_skills": "TEXT",
        "is_24h": "INTEGER DEFAULT 1",
        "scraped_at": "TEXT"
    }
    for col, c_type in job_new_cols.items():
        if col not in j_cols:
            cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col} {c_type}")

    # Applications table columns
    cursor.execute("PRAGMA table_info(applications)")
    a_cols = [row[1] for row in cursor.fetchall()]
    app_new_cols = {
        "draft_id": "TEXT",
        "drafted_at": "TEXT"
    }
    for col, c_type in app_new_cols.items():
        if col not in a_cols:
            cursor.execute(f"ALTER TABLE applications ADD COLUMN {col} {c_type}")

    conn.commit()

def seed_initial_data(conn):
    cursor = conn.cursor()

    # 1. Default Recruiter / Admin User
    cursor.execute("""
    INSERT INTO users (name, email, role, avatar_url)
    VALUES (?, ?, ?, ?)
    """, (
        "Praveen Valipireddy",
        "praveen@adroit-ai.com",
        "Lead Technical Recruiter & Admin",
        "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80"
    ))

    # 2. Seed Initial US Bench Consultants
    praveen_resume_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "resumes", "Praveen_Valipireddy_Resume.docx")
    praveen_token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tokens", "token_1.json")

    candidates = [
        (
            "Valipireddy Praveen",
            "praveen.valipireddy1998@gmail.com",
            "+1 (555) 382-9912",
            "Principal AI Automation & Cloud Architect",
            "Python, AI Automations, n8n, Make.com, LangChain, OpenAI APIs, Spring Boot, AWS, Docker",
            8,
            "$90/hr (C2C)",
            "US Citizen / C2C Eligible",
            "Available",
            "Dallas, TX (Hybrid/Remote)",
            "Praveen_Valipireddy_Resume.docx" if os.path.exists(praveen_resume_path) else None,
            praveen_resume_path if os.path.exists(praveen_resume_path) else None,
            "Specialist in enterprise AI agent automations, cloud architectures, n8n/Make workflows, and full-stack integration.",
            "praveen.valipireddy1998@gmail.com",
            praveen_token_path if os.path.exists(praveen_token_path) else None
        )
    ]

    cursor.executemany("""
    INSERT INTO candidates (
        name, email, phone, title, primary_skills, experience_years, 
        target_rate, visa_status, status, location, 
        resume_filename, resume_path, resume_summary, gmail_account, gmail_token_path
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, candidates)

    # 3. Seed Fresh US 24-Hour IT Jobs
    jobs = [
        (
            "Principal AI & Cloud Automation Engineer",
            "Apex Fintech Global",
            "Dallas, TX (Hybrid/Remote)",
            "Contract (C2C)",
            "$95 - $105/hr",
            "LinkedIn (Live 24h)",
            "https://www.linkedin.com/jobs/view/apex-fintech-ai-automation",
            "recruiter@apexfintech.com",
            "+1 (469) 882-9011",
            "Sarah Mitchell",
            "Seeking an AI Automation & Cloud Architect for 12+ month C2C engagement. Lead LLM agent automations and enterprise API workflows on AWS. Send resumes to recruiter@apexfintech.com.",
            "Python, AI Automations, LangChain, AWS, Docker",
            98,
            "Open",
            1
        ),
        (
            "Senior GenAI / LLM Systems Developer",
            "Cognitive Pulse AI",
            "San Francisco, CA (Remote)",
            "Contract (C2C)",
            "$90 - $110/hr",
            "Dice (Live 24h)",
            "https://www.dice.com/jobs/genai-eng-cognitive",
            "hiring@cognitivepulse.ai",
            "+1 (415) 773-8821",
            "David Vance",
            "Immediate C2C opening for GenAI Engineer. Experience with RAG pipelines, Vector databases, and LLM APIs. Contact hiring@cognitivepulse.ai with rate and updated resume.",
            "Python, LangChain, Vector DBs, PyTorch, OpenAI APIs",
            96,
            "Open",
            1
        ),
        (
            "Lead Kubernetes & DevOps Architect",
            "Vanguard Tech Solutions",
            "Austin, TX (Remote)",
            "Contract (6-12 Mos)",
            "$85 - $95/hr",
            "Indeed (Live 24h)",
            "https://www.indeed.com/viewjob?jk=vanguard-devops",
            "staffing@vanguardtech.io",
            "+1 (512) 662-3341",
            "Jennifer Hayes",
            "Urgent requirement for Senior DevOps/SRE Engineer. Multi-cluster Kubernetes, Terraform, ArgoCD, and AWS/Azure cloud security.",
            "Kubernetes, Terraform, ArgoCD, AWS, Azure, CI/CD",
            94,
            "Open",
            1
        ),
        (
            "Senior Full Stack React / TypeScript Specialist",
            "Northstar Health Systems",
            "New York, NY (Hybrid)",
            "Contract (C2C)",
            "$80 - $90/hr",
            "ZipRecruiter (Live 24h)",
            "https://www.ziprecruiter.com/jobs/northstar-fullstack",
            "techjobs@northstarhealth.org",
            "+1 (212) 993-4412",
            "Michael Chang",
            "Contract role for modernizing patient portal using React 19, TypeScript, Node.js microservices and PostgreSQL.",
            "TypeScript, React, Next.js, Node.js, PostgreSQL",
            92,
            "Open",
            1
        ),
        (
            "Java Spring Boot Microservices Architect",
            "Optima Financial Cloud",
            "Atlanta, GA (Remote)",
            "Contract (C2C)",
            "$90 - $100/hr",
            "CareerBuilder (Live 24h)",
            "https://www.careerbuilder.com/job/optima-java-spring",
            "resumes@optimafinancial.com",
            "+1 (404) 552-1928",
            "Amanda Cole",
            "High-priority C2C position for Senior Java 21 / Spring Boot Architect with Kafka and AWS experience.",
            "Java, Spring Boot, Microservices, Kafka, AWS",
            95,
            "Open",
            1
        )
    ]

    cursor.executemany("""
    INSERT INTO jobs (
        title, company, location, job_type, salary, 
        source, url, recruiter_email, recruiter_phone, recruiter_name, 
        description, matched_skills, match_score, status, is_24h
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, jobs)

    conn.commit()

# --- Database Helpers ---

def get_or_create_user(name, email, role="Recruiter", avatar_url=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("""
        INSERT INTO users (name, email, role, avatar_url)
        VALUES (?, ?, ?, ?)
        """, (name, email, role, avatar_url or "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80"))
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,))
        user = cursor.fetchone()
    conn.close()
    return dict(user)

def get_dashboard_stats():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM jobs")
    total_jobs = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM candidates")
    total_candidates = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM applications WHERE stage IN ('Drafted', 'Applied', 'Screening', 'Interviewing')")
    active_pipeline = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM applications WHERE stage = 'Drafted'")
    drafted_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM applications WHERE stage = 'Interviewing'")
    interviews = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM applications WHERE stage IN ('Offer', 'Hired')")
    offers = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM jobs WHERE recruiter_email IS NOT NULL AND recruiter_email != ''")
    jobs_with_email = cursor.fetchone()[0]

    # Pipeline stage breakdown
    cursor.execute("""
    SELECT stage, COUNT(*) as count 
    FROM applications 
    GROUP BY stage
    """)
    stage_rows = cursor.fetchall()
    stage_breakdown = {row["stage"]: row["count"] for row in stage_rows}

    # Job sources breakdown
    cursor.execute("""
    SELECT source, COUNT(*) as count
    FROM jobs
    GROUP BY source
    """)
    source_rows = cursor.fetchall()
    source_breakdown = {row["source"]: row["count"] for row in source_rows}

    conn.close()

    return {
        "total_jobs": total_jobs,
        "total_candidates": total_candidates,
        "active_pipeline": active_pipeline,
        "drafted_count": drafted_count,
        "interviews": interviews,
        "offers": offers,
        "jobs_with_email": jobs_with_email,
        "stage_breakdown": stage_breakdown,
        "source_breakdown": source_breakdown,
    }

def get_candidates():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM candidates ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        res_sum = d.get("resume_summary", "") or ""
        li_match = re.search(r'https?://[^\s,"]*linkedin\.com/in/([^\s,"?#]+)', res_sum)
        if li_match:
            slug = li_match.group(1).strip('/')
            d["linkedin_url"] = f"https://www.linkedin.com/in/{slug}/"
            d["profile_url"] = d["linkedin_url"]
        else:
            clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', d.get('name', 'consultant').lower()).strip('-')
            d["linkedin_url"] = f"https://www.linkedin.com/in/{clean_slug}/"
            d["profile_url"] = d["linkedin_url"]
        results.append(d)
    return results

def get_candidate_by_id(candidate_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def create_candidate(name, email, phone="", title="Technical Consultant", primary_skills="", experience_years=5, target_rate="$90/hr (C2C)", visa_status="C2C Eligible", status="Available", location="United States (Remote)", resume_filename=None, resume_path=None, resume_text=None, resume_summary="", gmail_account=None, gmail_token_path=None):
    if isinstance(primary_skills, (list, tuple, set)):
        primary_skills = ", ".join(str(s) for s in primary_skills)
    else:
        primary_skills = str(primary_skills or "")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO candidates (
        name, email, phone, title, primary_skills, experience_years, 
        target_rate, visa_status, status, location, 
        resume_filename, resume_path, resume_text, resume_summary, 
        gmail_account, gmail_token_path
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name, email, phone, title, primary_skills, experience_years, 
        target_rate, visa_status, status, location, 
        resume_filename, resume_path, resume_text, resume_summary, 
        gmail_account or email, gmail_token_path
    ))
    conn.commit()
    cand_id = cursor.lastrowid
    conn.close()
    return cand_id

def update_candidate(candidate_id, **fields):
    if not fields:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    set_clauses = []
    params = []
    for k, v in fields.items():
        set_clauses.append(f"{k} = ?")
        params.append(v)
    params.append(candidate_id)
    query = f"UPDATE candidates SET {', '.join(set_clauses)} WHERE id = ?"
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def delete_candidate(candidate_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))
    conn.commit()
    conn.close()

def get_jobs(query=None, location=None, source=None, job_type=None, contract_only=False, is_24h_only=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    raw_jobs = cursor.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
    jobs_list = [dict(j) for j in raw_jobs]
    conn.close()

    filtered = []
    q_lower = (query or "").strip().lower()
    q_terms = [t for t in q_lower.split() if len(t) > 1]
    source_clean = (source or "").lower().replace(".com", "").replace(" usa", "").strip()

    for j in jobs_list:
        title = (j.get("title") or "").lower()
        company = (j.get("company") or "").lower()
        desc = (j.get("description") or "").lower()
        loc = (j.get("location") or "").lower()
        src = (j.get("source") or "").lower()
        jtype = (j.get("job_type") or "").lower()
        skills = (j.get("matched_skills") or "").lower()

        # 1. Source filter (matches 'Dice' for 'Dice.com', 'LinkedIn' for 'LinkedIn (Live 24h)', etc.)
        if source and source != "All" and source_clean:
            if source_clean not in src:
                continue

        # 2. Location filter
        if location and location != "All" and location.lower() != "united states":
            if location.lower() not in loc:
                continue

        # 3. Contract filter
        if contract_only:
            if not any(k in jtype or k in desc for k in ["contract", "c2c", "corp", "1099", "temp"]):
                continue

        # 4. Relevance Scoring & Precise Title Matching
        if q_lower:
            relevance = 0

            # Tier 1: Exact phrase match in title (e.g. "data analyst" in "Senior Data Analyst") -> +100
            if q_lower in title:
                relevance += 100
            
            # Tier 2: All search words in title (e.g. "Clinical Data Business Analyst") -> +60
            elif len(q_terms) > 1 and all(term in title for term in q_terms):
                relevance += 60
            
            # Tier 3: Core role keyword in title (excluding generic terms) -> +25
            elif any(term in title for term in q_terms if term not in ["engineer", "developer", "specialist", "consultant", "lead", "senior", "junior"]):
                relevance += 25

            # Tier 4: Matched skills contains query -> +15
            if q_lower in skills or any(term in skills for term in q_terms if len(term) > 2):
                relevance += 15

            # If title has zero relation to query (e.g. "Cyber Security Engineer" when searching "data analyst"), exclude it
            if relevance == 0:
                continue

            j["_relevance"] = relevance
            j["match_score"] = min(99, max(75, 70 + (relevance // 4)))
            filtered.append(j)
        else:
            filtered.append(j)

    if q_lower:
        filtered.sort(key=lambda x: (x.get("_relevance", 0), x.get("is_24h", 0), x.get("id", 0)), reverse=True)
        for f in filtered:
            f.pop("_relevance", None)

    return filtered

def get_job_by_id(job_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_job_recruiter_info(job_id, email=None, phone=None, name=None, salary=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    updates = []
    params = []
    if email is not None:
        updates.append("recruiter_email = ?")
        params.append(email.strip())
    if phone is not None:
        updates.append("recruiter_phone = ?")
        params.append(phone.strip())
    if name is not None:
        updates.append("recruiter_name = ?")
        params.append(name.strip())
    if salary is not None:
        updates.append("salary = ?")
        params.append(salary.strip())

    if updates:
        params.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(sql, params)
        conn.commit()
    conn.close()

def save_or_update_scraped_job(job_data):
    conn = get_db_connection()
    cursor = conn.cursor()

    title = job_data.get("title", "Software Engineer")
    company = job_data.get("company", "Direct Client")
    location = job_data.get("location", "United States (Remote)")
    job_type = job_data.get("job_type", "Contract (C2C)")
    salary = job_data.get("salary", "Rate on Discussion (C2C)")
    source = job_data.get("source", "Dice")
    url = job_data.get("url", "")
    recruiter_email = job_data.get("recruiter_email", "")
    recruiter_phone = job_data.get("recruiter_phone", "")
    recruiter_name = job_data.get("recruiter_name", "")
    description = job_data.get("description", "")
    matched_skills = job_data.get("matched_skills", "")
    match_score = job_data.get("match_score", 90)

    # Check if this job exists by URL or title+company
    existing = None
    if url:
        cursor.execute("SELECT id, recruiter_email FROM jobs WHERE url = ?", (url,))
        existing = cursor.fetchone()
    if not existing:
        cursor.execute("SELECT id, recruiter_email FROM jobs WHERE title = ? AND company = ?", (title, company))
        existing = cursor.fetchone()

    if existing:
        job_id = existing[0]
        current_email = existing[1]
        # Only update if new email found and old was blank
        if recruiter_email and not current_email:
            cursor.execute("UPDATE jobs SET recruiter_email = ?, salary = COALESCE(?, salary) WHERE id = ?", (recruiter_email, salary, job_id))
            conn.commit()
        conn.close()
        return job_id
    else:
        cursor.execute("""
        INSERT INTO jobs (
            title, company, location, job_type, salary, 
            source, url, recruiter_email, recruiter_phone, recruiter_name, 
            description, matched_skills, match_score, status, is_24h
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Open', 1)
        """, (
            title, company, location, job_type, salary, 
            source, url, recruiter_email, recruiter_phone, recruiter_name, 
            description, matched_skills, match_score
        ))
        conn.commit()
        job_id = cursor.lastrowid
        conn.close()
        return job_id

def create_application(candidate_id, job_id, user_id=1, stage="Saved", match_score=90, notes=""):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM applications WHERE job_id = ? AND candidate_id = ?", (job_id, candidate_id))
    existing = cursor.fetchone()
    if existing:
        app_id = existing[0]
        cursor.execute("UPDATE applications SET stage = ?, notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (stage, notes, app_id))
    else:
        cursor.execute("""
        INSERT INTO applications (job_id, candidate_id, user_id, stage, notes)
        VALUES (?, ?, ?, ?, ?)
        """, (job_id, candidate_id, user_id, stage, notes))
        app_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return app_id

def mark_job_drafted(job_id, candidate_id, draft_id, user_id=1, notes=""):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM applications WHERE job_id = ? AND candidate_id = ?", (job_id, candidate_id))
    existing = cursor.fetchone()
    if existing:
        app_id = existing[0]
        cursor.execute("""
        UPDATE applications 
        SET stage = 'Drafted', draft_id = ?, drafted_at = CURRENT_TIMESTAMP, notes = ?, updated_at = CURRENT_TIMESTAMP 
        WHERE id = ?
        """, (draft_id, notes, app_id))
    else:
        cursor.execute("""
        INSERT INTO applications (job_id, candidate_id, user_id, stage, draft_id, drafted_at, notes)
        VALUES (?, ?, ?, 'Drafted', ?, CURRENT_TIMESTAMP, ?)
        """, (job_id, candidate_id, user_id, draft_id, notes))
        app_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return app_id

def get_pipeline():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT 
        a.id as app_id,
        a.stage,
        a.notes,
        a.draft_id,
        a.drafted_at,
        a.applied_date,
        a.updated_at,
        j.id as job_id,
        j.title as job_title,
        j.company as job_company,
        j.location as job_location,
        j.salary as job_salary,
        j.source as job_source,
        j.job_type as job_type,
        j.recruiter_email,
        j.url as job_url,
        j.match_score,
        c.id as candidate_id,
        c.name as candidate_name,
        c.title as candidate_title,
        c.email as candidate_email,
        c.phone as candidate_phone,
        c.target_rate,
        c.visa_status
    FROM applications a
    JOIN jobs j ON a.job_id = j.id
    JOIN candidates c ON a.candidate_id = c.id
    ORDER BY a.updated_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_pipeline_by_stages():
    apps = get_pipeline()
    stages = {
        "Saved": [],
        "Drafted": [],
        "Applied": [],
        "Screening": [],
        "Interviewing": [],
        "Offer": [],
        "Hired": [],
        "Rejected": []
    }
    for app in apps:
        stage = app.get("stage", "Saved")
        if stage not in stages:
            stages[stage] = []
        stages[stage].append(app)
    return stages

def update_application_stage(app_id, new_stage, notes=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if notes:
        cursor.execute("""
        UPDATE applications 
        SET stage = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (new_stage, notes, app_id))
    else:
        cursor.execute("""
        UPDATE applications 
        SET stage = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (new_stage, app_id))
    conn.commit()
    conn.close()

def log_activity(user_id, user_name, action, target_type, target_id, details):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO activity_logs (user_id, user_name, action, target_type, target_id, details)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, user_name, action, target_type, target_id, details))
    conn.commit()
    conn.close()

def get_activity_logs(limit=25):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
