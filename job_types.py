"""Job type (Full-time / Contract / Part-time) for the Jobs tab filter.

- canonical(text): a portal's or the recruiter's wording -> 'Full-time' | 'Contract' | 'Part-time' | ''.
- classify(job): a saved job's type from its job_type field, else its title / description wording.
  '' = the posting doesn't say - such jobs show under 'All types' only.
- Each portal is asked with its own filter code (portal_code); a portal that can't filter a type is
  skipped (portal_code returns None) and the Jobs tab says so.
"""
import re
from typing import Dict, Optional

TYPES = ("Full-time", "Contract", "Part-time")

_PART = re.compile(r"\bpart[\s-]?time\b", re.I)
_CONTRACT = re.compile(r"\b(contract(?:ual|or)?|c2c|corp[\s-]?to[\s-]?corp|1099|w-?2 contract|temporary|temp)\b", re.I)
_FULL = re.compile(r"\b(full[\s-]?time|permanent|fte|direct hire)\b", re.I)


def canonical(text: Optional[str]) -> str:
    t = (text or "").strip()
    if not t or t.lower() in ("all", "all types", "any"):
        return ""
    if _PART.search(t):
        return "Part-time"
    if _CONTRACT.search(t):
        return "Contract"
    if _FULL.search(t):
        return "Full-time"
    return ""


def classify(job: Dict) -> str:
    """The job's type: its own job_type field first, else the words of its title / description."""
    own = canonical(job.get("job_type"))
    if own:
        return own
    return canonical(f"{job.get('title') or ''} {(job.get('description') or '')[:1500]}")


# portal -> wanted type -> that portal's filter value (None = the portal can't filter that type)
_CODES = {
    "Dice": {"Full-time": "FULLTIME", "Contract": "CONTRACTS", "Part-time": "PARTTIME"},
    "LinkedIn": {"Full-time": "F", "Contract": "C", "Part-time": "P"},
    "Naukri": {"Full-time": "P", "Contract": "C", "Part-time": None},
    "Foundit": {"Full-time": "Full Time", "Contract": None, "Part-time": "Part Time"},
}


def portal_code(portal: str, wanted: str):
    """'' = no filter (All types); a code; or None = this portal can't filter that type."""
    if not wanted:
        return ""
    return _CODES.get(portal, {}).get(wanted, "")


def skip_reason(portal: str, wanted: str) -> str:
    return f"skipped - {portal} can't filter {wanted} jobs"
