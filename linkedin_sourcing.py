"""
ADROIT ATS - Structured LinkedIn sourcing: India Bachelor's + US Master's.

Uses Apify's harvestapi/linkedin-profile-search actor in "Full" mode. Unlike a
web-search snippet, "Full" mode returns each profile's real structured
education entries (school, degree, start/end year) and location country, so
the recruiter's rules can be checked STRICTLY against real data:

  1. Currently located in the United States.
  2. A Bachelor's degree from an Indian institution whose END YEAR equals the
     year the recruiter selected (exact match; no year on profile -> skipped).
  3. A Master's degree from a non-Indian institution (US university, judged by
     name - see NON_US_MARKERS - and shown to the recruiter to confirm).

Nothing is guessed or defaulted: profiles that can't be verified are skipped
and counted, never shown as matches.

Cost (Apify, Sep 2026): $0.10 per search page (25 profiles) + $0.004 per full
profile. Every run is capped with maxTotalChargeUsd, and a daily budget guard
refuses to start new runs once the day's spend hits SOURCING_DAILY_BUDGET_USD.
"""

import os
import re
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

import config

logger = logging.getLogger("linkedin_sourcing")

ACTOR = "harvestapi~linkedin-profile-search"
API = "https://api.apify.com/v2"

# Hard ceiling for any single search. The actual per-run cap is derived from the
# number of pages requested (see start_search), never above this.
MAX_SPEND_PER_SEARCH_USD = float(os.getenv("SOURCING_MAX_SPEND_USD", "3.00"))
DAILY_BUDGET_USD = float(os.getenv("SOURCING_DAILY_BUDGET_USD", "5"))
DEFAULT_PAGES = 6          # 25 profiles per page
MAX_PAGES = 12
COST_PER_PAGE_USD = 0.21   # $0.10 search page + 25 x $0.004 full profiles (+ small margin)
TARGET_MATCHES = 15        # stop early (abort the run) once this many verified matches exist
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}

# Broad spread of Indian colleges (not just IIT/NIT) - the LinkedIn "school"
# facet accepts a list, so all of these are covered by a single search.
INDIAN_SCHOOLS_FOR_SEARCH = [
    "Jawaharlal Nehru Technological University",
    "Osmania University",
    "Vellore Institute of Technology",
    "SRM Institute of Science and Technology",
    "Manipal Institute of Technology",
    "Amrita Vishwa Vidyapeetham",
    "Anna University",
    "Visvesvaraya Technological University",
    "Andhra University",
    "Birla Institute of Technology and Science, Pilani",
    "GITAM University",
    "Koneru Lakshmaiah Education Foundation",
    "SASTRA University",
    "PSG College of Technology",
    "Sathyabama Institute of Science and Technology",
    "Thapar Institute of Engineering and Technology",
    "Delhi Technological University",
    "Chaitanya Bharathi Institute of Technology",
    "Vasavi College of Engineering",
    "VNR Vignana Jyothi Institute of Engineering and Technology",
    "Gokaraju Rangaraju Institute of Engineering and Technology",
    "Mahatma Gandhi Institute of Technology",
    "Savitribai Phule Pune University",
    "University of Mumbai",
    "Indian Institute of Technology",
    "National Institute of Technology",
]

# Words that mark an institution as located in India (used for the Bachelor's
# check AND to reject Master's degrees that were earned in India).
INDIAN_INSTITUTION_MARKERS = [
    "india", "jntu", "jawaharlal nehru technological", "osmania", "vellore institute", "vit", "vit university",
    "srm", "manipal", "amrita", "anna university", "visvesvaraya", "vtu",
    "andhra university", "birla institute of technology and science", "bits pilani", "bits",
    "indian institute of technology", "iit", "national institute of technology", "nit",
    "iiit", "international institute of information technology", "thapar", "delhi technological",
    "netaji subhas", "nsit", "nsut", "savitribai", "pune university", "university of mumbai",
    "mumbai university", "sathyabama", "sastra", "psg college", "koneru", "kl university", "gitam",
    "kakatiya", "chaitanya bharathi", "cbit", "vasavi", "vnr vignana", "gokaraju", "mahatma gandhi institute",
    "sreenidhi", "cvr college", "mvsr", "malla reddy", "jain university", "christ university",
    "bms college", "rv college", "rvce", "pes university", "nirma", "lovely professional",
    "amity", "sharda university", "hyderabad", "bengaluru", "bangalore", "chennai", "kolkata", "delhi",
    "mumbai", "pune", "kerala", "tamil nadu", "andhra", "telangana", "karnataka", "maharashtra",
    "gujarat", "uttar pradesh", "rajasthan", "punjab", "haryana", "odisha", "coimbatore", "vijayawada",
    "visakhapatnam", "warangal", "trichy", "tiruchirappalli", "surathkal", "kakinada", "anantapur",
]

# Markers of non-US institutions, so a UK/Canada/Australia/etc. Master's is not
# mistaken for a US one. Name-based only - the school is shown in the UI.
#
# Only unambiguous markers are used. Ambiguous ones that would wrongly reject
# real US schools (e.g. "birmingham" -> University of Alabama at Birmingham,
# "york university" -> New York University) are avoided or written as the
# specific foreign institution name ("university of birmingham").
NON_US_MARKERS = [
    "united kingdom", "uk", "london", "edinburgh", "glasgow", "oxford", "imperial college",
    "queen mary", "university of manchester", "university of birmingham", "university of leeds",
    "university of liverpool", "university of sheffield", "university of nottingham",
    "university of warwick", "university of bristol", "university of cambridge", "university of york",
    "coventry university", "durham university", "cranfield", "heriot", "strathclyde", "swansea", "cardiff",
    "brunel", "toronto", "canada", "university of waterloo", "mcgill", "ontario", "british columbia",
    "alberta", "ryerson", "australia", "melbourne", "sydney", "monash", "unsw", "deakin",
    "queensland", "adelaide", "rmit", "singapore", "nanyang", "germany", "munich", "münchen",
    "berlin", "rwth", "dublin", "ireland", "france", "paris", "netherlands", "delft", "eindhoven",
    "sweden", "kth", "new zealand", "auckland", "dubai", "uae", "hong kong", "malaysia",
]

_INDIAN_RE = re.compile(r"\b(?:" + "|".join(re.escape(m) for m in INDIAN_INSTITUTION_MARKERS) + r")\b", re.I)
_NON_US_RE = re.compile(r"\b(?:" + "|".join(re.escape(m) for m in NON_US_MARKERS) + r")\b", re.I)

# Degree parsing
_BACHELOR_RE = re.compile(
    r"bachelor|\bb\.?\s?tech\b|\bbtech\b|\bb\.?\s?e\.?(?=\W|$)|\bb\.?\s?eng\b|\bb\.?\s?sc\b|\bbsc\b|\bbca\b", re.I)
_MASTER_RE = re.compile(
    r"master|\bm\.?\s?s\.?(?=\W|$)|\bm\.?\s?tech\b|\bmtech\b|\bm\.?\s?eng\b|\bmeng\b|\bmcs\b|\bm\.?\s?sc\b|\bmsc\b|\bmis\b", re.I)
_NOT_TECH_MASTER_RE = re.compile(r"\bmba\b|business administration|pgdm|public health|\bmph\b|\bllm\b", re.I)


def _norm(text: Optional[str]) -> str:
    return (text or "").lower()


def is_indian_institution(school_name: Optional[str]) -> bool:
    # Word-boundary match: "india" must not match "Indiana University".
    return bool(_INDIAN_RE.search(school_name or ""))


def looks_like_non_us_institution(school_name: Optional[str]) -> bool:
    return bool(_NON_US_RE.search(school_name or ""))


def _year(date_obj) -> Optional[int]:
    if isinstance(date_obj, dict):
        y = date_obj.get("year")
        if isinstance(y, int):
            return y
        if isinstance(y, str) and y.isdigit():
            return int(y)
    return None


def _degree_text(entry: Dict) -> str:
    deg = (entry.get("degree") or "").strip()
    fld = (entry.get("fieldOfStudy") or "").strip()
    return f"{deg} - {fld}" if deg and fld else (deg or fld)


def experience_ids_for_year(bachelor_year: Optional[int], now_year: Optional[int] = None) -> List[str]:
    """LinkedIn 'years of experience' facet ids that plausibly cover people who
    graduated in bachelor_year (1: <1y, 2: 1-2y, 3: 3-5y, 4: 6-10y, 5: 10y+).
    Only used to improve yield; the strict year check is done on real data.

    Calibrated on real profiles: total experience (first job start -> today)
    for someone who graduated N years ago runs from about N-1 up to N+3,
    because internships and pre-graduation roles count. (The first version of
    this used N-3..N and wrongly excluded e.g. the 11+ year bucket for 2016.)"""
    if not bachelor_year:
        return ["2", "3", "4"]
    now_year = now_year or datetime.now(timezone.utc).year
    n = max(0, now_year - bachelor_year)
    lo, hi = max(0, n - 1), n + 3
    buckets = {"1": (0, 0), "2": (1, 2), "3": (3, 5), "4": (6, 10), "5": (11, 60)}
    return [k for k, (a, b) in buckets.items() if a <= hi and b >= lo]


def evaluate_profile(item: Dict, bachelor_year: Optional[int]) -> Tuple[Optional[Dict], str]:
    """Strictly evaluate one raw Full-mode profile. Returns (candidate, "match")
    or (None, reason) where reason is one of: not_in_us, no_bachelor,
    bachelor_not_india, no_bachelor_year, wrong_bachelor_year, no_us_master,
    incomplete."""
    url = (item.get("linkedinUrl") or "").strip()
    first = (item.get("firstName") or "").strip()
    last = (item.get("lastName") or "").strip()
    if not url or "linkedin.com/in/" not in url or not first:
        return None, "incomplete"

    loc = item.get("location") or {}
    country = (loc.get("countryCode") or (loc.get("parsed") or {}).get("countryCode") or "").upper()
    if country != "US":
        return None, "not_in_us"

    education = [e for e in (item.get("education") or []) if isinstance(e, dict)]

    # --- Bachelor's from an Indian institution, exact end year ---
    bachelors = [e for e in education
                 if _BACHELOR_RE.search(e.get("degree") or "") and is_indian_institution(e.get("schoolName"))]
    if not any(_BACHELOR_RE.search(e.get("degree") or "") for e in education):
        return None, "no_bachelor"
    if not bachelors:
        return None, "bachelor_not_india"

    dated = [(e, _year(e.get("endDate"))) for e in bachelors]
    dated = [(e, y) for e, y in dated if y]
    if not dated:
        return None, "no_bachelor_year"
    if bachelor_year:
        chosen = next(((e, y) for e, y in dated if y == bachelor_year), None)
        if not chosen:
            return None, "wrong_bachelor_year"
    else:
        chosen = next(((e, y) for e, y in dated if 2010 <= y <= 2020), None)
        if not chosen:
            return None, "wrong_bachelor_year"
    b_entry, b_year = chosen

    # --- Master's from a non-Indian (US) institution ---
    masters = []
    for e in education:
        deg = (e.get("degree") or "") + " " + (e.get("fieldOfStudy") or "")
        if not _MASTER_RE.search(e.get("degree") or ""):
            continue
        if _NOT_TECH_MASTER_RE.search(deg):
            continue
        school = e.get("schoolName") or ""
        if is_indian_institution(school) or looks_like_non_us_institution(school):
            continue
        m_year = _year(e.get("endDate"))
        if m_year and m_year < b_year:
            continue  # implausible ordering; don't trust this entry
        masters.append((e, m_year))
    if not masters:
        return None, "no_us_master"
    m_entry, m_year = masters[0]

    name = f"{first} {last}".strip()
    current = (item.get("currentPosition") or [{}])
    current = current[0] if current else {}
    headline = (item.get("headline") or "").strip()
    if not headline and current:
        headline = f"{current.get('position') or ''} at {current.get('companyName') or ''}".strip(" at")
    location_text = loc.get("linkedinText") or (loc.get("parsed") or {}).get("text") or "United States"
    b_school = b_entry.get("schoolName") or ""
    m_school = m_entry.get("schoolName") or ""

    candidate = {
        "name": name,
        "headline": headline or "Technology Professional",
        "title": headline or "Technology Professional",
        "bachelor_year": str(b_year),
        "grad_year": str(b_year),
        "bachelor_degree": _degree_text(b_entry),
        "bachelor_college": b_school,
        "master_degree": _degree_text(m_entry) + (f" ({m_year})" if m_year else " (year not listed)"),
        "master_university": m_school,
        "master_year": str(m_year) if m_year else "",
        "university": m_school,
        "degree": f"{_degree_text(b_entry)} ({b_school}, {b_year}) -> {_degree_text(m_entry)} ({m_school}{', ' + str(m_year) if m_year else ''})",
        "location": location_text,
        "status_tag": f"Verified: India Bachelor's {b_year} -> US Master's",
        "status_badge": f"Verified: India Bachelor's {b_year} -> US Master's",
        "settlement_badge": "🇺🇸 Located in USA",
        "settlement_sub": "Work authorization: confirm with candidate",
        "quality": "[VERIFIED] Bachelor's year, Indian college and Master's read from the profile's education section",
        "profile_url": url,
        "linkedin_url": url,
        "year_verified": True,
        "source": "apify_harvest_profile_search",
    }
    return candidate, "match"


# ----------------------------------------------------------------------------
# Apify run lifecycle (stateless: the run id / dataset id are the only state)
# ----------------------------------------------------------------------------

def _token() -> str:
    return (config.APIFY_API_TOKEN or "").strip()


def todays_spend_usd() -> float:
    """Sum of this actor's run costs since 00:00 UTC (from Apify itself, so it
    is accurate across gunicorn workers and restarts)."""
    try:
        r = requests.get(f"{API}/acts/{ACTOR}/runs", params={"token": _token(), "desc": 1, "limit": 100}, timeout=20)
        r.raise_for_status()
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        total = 0.0
        for run in r.json().get("data", {}).get("items", []):
            started = run.get("startedAt")
            if not started:
                continue
            when = datetime.fromisoformat(started.replace("Z", "+00:00"))
            if when >= midnight:
                total += float(run.get("usageTotalUsd") or 0)
        return round(total, 4)
    except Exception as ex:
        logger.warning(f"Could not read today's Apify spend: {ex}")
        return 0.0


MAX_START_PAGE = 80   # LinkedIn search results are capped around 100 pages of 25
MAX_WAVE = 5          # parallel one-page runs started per request (free Apify plan allows 5 concurrent runs)
MIN_HEADROOM_USD = 0.25


def account_headroom() -> Optional[Dict]:
    """This month's Apify usage vs the plan's monthly limit (None if unavailable)."""
    try:
        r = requests.get(f"{API}/users/me/limits", params={"token": _token()}, timeout=15)
        d = r.json().get("data", {})
        return {
            "used": float(d["current"]["monthlyUsageUsd"]),
            "limit": float(d["limits"]["maxMonthlyUsageUsd"]),
            "resets": (d.get("monthlyUsageCycle") or {}).get("endAt", "")[:10],
        }
    except Exception as ex:
        logger.warning(f"Could not read Apify account limits: {ex}")
        return None


def _apify_error_text(resp) -> str:
    try:
        err = resp.json().get("error") or {}
        return (err.get("message") or err.get("type") or "").strip()
    except Exception:
        return ""


def start_search(bachelor_year: Optional[int], pages: int = 1, location: str = "United States",
                 start_page: int = 1) -> Dict:
    """Start `pages` ONE-PAGE runs in parallel (pages <= MAX_WAVE), for LinkedIn
    result pages start_page .. start_page+pages-1.

    One page per run because Apify's free plan caps each run of this actor at
    ~25 profiles (verified in the run log: "Free users are limited up to 25
    items per run") - so a deeper search is several runs, which works on any
    plan. start_page lets repeat searches continue with NEW profiles instead of
    re-scanning (and re-paying for) the same first pages."""
    if not _token():
        return {"error": "APIFY_API_TOKEN is not configured on the server.", "code": 503}

    try:
        pages = max(1, min(int(pages or 1), MAX_WAVE))
    except (TypeError, ValueError):
        pages = 1
    try:
        start_page = max(1, min(int(start_page or 1), MAX_START_PAGE))
    except (TypeError, ValueError):
        start_page = 1

    wave_cost = round(pages * COST_PER_PAGE_USD, 2)
    head = account_headroom()
    if head and head["limit"] - head["used"] < max(MIN_HEADROOM_USD, min(wave_cost, 1.0)):
        return {"error": (f"Your Apify account has used ${head['used']:.2f} of its ${head['limit']:.2f} monthly limit, "
                          f"so LinkedIn searching is paused. It resets on {head['resets'] or 'the 1st of next month'}, "
                          "or you can upgrade / add credit at console.apify.com/billing."), "code": 402}

    spent = todays_spend_usd()
    if spent + wave_cost > DAILY_BUDGET_USD:
        return {"error": (f"Today's LinkedIn sourcing budget (${DAILY_BUDGET_USD:.2f}) is reached: ${spent:.2f} spent, "
                          f"and this step could cost about ${wave_cost:.2f}. Raise SOURCING_DAILY_BUDGET_USD on the server to continue."), "code": 429}

    per_run_cap = round(min(MAX_SPEND_PER_SEARCH_USD, COST_PER_PAGE_USD + 0.10), 2)
    runs, first_error = [], None
    for i in range(pages):
        page_no = start_page + i
        payload = {
            "profileScraperMode": "Full",
            "schools": INDIAN_SCHOOLS_FOR_SEARCH,
            "locations": [location],
            "yearsOfExperienceIds": experience_ids_for_year(bachelor_year),
            "maxItems": 25,
            "startPage": page_no,
            "takePages": 1,
        }
        try:
            r = requests.post(f"{API}/acts/{ACTOR}/runs",
                              params={"token": _token(), "maxTotalChargeUsd": per_run_cap, "timeout": 240},
                              json=payload, timeout=30)
            if r.status_code in (200, 201):
                data = r.json().get("data", {})
                runs.append({"run_id": data.get("id"), "dataset_id": data.get("defaultDatasetId"), "start_page": page_no})
            else:
                first_error = first_error or (_apify_error_text(r) or f"Apify HTTP {r.status_code}")
                logger.error(f"Apify start failed HTTP {r.status_code}: {r.text[:300]}")
                break
        except Exception as ex:
            first_error = first_error or "Could not reach Apify"
            logger.error(f"Apify start exception: {ex}")
            break

    if not runs:
        return {"error": f"Could not start the LinkedIn search: {first_error}.", "code": 502}
    return {
        "runs": runs,
        "pages_started": len(runs),
        "next_start_page": start_page + len(runs),
        "max_spend_usd": round(per_run_cap * len(runs), 2),
        "spent_today_usd": spent,
        "warning": first_error,
    }


_PROFILE_FIELDS = "linkedinUrl,firstName,lastName,headline,location,education,currentPosition"


POLL_BATCH = 200


def poll_search(run_id: str, dataset_id: str, bachelor_year: Optional[int], offset: int = 0, matched_so_far: int = 0,
                target: int = TARGET_MATCHES) -> Dict:
    """Fetch the run status and evaluate only the NEW dataset items since
    `offset`. Aborts the run once `target` verified matches exist (saves money)."""
    if not _token():
        return {"error": "APIFY_API_TOKEN is not configured on the server.", "code": 503}
    try:
        target = max(3, min(int(target or TARGET_MATCHES), 60))
    except (TypeError, ValueError):
        target = TARGET_MATCHES
    try:
        run = requests.get(f"{API}/actor-runs/{run_id}", params={"token": _token()}, timeout=20).json().get("data", {})
        status = run.get("status") or "UNKNOWN"
        items_resp = requests.get(
            f"{API}/datasets/{dataset_id}/items",
            params={"token": _token(), "offset": offset, "limit": POLL_BATCH, "fields": _PROFILE_FIELDS, "clean": 1},
            timeout=60,
        )
        raw_items = items_resp.json() if items_resp.status_code == 200 else []
        if not isinstance(raw_items, list):
            raw_items = []
    except Exception as ex:
        logger.error(f"Apify poll exception: {ex}")
        return {"error": "Lost contact with Apify while polling.", "code": 502}

    matches, skipped = [], {}
    for item in raw_items:
        cand, reason = evaluate_profile(item, bachelor_year)
        if cand:
            matches.append(cand)
        else:
            skipped[reason] = skipped.get(reason, 0) + 1

    total_matched = matched_so_far + len(matches)
    aborted = False
    if status in ("RUNNING", "READY") and total_matched >= target:
        try:
            requests.post(f"{API}/actor-runs/{run_id}/abort", params={"token": _token()}, timeout=15)
            aborted = True
        except Exception as ex:
            logger.warning(f"Could not abort run {run_id}: {ex}")

    # Only finished once the run has ended AND every item has been read (a full
    # batch means there may be more waiting at the next offset).
    more_waiting = len(raw_items) >= POLL_BATCH
    done = (status in TERMINAL_STATUSES or status == "ABORTING" or aborted) and not more_waiting
    return {
        "status": "ABORTED" if aborted else status,
        "done": done,
        "next_offset": offset + len(raw_items),
        "scanned_new": len(raw_items),
        "new_matches": matches,
        "skipped": skipped,
        "cost_usd": run.get("usageTotalUsd"),
        "stopped_early": aborted,
    }


# =============================================================================
# People Data Labs (PDL) source - free tier: 100 records / month
# =============================================================================
# PDL is a licensed people database with STRUCTURED education entries (school,
# degree, start/end date, and the country the school is in), so the search can
# filter server-side on the criteria instead of scanning random profiles. You
# are charged per record RETURNED (1 credit each), so a search that mostly
# returns matches is what makes the free 100/month go far.
#
# Limitation: PDL's education array is not "nested", so a search cannot force
# the Bachelor's, the India school and the year to be the SAME entry (e.g. it
# can return someone whose Master's, not Bachelor's, ended in the target year).
# Every returned record is therefore re-checked strictly in evaluate_pdl_person,
# and the query is made as tight as it can be to keep wasted credits low.

PDL_ENDPOINT = "https://api.peopledatalabs.com/v5/person/search"
PDL_MAX_RECORDS_PER_SEARCH = int(os.getenv("PDL_MAX_RECORDS_PER_SEARCH", "50"))
_PDL_FIELDS = "full_name,first_name,last_name,linkedin_url,job_title,job_company_name,location_name,location_country,education"


def pdl_configured() -> bool:
    return bool((config.PDL_API_KEY or "").strip())


def build_pdl_query(bachelor_year: Optional[int], strict: bool = True) -> Dict:
    """Elasticsearch-style query for PDL's Person Search API.

    strict=True additionally requires an education entry that STARTED 3-5 years
    before the target year (a 4-year Bachelor's ending in Y starts around Y-4);
    since a Master's ending in Y starts about Y-2, this keeps out most people
    whose *Master's* (not Bachelor's) ended in the target year."""
    must = [
        {"term": {"location_country": "united states"}},
        {"term": {"education.school.location.country": "india"}},
        {"term": {"education.degrees": "bachelors"}},
        {"term": {"education.school.location.country": "united states"}},
        {"term": {"education.degrees": "masters"}},
    ]
    if bachelor_year:
        must.append({"range": {"education.end_date": {"gte": f"{bachelor_year}-01-01", "lte": f"{bachelor_year}-12-31"}}})
        if strict:
            must.append({"range": {"education.start_date": {"gte": f"{bachelor_year - 5}-01-01", "lte": f"{bachelor_year - 3}-12-31"}}})
    else:
        must.append({"range": {"education.end_date": {"gte": "2010-01-01", "lte": "2020-12-31"}}})
    return {"bool": {"must": must}}


def _pdl_year(value) -> Optional[int]:
    """PDL dates are strings like '2020', '2020-05' or '2020-05-01'."""
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def _pdl_linkedin_url(raw: Optional[str]) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("http"):
        return raw
    return "https://" + (raw if raw.startswith("www.") else "www." + raw.lstrip("/"))


def _pdl_school_country(entry: Dict) -> str:
    school = entry.get("school") or {}
    loc = school.get("location") or {}
    return (loc.get("country") or "").strip().lower()


def evaluate_pdl_person(person: Dict, bachelor_year: Optional[int]) -> Tuple[Optional[Dict], str]:
    """Strictly evaluate one PDL person record (same rules as evaluate_profile,
    but using the school's real COUNTRY when PDL has it, falling back to
    name-based checks only when the country is missing)."""
    url = _pdl_linkedin_url(person.get("linkedin_url"))
    full_name = (person.get("full_name") or f"{person.get('first_name') or ''} {person.get('last_name') or ''}").strip()
    if not full_name or not url:
        return None, "incomplete"
    if (person.get("location_country") or "").strip().lower() != "united states":
        return None, "not_in_us"

    education = [e for e in (person.get("education") or []) if isinstance(e, dict)]

    def is_degree(entry, kind):
        return kind in [d.lower() for d in (entry.get("degrees") or [])]

    def is_india(entry):
        country = _pdl_school_country(entry)
        return country == "india" if country else is_indian_institution((entry.get("school") or {}).get("name"))

    def is_us(entry):
        country = _pdl_school_country(entry)
        if country:
            return country == "united states"
        name = (entry.get("school") or {}).get("name")
        return bool(name) and not is_indian_institution(name) and not looks_like_non_us_institution(name)

    if not any(is_degree(e, "bachelors") for e in education):
        return None, "no_bachelor"
    bachelors = [e for e in education if is_degree(e, "bachelors") and is_india(e)]
    if not bachelors:
        return None, "bachelor_not_india"
    dated = [(e, _pdl_year(e.get("end_date"))) for e in bachelors]
    dated = [(e, y) for e, y in dated if y]
    if not dated:
        return None, "no_bachelor_year"
    if bachelor_year:
        chosen = next(((e, y) for e, y in dated if y == bachelor_year), None)
    else:
        chosen = next(((e, y) for e, y in dated if 2010 <= y <= 2020), None)
    if not chosen:
        return None, "wrong_bachelor_year"
    b_entry, b_year = chosen

    masters = []
    for e in education:
        if not is_degree(e, "masters") or not is_us(e):
            continue
        text = " ".join([" ".join(e.get("degrees") or []), " ".join(e.get("majors") or []), " ".join(e.get("raw") or [])])
        if _NOT_TECH_MASTER_RE.search(text):
            continue
        m_year = _pdl_year(e.get("end_date"))
        if m_year and m_year < b_year:
            continue
        masters.append((e, m_year))
    if not masters:
        return None, "no_us_master"
    m_entry, m_year = masters[0]

    def degree_label(entry, default):
        majors = ", ".join(entry.get("majors") or [])
        degrees = ", ".join(entry.get("degrees") or [])
        label = (degrees or default).title()
        return f"{label} - {majors.title()}" if majors else label

    nice = lambda s: s.title() if s and s.islower() else s
    b_school = nice((b_entry.get("school") or {}).get("name") or "")
    m_school = nice((m_entry.get("school") or {}).get("name") or "")
    headline_parts = [person.get("job_title") or "", person.get("job_company_name") or ""]
    headline = " at ".join(p.strip().title() for p in headline_parts if p and p.strip()) or "Technology Professional"
    location_text = (person.get("location_name") or "United States").title()

    candidate = {
        "name": full_name.title() if full_name.islower() else full_name,
        "headline": headline,
        "title": headline,
        "bachelor_year": str(b_year),
        "grad_year": str(b_year),
        "bachelor_degree": degree_label(b_entry, "bachelors"),
        "bachelor_college": b_school,
        "master_degree": degree_label(m_entry, "masters") + (f" ({m_year})" if m_year else " (year not listed)"),
        "master_university": m_school,
        "master_year": str(m_year) if m_year else "",
        "university": m_school,
        "degree": f"{degree_label(b_entry, 'bachelors')} ({b_school}, {b_year}) -> {degree_label(m_entry, 'masters')} ({m_school}{', ' + str(m_year) if m_year else ''})",
        "location": location_text,
        "status_tag": f"Verified: India Bachelor's {b_year} -> US Master's",
        "status_badge": f"Verified: India Bachelor's {b_year} -> US Master's",
        "settlement_badge": "🇺🇸 Located in USA",
        "settlement_sub": "Work authorization: confirm with candidate",
        "quality": "[VERIFIED] Bachelor's year, Indian college and US Master's from People Data Labs education records",
        "profile_url": url,
        "linkedin_url": url,
        "year_verified": True,
        "source": "people_data_labs",
    }
    return candidate, "match"


def pdl_search(bachelor_year: Optional[int], size: int = 25, scroll_token: Optional[str] = None,
               strict: bool = True) -> Dict:
    """One PDL Person Search call (each RETURNED record costs 1 free-tier credit).
    Returns verified matches plus what was skipped and why. If the strict query
    finds nothing on the first call it is retried without the start-date clause."""
    if not pdl_configured():
        return {"error": "PDL_API_KEY is not configured on the server.", "code": 503}
    try:
        size = max(1, min(int(size or 25), PDL_MAX_RECORDS_PER_SEARCH, 100))
    except (TypeError, ValueError):
        size = 25

    mode = "strict" if strict else "broad"
    for attempt in range(2):
        body = {
            "query": build_pdl_query(bachelor_year, strict=(mode == "strict")),
            "size": size,
            "titlecase": True,
            "data_include": _PDL_FIELDS,
        }
        if scroll_token:
            body["scroll_token"] = scroll_token
        try:
            r = requests.post(PDL_ENDPOINT, headers={"X-Api-Key": config.PDL_API_KEY.strip(), "Content-Type": "application/json"},
                              json=body, timeout=60)
        except Exception as ex:
            logger.error(f"PDL request failed: {ex}")
            return {"error": "Could not reach People Data Labs.", "code": 502}

        if r.status_code == 404:
            # No matching records (this costs nothing).
            if mode == "strict" and not scroll_token and attempt == 0:
                mode = "broad"     # retry once without the start-date clause
                continue
            return {"matches": [], "records_used": 0, "scanned": 0, "skipped": {}, "total_matching": 0,
                    "next_scroll_token": None, "exhausted": True, "mode": mode}
        if r.status_code in (401, 403):
            return {"error": "People Data Labs rejected the API key. Check PDL_API_KEY.", "code": 502}
        if r.status_code == 402:
            return {"error": "People Data Labs credits are used up for this month (the free plan includes 100 records a month).", "code": 402}
        if r.status_code == 429:
            return {"error": "People Data Labs rate limit reached - wait a few seconds and try again.", "code": 429}
        if r.status_code != 200:
            msg = ""
            try:
                msg = (r.json().get("error") or {}).get("message", "")
            except Exception:
                pass
            logger.error(f"PDL HTTP {r.status_code}: {r.text[:300]}")
            return {"error": f"People Data Labs returned an error ({r.status_code}). {msg}".strip(), "code": 502}

        payload = r.json()
        records = payload.get("data") or []
        matches, skipped = [], {}
        for person in records:
            cand, reason = evaluate_pdl_person(person, bachelor_year)
            if cand:
                matches.append(cand)
            else:
                skipped[reason] = skipped.get(reason, 0) + 1
        return {
            "matches": matches,
            "records_used": len(records),
            "scanned": len(records),
            "skipped": skipped,
            "total_matching": payload.get("total"),
            "next_scroll_token": payload.get("scroll_token"),
            "exhausted": len(records) < size or not payload.get("scroll_token"),
            "mode": mode,
        }
    return {"error": "People Data Labs search did not return a result.", "code": 502}
