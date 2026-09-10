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
