import re
import os
import io
import base64
import difflib
import json
import time
import logging
import docx
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pypdf

import config
import docx_editor

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
            # Same paragraph order (tables included, where they sit on the page) that the
            # in-place editor shows the AI, so the text box matches what gets optimized.
            text = docx_editor.extract_text(file_bytes)
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
        return "Gemini took too long to answer (over 80 seconds) - try again"
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


# Two calls per optimization, so each does one job well:
#   STEP 1 - analysis (small JSON): match %, missing/risky skills, and the list of skills that
#            pass the master prompt's Skill Classification Engine and may be added.
#   STEP 2 - the COMPLETE updated resume as plain text (item 6 of the master prompt's final
#            output format). Plain text, not a JSON string, so a long resume can't break the
#            answer with a stray quote/newline or run out of room next to the analysis.
# The master prompt itself is sent verbatim as the system prompt in both; the contracts below
# only say how this app reads each answer.
_ANALYSIS_CONTRACT = """=== HOW THIS APP READS YOUR ANSWER - STEP 1 OF 2: ANALYSIS ONLY (this replaces the 'FINAL OUTPUT FORMAT' and 'OUTPUT' wording above for this step) ===
One decision rule is missing from the MATCH-BASED DECISION RULES above: BELOW 60% MATCH -> Reject (do not optimize).
Do NOT write or rewrite the resume in this step - a second step writes the complete updated resume from your analysis.

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
  "risky_skills_avoided": ["skills you will NOT add, and why in a few words"],
  "skills_to_add": {"summary": ["..."], "technical_skills": ["..."], "projects": ["..."], "environment": ["..."]},
  "ats_optimization_notes": ["Keywords optimized: ...", "Domain alignment improvements: ...", "Recent project enhancements: ..."],
  "target_match_percentage": <integer 0-100>
}
match_breakdown must add up to initial_match_percentage.
skills_to_add lists ONLY skills that pass your Skill Classification Engine as 'Missing But Addable' (realistic, supportable, timeline-valid, role-appropriate for this candidate), each under the section where it will go. The next step may add NO technology that is not in this list or already in the resume, so leave out anything risky. Use empty lists when nothing should be added."""

_REWRITE_INTRO = """=== HOW THIS APP READS YOUR ANSWER - STEP 2 OF 2: THE COMPLETE UPDATED RESUME (this replaces the 'FINAL OUTPUT FORMAT' and 'OUTPUT' wording above for this step) ===
The analysis and the match decision are already done - see APPROVED ANALYSIS in the message. Now provide item 6 of the final output format: the COMPLETE updated resume, preserving the original format and structure.
Return ONLY the resume as plain text - no JSON, no markdown fences, no analysis, no commentary before or after.

Rules:
- Apply the enhancement level from the approved match decision (Moderate / Strong / Minimal), following every STRICT RESUME SAFETY RULE, the PROJECT ENHANCEMENT LOGIC and the SKILL INSERTION RULES above: the changes belong mainly in the Professional Summary, Technical Skills, and the most recent project with its Environment line.
- You may add ONLY the skills listed under 'Skills you may add' in the approved analysis. Never add a technology that is neither in that list nor already in the resume, and never add anything under 'Skills NOT to add'.
- Everything you do not need to change stays EXACTLY as it is, word for word. Never change a date, an employer, a job title, education, contact details or the total years of experience, and never state a new number of years for any technology.
@@FORMAT@@"""

_REWRITE_FORMAT_TEXT = """- Keep the resume's section order, headings and bullet structure exactly as they are. Begin with the resume's first line (the candidate's name)."""

_REWRITE_FORMAT_DOCX = """- The resume is given as the NUMBERED PARAGRAPHS of the candidate's ORIGINAL Word file, for example "[12] (bullet) Built batch pipelines". This app writes your text back into that file paragraph by paragraph, which is how the original fonts, layout and bullets are preserved. So return the COMPLETE resume in exactly the same form: every paragraph, in the same order, each on its own line that starts with its own number in square brackets, for example "[12] Built batch pipelines". Do not copy the tags in parentheses. Repeat every unchanged paragraph exactly as given.
- A paragraph tagged 'heading/title line' or 'locked' must be repeated exactly as given.
- To add a NEW paragraph (for example a new bullet), write it on its own line as "[+] text" directly after the paragraph it should follow. It copies that paragraph's formatting, so add new bullets only right after a bullet of the same section. Never delete a paragraph."""

_REWRITE_CONTRACT_TEXT = _REWRITE_INTRO.replace("@@FORMAT@@", _REWRITE_FORMAT_TEXT)
_REWRITE_CONTRACT_DOCX = _REWRITE_INTRO.replace("@@FORMAT@@", _REWRITE_FORMAT_DOCX)

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


def _skill_plan(data: dict) -> dict:
    sa = data.get("skills_to_add") if isinstance(data.get("skills_to_add"), dict) else {}
    return {
        "summary": _as_list(sa.get("summary")),
        "technical_skills": _as_list(sa.get("technical_skills")),
        "projects": _as_list(sa.get("projects") or sa.get("recent_projects")),
        "environment": _as_list(sa.get("environment")),
    }


def _analysis_result(data: dict, resume_text: str):
    """Step 1: Gemini's analysis JSON -> the app's response shape, with the master prompt's rules
    applied in code. Returns (result, plan) or (None, reason) if the answer is unusable. The
    result starts as 'resume unchanged'; step 2 fills in the rewrite."""
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
        "optimized": False,
        "not_optimized_reason": "",
        "ai_unavailable_reason": "",
        "domain_detected": str(data.get("domain_detected") or "Not detected").strip(),
        "mandatory_matched_skills": _as_list(data.get("strong_match_skills")),
        "partial_match_skills": _as_list(data.get("partial_match_skills")),
        "mandatory_missing_skills": _as_list(data.get("mandatory_missing_skills")),
        "preferred_missing_skills": _as_list(data.get("preferred_missing_skills")),
        "risky_skills_avoided": _as_list(data.get("risky_skills_avoided")),
        "skills_added": {"summary": [], "technical_skills": [], "recent_projects": [], "environment": []},
        "ats_optimization_notes": _as_list(data.get("ats_optimization_notes")),
        "updated_resume_text": resume_text,
        "format_preserved": False,
        "docx_base64": "",
        "changes": [],
        "skipped_edits": [],
    }, _skill_plan(data)


def _tech_present(term: str, text: str) -> bool:
    # '-' is a separator here ("AWS-hosted", "Java-based" do mention AWS / Java), unlike '+', '#', '.'
    return re.search(r"(?<![\w.+#])" + re.escape(term) + r"(?![\w+#])", text or "", re.I) is not None


def _unapproved_tech(original_text: str, new_text: str, plan: dict):
    """Known technologies (TECH_KEYWORDS) that `new_text` mentions, that the ORIGINAL resume does
    not, and that step 1 did not approve in skills_to_add. This is what stops the writer from
    adding a technology the analysis classified as risky or never approved."""
    approved = [s.lower() for skills in plan.values() for s in skills if len(s) >= 2]
    bad = []
    for term in TECH_KEYWORDS:
        if _tech_present(term, new_text) and not _tech_present(term, original_text):
            t = term.lower()
            if not any(t in a or (len(a) >= 3 and a in t) for a in approved):
                bad.append(term)
    return bad


_SECTION_HINTS = (
    ("summary", ("summary", "profile", "objective")),
    ("technical_skills", ("skill", "technolog", "competenc", "tools")),
    ("projects", ("experience", "project", "employment", "history")),
)
_ENV_LINE_RE = re.compile(r"^\s*(?:environment|tools?(?: used)?|technologies(?: used)?)\s*[:\-]", re.I)


def _section_of_heading(line: str):
    low = line.lower()
    for key, hints in _SECTION_HINTS:
        if any(h in low for h in hints):
            return key
    return None


def _looks_like_heading(line: str) -> bool:
    if not line or len(line) > 45 or line.endswith(".") or line[:1] in "•-*▪●◦–" or _ENV_LINE_RE.match(line):
        return False
    return line.isupper() or line.endswith(":") or (_section_of_heading(line) is not None and len(line.split()) <= 4)


def _added_by_section(original: str, updated: str) -> dict:
    """The new/changed lines of `updated` (compared with `original`), grouped by the resume
    section they sit in: summary / technical_skills / projects (experience bullets) / environment."""
    o = [l.strip() for l in original.splitlines()]
    n = [l.strip() for l in updated.splitlines()]
    changed = set()
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, o, n, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            changed.update(range(j1, j2))
    groups = {"summary": [], "technical_skills": [], "projects": [], "environment": []}
    section = None
    for j, line in enumerate(n):
        if _looks_like_heading(line):
            section = _section_of_heading(line)      # an unrecognised heading (EDUCATION...) ends the previous section
            continue
        if j in changed:
            key = "environment" if _ENV_LINE_RE.match(line) else section
            if key:
                groups[key].append(line)
    return {k: "\n".join(v) for k, v in groups.items()}


def _verified_skills_added(plan: dict, original: str, updated: str) -> dict:
    """The master prompt's 'Skills Added' section, reported from what is really in the final
    resume: each skill the analysis approved is listed under the section whose new/changed lines
    actually contain it (not where the plan said it would go)."""
    approved = []
    for skills in plan.values():
        for s in skills:
            if s.lower() not in [a.lower() for a in approved]:
                approved.append(s)
    groups = _added_by_section(original, updated)
    found = lambda key: [s for s in approved if _tech_present(s, groups[key])]
    return {"summary": found("summary"), "technical_skills": found("technical_skills"),
            "recent_projects": found("projects"), "environment": found("environment")}


def _approved_block(result: dict, plan: dict) -> str:
    def line(label, skills):
        return f"  {label}: {', '.join(skills) if skills else '(none)'}"
    return (
        "APPROVED ANALYSIS (from step 1 - follow it exactly):\n"
        f"Match decision: {result['match_decision']} (initial match {result['initial_match_percentage']}%, aim for about {result['target_match_percentage']}%)\n"
        f"Domain: {result['domain_detected']}\n"
        "Skills you may add (only these):\n"
        + line("Professional Summary", plan["summary"]) + "\n"
        + line("Technical Skills", plan["technical_skills"]) + "\n"
        + line("Recent project bullets", plan["projects"]) + "\n"
        + line("Environment line", plan["environment"]) + "\n"
        f"Skills NOT to add: {', '.join(result['risky_skills_avoided']) or '(none listed)'}\n"
    )


def _strip_fences(text: str) -> str:
    return re.sub(r"^```[a-z]*\s*|\s*```$", "", (text or "").strip(), flags=re.I).strip()


def _finish_rewrite(result, plan, raw, truncated, resume_text, docx_bytes):
    """Step 2: turns the written resume into the final result. Any problem leaves the resume
    unchanged and says why (result['not_optimized_reason'])."""
    problem, new_docx, changes, skipped, updated = "", None, [], [], resume_text

    if docx_bytes is not None:
        _, paragraphs = docx_editor.load_paragraphs(docx_bytes)
        entries = docx_editor.parse_numbered(raw)
        if truncated and entries:
            entries = entries[:-1]              # the last paragraph may have been cut off mid-sentence
        edits, seen = docx_editor.edits_from_rewrite(entries, paragraphs)
        if seen < max(1, int(0.8 * len(paragraphs))):
            problem = (f"the rewritten resume came back incomplete ({seen} of {len(paragraphs)} paragraphs"
                       f"{' - it hit the length limit' if truncated else ''})")
        elif not edits:
            problem = "the AI returned the resume without changing anything"
        else:
            def unapproved(_old, new, _inserting):
                bad = _unapproved_tech(resume_text, new, plan)
                return f"it adds technology the analysis did not approve ({', '.join(bad)})" if bad else ""
            try:
                new_docx, changes, skipped = docx_editor.apply_edits(docx_bytes, edits, extra_check=unapproved)
            except Exception as ex:
                logger.warning(f"Applying docx edits failed: {ex}")
                problem = "the changes could not be applied to your Word file"
            else:
                if not changes:
                    new_docx, problem = None, "none of the AI's changes passed the safety checks (see below)"
                else:
                    updated = docx_editor.extract_text(new_docx)
                    problem = check_resume_safety(resume_text, updated)
                    if problem:
                        new_docx, changes, updated = None, [], resume_text
    else:
        updated = _strip_fences(raw)
        if truncated:
            problem = "the rewritten resume hit the length limit and was cut off"
        elif not updated:
            problem = "the AI returned no resume"
        else:
            problem = check_resume_safety(resume_text, updated)
            if not problem:
                bad = _unapproved_tech(resume_text, updated, plan)
                if bad:
                    problem = f"it added technology the analysis did not approve ({', '.join(bad)})"
        if problem or updated.strip() == resume_text.strip():
            if not problem:
                problem = "the AI returned the resume without changing anything"
            updated = resume_text

    if problem:
        result["not_optimized_reason"] = f"Your resume was left unchanged because {problem}. Click Optimize again to retry."
        result["skipped_edits"] = skipped
        return result

    result.update({
        "optimized": True,
        "updated_resume_text": updated,
        "skills_added": _verified_skills_added(plan, resume_text, updated),
        "format_preserved": new_docx is not None,
        "docx_base64": base64.b64encode(new_docx).decode("ascii") if new_docx is not None else "",
        "changes": changes,
        "skipped_edits": skipped,
    })
    return result


def _generate(client, types, system: str, prompt: str, want_json: bool, max_tokens: int, started: float):
    """One Gemini call, retried once on a transient 503-type error while there is still time.
    Returns (text, truncated); raises on failure."""
    kwargs = {"response_mime_type": "application/json"} if want_json else {}
    gen_config = types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens, temperature=0.3, **kwargs)
    response = None
    for attempt in range(2):
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=gen_config)
            break
        except Exception as ex:
            if attempt == 0 and _is_transient_gemini_error(ex) and time.monotonic() - started < 45:
                time.sleep(2)
                continue
            raise
    text = (response.text or "").strip()
    truncated = False
    try:
        truncated = "MAX_TOKENS" in str(response.candidates[0].finish_reason).upper()
    except Exception:
        pass
    return text, truncated


def _ai_optimize(resume_text: str, jd_text: str, custom_instructions: str, docx_bytes: bytes = None):
    """Runs the master prompt through Gemini in two steps (analysis, then the complete rewritten
    resume). Returns (result, reason): result is the app's response dict, or None - never an
    exception - when AI is unavailable, with `reason` saying why (no key, free quota used up,
    malformed answer, ...). If step 1 works but step 2 fails, the analysis is still returned with
    the resume unchanged. With `docx_bytes` the rewrite is written back into that original file."""
    if not gemini_configured():
        return None, "no GEMINI_API_KEY is configured on the server"
    master = load_master_prompt()
    if not master:
        return None, "MASTER_RESUME_PROMPT.md is missing on the server"

    started = time.monotonic()
    custom = custom_instructions or "None"
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY.strip(), http_options=types.HttpOptions(timeout=80000))
        raw1, _ = _generate(
            client, types, master + "\n\n" + _ANALYSIS_CONTRACT,
            f"JOB DESCRIPTION:\n{jd_text}\n\nRESUME:\n{resume_text}\n\nOPTIONAL INSTRUCTIONS:\n{custom}\n",
            want_json=True, max_tokens=8192, started=started)
    except Exception as ex:
        reason = _explain_gemini_error(ex)
        logger.warning(f"Gemini analysis unavailable: {reason}")
        return None, reason

    if not raw1:
        return None, "Gemini returned an empty response (it may have blocked the content)"
    data = _parse_json_object(raw1)
    if data is None:
        return None, "Gemini's answer was not valid JSON (it may have been cut off) - try again"
    result, plan = _analysis_result(data, resume_text)
    if result is None:
        return None, plan                       # (None, reason)

    initial, level = result["initial_match_percentage"], result["enhancement_level"]
    if initial < 60:
        result["not_optimized_reason"] = ("The match is below 60%, so per your master prompt this profile is rejected "
                                          "and the resume was left unchanged.")
        return result, ""
    if level == "None":
        result["not_optimized_reason"] = ("The match is already above 90%, so per your master prompt no major changes are "
                                          "needed - your resume was left unchanged.")
        return result, ""

    # Step 2: the complete updated resume, as plain text.
    try:
        resume_block = docx_editor.numbered_listing(docx_editor.load_paragraphs(docx_bytes)[1]) if docx_bytes is not None else resume_text
        raw2, truncated = _generate(
            client, types, master + "\n\n" + (_REWRITE_CONTRACT_DOCX if docx_bytes is not None else _REWRITE_CONTRACT_TEXT),
            f"{_approved_block(result, plan)}\nJOB DESCRIPTION:\n{jd_text}\n\nRESUME:\n{resume_block}\n\nOPTIONAL INSTRUCTIONS:\n{custom}\n",
            want_json=False, max_tokens=16384, started=started)
    except Exception as ex:
        reason = _explain_gemini_error(ex)
        logger.warning(f"Gemini rewrite step failed: {reason}")
        result["not_optimized_reason"] = (f"The match analysis worked, but the step that writes the updated resume failed: "
                                          f"{reason}. Your resume was left unchanged - click Optimize again to retry.")
        return result, ""
    return _finish_rewrite(result, plan, raw2, truncated, resume_text, docx_bytes), ""


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


def optimize_resume_for_jd(resume_text, jd_text, custom_instructions="", docx_bytes=None):
    """Master Resume Optimization & JD Alignment (see MASTER_RESUME_PROMPT.md).

    With Gemini available, the master prompt drives the full analysis + rewrite; the match band
    is applied in code and mechanically checkable safety rules (dates, years of experience) are
    enforced on the result. Without Gemini it returns the keyword analysis only and leaves the
    resume unchanged.

    `docx_bytes` (the original Word file) switches to in-place editing: the result then carries
    `docx_base64`, that same file with only the AI's targeted edits applied, so the original
    formatting is preserved. If the file can't be read, it falls back to the plain-text path."""
    jd_text = (jd_text or "").strip()
    if docx_bytes is not None:
        try:
            resume_text = docx_editor.extract_text(docx_bytes)
        except Exception as ex:
            logger.warning(f"Could not read the original .docx, using text instead: {ex}")
            docx_bytes = None
    resume_text = (resume_text or "").strip()
    if not resume_text or not jd_text:
        return {"error": "Both resume and job description are required."}

    result, reason = _ai_optimize(resume_text, jd_text, (custom_instructions or "").strip(), docx_bytes)
    if result is None:
        result = _keyword_analysis(resume_text, jd_text, reason)
    result.setdefault("format_preserved", False)
    result.setdefault("docx_base64", "")
    result.setdefault("changes", [])
    result.setdefault("skipped_edits", [])
    return result

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
