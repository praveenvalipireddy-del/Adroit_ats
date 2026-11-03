"""The skills match on job cards (free, no AI): of the skills a job asks for, how many does this consultant have?

The job's skills come from the whole posting; the consultant's from their listed skills and their resume text.
A percentage is only given when the posting names at least MIN_SKILLS skills: "2 of 2" is not a 100% match.
This is a quick keyword check. The Resume Optimizer's match check (AI) is the careful reading of one job.
"""
import re
from typing import Dict, Iterable, List, Optional

import resume_bot

MIN_SKILLS = 3
# Scoring reads each whole posting, so a very wide search scores its most relevant jobs only (the list shows 200).
MAX_SCORED = 500

# Versions of something already in the list would count one skill twice.
_VERSIONS = {"Java 21", "Java 17", "React 19", "GPT-4o"}
_EXTRA = ["AI", "ML", "Machine Learning", "Artificial Intelligence", "Deep Learning", "Generative AI", "NLP", "ETL", "SAP", "Looker", "Scala",
          "C++", "Pandas", "NumPy", "Git", "Jira", "Informatica", "SSIS", "Alteryx", "DevOps", "SRE", "PowerShell", "Bash", "Postgres", "K8s"]
# Ordinary words or company names unless written exactly like the technology ("react quickly", "Oracle Health" stays a known risk).
_EXACT_CASE = {"AI", "ML", "React", "Spark", "Claude", "Gemini", "Oracle", "RAG", "MCP", "SAP", "Git", "Bash", "Scala", "Looker", "Flask", "Maven"}
# Different names for one skill.
_SAME = {"amazon web services": "aws", "google cloud": "gcp", "machine learning": "ml", "artificial intelligence": "ai",
         "postgres": "postgresql", "k8s": "kubernetes"}

_KNOWN = [t for t in dict.fromkeys(list(resume_bot.TECH_KEYWORDS) + _EXTRA) if t not in _VERSIONS]


def _pattern(terms: Iterable[str], flags=0):
    terms = sorted(terms, key=len, reverse=True)      # longest first: "Azure DevOps" before "Azure"
    if not terms:
        return None
    # the same word boundaries as the Resume Optimizer's technology guard (resume_bot._tech_present)
    return re.compile(r"(?<![\w.+#])(?:" + "|".join(re.escape(t) for t in terms) + r")(?![\w+#])", flags)


# One compiled check per skill: a plain "is the text there at all" comes first, which is what keeps a search fast.
_CHECKS = [(term, term.lower(), term in _EXACT_CASE, _pattern([term], 0 if term in _EXACT_CASE else re.I)) for term in _KNOWN]


def _key(term: str) -> str:
    low = " ".join(term.lower().split())
    return _SAME.get(low, low)


def _known_in(text: str) -> Dict[str, str]:
    """{skill key: its name} for the known skills a text names. "Azure DevOps" is one skill, not Azure as well."""
    text = text or ""
    low = text.lower()
    named = [term for term, term_low, exact, rx in _CHECKS if ((term in text) if exact else (term_low in low)) and rx.search(text)]
    lows = [n.lower() for n in named]
    found = {}
    for name, name_low in zip(named, lows):
        if any(name_low != other and _pattern([name], re.I).search(other) for other in lows if len(other) > len(name_low)):
            continue
        found.setdefault(_key(name), name)
    return found


def consultant_profile(cand: Dict) -> Dict:
    listed = [s.strip() for s in re.split(r"[,;/\n|]", (cand or {}).get("primary_skills") or "") if s.strip()]
    keys = set(_known_in((cand or {}).get("resume_text") or ""))
    keys.update(_key(s) for s in listed)
    return {"keys": keys, "listed": listed, "listed_rx": _pattern(listed, re.I)}


def match(profile: Dict, text: str) -> Dict:
    """{pct (None when the posting names too few skills), hit, missing, asked, no_profile}."""
    asked = _known_in(text)
    if profile["listed_rx"]:
        for m in profile["listed_rx"].finditer(text or ""):
            asked.setdefault(_key(m.group(0)), m.group(0))
    hit = [name for key, name in asked.items() if key in profile["keys"]]
    missing = [name for key, name in asked.items() if key not in profile["keys"]]
    pct: Optional[int] = round(100 * len(hit) / len(asked)) if len(asked) >= MIN_SKILLS else None
    return {"pct": pct if profile["keys"] else None, "hit": hit, "missing": missing, "asked": len(asked), "no_profile": not profile["keys"]}


def job_text(job: Dict, full_text: str = "") -> str:
    return " ".join([job.get("title") or "", full_text or job.get("full_description") or job.get("description") or ""])


def attach(cand: Dict, jobs: List[Dict], read_full_texts) -> None:
    """Puts j["skill_match"] on each job. read_full_texts(ids) -> {id: stored posting}, called in small batches so a
    search never holds thousands of whole postings in memory."""
    profile = consultant_profile(cand)
    batch_size = 200
    jobs = jobs[:MAX_SCORED]
    for start in range(0, len(jobs), batch_size):
        batch = jobs[start:start + batch_size]
        ids = [j["id"] for j in batch if j.get("has_full_description") and j.get("id")]
        full = read_full_texts(ids) if ids else {}
        for j in batch:
            j["skill_match"] = match(profile, job_text(j, full.get(j.get("id")) or ""))
