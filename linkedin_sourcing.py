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

MAX_SPEND_PER_SEARCH_USD = float(os.getenv("SOURCING_MAX_SPEND_USD", "0.75"))
DAILY_BUDGET_USD = float(os.getenv("SOURCING_DAILY_BUDGET_USD", "5"))
DEFAULT_PAGES = 3          # 25 profiles per page
TARGET_MATCHES = 12        # stop early (abort the run) once this many verified matches exist
RUN_TIMEOUT_SECS = 300
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
    Only used to improve yield; the strict year check is done on real data."""
    if not bachelor_year:
        return ["2", "3", "4"]
    now_year = now_year or datetime.now(timezone.utc).year
    n = max(0, now_year - bachelor_year)
    lo, hi = max(0, n - 3), n
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


def start_search(bachelor_year: Optional[int], pages: int = DEFAULT_PAGES, location: str = "United States") -> Dict:
    if not _token():
        return {"error": "APIFY_API_TOKEN is not configured on the server.", "code": 503}
    spent = todays_spend_usd()
    if spent >= DAILY_BUDGET_USD:
        return {"error": f"Daily LinkedIn sourcing budget reached (${spent:.2f} of ${DAILY_BUDGET_USD:.2f}). Try again tomorrow or raise SOURCING_DAILY_BUDGET_USD.", "code": 429}

    pages = max(1, min(int(pages or DEFAULT_PAGES), 4))
    payload = {
        "profileScraperMode": "Full",
        "schools": INDIAN_SCHOOLS_FOR_SEARCH,
        "locations": [location],
        "yearsOfExperienceIds": experience_ids_for_year(bachelor_year),
        "maxItems": pages * 25,
        "takePages": pages,
    }
    try:
        r = requests.post(
            f"{API}/acts/{ACTOR}/runs",
            params={"token": _token(), "maxTotalChargeUsd": MAX_SPEND_PER_SEARCH_USD, "timeout": RUN_TIMEOUT_SECS},
            json=payload, timeout=30,
        )
        if r.status_code not in (200, 201):
            logger.error(f"Apify start failed HTTP {r.status_code}: {r.text[:300]}")
            return {"error": f"Could not start the LinkedIn search (Apify HTTP {r.status_code}).", "code": 502}
        data = r.json().get("data", {})
        return {
            "run_id": data.get("id"),
            "dataset_id": data.get("defaultDatasetId"),
            "max_spend_usd": MAX_SPEND_PER_SEARCH_USD,
            "spent_today_usd": spent,
            "pages": pages,
        }
    except Exception as ex:
        logger.error(f"Apify start exception: {ex}")
        return {"error": "Could not reach Apify to start the search.", "code": 502}


_PROFILE_FIELDS = "linkedinUrl,firstName,lastName,headline,location,education,currentPosition"


def poll_search(run_id: str, dataset_id: str, bachelor_year: Optional[int], offset: int = 0, matched_so_far: int = 0) -> Dict:
    """Fetch the run status and evaluate only the NEW dataset items since
    `offset`. Aborts the run once enough verified matches exist (saves money)."""
    if not _token():
        return {"error": "APIFY_API_TOKEN is not configured on the server.", "code": 503}
    try:
        run = requests.get(f"{API}/actor-runs/{run_id}", params={"token": _token()}, timeout=20).json().get("data", {})
        status = run.get("status") or "UNKNOWN"
        items_resp = requests.get(
            f"{API}/datasets/{dataset_id}/items",
            params={"token": _token(), "offset": offset, "limit": 100, "fields": _PROFILE_FIELDS, "clean": 1},
            timeout=45,
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
    if status in ("RUNNING", "READY") and total_matched >= TARGET_MATCHES:
        try:
            requests.post(f"{API}/actor-runs/{run_id}/abort", params={"token": _token()}, timeout=15)
            aborted = True
        except Exception as ex:
            logger.warning(f"Could not abort run {run_id}: {ex}")

    done = status in TERMINAL_STATUSES or status == "ABORTING" or aborted
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
