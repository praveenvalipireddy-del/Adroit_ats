"""Indeed job listings (USA + India) via the valig/indeed-jobs-scraper Apify actor.

- One run = one keyword search on indeed.com (US) or in.indeed.com (India), newest first.
- Cost (Apify store, Oct 2026): $0.0001 per job + $0.001 per run - 25 jobs cost about $0.0035.
  Paid, so it is searched only when the recruiter picks Indeed (or "All" in the India market,
  which is already paid) - never by the free automatic US searches.
- Indeed's actor has no job-type filter: a Contract search adds "contract" to the keywords and each
  job keeps the type Indeed states (jobTypes), so the Jobs-tab type filter still works on it.
- Read tolerantly (nested employer / location / salary objects, or flat fields) - actors rename fields.
"""
import logging
from typing import Dict, List

import india_job_scrapers as ij
from us_job_scrapers import extract_email_from_text

logger = logging.getLogger("indeed_jobs")

ACTOR = "valig~indeed-jobs-scraper"
SOURCE = "Indeed (Live)"
PRICE_PER_JOB = 0.0001
PRICE_PER_RUN = 0.001


def _salary(it: Dict) -> str:
    s = it.get("baseSalary") or it.get("salary") or {}
    if isinstance(s, str):
        return s if any(c.isdigit() for c in s) else ""
    lo, hi = s.get("min"), s.get("max")
    if not lo and not hi:
        return ""
    cur = {"USD": "$", "INR": "₹"}.get(s.get("currencyCode") or "", (s.get("currencyCode") or "") + " ")
    unit = {"HOUR": "/hr", "YEAR": "/yr", "MONTH": "/month", "DAY": "/day", "WEEK": "/week"}.get(s.get("unitOfWork") or "", "")
    fmt = lambda v: f"{cur}{v:,.0f}" if isinstance(v, (int, float)) else f"{cur}{v}"  # noqa: E731
    return (f"{fmt(lo)} - {fmt(hi)}" if lo and hi and lo != hi else fmt(lo or hi)) + unit


def _location(it: Dict, default: str) -> str:
    loc = it.get("location")
    if isinstance(loc, dict):
        parts = [loc.get("city"), loc.get("admin1Code") if (loc.get("countryCode") or "").upper() == "US" else None]
        text = ", ".join(p for p in parts if p)
        return text or loc.get("countryName") or default
    return ij._first(it, "location", "formattedLocation", default=default)


def _text(v) -> str:
    if isinstance(v, dict):
        return (v.get("text") or "").strip()
    return str(v or "").strip()


def scrape_indeed(keyword: str, location: str = "United States", country: str = "United States", limit: int = 25,
                  job_type: str = "") -> List[Dict]:
    india = country.lower() == "india"
    loc = "" if location.strip().lower() in ("", "united states", "usa", "us", "india", "all india") else location.strip()
    payload = {"country": "in" if india else "us",
               "title": keyword + (" contract" if job_type == "Contract" and "contract" not in keyword.lower() else ""),
               "location": loc, "limit": limit, "datePosted": "3" if india else "1"}
    items, err = ij._run_actor_sync(ACTOR, payload)
    ij.LAST_ERROR["Indeed"] = err or ""
    if err:
        logger.error(f"Indeed scraping error: {err}")
        return []
    jobs, dropped = [], 0
    for idx, it in enumerate(items):
        title = ij._first(it, "title", "jobTitle", "positionName")
        key = ij._first(it, "key", "jobKey", "id")
        url = ij._first(it, "url", "jobUrl", "link") or (f"https://{'in' if india else 'www'}.indeed.com/viewjob?jk={key}" if key else "")
        if not title or not url:
            dropped += 1
            continue
        employer = it.get("employer") if isinstance(it.get("employer"), dict) else {}
        company = employer.get("name") or ij._first(it, "company", "companyName", default="Employer")
        types = it.get("jobTypes")
        types = ", ".join(str(v) for v in types.values()) if isinstance(types, dict) else ij._first(it, "jobType", "jobTypes")
        desc = _text(it.get("description")) or ij._first(it, "descriptionText", "snippet")
        where = _location(it, "India" if india else "United States")
        jobs.append({
            "title": title,
            "company": company,
            "location": where,
            "job_type": types,
            "salary": _salary(it),                       # only what Indeed states - never invented
            "source": SOURCE,
            "url": url,
            "recruiter_email": extract_email_from_text(desc) or "",
            "description": (f"Posted {ij._first(it, 'datePublished', 'dateOnIndeed', default='recently')[:10]} on Indeed: "
                            f"{title} at {company} ({where}).\n\n{desc[:6000]}").strip(),
            "matched_skills": keyword,
            "match_score": 95 - idx,
            "is_24h": 1,
            "country": "India" if india else "United States",
        })
    if dropped:
        logger.warning(f"Indeed: {dropped} of {len(items)} results had no title or link; fields seen: {sorted(items[0].keys())[:30]}")
        if not jobs:
            ij.LAST_ERROR["Indeed"] = (f"Indeed returned {len(items)} jobs but in a format this app doesn't read yet "
                                       "(the scraper changed its fields) - tell the developer")
    return jobs
