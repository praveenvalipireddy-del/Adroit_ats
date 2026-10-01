# 🚀 Adroit AI ATS & Recruitment Portal - Developer Handover Guide

Welcome! This repository contains the complete **Adroit AI ATS** recruitment management portal, multi-portal live job scraper, and AI resume optimization engine.

---

## 🛠️ Tech Stack & Requirements
- **Python**: 3.10+ (Tested on Python 3.12)
- **Framework**: Flask, Gunicorn
- **Database**: SQLite (default `ats.db`, schema automatically self-initializes on startup; can be pointed to PostgreSQL)
- **OS**: Any Linux / Windows / Docker server

---

## ⚡ Option A: 1-Command Deployment via Docker (Recommended)

If the server has Docker installed:
```bash
# 1. Build and run in background
docker compose up -d --build

# 2. View live logs
docker compose logs -f
```
The application will be live on `http://<server-ip>:5000`.

---

## 🐍 Option B: Native Linux / Ubuntu Deployment

```bash
# 1. Clone repository or extract files
git clone https://github.com/praveenvalipireddy-del/adroit-ats.git
cd adroit-ats

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install requirements
pip install -r requirements.txt

# 4. Start production Gunicorn server
gunicorn app:app --workers=3 --bind 0.0.0.0:5000 --timeout=120
```

---

## 🔒 Environment Variables (`.env`)

Create a `.env` file in the root directory:
```ini
FLASK_ENV=production
SECRET_KEY=your-strong-random-secret-key
PORT=5000

# Apify Token for Indeed 24h scraping
APIFY_API_TOKEN=your_apify_token_here

# (Optional) Azure Entra ID Single Sign-On
CLIENT_ID=
CLIENT_SECRET=
TENANT_ID=
REDIRECT_URI=http://your-server-domain/auth/callback
```

---

## 🌐 Nginx Reverse Proxy Configuration (Sample)

```nginx
server {
    listen 80;
    server_name ats.yourcompany.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 📂 Key Architecture Modules
1. `app.py`: Main Flask API & dashboard routing (with self-contained embedded asset fallback).
2. `resume_bot.py`: Master Resume Optimization engine, ATS keyword scoring, timeline safety checks, and Word (`.docx`) file builder.
3. `live_job_scraper.py`: Real-time multi-source scraper for **LinkedIn** (24h Contract filter `f_TPR=r86400&f_JT=C`), **Indeed**, and **Dice**.
4. `models.py`: Database access layer (Users, Candidates, Jobs, Pipeline stages, Audit activity logs).
5. `apify_service.py`: Indeed live actor connector.

---

## 🎓 Education Filters (Sourcing tab)

**What recruiters get:** two searches over the team's saved LinkedIn profiles, at the top of the Sourcing tab. They are free (no Apify credits) and shared by the whole team.
- **Filter A - Indian College → US Master's:** pick an Indian college (type a name or short form, e.g. `JNTUH`, `Osmania`). Shows people with a Bachelor's there **and** a Master's from any US university.
- **Filter B - US University → Indian Undergrad:** pick a US university (e.g. `UT Dallas`, `UNT`). Shows people with a Master's there **and** a Bachelor's from any Indian college.
- Optional: year range (applies to the degree at the chosen school; entries with no year are left out when a range is set), location, and skill/title/company keyword. 25 per page; **⬇ Excel** downloads every match (up to 10,000). Each download is logged with the recruiter's id.

**Where the profiles come from:** every profile an Apify "Search LinkedIn" run scans is saved with its full education list (not only that year's matches). Existing Sourcing results are imported automatically at startup. Profiles are deduplicated on the LinkedIn URL.

**How a school's country is known:** from the college list (`institutions_seed.py`, table `institution_aliases`). Matching is exact first, then a close-spelling match (rapidfuzz) that never accepts a different campus. A school that isn't recognised gets no country, so it never counts in either filter. It is listed under **Admin & Settings → Colleges not recognised**, where an admin links it to the right college (or adds a new one). Every saved profile with that name then counts immediately.

**Setup:** nothing extra. Tables are created on startup and `rapidfuzz` is in `requirements.txt`. To add colleges for everyone, edit `institutions_seed.py` and bump `SEED_VERSION`.

**Code:** `education_match.py` (name/degree matching), `linkedin_ingest.py` (providers + storage), `education_filters.py` (Filter A/B, Excel), routes under `/api/education/*` in `app.py`. Tests: `test_education_match.py`, `test_education_filters.py`, `test_education_filters_browser.py`.
