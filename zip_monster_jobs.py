"""ZipRecruiter (USA + Canada) and Monster (USA) job listings via Apify actors - paid, so searched only when the recruiter picks
the portal (never by "All portals" or the automatic searches).

- ZipRecruiter: silentflow/ziprecruiter-scraper-ppe. Store price (Nov 2026): $0.00099 per job + about $0.003 per job
  for the full posting (includeDetails) - 20 jobs about $0.08 a search.
- Monster: blackfalcondata/monster-scraper. $0.005 per run + $0.001 per job - 25 jobs about $0.03 a search.
- Each job keeps the type the portal states (Contract / Full-time / Part-time), so the Jobs-tab type filter works.
- Read tolerantly (flat or nested fields) - actors rename fields. Never invents a value: missing stays empty.
"""
import logging
from typing import Dict, List

import india_job_scrapers as ij
from us_job_scrapers import extract_email_from_text

logger = logging.getLogger("zip_monster_jobs")

ZIP_ACTOR = "silentflow~ziprecruiter-scraper-ppe"
MONSTER_ACTOR = "blackfalcondata~monster-scraper"
ZIP_SOURCE = "ZipRecruiter (Live)"
MONSTER_SOURCE = "Monster (Live)"
ZIP_LIMIT = 20
MONSTER_LIMIT = 25
ZIP_TYPES = {"Contract": "contract", "Full-time": "full_time", "Part-time": "part_time"}
MONSTER_TYPES = {"Contract": "CONTRACT", "Full-time": "FULL_TIME", "Part-time": "PART_TIME"}
_TYPE_WORDS = {"contractor": "Contract", "contract": "Contract", "temporary": "Contract", "temp": "Contract",
               "full_time": "Full-time", "full-time": "Full-time", "fulltime": "Full-time", "permanent": "Full-time",
               "part_time": "Part-time", "part-time": "Part-time", "parttime": "Part-time", "intern": "Internship",
               "internship": "Internship"}
_US_WIDE = ("", "united states", "usa", "us", "anywhere in the us", "canada", "all canada", "anywhere in canada")


def _type(v) -> str:
    vals = v if isinstance(v, list) else [v]
    out = []
    for x in vals:
        t = _TYPE_WORDS.get(str(x or "").strip().lower())
        if t and t not in out:
            out.append(t)
    return ", ".join(out)


def _text(v) -> str:
    if isinstance(v, dict):
        return str(v.get("text") or v.get("plain") or "").strip()
    return str(v or "").strip()


def _location(it: Dict, market: str = "United States") -> str:
    loc = it.get("location")
    if isinstance(loc, dict):
        loc = ", ".join(p for p in (loc.get("city"), loc.get("state") or loc.get("region")) if p)
    loc = str(loc or "").strip()
    if not loc:
        loc = ", ".join(p for p in (it.get("city"), it.get("state")) if p)
    return loc.removesuffix(", US").removesuffix(", CA").strip() or market


def _is_test_posting(title: str, company: str) -> bool:
    """ZipRecruiter lists its own test ads ('PAT_Java' by 'PAT_TestAccount') - not real jobs."""
    return title.upper().startswith("PAT_") or "testaccount" in company.lower().replace(" ", "")


def _jobs(items: List[Dict], portal: str, source: str, keyword: str, market: str = "United States") -> List[Dict]:
    jobs, dropped = [], 0
    for idx, it in enumerate(items or []):
        title = _text(it.get("title") or it.get("jobTitle"))
        url = _text(it.get("url") or it.get("applyUrl") or it.get("portalUrl"))
        company = _text(it.get("company") or it.get("companyName")) or "Employer"
        if not title or not url.startswith("http"):
            dropped += 1
            continue
        if _is_test_posting(title, company):
            continue
        desc = _text(it.get("description") or it.get("descriptionText"))
        where = _location(it, market)
        posted = _text(it.get("postedDate") or it.get("datePosted"))[:10] or "recently"
        jobs.append({
            "title": title,
            "company": company,
            "location": where,
            "job_type": _type(it.get("employmentType") or it.get("jobType")),
            "salary": _text(it.get("salaryText")),                 # only what the portal states - never invented
            "source": source,
            "url": url,
            "recruiter_email": extract_email_from_text(desc) or "",
            "description": f"Posted {posted} on {portal}: {title} at {company} ({where}).\n\n{desc[:6000]}".strip(),
            "matched_skills": keyword,
            "match_score": 95 - idx,
            "is_24h": 1,
            "country": market,
        })
    if dropped and not jobs:
        ij.LAST_ERROR[portal] = (f"{portal} returned {len(items)} jobs but in a format this app doesn't read yet "
                                 "(the scraper changed its fields) - tell the developer")
        logger.warning(f"{portal}: fields seen: {sorted((items or [{}])[0].keys())[:30]}")
    return jobs


def scrape_ziprecruiter(keyword: str, location: str = "United States", job_type: str = "", country: str = "United States") -> List[Dict]:
    """country "Canada" searches ziprecruiter.ca (its jobs are saved as Canada jobs)."""
    market = "Canada" if (country or "").strip().lower() == "canada" else "United States"
    payload = {"searches": [keyword], "postedWithin": "week", "maxItems": ZIP_LIMIT, "maxPages": 1, "includeDetails": True}
    if market == "Canada":
        payload["countryName"] = "canada"
    if location.strip().lower() not in _US_WIDE:
        payload["location"] = location.strip()
    if ZIP_TYPES.get(job_type):
        payload["jobType"] = ZIP_TYPES[job_type]
    items, err = ij._run_actor_sync(ZIP_ACTOR, payload, timeout=200)
    ij.LAST_ERROR["ZipRecruiter"] = err or ""
    if err:
        logger.error(f"ZipRecruiter scraping error: {err}")
        return []
    return _jobs(items, "ZipRecruiter", ZIP_SOURCE, keyword, market)


def scrape_monster(keyword: str, location: str = "United States", job_type: str = "") -> List[Dict]:
    payload = {"query": keyword, "country": "US", "datePosted": "LAST_2_DAYS", "maxResults": MONSTER_LIMIT, "descriptionFormat": "text"}
    if location.strip().lower() not in _US_WIDE:
        payload["location"] = location.strip()
    if MONSTER_TYPES.get(job_type):
        payload["employmentType"] = MONSTER_TYPES[job_type]
    items, err = ij._run_actor_sync(MONSTER_ACTOR, payload, timeout=200)
    ij.LAST_ERROR["Monster"] = err or ""
    if err:
        logger.error(f"Monster scraping error: {err}")
        return []
    return _jobs(items, "Monster", MONSTER_SOURCE, keyword)
