import re
import os
import io
import json
import time
import logging
import docx
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pypdf

import config

logger = logging.getLogger("resume_bot")

# Free-tier Gemini model for the real AI rewrite (aistudio.google.com/apikey - no card
# needed). Kept on the "-latest" alias so it keeps pointing at a free-tier-eligible Flash
# model as Google updates what that alias means, rather than pinning a dated version here.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

# Master domains catalog
DOMAINS = {
    "Fintech / Banking": ["bank", "fintech", "payment", "trading", "settlement", "clearing", "credit", "wealth", "lending", "fraud", "risk", "capital", "financial"],
    "Healthcare": ["health", "hospital", "clinical", "patient", "ehr", "hipaa", "medical", "pharma", "biotech", "care"],
    "E-Commerce / Retail": ["retail", "ecommerce", "e-commerce", "cart", "checkout", "inventory", "catalog", "supply chain", "order"],
    "Telecom": ["telecom", "5g", "network", "voip", "fiber", "carrier", "broadband"],
    "SaaS / Cloud Platform": ["saas", "multi-tenant", "b2b", "subscription", "platform", "cloud-native", "api platform"],
    "Insurance": ["insurance", "policy", "claims", "underwriting", "actuarial"]
}

# Technical keywords dictionary
TECH_KEYWORDS = [
    "Java", "Java 21", "Java 17", "Spring Boot", "Spring Cloud", "Microservices", "REST API", "GraphQL",
    "Kafka", "RabbitMQ", "ActiveMQ", "AWS", "Amazon Web Services", "Azure", "GCP", "Google Cloud",
    "Docker", "Kubernetes", "EKS", "AKS", "Terraform", "CI/CD", "Jenkins", "GitLab CI", "GitHub Actions",
    "Python", "FastAPI", "Django", "Flask", "PyTorch", "TensorFlow", "LangChain", "LLM", "RAG", "Vector DB",
    "React", "React 19", "Next.js", "TypeScript", "JavaScript", "Angular", "Vue.js", "Node.js", "Tailwind CSS",
    "PostgreSQL", "MySQL", "Oracle", "MongoDB", "Cassandra", "Redis", "Elasticsearch", "Snowflake", "dbt",
    "Airflow", "Spark", "Hadoop", "SQL", "gRPC", "Prometheus", "Grafana", "Splunk", "ArgoCD", "Linux"
]

def extract_text_from_file_bytes(file_bytes, filename, strict=False):
    """Extracts text from PDF, DOCX, or plain text bytes. With strict=True a file that can't be
    parsed returns '' instead of its raw bytes decoded as text (which for a broken .docx/.pdf is
    just binary garbage that would then be treated as the resume)."""
    name = filename.lower()
    text = ""
    try:
        if name.endswith(".docx"):
            doc = docx.Document(io.BytesIO(file_bytes))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    paragraphs.append(" | ".join([c.text.strip() for c in row.cells if c.text.strip()]))
            text = "\n\n".join(paragraphs)
        elif name.endswith(".pdf"):
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            text = "\n\n".join(pages_text)
        else:
            text = file_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"Error extracting text from {filename}: {e}")
        text = "" if strict else file_bytes.decode("utf-8", errors="ignore")
    return text.strip()

def detect_domain(text):
    text_lower = text.lower()
    best_domain = "Enterprise SaaS & Cloud"
    highest_score = 0

    for domain, keywords in DOMAINS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > highest_score:
            highest_score = score
            best_domain = domain

    return best_domain

def extract_skills_from_text(text):
    found = []
    text_lower = f" {text.lower()} "
    for tech in TECH_KEYWORDS:
        # Simple word boundary check
        pattern = r'(?i)\b' + re.escape(tech) + r'\b'
        if re.search(pattern, text):
            found.append(tech)
    return list(dict.fromkeys(found))

def gemini_configured() -> bool:
    return bool((config.GEMINI_API_KEY or "").strip())


def _explain_gemini_error(ex: Exception) -> str:
    """Short, user-safe reason a Gemini call failed - shown in the result so a problem (bad
    key, exhausted free quota, wrong model, timeout) is diagnosable instead of the page
    silently doing nothing. Never includes the API key itself."""
    msg = str(ex)
    key = (config.GEMINI_API_KEY or "").strip()
    if key:
        msg = msg.replace(key, "***")
    low = msg.lower()
    if "429" in msg or "resource_exhausted" in low or "quota" in low:
        return "Gemini's free daily quota is used up - it resets at midnight Pacific time"
    if "api key" in low or "api_key" in low or "401" in msg or "403" in msg or "permission_denied" in low or "unauthenticated" in low:
        return "Google rejected the API key - check GEMINI_API_KEY was copied correctly"
    if "404" in msg or "not found" in low or "is not supported" in low:
        return f"Gemini model '{GEMINI_MODEL}' isn't available on this key - set GEMINI_MODEL to a current model name"
    if "timeout" in low or "timed out" in low or "deadline" in low:
        return "Gemini took too long to answer (over 90 seconds) - try again"
    return "Gemini call failed: " + " ".join(msg.split())[:140]


def _is_transient_gemini_error(ex: Exception) -> bool:
    low = str(ex).lower()
    return any(t in low for t in ("503", "unavailable", "overloaded", "500 internal", "temporarily"))


# --------------------------------------------------------------------------------------
# The recruiter's own master prompt is the spec. It is sent to Gemini verbatim (so editing
# MASTER_RESUME_PROMPT.md changes the optimizer's behaviour with no code change); the code
# below only adds a machine-readable output contract and enforces the rules it can verify.
# --------------------------------------------------------------------------------------
MASTER_PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MASTER_RESUME_PROMPT.md")


def load_master_prompt() -> str:
    try:
        with open(MASTER_PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as ex:
        logger.error(f"Could not read {MASTER_PROMPT_FILE}: {ex}")
        return ""


_APP_OUTPUT_CONTRACT = """=== HOW THIS APP READS YOUR ANSWER (this replaces the 'FINAL OUTPUT FORMAT' and 'OUTPUT' wording above) ===
One decision rule is missing from the MATCH-BASED DECISION RULES above: BELOW 60% MATCH -> Reject. Do not optimize; return the original resume text unchanged in updated_resume_text.

Respond with ONE JSON object and nothing else - no markdown fences, no commentary before or after - using exactly these keys:
{
  "initial_match_percentage": <integer 0-100, realistic and NOT inflated>,
  "match_breakdown": {
    "mandatory_skills": <0-40>, "recent_project_relevance": <0-25>, "domain_experience": <0-15>,
    "tools_frameworks_cloud": <0-10>, "certifications_education": <0-5>, "location_work_authorization": <0-5>
  },
  "domain_detected": "<domain>",
  "strong_match_skills": ["..."],
  "partial_match_skills": ["..."],
  "mandatory_missing_skills": ["..."],
  "preferred_missing_skills": ["..."],
  "risky_skills_avoided": ["skills you deliberately did NOT add, and why in a few words"],
  "skills_added": {"summary": ["..."], "technical_skills": ["..."], "projects": ["..."], "environment": ["..."]},
  "ats_optimization_notes": ["Keywords optimized: ...", "Domain alignment improvements: ...", "Recent project enhancements: ..."],
  "target_match_percentage": <integer 0-100>,
  "updated_resume_text": "<the COMPLETE updated resume as plain text, same section order and bullet structure as the original; the unchanged original if rejected>"
}
match_breakdown must add up to initial_match_percentage. Never change or drop any date, never change total years of experience, and never state a new number of years for any technology."""

_BREAKDOWN_CAPS = {
    "mandatory_skills": 40, "recent_project_relevance": 25, "domain_experience": 15,
    "tools_frameworks_cloud": 10, "certifications_education": 5, "location_work_authorization": 5,
}


def decide_enhancement(initial: int):
    """(decision label, enhancement level, highest allowed target %) from the master prompt's
    match bands. Decided here in code from the percentage - never taken from the model - so
    the bands are always applied exactly as written."""
    if initial < 60:
        return "Reject (Profile Gap Too Wide)", "None", initial
    if initial <= 70:
        return "Moderate Enhancement (Candidate Viable for C2C Submission)", "Moderate", 90
    if initial <= 85:
        return "Strong ATS Optimization (High Placement Probability)", "Strong", 96
    if initial <= 90:
        return "Minimal Enhancement / Fine-Tuning", "Minimal", 95
    return "No Major Changes Needed (Already a Strong Match)", "None", initial


_SECTION_WORDS = ("summary", "profile", "experience", "skills", "resume", "objective", "education", "curriculum", "contact")


def detect_candidate_name(resume_text: str):
    """(name, detected). detected=False means the first line doesn't look like a person's name
    (e.g. it is a section heading), so callers should use the selected consultant's name."""
    lines = [l.strip() for l in (resume_text or "").split("\n") if l.strip()]
    if not lines:
        return "", False
    first = lines[0]
    looks_like_name = (
        len(first) <= 40 and "@" not in first and ":" not in first
        and not re.search(r"\d", first) and 1 <= len(first.split()) <= 5
        and re.search(r"[A-Za-z]", first)
        and not any(w in first.lower() for w in _SECTION_WORDS)
    )
    return (first, True) if looks_like_name else ("Technical Consultant", False)


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_YEARS_EXP_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", re.I)


def check_resume_safety(original: str, updated: str) -> str:
    """The master prompt's NEVER rules that can be verified mechanically. Returns '' when the
    rewrite is acceptable, otherwise the reason it must be discarded."""
    o_years, n_years = set(_YEAR_RE.findall(original)), set(_YEAR_RE.findall(updated))
    if o_years != n_years:
        return "it changed employment/education dates (" + ", ".join(sorted(o_years ^ n_years)) + ")"
    added = set(_YEARS_EXP_RE.findall(updated)) - set(_YEARS_EXP_RE.findall(original))
    if added:
        return "it introduced new years-of-experience claims (" + ", ".join(sorted(added)) + " years)"
    if len(updated.strip()) < 0.6 * len(original.strip()):
        return "it returned a much shorter resume than the original (likely cut off)"
    return ""


def _parse_json_object(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _as_list(value):
    return [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else []


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_ai_result(data: dict, resume_text: str):
    """Turns Gemini's JSON into the app's response shape, applying the master prompt's rules
    in code. Returns (result, reason); result is None only if the answer is unusable."""
    breakdown_raw = data.get("match_breakdown") if isinstance(data.get("match_breakdown"), dict) else {}
    parts = {}
    for key, cap in _BREAKDOWN_CAPS.items():
        n = _num(breakdown_raw.get(key))
        if n is not None:
            parts[key] = max(0.0, min(float(cap), n))
    if len(parts) == len(_BREAKDOWN_CAPS):
        initial = int(round(sum(parts.values())))       # the weighted parts decide, so the % can't be inflated on its own
    else:
        n = _num(data.get("initial_match_percentage"))
        if n is None:
            return None, "Gemini's answer had no match percentage"
        initial = int(round(max(0.0, min(100.0, n))))
        parts = {}

    decision, level, target_cap = decide_enhancement(initial)
    tgt = _num(data.get("target_match_percentage"))
    target = initial if target_cap <= initial else int(round(max(initial, min(target_cap, tgt if tgt is not None else target_cap))))

    sa = data.get("skills_added") if isinstance(data.get("skills_added"), dict) else {}
    skills_added = {
        "summary": _as_list(sa.get("summary")),
        "technical_skills": _as_list(sa.get("technical_skills")),
        "recent_projects": _as_list(sa.get("projects") or sa.get("recent_projects")),
        "environment": _as_list(sa.get("environment")),
    }
    empty_added = {"summary": [], "technical_skills": [], "recent_projects": [], "environment": []}

    updated = str(data.get("updated_resume_text") or "").strip()
    optimized, not_optimized_reason = False, ""
    if initial < 60:
        updated, skills_added = resume_text, empty_added
        not_optimized_reason = ("The match is below 60%, so per your master prompt this profile is rejected "
                                "and the resume was left unchanged.")
    else:
        problem = "Gemini did not return an updated resume" if not updated else check_resume_safety(resume_text, updated)
        if problem:
            updated, skills_added = resume_text, empty_added
            not_optimized_reason = f"The AI's rewrite was discarded because {problem}. The resume was left unchanged - click Optimize again to retry."
        else:
            optimized = True

    name, detected = detect_candidate_name(resume_text)
    return {
        "candidate_name": name,
        "candidate_name_detected": detected,
        "initial_match_percentage": initial,
        "target_match_percentage": target,
        "match_breakdown": parts,
        "match_decision": decision,
        "enhancement_level": level,
        "analysis_source": "gemini",
        "ai_powered": True,
        "optimized": optimized,
        "not_optimized_reason": not_optimized_reason,
        "ai_unavailable_reason": "",
        "domain_detected": str(data.get("domain_detected") or "Not detected").strip(),
        "mandatory_matched_skills": _as_list(data.get("strong_match_skills")),
        "partial_match_skills": _as_list(data.get("partial_match_skills")),
        "mandatory_missing_skills": _as_list(data.get("mandatory_missing_skills")),
        "preferred_missing_skills": _as_list(data.get("preferred_missing_skills")),
        "risky_skills_avoided": _as_list(data.get("risky_skills_avoided")),
        "skills_added": skills_added,
        "ats_optimization_notes": _as_list(data.get("ats_optimization_notes")),
        "updated_resume_text": updated,
    }, ""


def _ai_optimize(resume_text: str, jd_text: str, custom_instructions: str):
    """Runs the master prompt through Gemini. Returns (result, reason): result is the app's
    response dict, or None - never an exception - when AI is unavailable, with `reason` saying
    why (no key, free quota used up, malformed answer, ...)."""
    if not gemini_configured():
        return None, "no GEMINI_API_KEY is configured on the server"
    master = load_master_prompt()
    if not master:
        return None, "MASTER_RESUME_PROMPT.md is missing on the server"
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY.strip(), http_options=types.HttpOptions(timeout=90000))
        prompt = (
            f"JOB DESCRIPTION:\n{jd_text}\n\n"
            f"RESUME:\n{resume_text}\n\n"
            f"OPTIONAL INSTRUCTIONS:\n{custom_instructions or 'None'}\n"
        )
        gen_config = types.GenerateContentConfig(
            system_instruction=master + "\n\n" + _APP_OUTPUT_CONTRACT,
            response_mime_type="application/json",
            max_output_tokens=16384,
            temperature=0.3,
        )
        response = None
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=gen_config)
                break
            except Exception as ex:
                if attempt == 0 and _is_transient_gemini_error(ex):
                    time.sleep(2)
                    continue
                raise
        raw_text = (response.text or "").strip()
    except Exception as ex:
        reason = _explain_gemini_error(ex)
        logger.warning(f"Gemini optimization unavailable: {reason}")
        return None, reason

    if not raw_text:
        return None, "Gemini returned an empty response (it may have blocked the content)"
    data = _parse_json_object(raw_text)
    if data is None:
        return None, "Gemini's answer was not valid JSON (it may have been cut off) - try again"
    return _normalize_ai_result(data, resume_text)


def _cert_edu_points(text: str) -> int:
    return 5 if re.search(r"\b(bachelor|master|b\.?tech|m\.?tech|b\.?e\b|m\.?s\b|mba|degree|certified|certification|certificate)\b", text, re.I) else 0


def _work_auth_points(text: str) -> int:
    return 5 if re.search(r"\b(u\.?s\.? citizen|green card|gc ead|h-?1b|h4 ?ead|opt|cpt|work authori[sz]ation|authori[sz]ed to work|visa)\b", text, re.I) else 0


def _keyword_analysis(resume_text: str, jd_text: str, ai_reason: str):
    """Analysis-only fallback used when Gemini is unavailable. It measures how many of ~70
    well-known technologies from the JD appear in the resume - a real, verifiable number - but
    it does NOT rewrite the resume: deciding which missing skills a candidate can truthfully
    claim is exactly the judgement the master prompt reserves for a careful reader, and
    inserting them mechanically would fabricate experience."""
    jd_skills = extract_skills_from_text(jd_text)
    resume_skills = extract_skills_from_text(resume_text)
    jd_domain = detect_domain(jd_text)
    resume_lower = {s.lower() for s in resume_skills}
    matched = [s for s in jd_skills if s.lower() in resume_lower]
    missing = [s for s in jd_skills if s.lower() not in resume_lower]
    ratio = (len(matched) / len(jd_skills)) if jd_skills else 0.0
    domain_matched = 1.0 if any(kw in resume_text.lower() for kw in DOMAINS.get(jd_domain, [])) else 0.0

    # Same weights as the master prompt. Parts a keyword scan cannot see (recent-project
    # relevance beyond keywords) reuse the skill ratio; certs/education and work authorization
    # only score when the resume actually mentions them - no free points.
    initial = int(round(
        ratio * 40 + ratio * 25 + domain_matched * 15
        + min(1.0, len(resume_skills) / 8.0) * 10
        + _cert_edu_points(resume_text) + _work_auth_points(resume_text)
    ))
    initial = max(0, min(100, initial))
    decision, level, _ = decide_enhancement(initial)
    name, detected = detect_candidate_name(resume_text)
    return {
        "candidate_name": name,
        "candidate_name_detected": detected,
        "initial_match_percentage": initial,
        "target_match_percentage": initial,
        "match_breakdown": {},
        "match_decision": decision if initial < 60 else f"Analysis only - AI rewrite unavailable (match band: {decision})",
        "enhancement_level": "None",
        "analysis_source": "keyword-scan",
        "ai_powered": False,
        "optimized": False,
        "not_optimized_reason": ("The resume was not changed. Without the AI, the app can only report the keyword gap - "
                                 "it can't tell which missing skills this candidate can truthfully claim, and inserting them "
                                 "mechanically would invent experience."),
        "ai_unavailable_reason": ai_reason,
        "domain_detected": jd_domain,
        "mandatory_matched_skills": matched,
        "partial_match_skills": [],
        "mandatory_missing_skills": missing,
        "preferred_missing_skills": [],
        "risky_skills_avoided": [],
        "skills_added": {"summary": [], "technical_skills": [], "recent_projects": [], "environment": []},
        "ats_optimization_notes": [
            f"Keyword scan only: compared the JD against ~{len(TECH_KEYWORDS)} well-known technologies, so skills outside that list are not counted.",
            f"Found {len(matched)} of {len(jd_skills)} recognised JD technologies in the resume.",
        ],
        "updated_resume_text": resume_text,
    }


def optimize_resume_for_jd(resume_text, jd_text, custom_instructions=""):
    """Master Resume Optimization & JD Alignment (see MASTER_RESUME_PROMPT.md).

    With Gemini available, the master prompt drives the full analysis + rewrite; the match band
    is applied in code and mechanically checkable safety rules (dates, years of experience) are
    enforced on the result. Without Gemini it returns the keyword analysis only and leaves the
    resume unchanged."""
    resume_text = (resume_text or "").strip()
    jd_text = (jd_text or "").strip()
    if not resume_text or not jd_text:
        return {"error": "Both resume and job description are required."}

    result, reason = _ai_optimize(resume_text, jd_text, (custom_instructions or "").strip())
    if result is not None:
        return result
    return _keyword_analysis(resume_text, jd_text, reason)

def create_docx_resume(resume_text, candidate_name="Candidate"):
    """Generates a clean, professionally formatted Microsoft Word (.docx) document."""
    doc = docx.Document()

    # Set 0.75 in margins
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)

    lines = [l.strip() for l in resume_text.split("\n") if l.strip()]

    # Heading / Name. The resume's first line is only dropped when it IS the name line being
    # replaced by the heading - dropping it unconditionally deleted real content (e.g. a
    # "Professional Summary" heading) from resumes that don't start with the name.
    if lines:
        name_p = doc.add_paragraph()
        name_run = name_p.add_run(candidate_name)
        name_run.bold = True
        name_run.font.size = Pt(18)
        name_run.font.color.rgb = RGBColor(15, 23, 42)
        name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        name_line, name_found = detect_candidate_name(resume_text)
        if name_found and lines[0].strip().lower() == name_line.strip().lower():
            lines = lines[1:]

    for line in lines:
        # Check if section title
        if line.isupper() and len(line) < 40 or line.endswith(":") and len(line) < 35:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(3)
            run = p.add_run(line)
            run.bold = True
            run.font.size = Pt(12)
            run.font.color.rgb = RGBColor(99, 102, 241)
        elif line.startswith("•") or line.startswith("-") or line.startswith("*"):
            p = doc.add_paragraph(line.lstrip("•-* "), style='List Bullet')
            p.paragraph_format.space_after = Pt(2)
        else:
            p = doc.add_paragraph(line)
            p.paragraph_format.space_after = Pt(3)

    byte_io = io.BytesIO()
    doc.save(byte_io)
    byte_io.seek(0)
    return byte_io
