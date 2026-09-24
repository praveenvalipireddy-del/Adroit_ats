import os
import re
import sqlite3
import urllib.parse
import json
import logging
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import config

logger = logging.getLogger("models")


class DbRow(dict):
    """Row wrapper compatible with both SQLite Row and psycopg2 tuple/dict access."""
    def __init__(self, col_names, row_tuple):
        super().__init__(zip(col_names, row_tuple))
        self._tuple = row_tuple
        self._col_names = col_names

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._tuple[key]
        return super().__getitem__(key)


class PgCursorWrapper:
    def __init__(self, cursor, conn):
        self._cursor = cursor
        self._conn = conn
        self.lastrowid = None

    @property
    def description(self):
        return self._cursor.description

    def _adapt_query(self, sql):
        s = sql.strip()
        is_insert = bool(re.match(r'^\s*INSERT\s+INTO\s+', s, re.IGNORECASE))
        has_returning = bool(re.search(r'\bRETURNING\b', s, re.IGNORECASE))

        # Convert SQLite AUTOINCREMENT to Postgres SERIAL
        s = re.sub(r'id\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', 'id SERIAL PRIMARY KEY', s, flags=re.IGNORECASE)
        # Convert parameter placeholders ? to %s
        s = s.replace('?', '%s')

        append_returning = is_insert and not has_returning
        if append_returning:
            if s.endswith(';'):
                s = s[:-1].strip()
            s += ' RETURNING id'

        return s, append_returning

    def execute(self, sql, params=None):
        adapted_sql, append_returning = self._adapt_query(sql)
        try:
            if params is not None:
                self._cursor.execute(adapted_sql, params)
            else:
                self._cursor.execute(adapted_sql)
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise

        if append_returning:
            try:
                row = self._cursor.fetchone()
                if row:
                    self.lastrowid = row[0]
            except Exception:
                self.lastrowid = None
        else:
            self.lastrowid = None

        return self

    def executemany(self, sql, seq_of_params):
        adapted_sql, _ = self._adapt_query(sql)
        try:
            self._cursor.executemany(adapted_sql, seq_of_params)
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        col_names = [desc[0] for desc in self._cursor.description]
        return DbRow(col_names, row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return []
        col_names = [desc[0] for desc in self._cursor.description]
        return [DbRow(col_names, r) for r in rows]

    def close(self):
        self._cursor.close()


class PgConnectionWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return PgCursorWrapper(self._conn.cursor(), self._conn)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def is_postgres(conn):
    return isinstance(conn, PgConnectionWrapper)


def get_db_connection():
    # If DATABASE_URL is configured (Render PostgreSQL), connect via psycopg2
    if getattr(config, "DATABASE_URL", None):
        try:
            import psycopg2
            raw_conn = psycopg2.connect(config.DATABASE_URL)
            return PgConnectionWrapper(raw_conn)
        except Exception as e:
            print(f"[WARN] Failed to connect to PostgreSQL: {e}. Falling back to SQLite.")

    # Local development fallback: SQLite
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
        password_hash TEXT,
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
        assigned_user_id INTEGER DEFAULT 1,
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

    # Ensure all primary US Bench Consultants always exist (Localhost & Render)
    ensure_default_consultants(conn)

    # Ensure fresh 24h US IT jobs exist (Localhost & Render)
    ensure_default_jobs(conn)

    conn.close()

def migrate_db(conn):
    """Automatically adds missing columns to existing SQLite or Postgres tables."""
    cursor = conn.cursor()
    use_pg = is_postgres(conn)

    def get_existing_cols(table_name):
        if use_pg:
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table_name,))
            return [row[0].lower() for row in cursor.fetchall()]
        else:
            cursor.execute(f"PRAGMA table_info({table_name})")
            return [row[1].lower() for row in cursor.fetchall()]

    # Users table columns
    u_cols = get_existing_cols("users")
    if "password_hash" not in u_cols:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        except Exception:
            pass

    # Candidate table columns
    c_cols = get_existing_cols("candidates")
    candidate_new_cols = {
        "visa_status": "TEXT DEFAULT 'C2C Eligible'",
        "resume_filename": "TEXT",
        "resume_path": "TEXT",
        "resume_text": "TEXT",
        "gmail_account": "TEXT",
        "gmail_token_path": "TEXT",
        "gmail_app_password": "TEXT",
        "assigned_user_id": "INTEGER DEFAULT 1"
    }
    for col, c_type in candidate_new_cols.items():
        if col.lower() not in c_cols:
            try:
                cursor.execute(f"ALTER TABLE candidates ADD COLUMN {col} {c_type}")
            except Exception:
                pass

    # Jobs table columns
    j_cols = get_existing_cols("jobs")
    job_new_cols = {
        "recruiter_email": "TEXT",
        "recruiter_phone": "TEXT",
        "recruiter_name": "TEXT",
        "matched_skills": "TEXT",
        "is_24h": "INTEGER DEFAULT 1",
        "scraped_at": "TEXT"
    }
    for col, c_type in job_new_cols.items():
        if col.lower() not in j_cols:
            try:
                cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col} {c_type}")
            except Exception:
                pass

    # Applications table columns
    a_cols = get_existing_cols("applications")
    app_new_cols = {
        "draft_id": "TEXT",
        "drafted_at": "TEXT"
    }
    for col, c_type in app_new_cols.items():
        if col.lower() not in a_cols:
            try:
                cursor.execute(f"ALTER TABLE applications ADD COLUMN {col} {c_type}")
            except Exception:
                pass

    # Ensure default Admin account has password_hash and 'Admin' role
    try:
        default_admin_hash = generate_password_hash("Admin@2026")
        cursor.execute("SELECT id, password_hash, role FROM users WHERE email = ?", ("praveen@adroit-ai.com",))
        admin_user = cursor.fetchone()
        if admin_user:
            admin_id = admin_user["id"]
            has_pw = bool(admin_user["password_hash"]) if "password_hash" in [col[0] for col in cursor.description] and admin_user["password_hash"] else False
            if not has_pw:
                cursor.execute("UPDATE users SET password_hash = ?, role = 'Admin' WHERE id = ?", (default_admin_hash, admin_id))
            else:
                cursor.execute("UPDATE users SET role = 'Admin' WHERE id = ?", (admin_id,))
        else:
            cursor.execute("""
            INSERT INTO users (name, email, password_hash, role, avatar_url)
            VALUES (?, ?, ?, ?, ?)
            """, (
                "Praveen Valipireddy",
                "praveen@adroit-ai.com",
                default_admin_hash,
                "Admin",
                "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80"
            ))

        cursor.execute("SELECT id, password_hash FROM users WHERE email = ?", ("praveen@aventra-ai.com",))
        aventra_user = cursor.fetchone()
        if aventra_user:
            av_has_pw = bool(aventra_user["password_hash"]) if "password_hash" in [col[0] for col in cursor.description] and aventra_user["password_hash"] else False
            if not av_has_pw:
                cursor.execute("UPDATE users SET password_hash = ?, role = 'Admin' WHERE id = ?", (default_admin_hash, aventra_user["id"]))

        # Admin password: set ADMIN_PASSWORD on the server to control it. Without it the
        # default published in this public repo stays in effect, so warn loudly.
        if config.ADMIN_PASSWORD:
            new_admin_hash = generate_password_hash(config.ADMIN_PASSWORD)
            for admin_email in ("praveen@adroit-ai.com", "praveen@aventra-ai.com"):
                cursor.execute("UPDATE users SET password_hash = ? WHERE email = ?", (new_admin_hash, admin_email))
        else:
            cursor.execute("SELECT password_hash FROM users WHERE email = ?", ("praveen@adroit-ai.com",))
            pw_row = cursor.fetchone()
            if pw_row and pw_row["password_hash"] and check_password_hash(pw_row["password_hash"], "Admin@2026"):
                logger.warning("SECURITY: the admin account still uses the default password published in the source code. "
                               "Set ADMIN_PASSWORD in the server environment to change it.")

        # Migrate any orphaned candidates without assigned_user_id to Admin (id=1)
        cursor.execute("UPDATE candidates SET assigned_user_id = 1 WHERE assigned_user_id IS NULL OR assigned_user_id = 0")
    except Exception as ex:
        print("[WARN] migrate admin/candidates error:", ex)

    conn.commit()


def ensure_default_consultants(conn):
    """Guarantees that all primary US Bench Consultants exist in the database across all environments."""
    cursor = conn.cursor()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    resumes_dir = os.path.join(base_dir, "data", "resumes")
    tokens_dir = os.path.join(base_dir, "data", "tokens")

    consultants = [
        {
            "name": "Valipireddy Praveen",
            "email": "praveen.valipireddy1998@gmail.com",
            "phone": "+91 7075828135",
            "title": "Principal AI Automation & Cloud Architect",
            "primary_skills": "Python, AI Automations, n8n, Make.com, LangChain, Spring Boot, AWS, Docker",
            "experience_years": 12,
            "target_rate": "$90/hr (C2C)",
            "visa_status": "US Citizen / C2C Eligible",
            "status": "Available",
            "location": "Dallas, TX (Hybrid/Remote)",
            "resume_filename": "Praveen_Valipireddy_Resume.docx",
            "resume_summary": "12+ years architecting enterprise scale fintech & healthcare cloud services with Spring Boot and AWS. LinkedIn: https://www.linkedin.com/in/valipireddy-praveen/",
            "gmail_account": "praveen.ai7075@gmail.com",
            "gmail_token_path": os.path.join(tokens_dir, "token_1.json"),
            "gmail_app_password": None
        },
        {
            "name": "Sai Teja",
            "email": "saitejat111@gmail.com",
            "phone": "+1 (469) 555-0192",
            "title": "DevOps Engineer",
            "primary_skills": "DevOps, AWS, Terraform, CI/CD, Kubernetes, Docker, Linux, Bash",
            "experience_years": 8,
            "target_rate": "$70/hr (C2C)",
            "visa_status": "H1B / C2C Eligible",
            "status": "Available",
            "location": "Dallas, TX (Open to Relocate)",
            "resume_filename": "Sai_Teja_Resume.pdf",
            "resume_summary": "DevOps & Cloud Engineer | AWS, Terraform, CI/CD, Kubernetes. LinkedIn: https://www.linkedin.com/in/saiteja-devops/",
            "gmail_account": "saitejat111@gmail.com",
            "gmail_token_path": None,
            "gmail_app_password": None
        },
        {
            "name": "Karun",
            "email": "karun.aiengineer@gmail.com",
            "phone": "+1 (629) 203-4747",
            "title": "Data Analyst",
            "primary_skills": "Data Analyst, SQL, Python, PowerBI, Tableau, Excel, Azure AI, Business Intelligence",
            "experience_years": 6,
            "target_rate": "$60/hr (C2C)",
            "visa_status": "OPT / STEM OPT (C2C)",
            "status": "Available",
            "location": "Dallas, TX (Hybrid/Remote)",
            "resume_filename": "Resume_KarunKumar_CRG_JuniorBA.docx",
            "resume_summary": "Data Analyst & BI Specialist | SQL, Python, PowerBI, Tableau. LinkedIn: https://www.linkedin.com/in/karun-data/",
            "gmail_account": "karun.aiengineer@gmail.com",
            "gmail_token_path": None,
            "gmail_app_password": "rpxipiywgcctdlqz"
        },
        {
            "name": "Thirupathi",
            "email": "thirupathi.aiengineer@gmail.com",
            "phone": "+1 (512) 674-8891",
            "title": "AI Engineer & Snowflake Specialist",
            "primary_skills": "AI Engineering, Snowflake, Python, LangChain, Azure AI, Vector DBs, SQL",
            "experience_years": 14,
            "target_rate": "$90/hr (C2C)",
            "visa_status": "H1B (Transfer/C2C)",
            "status": "Available",
            "location": "Austin, TX (Hybrid/Remote)",
            "resume_filename": "Thirupathi_AI_Engineer.docx",
            "resume_summary": "14+ years in data platform engineering, Snowflake enterprise data warehouse, and generative AI agents. LinkedIn: https://www.linkedin.com/in/thirupathi-ai/",
            "gmail_account": "thirupathi.aiengineer@gmail.com",
            "gmail_token_path": None,
            "gmail_app_password": None
        },
        {
            "name": "Vikas Reddy",
            "email": "vikas.reddy@talent.internal",
            "phone": "+1 (214) 779-1022",
            "title": "Cloud DevOps Engineer | AWS, Kubernetes, Terraform",
            "primary_skills": "AWS, Kubernetes, Terraform, Docker, CI/CD, Helm, Python, Linux",
            "experience_years": 7,
            "target_rate": "$85/hr (C2C)",
            "visa_status": "H1B / C2C Eligible",
            "status": "Available",
            "location": "Dallas, TX (Hybrid/Remote)",
            "resume_filename": "Vikas_Reddy_DevOps.docx",
            "resume_summary": "7+ years Cloud DevOps architecting multi-region EKS clusters, automated Terraform infrastructure, and GitOps pipelines.",
            "gmail_account": "vikas.reddy@talent.internal",
            "gmail_token_path": None,
            "gmail_app_password": None
        }
    ]

    for c in consultants:
        cursor.execute("SELECT id, resume_path, gmail_app_password FROM candidates WHERE email = ? OR name = ?", (c["email"], c["name"]))
        row = cursor.fetchone()
        
        full_res_path = os.path.join(resumes_dir, c["resume_filename"]) if c["resume_filename"] else None
        res_exists = full_res_path and os.path.exists(full_res_path)
        actual_res_path = full_res_path if res_exists else None

        tok_path = c["gmail_token_path"]
        actual_tok_path = tok_path if tok_path and os.path.exists(tok_path) else None

        if not row:
            cursor.execute("""
            INSERT INTO candidates (
                name, email, phone, title, primary_skills, experience_years,
                target_rate, visa_status, status, location,
                resume_filename, resume_path, resume_summary, gmail_account,
                gmail_token_path, gmail_app_password
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                c["name"], c["email"], c["phone"], c["title"], c["primary_skills"],
                c["experience_years"], c["target_rate"], c["visa_status"], c["status"],
                c["location"], c["resume_filename"], actual_res_path, c["resume_summary"],
                c["gmail_account"], actual_tok_path, c["gmail_app_password"]
            ))
        else:
            cand_id = row[0]
            # Update resume path and password if available
            if actual_res_path:
                cursor.execute("UPDATE candidates SET resume_path = ?, resume_filename = ? WHERE id = ?", (actual_res_path, c["resume_filename"], cand_id))
            if c["gmail_app_password"] and (not row[2]):
                cursor.execute("UPDATE candidates SET gmail_app_password = ? WHERE id = ?", (c["gmail_app_password"], cand_id))

    conn.commit()

def ensure_default_jobs(conn):
    """Guarantees that full catalog of 24h US contract jobs is available in the database across environments."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM jobs")
    count = cursor.fetchone()[0]
    if count < 20:
        seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "seed_jobs.json")
        if os.path.exists(seed_path):
            try:
                with open(seed_path, "r", encoding="utf-8") as f:
                    jobs = json.load(f)
                for j in jobs:
                    cursor.execute("""
                    INSERT INTO jobs (
                        title, company, location, job_type, salary, source, url,
                        recruiter_email, recruiter_phone, recruiter_name, description,
                        matched_skills, match_score, status, is_24h
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        j.get("title"), j.get("company"), j.get("location"), j.get("job_type", "Contract (C2C)"),
                        j.get("salary"), j.get("source", "Dice"), j.get("url"),
                        j.get("recruiter_email"), j.get("recruiter_phone"), j.get("recruiter_name"),
                        j.get("description"), j.get("matched_skills"), j.get("match_score", 88),
                        j.get("status", "Open"), j.get("is_24h", 1)
                    ))
                conn.commit()
                logger.info(f"Seeded {len(jobs)} live US IT jobs into database.")
            except Exception as e:
                logger.error(f"Error seeding jobs: {e}")

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

def get_or_create_user(name, email, role="Recruiter", avatar_url=None, password="Password@123"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    if not user:
        p_hash = generate_password_hash(password)
        cursor.execute("""
        INSERT INTO users (name, email, password_hash, role, avatar_url)
        VALUES (?, ?, ?, ?, ?)
        """, (name, email, p_hash, role, avatar_url or "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80"))
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,))
        user = cursor.fetchone()
    conn.close()
    return dict(user)

def create_user(name, email, password, role="Recruiter", avatar_url=None):
    """Creates a new recruiter account with hashed password."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        raise ValueError(f"User with email '{email}' already exists.")

    p_hash = generate_password_hash(password)
    cursor.execute("""
    INSERT INTO users (name, email, password_hash, role, avatar_url)
    VALUES (?, ?, ?, ?, ?)
    """, (
        name,
        email,
        p_hash,
        role,
        avatar_url or "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=150&auto=format&fit=crop&q=80"
    ))
    conn.commit()
    user_id = cursor.lastrowid
    cursor.execute("SELECT id, name, email, role, avatar_url, created_at FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return dict(user)

def authenticate_user(email, password):
    """Verifies user credentials and returns safe user dict if authenticated."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, password_hash, role, avatar_url, created_at FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    user = dict(row)
    p_hash = user.get("password_hash")
    if not p_hash:
        if password in ["Admin@2026", "Password@123", "password"]:
            update_user_password(user["id"], password)
            del user["password_hash"]
            return user
        return None
    if check_password_hash(p_hash, password):
        del user["password_hash"]
        return user
    return None

def get_users():
    """Returns list of recruiters with active candidate count."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT 
        u.id, 
        u.name, 
        u.email, 
        u.role, 
        u.avatar_url, 
        u.created_at,
        COUNT(c.id) as consultant_count
    FROM users u
    LEFT JOIN candidates c ON c.assigned_user_id = u.id
    GROUP BY u.id, u.name, u.email, u.role, u.avatar_url, u.created_at
    ORDER BY u.id ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_user_by_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, role, avatar_url, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_user_password(user_id, new_password):
    conn = get_db_connection()
    cursor = conn.cursor()
    p_hash = generate_password_hash(new_password)
    cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (p_hash, user_id))
    conn.commit()
    conn.close()

def delete_user(user_id):
    """Deletes a recruiter account and reassigns their candidates to admin (id=1)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE candidates SET assigned_user_id = 1 WHERE assigned_user_id = ?", (user_id,))
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

def assign_candidate_to_recruiter(candidate_id, new_user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE candidates SET assigned_user_id = ? WHERE id = ?", (new_user_id, candidate_id))
    conn.commit()
    conn.close()

def get_dashboard_stats(user_id=None, is_admin=False):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM jobs")
    total_jobs = cursor.fetchone()[0]

    if not is_admin and user_id:
        cursor.execute("SELECT COUNT(*) FROM candidates WHERE assigned_user_id = ?", (user_id,))
        total_candidates = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(*) FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?)
          AND a.stage IN ('Drafted', 'Applied', 'Screening', 'Interviewing')
        """, (user_id, user_id))
        active_pipeline = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(*) FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?) AND a.stage = 'Drafted'
        """, (user_id, user_id))
        drafted_count = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(*) FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?) AND a.stage = 'Interviewing'
        """, (user_id, user_id))
        interviews = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(*) FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?) AND a.stage IN ('Offer', 'Hired')
        """, (user_id, user_id))
        offers = cursor.fetchone()[0]

        cursor.execute("""
        SELECT a.stage, COUNT(*) as count 
        FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?)
        GROUP BY a.stage
        """, (user_id, user_id))
        stage_rows = cursor.fetchall()
        stage_breakdown = {row["stage"]: row["count"] for row in stage_rows}
    else:
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

        cursor.execute("""
        SELECT stage, COUNT(*) as count 
        FROM applications 
        GROUP BY stage
        """)
        stage_rows = cursor.fetchall()
        stage_breakdown = {row["stage"]: row["count"] for row in stage_rows}

    cursor.execute("SELECT COUNT(*) FROM jobs WHERE recruiter_email IS NOT NULL AND recruiter_email != ''")
    jobs_with_email = cursor.fetchone()[0]

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

def get_candidates(user_id=None, is_admin=False):
    """
    Returns candidate list. 
    If not is_admin and user_id provided: returns only candidates assigned to that user.
    If is_admin and user_id provided: returns candidates filtered by that specific user.
    If is_admin and user_id is None: returns all candidates across the agency.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
    SELECT c.*, u.name as recruiter_name, u.email as recruiter_email
    FROM candidates c
    LEFT JOIN users u ON c.assigned_user_id = u.id
    """
    if not is_admin and user_id:
        query += " WHERE (c.assigned_user_id = ? OR c.assigned_user_id = 1 OR c.assigned_user_id IS NULL)"
        cursor.execute(query + " ORDER BY c.id ASC", (user_id,))
    elif is_admin and user_id:
        query += " WHERE (c.assigned_user_id = ? OR c.assigned_user_id = 1 OR c.assigned_user_id IS NULL)"
        cursor.execute(query + " ORDER BY c.id ASC", (user_id,))
    else:
        cursor.execute(query + " ORDER BY c.id ASC")

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

def get_candidate_by_id(candidate_id, user_id=None, is_admin=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
    SELECT c.*, u.name as recruiter_name, u.email as recruiter_email
    FROM candidates c
    LEFT JOIN users u ON c.assigned_user_id = u.id
    WHERE c.id = ?
    """
    if not is_admin and user_id:
        query += " AND c.assigned_user_id = ?"
        cursor.execute(query, (candidate_id, user_id))
    else:
        cursor.execute(query, (candidate_id,))

    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def create_candidate(name, email, phone="", title="Technical Consultant", primary_skills="", experience_years=5, target_rate="$90/hr (C2C)", visa_status="C2C Eligible", status="Available", location="United States (Remote)", resume_filename=None, resume_path=None, resume_text=None, resume_summary="", gmail_account=None, gmail_token_path=None, assigned_user_id=1):
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
        gmail_account, gmail_token_path, assigned_user_id
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name, email, phone, title, primary_skills, experience_years, 
        target_rate, visa_status, status, location, 
        resume_filename, resume_path, resume_text, resume_summary, 
        gmail_account or email, gmail_token_path, assigned_user_id or 1
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

def delete_candidate(candidate_id, user_id=None, is_admin=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    if not is_admin and user_id:
        cursor.execute("DELETE FROM candidates WHERE id = ? AND assigned_user_id = ?", (candidate_id, user_id))
    else:
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

def get_pipeline(user_id=None, is_admin=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
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
        c.visa_status,
        c.assigned_user_id
    FROM applications a
    JOIN jobs j ON a.job_id = j.id
    JOIN candidates c ON a.candidate_id = c.id
    """
    if not is_admin and user_id:
        query += " WHERE (a.user_id = ? OR c.assigned_user_id = ?)"
        cursor.execute(query + " ORDER BY a.updated_at DESC", (user_id, user_id))
    elif is_admin and user_id:
        query += " WHERE (a.user_id = ? OR c.assigned_user_id = ?)"
        cursor.execute(query + " ORDER BY a.updated_at DESC", (user_id, user_id))
    else:
        cursor.execute(query + " ORDER BY a.updated_at DESC")

    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_pipeline_by_stages(user_id=None, is_admin=False):
    apps = get_pipeline(user_id=user_id, is_admin=is_admin)
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
