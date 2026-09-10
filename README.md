# 🚀 Adroit AI - 24h Job Sourcing & AI Resume Bot Microservice

A high-performance Python microservice designed for **Mymulya (`mymulya.com`)** US IT Staffing & Bench Sales teams.

---

## ⚡ The 2 Core Powerhouse Engines

### 1. 🔍 Live 24-Hour Contract (C2C) Job Scraper
- **Multi-Portal**: Scrapes real-time contract postings from **LinkedIn** (`f_TPR=r86400` + `f_JT=C`), **Indeed** (via Apify actor with `fromDays: 1`), and **Dice / Remote feeds**.
- **Live Pay Rates**: Scrapes actual client pay rates ($85–$140/hr C2C) or displays `Rate on Discussion (C2C)`.
- **REST Endpoint**: `POST /api/jobs/search`

### 2. 🤖 AI Resume Bot & Master JD Alignment Studio
- **Master Prompt Compliance**: Applies weighted scoring (40% mandatory skills, 25% recent projects, 15% domain, 10% tools, 5% education, 5% work auth).
- **Timeline Safety**: Preserves all employment chronology, company names, and dates without hallucination.
- **Word (.docx) Generator**: Instant download of client-ready formatted `.docx` resumes.
- **REST Endpoints**:
  - `POST /api/resume-bot/extract` (PDF / Word file parsing)
  - `POST /api/resume-bot/optimize` (ATS alignment & scoring)
  - `POST /api/resume-bot/download-docx` (Word file download)

---

## 🛠️ Tech Stack & Requirements
- **Backend**: Python 3.10+ (Flask, Gunicorn)
- **Scraping**: `requests`, `beautifulsoup4`, `apify_service.py`
- **Document AI**: `python-docx`, `pypdf`

---

## 🚀 1-Command Startup

```bash
# Via Docker:
docker compose up -d --build

# Via Native Python:
pip install -r requirements.txt
python app.py  # or: gunicorn app:app --workers=3 --bind 0.0.0.0:5000
```
- Open in browser: `http://localhost:5000` (or your server domain)
- Sign in with 1-Click Dev Mode.

---

## 📂 Integration with Mymulya React + Spring Boot
- **React Drop-In Component**: `react_components/HotlistAiIntegration.jsx` (Adds 24h Jobs & AI Resume buttons directly to Grand Hotlist table rows).
- **API Documentation**: `API_DOCS.md`
