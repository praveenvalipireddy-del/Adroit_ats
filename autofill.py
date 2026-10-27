"""Job Hunt Autofill - the server side of the Chrome extension (chrome_extension/).

The extension's side panel reads the job on a careers page (Greenhouse, Lever, Keka, Workday ...),
fills the application form with the chosen consultant's details, attaches their resume and asks the
AI only for the questions that aren't standard. It never clicks Submit - the recruiter does.

- AUTOFILL_FIELDS: the "Autofill answers" a recruiter fills once per consultant (stored as JSON in
  candidates.autofill_answers). Unknown keys are dropped; values are clipped.
- Connection key: the extension can't use the browser login cookie, so the recruiter copies a key from
  Job Hunt into the extension. It is signed with the app secret, expires after 90 days, and stops
  working when the user's password changes (part of the password hash is inside the signature).
- profile(): everything the form filler needs - the consultant's record + their autofill answers,
  with names split and nothing invented (an empty answer stays empty and the field is left for you).
- answer_questions(): the AI writes answers for the non-standard questions, from the consultant's
  resume and answers only; anything it can't support from them is returned empty, never made up.
"""
import hashlib
import json
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

KEY_SALT = "jobhunt-autofill-extension"
KEY_MAX_AGE = 90 * 24 * 3600

# key -> (label shown in Job Hunt, max length). Order = the order of the form in Job Hunt.
AUTOFILL_FIELDS = {
    "linkedin_url": ("LinkedIn profile URL", 300),
    "portfolio_url": ("GitHub / portfolio / website", 300),
    "address_line": ("Street address", 200),
    "city": ("City", 100),
    "state": ("State / province", 100),
    "postal_code": ("ZIP / postal code", 20),
    "current_company": ("Current / last employer", 150),
    "work_authorized": ("Authorized to work in the job's country? (Yes / No)", 10),
    "needs_sponsorship": ("Will need visa sponsorship? (Yes / No)", 10),
    "willing_to_relocate": ("Willing to relocate? (Yes / No)", 10),
    "notice_period": ("Notice period / earliest start", 100),
    "expected_salary": ("Expected salary / rate (if different from the target rate)", 100),
    "highest_degree": ("Highest degree (e.g. Master's)", 100),
    "field_of_study": ("Field of study", 150),
    "university": ("University / college", 200),
    "graduation_year": ("Graduation year", 10),
    "how_heard": ("How did you hear about us? (e.g. LinkedIn)", 100),
    "gender": ("Gender (voluntary - blank = 'Decline to self-identify')", 60),
    "race": ("Race / ethnicity (voluntary)", 100),
    "veteran": ("Veteran status (voluntary)", 100),
    "disability": ("Disability status (voluntary)", 100),
    "notes_for_ai": ("Anything else the AI may use for questions (facts only)", 1500),
}
YES_NO_KEYS = ("work_authorized", "needs_sponsorship", "willing_to_relocate")
DECLINE = "Decline to self-identify"


def clean_answers(raw) -> Dict[str, str]:
    """Only known keys, as clipped strings; Yes/No questions normalised to 'Yes' / 'No' / ''."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, (_label, limit) in AUTOFILL_FIELDS.items():
        val = re.sub(r"[ \t]+", " ", str(raw.get(key) or "")).strip()[:limit]
        if key in YES_NO_KEYS and val:
            low = val.lower()
            val = "Yes" if low in ("y", "yes", "true", "1") else "No" if low in ("n", "no", "false", "0") else ""
        if val:
            out[key] = val
    return out


def load_answers(cand: Dict) -> Dict[str, str]:
    return clean_answers((cand or {}).get("autofill_answers") or "{}")


# ---- connection key

def _pw_fingerprint(password_hash: str) -> str:
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:12]


def make_key(secret: str, user_id: int, password_hash: str) -> str:
    return URLSafeTimedSerializer(secret, salt=KEY_SALT).dumps({"u": int(user_id), "p": _pw_fingerprint(password_hash)})


def read_key(secret: str, key: str) -> Optional[Dict]:
    """{'u': user_id, 'p': fingerprint} for a valid, unexpired key, else None."""
    try:
        data = URLSafeTimedSerializer(secret, salt=KEY_SALT).loads((key or "").strip(), max_age=KEY_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("u"), int) else None


def key_matches_password(data: Dict, password_hash: str) -> bool:
    return bool(data) and data.get("p") == _pw_fingerprint(password_hash)


# ---- the consultant's details for the form

def split_name(name: str):
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def profile(cand: Dict, resume_filename: str = "") -> Dict:
    """What the filler fills. Empty values are left out so the field stays empty (never invented)."""
    a = load_answers(cand)
    first, last = split_name(cand.get("name") or "")
    loc = (cand.get("location") or "").strip()
    city = a.get("city") or (re.split(r"[,(]", loc)[0].strip() if loc else "")
    p = {
        "full_name": (cand.get("name") or "").strip(),
        "first_name": first, "last_name": last,
        "email": (cand.get("email") or "").strip(),
        "phone": (cand.get("phone") or "").strip(),
        "title": (cand.get("title") or "").strip(),
        "location": loc,
        "city": city,
        "country": (cand.get("country") or "").strip(),
        "years_experience": str(cand.get("experience_years") or "").strip(),
        "visa_status": (cand.get("visa_status") or "").strip(),
        "expected_salary": a.get("expected_salary") or (cand.get("target_rate") or "").strip(),
        "skills": (cand.get("primary_skills") or "").strip(),
    }
    for k, v in a.items():
        if k not in ("city", "expected_salary", "notes_for_ai"):
            p[k] = v
    for k in ("gender", "race", "veteran", "disability"):
        p.setdefault(k, DECLINE)
    out = {k: v for k, v in p.items() if v}
    out["resume_filename"] = resume_filename or ""
    return out


# ---- the job read from the page

def clean_job(job: Dict) -> Dict:
    job = job if isinstance(job, dict) else {}
    url = str(job.get("url") or "").strip()[:1000]
    if url and not re.match(r"^https?://", url):
        url = ""
    return {
        "title": re.sub(r"\s+", " ", str(job.get("title") or "")).strip()[:200],
        "company": re.sub(r"\s+", " ", str(job.get("company") or "")).strip()[:200],
        "location": re.sub(r"\s+", " ", str(job.get("location") or "")).strip()[:200],
        "url": url,
        "description": str(job.get("description") or "").strip()[:20000],
    }


PORTALS = (("greenhouse.io", "Greenhouse"), ("lever.co", "Lever"), ("ashbyhq.com", "Ashby"), ("myworkdayjobs.com", "Workday"),
           ("keka.com", "Keka"), ("darwinbox", "Darwinbox"), ("bamboohr.com", "BambooHR"), ("smartrecruiters.com", "SmartRecruiters"),
           ("workable.com", "Workable"), ("zohorecruit", "Zoho Recruit"), ("freshteam.com", "Freshteam"), ("icims.com", "iCIMS"),
           ("dice.com", "Dice"), ("linkedin.com", "LinkedIn"), ("indeed.com", "Indeed"), ("naukri.com", "Naukri"))


def source_for(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    for needle, name in PORTALS:
        if needle in host:
            return name if name in ("Dice", "LinkedIn", "Indeed", "Naukri") else f"Company site ({name})"
    return "Company site"


# ---- AI answers for the non-standard questions

ANSWER_SYSTEM = """You fill job application questions for a staffing recruiter, on behalf of the consultant described below.
Answer ONLY from the consultant's resume, profile and notes. Never invent employers, dates, numbers, degrees, certifications, clearances or links.
If a question can't be answered from them (or asks for a personal opinion you can't know), return an empty string for it - the recruiter will answer it.
Keep answers short and professional: one line for short fields; 2-5 sentences for open questions like "why are you interested"; first person, as the consultant.
For questions with options, return exactly one of the given options (copy its text), or "" if none is supported.
Return JSON only: {"answers": {"<question id>": "<answer>"}}"""


def answer_prompt(cand: Dict, job: Dict, questions: List[Dict]) -> str:
    a = load_answers(cand)
    facts = [f"Name: {cand.get('name') or ''}", f"Title: {cand.get('title') or ''}", f"Experience: {cand.get('experience_years') or ''} years",
             f"Location: {cand.get('location') or ''}", f"Work authorization / visa: {cand.get('visa_status') or ''}",
             f"Target rate / salary: {a.get('expected_salary') or cand.get('target_rate') or ''}", f"Skills: {cand.get('primary_skills') or ''}"]
    for k, v in a.items():
        facts.append(f"{AUTOFILL_FIELDS[k][0]}: {v}")
    qs = []
    for q in questions:
        line = f'- id "{q["id"]}": {q["label"]}'
        if q.get("options"):
            line += " | options: " + " / ".join(q["options"])
        qs.append(line)
    return (f"CONSULTANT PROFILE:\n" + "\n".join(facts) +
            f"\n\nRESUME:\n{(cand.get('resume_text') or '')[:12000]}\n\n"
            f"JOB: {job.get('title')} at {job.get('company')} ({job.get('location')})\n{(job.get('description') or '')[:6000]}\n\n"
            f"QUESTIONS:\n" + "\n".join(qs))


def clean_questions(raw) -> List[Dict]:
    out = []
    for q in (raw if isinstance(raw, list) else [])[:40]:
        if not isinstance(q, dict):
            continue
        qid = re.sub(r"[^A-Za-z0-9_-]", "", str(q.get("id") or ""))[:40]
        label = re.sub(r"\s+", " ", str(q.get("label") or "")).strip()[:400]
        if not qid or not label:
            continue
        opts = [re.sub(r"\s+", " ", str(o)).strip()[:150] for o in (q.get("options") or [])[:30] if str(o).strip()]
        out.append({"id": qid, "label": label, "options": opts})
    return out


def parse_answers(raw_text: str, questions: List[Dict]) -> Dict[str, str]:
    """Answers keyed by question id; an option answer must be one of the options (else dropped)."""
    try:
        m = re.search(r"\{.*\}", raw_text or "", re.S)
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return {}
    by_id = {q["id"]: q for q in questions}
    out = {}
    for qid, val in answers.items():
        q = by_id.get(str(qid))
        val = str(val or "").strip()[:3000]
        if not q or not val:
            continue
        if q["options"]:
            match = next((o for o in q["options"] if o.lower() == val.lower()), None)
            if not match:
                continue
            val = match
        out[q["id"]] = val
    return out


# ---- quick match (free, no AI): how much of the job's wording the consultant's skills cover

def keyword_match(cand: Dict, job: Dict) -> Dict:
    skills = [s.strip() for s in re.split(r"[,;/\n|]", cand.get("primary_skills") or "") if s.strip()]
    text = " ".join([job.get("title") or "", job.get("description") or ""]).lower()
    if not skills or not text.strip():
        return {"score": None, "matched": [], "missing": [], "note": "Add the consultant's skills and open a job page to see a match."}
    matched = [s for s in skills if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", text)]
    score = round(100 * len(matched) / len(skills))
    return {"score": score, "matched": matched[:15], "missing": [s for s in skills if s not in matched][:8], "method": "keywords",
            "note": f"{len(matched)} of {len(skills)} of the consultant's skills appear in this job."}


MATCH_SYSTEM = """You compare a consultant's resume with a job posting for a staffing recruiter.
Score 0-100 how well the consultant fits (skills, experience level, role). Be honest; don't inflate.
List up to 6 job requirements the resume clearly shows ("strengths") and up to 6 important ones it doesn't show ("missing").
Use short skill-style phrases taken from the job. Return JSON only: {"score": 0, "strengths": [], "missing": [], "summary": "one sentence"}"""


def match_prompt(cand: Dict, job: Dict) -> str:
    head = f"RESUME ({cand.get('name') or ''}, {cand.get('title') or ''}, {cand.get('experience_years') or ''} yrs):"
    body = (cand.get("resume_text") or cand.get("primary_skills") or "")[:12000]
    jd = (job.get("description") or "")[:8000]
    return "\n".join([head, body, "", f"JOB: {job.get('title')} at {job.get('company')}", jd])


def parse_match(raw_text: str) -> Optional[Dict]:
    try:
        m = re.search(r"\{.*\}", raw_text or "", re.S)
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        score = max(0, min(100, int(round(float(data.get("score"))))))
    except (TypeError, ValueError):
        return None
    lst = lambda k: [re.sub(r"\s+", " ", str(x)).strip()[:80] for x in (data.get(k) or [])[:6] if str(x).strip()]  # noqa: E731
    return {"score": score, "matched": lst("strengths"), "missing": lst("missing"),
            "note": re.sub(r"\s+", " ", str(data.get("summary") or "")).strip()[:300], "method": "ai"}
