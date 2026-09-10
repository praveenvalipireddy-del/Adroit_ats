# 🚀 Adroit AI & 24h Job Sourcing Microservice
### Core Integration Guide for Mymulya Engineering Team (React + Spring Boot)

This service provides the two core AI engines for the Mymulya platform:
1. **🔍 Real-Time 24h Contract (C2C) Job Scraper** (LinkedIn Guest API, Indeed via Apify, Dice)
2. **🤖 AI Resume Bot & Master JD Alignment Studio** (Weighted 6-Rule Matcher + Branded Word `.docx` Generator)

---

## 🏗️ Architecture in Mymulya Ecosystem

```
   [ Mymulya React SPA (mymulya.com) ]
          │
          ├── (Consultant Hotlist, RTR, Submissions) ──► [ Spring Boot Microservices ]
          │
          └── (24h Live Job Scraping & AI Resume) ─────► [ Adroit AI Microservice (Port 5000) ]
                                                         ├── live_job_scraper.py
                                                         └── resume_bot.py
```

---

## 📡 The 2 Core REST API Services

### 1. 🔍 Live 24-Hour Job Search Engine
Scrapes 100% real live contract (C2C) postings posted in the **last 24 hours** from LinkedIn, Indeed, and Dice with genuine apply URLs and live pay rates.

- **Endpoint**: `POST /api/jobs/search`
- **Headers**: `Content-Type: application/json`

**React Request**:
```json
{
  "query": "Dotnet Developer",
  "location": "United States",
  "source": "All",
  "contract_only": true,
  "time_filter": "past_24h"
}
```

**JSON Response**:
```json
{
  "count": 12,
  "query": "Dotnet Developer",
  "contract_only": true,
  "time_filter": "past_24h",
  "results": [
    {
      "id": "live-li-1",
      "title": "Senior .NET Core C2C Developer",
      "company": "Apex Systems",
      "location": "Remote, USA",
      "job_type": "Contract (C2C)",
      "salary": "$85 - $110/hr",
      "source": "LinkedIn (Live 24h)",
      "url": "https://www.linkedin.com/jobs/view/416789...",
      "posted_time": "Today (<24h)",
      "match_score": 98
    }
  ]
}
```

---

### 2. 🤖 AI Resume Bot & Master Alignment Engine
Applies your **Master Resume Optimization Prompt rules** (40% mandatory skills, 25% recent projects, 15% domain, 10% tools, 5% education, 5% work auth; timeline validity safety checks).

#### A. Optimize Resume against Target JD
- **Endpoint**: `POST /api/resume-bot/optimize`
- **Request Body**:
```json
{
  "resume_text": "Candidate full resume text...",
  "jd_text": "Job description text...",
  "custom_instructions": "Focus on .NET 8 and Microservices"
}
```
- **Response**:
```json
{
  "candidate_name": "Malla Reddy G",
  "domain_detected": ".NET Full Stack & Cloud Architecture",
  "initial_match_percentage": 68,
  "target_match_percentage": 94,
  "mandatory_skills_aligned": [".NET Core", "C#", "Azure", "Microservices", "SQL"],
  "safe_skills_injected": ["Docker", "Kafka Event-Driven Architecture"],
  "tailored_resume_text": "Complete optimized resume content with targeted action verbs..."
}
```

#### B. Extract Text from Uploaded Resume (PDF / Word)
- **Endpoint**: `POST /api/resume-bot/extract`
- **Method**: Multipart Form-Data (`file: <resume.pdf / resume.docx>`)
- **Response**: `{ "filename": "resume.docx", "text": "Extracted text..." }`

#### C. Download Formatted Word (.docx) Document
- **Endpoint**: `POST /api/resume-bot/download-docx`
- **Request Body**:
```json
{
  "candidate_name": "Malla Reddy G",
  "resume_text": "Tailored resume text..."
}
```
- **Response**: Direct binary download of styled `Malla_Reddy_G_Optimized_Resume.docx`.

---

## ⚡ 1-Command Server Startup

```bash
# Docker:
docker compose up -d --build

# Native Linux / Python:
pip install -r requirements.txt
gunicorn app:app --workers=3 --bind 0.0.0.0:5000
```
