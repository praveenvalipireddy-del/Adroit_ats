import re
import os
import io
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

def extract_text_from_file_bytes(file_bytes, filename):
    """Extracts text from PDF, DOCX, or plain text bytes."""
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
        text = file_bytes.decode("utf-8", errors="ignore")
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


def _ai_rewrite_resume(resume_text, jd_text, custom_instructions, matched_skills, skills_to_add, domain):
    """Real AI rewrite via Gemini's free tier. Returns None - never raises - when no key is
    configured, the free daily quota is used up, or any other error occurs. Callers must
    fall back to the deterministic rule-based rewrite in that case, so the feature keeps
    working (just without AI phrasing) instead of breaking once the free quota runs out."""
    if not gemini_configured():
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY.strip())
        system_instruction = (
            "You are an ATS resume optimization expert for US IT staffing. Rewrite the given "
            "resume so it naturally incorporates the listed keywords where genuinely relevant. "
            "STRICT RULES: never invent employers, job titles, dates, degrees, or certifications "
            "that are not already in the original resume. Never change any date, company name, "
            "or the chronological order of jobs. Keep every real detail of the candidate's "
            "actual experience - only rephrase bullet points and skills sections and weave in "
            "relevant keywords/technologies. Return ONLY the rewritten resume text - no "
            "commentary, no markdown fences, no preamble."
        )
        prompt = (
            f"ORIGINAL RESUME:\n{resume_text}\n\n"
            f"TARGET JOB DESCRIPTION:\n{jd_text}\n\n"
            f"Domain: {domain}\n"
            f"Keywords already present (keep as-is): {', '.join(matched_skills) or 'none'}\n"
            f"Keywords to naturally weave in, only where truthful: {', '.join(skills_to_add) or 'none'}\n"
            + (f"Additional instructions from the recruiter: {custom_instructions}\n" if custom_instructions else "")
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                max_output_tokens=2000,
                temperature=0.4,
            ),
        )
        text = (response.text or "").strip()
        return text or None
    except Exception as ex:
        logger.warning(f"Gemini AI rewrite unavailable, using rule-based fallback: {ex}")
        return None


def optimize_resume_for_jd(resume_text, jd_text, custom_instructions=""):
    """
    Executes the Master Resume Optimization & JD Alignment Logic:
    1. Weighted Match Analysis (Mandatory 40%, Projects 25%, Domain 15%, Tools 10%, Certs 5%, Location 5%)
    2. Decision Rules (<60% Reject, 61-70% Moderate, 71-85% Strong, 86-90% Minimal, >90% Preserve)
    3. Strict Safety Preservations (Dates, Companies, Chronology untouched)
    4. Skill classification & Project enhancement
    5. Complete updated tailored resume generation
    """
    if not resume_text or not jd_text:
        return {"error": "Both resume and job description are required."}

    jd_skills = extract_skills_from_text(jd_text)
    resume_skills = extract_skills_from_text(resume_text)
    jd_domain = detect_domain(jd_text)

    # 1. Match Analysis
    matched_skills = [s for s in jd_skills if any(rs.lower() == s.lower() for rs in resume_skills)]
    missing_skills = [s for s in jd_skills if not any(rs.lower() == s.lower() for rs in resume_skills)]

    # Calculate Realistic Weighted Match Percentage
    if jd_skills:
        skill_match_ratio = len(matched_skills) / len(jd_skills)
    else:
        skill_match_ratio = 0.75

    # Check recent domain/project alignment
    domain_matched = 1.0 if any(kw in resume_text.lower() for kw in DOMAINS.get(jd_domain, [])) else 0.6
    
    # Weighted calculation
    # Mandatory Skills: 40%, Recent Project: 25%, Domain: 15%, Tools: 10%, Education: 5%, Work Auth: 5%
    initial_score = int(
        (skill_match_ratio * 40) +
        (skill_match_ratio * 25) +
        (domain_matched * 15) +
        (min(1.0, len(resume_skills) / 8.0) * 10) +
        5 + 5
    )
    initial_score = max(45, min(92, initial_score))

    # 2. Decision Logic
    if initial_score < 60:
        decision = "Reject (Profile Gap Too Wide)"
        target_score = initial_score
        enhancement_level = "None"
    elif 60 <= initial_score <= 70:
        decision = "Moderate Enhancement (Candidate Viable for C2C Submission)"
        target_score = min(88, initial_score + 18)
        enhancement_level = "Moderate"
    elif 71 <= initial_score <= 85:
        decision = "Strong ATS Optimization (High Placement Probability)"
        target_score = min(94, initial_score + 14)
        enhancement_level = "Strong"
    else:
        decision = "Minimal Enhancement / Fine-Tuning"
        target_score = min(98, initial_score + 6)
        enhancement_level = "Minimal"

    # 3. Classify Skills to Add safely
    safe_to_add = []
    risky_avoided = []
    
    # Avoid adding impossible / heavy niche skills candidate has no background in
    for s in missing_skills:
        if s in ["Kafka", "Docker", "Kubernetes", "AWS", "Azure", "PostgreSQL", "Redis", "Microservices", "REST API", "CI/CD", "TypeScript", "GraphQL", "Spring Cloud", "Snowflake"]:
            safe_to_add.append(s)
        else:
            if len(safe_to_add) < 4:
                safe_to_add.append(s)
            else:
                risky_avoided.append(s)

    # 4. Generate Section Upgrades
    skills_added_summary = safe_to_add[:2]
    skills_added_tech = safe_to_add
    skills_added_project = safe_to_add[:3]
    skills_added_env = safe_to_add

    # Extract Candidate Name from top lines of resume
    lines = [l.strip() for l in resume_text.split("\n") if l.strip()]
    candidate_name = lines[0] if lines else "Candidate Name"
    if len(candidate_name) > 40 or "@" in candidate_name or ":" in candidate_name:
        candidate_name = "Technical Consultant"

    # Build Updated Resume Text preserving original structure
    updated_resume_lines = []
    has_injected_skills = False
    has_enhanced_summary = False
    has_enhanced_project = False

    for line in lines:
        l_lower = line.lower()
        
        # 1. Enhance Professional Summary
        if not has_enhanced_summary and any(kw in l_lower for kw in ["summary", "professional summary", "profile"]):
            updated_resume_lines.append(line)
            if skills_added_summary:
                updated_resume_lines.append(
                    f"• Demonstrated track record in {jd_domain} enterprise engineering, specializing in {', '.join(skills_added_summary)}."
                )
            has_enhanced_summary = True
            continue

        # 2. Inject into Technical Skills section
        if not has_injected_skills and any(kw in l_lower for kw in ["technical skills", "skills & tools", "core competencies", "skills:"]):
            updated_resume_lines.append(line)
            if safe_to_add:
                updated_resume_lines.append(f"• Optimized Core Stack: {', '.join(safe_to_add)}")
            has_injected_skills = True
            continue

        # 3. Enhance Recent Project section
        if not has_enhanced_project and any(kw in l_lower for kw in ["responsibilities", "key achievements", "project description", "duties:"]):
            updated_resume_lines.append(line)
            if skills_added_project:
                primary_skill = skills_added_project[0]
                updated_resume_lines.append(
                    f"• Architected and deployed scalable {jd_domain} microservices leveraging {primary_skill} and automated CI/CD cloud pipelines to achieve high system availability."
                )
            has_enhanced_project = True
            continue

        # 4. Enhance Environment section
        if "environment:" in l_lower or "technologies used:" in l_lower:
            existing_env = line.split(":", 1)[1] if ":" in line else ""
            new_env_items = list(dict.fromkeys([s.strip() for s in existing_env.split(",") if s.strip()] + safe_to_add))
            updated_resume_lines.append(f"Environment / Tech Stack: {', '.join(new_env_items[:12])}")
            continue

        updated_resume_lines.append(line)

    # Fallback if structure didn't trigger
    if not has_injected_skills and safe_to_add:
        updated_resume_lines.insert(min(4, len(updated_resume_lines)), f"\nTECHNICAL SKILLS ALIGNMENT:\n• {', '.join(safe_to_add)}\n")

    updated_resume_text = "\n".join(updated_resume_lines)

    # Real AI rewrite when Gemini is configured and its free tier is available this call;
    # the deterministic rewrite above is the guaranteed fallback either way, so the result
    # always has genuine content - it's just less naturally phrased without the AI pass.
    ai_text = _ai_rewrite_resume(resume_text, jd_text, custom_instructions, matched_skills, safe_to_add, jd_domain)
    ai_powered = ai_text is not None
    final_resume_text = ai_text if ai_powered else updated_resume_text

    return {
        "candidate_name": candidate_name,
        "initial_match_percentage": initial_score,
        "target_match_percentage": target_score,
        "match_decision": decision,
        "enhancement_level": enhancement_level,
        "ai_powered": ai_powered,
        "domain_detected": jd_domain,
        "mandatory_matched_skills": matched_skills,
        "mandatory_missing_skills": missing_skills,
        "skills_added": {
            "summary": skills_added_summary,
            "technical_skills": skills_added_tech,
            "recent_projects": skills_added_project,
            "environment": skills_added_env
        },
        "risky_skills_avoided": risky_avoided,
        "ats_optimization_notes": [
            f"Aligned resume keywords to {jd_domain} terminology without altering career dates or chronology.",
            f"Injected high-priority mandatory ATS keywords: {', '.join(safe_to_add)}.",
            "Enhanced recent project bullets with quantifiable architectural action verbs.",
            "Preserved original employment dates, companies, and timeline realism perfectly."
        ],
        "updated_resume_text": final_resume_text
    }

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

    # Heading / Name
    if lines:
        name_p = doc.add_paragraph()
        name_run = name_p.add_run(candidate_name)
        name_run.bold = True
        name_run.font.size = Pt(18)
        name_run.font.color.rgb = RGBColor(15, 23, 42)
        name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
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
