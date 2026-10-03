"""Experience required by a job posting, and the Jobs-tab experience filter.

Best source first - nothing is invented:
1. "posting": the portal's own experience field. Naukri / Foundit return one (e.g. "3-6 Yrs"),
   which the India scrapers store in the description as "Experience: <label>".
2. "text": years written in the posting text - the full description when it has been read from the
   posting, else the stored description (e.g. "5+ years", "3-5 years of experience",
   "minimum 7 years").
3. "title": no years anywhere, but the title says Senior / Lead / Principal / Architect / Manager /
   Director (senior) or Junior / Entry level / Intern (junior). A clue, labelled as such.
4. Otherwise "Not stated".

Filter (recruiter enters the consultant's years N): show a job when its minimum is at most N + 1
(one year of stretch - recruiters often submit slightly under the minimum); with no years stated,
a senior title is hidden for N < 6; jobs that state nothing are shown unless the recruiter turns
"include jobs that don't state experience" off.
"""
import re
from typing import Dict, Optional

STRETCH_YEARS = 1
SENIOR_TITLE_MIN_YEARS = 6

_UNIT = r"(?:\+\s*)?(?:yrs?|years?)"
_RANGE_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*\+?\s*(?:-|–|—|to)\s*(\d{1,2})\s*" + _UNIT, re.I)
_PLUS_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*\+\s*(?:yrs?|years?)", re.I)
_MIN_RE = re.compile(r"(?:minimum(?:\s+of)?|at\s+least|min\.?)\s*(\d{1,2})\s*" + _UNIT, re.I)
_EXP_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:yrs?|years?)\s*(?:of\s+)?(?:[a-z/+#.-]+\s+){0,4}?(?:experience|exp)\b", re.I)
_POSTING_LABEL_RE = re.compile(r"Experience:\s*([^.\n]{1,40})")

_SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|principal|staff|architect|manager|director|head of|vp|vice president)\b", re.I)
_JUNIOR_RE = re.compile(r"\b(junior|jr\.?|entry[\s-]level|graduate|intern|internship|trainee|fresher)\b", re.I)


def parse_years(text: str) -> Optional[Dict]:
    """{'min': int, 'max': int|None} from the first experience statement in `text`, or None."""
    if not text:
        return None
    found = []
    for rx, kind in ((_RANGE_RE, "range"), (_MIN_RE, "min"), (_PLUS_RE, "plus"), (_EXP_RE, "exact")):
        for m in rx.finditer(text):
            found.append((m.start(), kind, m))
    for _, kind, m in sorted(found, key=lambda x: x[0]):
        if kind == "range":
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo <= hi <= 40:
                return {"min": lo, "max": hi}
        else:
            n = int(m.group(1))
            if n <= 40:
                return {"min": n, "max": None}
    return None


def title_level(title: str) -> Optional[str]:
    t = title or ""
    if _SENIOR_RE.search(t):
        return "senior"
    if _JUNIOR_RE.search(t):
        return "junior"
    return None


def classify(job: Dict) -> Dict:
    """{'min', 'max', 'source': 'posting'|'text'|'title'|'', 'level', 'label'} for one job row."""
    desc = job.get("description") or ""
    full = job.get("full_description") or ""
    years, source = None, ""
    m = _POSTING_LABEL_RE.search(desc)
    if m and "not specified" not in m.group(1).lower():
        years = parse_years(m.group(1)) or parse_years(m.group(1) + " years")
        source = "posting" if years else ""
    if not years:
        years = parse_years(full) or parse_years(desc)
        source = "text" if years else ""
    level = title_level(job.get("title") or "")
    if years:
        label = f"{years['min']}-{years['max']} yrs" if years.get("max") else f"{years['min']}+ yrs"
        return {"min": years["min"], "max": years.get("max"), "source": source, "level": level, "label": label}
    if level:
        return {"min": None, "max": None, "source": "title", "level": level,
                "label": f"{level.title()} (from title)"}
    return {"min": None, "max": None, "source": "", "level": None, "label": "Not stated"}


def fits(exp: Dict, my_years: Optional[int], include_unstated: bool = True) -> bool:
    """Should a job with experience `exp` be shown for a consultant with `my_years`?"""
    if my_years is None:
        return True
    if exp.get("min") is not None:
        return exp["min"] <= my_years + STRETCH_YEARS
    if exp.get("level") == "senior":
        return my_years >= SENIOR_TITLE_MIN_YEARS
    if exp.get("level") == "junior":
        return True
    return include_unstated


def linkedin_levels(my_years: Optional[int]) -> str:
    """LinkedIn's own experience-level filter (f_E) for a consultant's years: 1 Internship,
    2 Entry level, 3 Associate, 4 Mid-Senior level, 5 Director. '' = no filter."""
    if my_years is None:
        return ""
    if my_years <= 1:
        return "1,2"
    if my_years <= 4:
        return "2,3,4"
    if my_years <= 7:
        return "3,4"
    return "4,5"
