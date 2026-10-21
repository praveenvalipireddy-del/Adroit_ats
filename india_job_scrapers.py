"""
ADROIT ATS - India job market sourcing: Naukri, Foundit (Monster India), LinkedIn India.

Naukri.com runs Akamai Bot Manager and requires a signed, JS-generated token on every
search request (session warming + Indian residential proxies needed) - a direct
requests/BeautifulSoup scraper gets blocked immediately, the same wall LinkedIn profile
scraping hits. Foundit's search endpoint is unauthenticated and works over plain HTTP, but
building our own scraper for either would mean re-solving a problem already-maintained
Apify actors solve. Both actors below are pay-per-result (~$0.001/job): a search that finds
nothing costs nothing.

LinkedIn's public JOB search (not profile search - a different, less-protected endpoint)
already works via direct scraping in us_job_scrapers.py, so India just reuses it with
location="India" - no new integration needed there.

Cost (Oct 2026): Naukri actor $0.004/job on Apify's FREE plan ($0.001 from the Gold plan up) plus
$0.00005 per run; Foundit actor ~$0.89/1,000 results.

Each scraper records why it found nothing (LAST_ERROR[source]) - e.g. the Apify account's monthly credit
is used up - and run_multi_source_india_scrape() reports a status per portal, so the Jobs tab can say
"Naukri: 0 jobs - <reason>" instead of quietly showing only LinkedIn.
"""
import logging
from typing import Dict, List, Optional

import requests

import config
from us_job_scrapers import scrape_linkedin_us, extract_salary_from_text

logger = logging.getLogger("india_job_scrapers")

API = "https://api.apify.com/v2"
NAUKRI_ACTOR = "epicscrapers~naukri-scraper"
FOUNDIT_ACTOR = "crawloop~foundit-jobs-scraper"


# source -> why its last search found nothing ('' = it worked)
LAST_ERROR: Dict[str, str] = {}
SOURCES = ("Naukri", "Foundit", "LinkedIn")


def _token() -> str:
    return (config.APIFY_API_TOKEN or "").strip()


def explain_apify_error(status: int, text: str) -> str:
    """Apify's answer in words a recruiter can act on."""
    t = (text or "").lower()
    if status in (402, 403) and ("limit" in t or "exceed" in t or "credit" in t or "usage" in t or "payment" in t):
        return "the Apify account's monthly credit is used up - add credit or wait for the monthly reset (apify.com > Billing)"
    if status == 401:
        return "the Apify API token on the server is not valid - check APIFY_API_TOKEN on Render"
    if status == 404:
        return "the Apify scraper was not found (it may have been renamed)"
    if status in (408, 504) or "timed out" in t or "timeout" in t:
        return "the search took too long and was stopped - try again"
    return f"Apify answered HTTP {status}: {text[:160]}"


def _run_actor_sync(actor: str, payload: Dict, timeout: int = 150):
    """Runs an Apify actor synchronously and returns its dataset items. A search that
    matches nothing returns an empty list at no cost - only returned records are billed."""
    if not _token():
        return None, "APIFY_API_TOKEN is not configured on the server."
    import time
    started = time.monotonic()
    try:
        r = requests.post(
            f"{API}/acts/{actor}/run-sync-get-dataset-items",
            params={"token": _token()},
            json=payload,
            timeout=timeout,
        )
        if r.status_code in (200, 201):
            items = r.json()
            items = items if isinstance(items, list) else []
            # every run is logged (search terms, results, time) - an empty result is visible in the server log
            logger.info(f"Apify {actor}: {len(items)} results in {time.monotonic() - started:.0f}s for "
                        f"{ {k: v for k, v in payload.items() if k in ('keyword', 'position', 'location', 'jobAge', 'postedWithinDays')} }")
            return items, None
        logger.warning(f"Apify {actor}: HTTP {r.status_code} after {time.monotonic() - started:.0f}s: {r.text[:300]}")
        return None, explain_apify_error(r.status_code, r.text)
    except requests.Timeout:
        return None, f"the search took longer than {timeout} seconds and was stopped - try again"
    except Exception as ex:
        return None, f"could not reach Apify ({ex.__class__.__name__})"


def scrape_naukri(keyword: str = "Java", location: str = "India", limit: int = 15) -> List[Dict]:
    """Real live Naukri.com listings via the epicscrapers/naukri-scraper Apify actor."""
    payload = {
        "keyword": keyword,
        "location": "" if location.lower() == "india" else location,  # blank = all-India
        "maxResultsPerQuery": limit,
        "jobAge": "3",
        "sort": "date",
    }
    items, err = _run_actor_sync(NAUKRI_ACTOR, payload)
    LAST_ERROR["Naukri"] = err or ""
    if err:
        logger.error(f"Naukri scraping error: {err}")
        return []

    jobs = []
    for idx, it in enumerate(items):
        title = (it.get("title") or "").strip()
        url = (it.get("jdURL") or "").strip()
        if not title or not url:
            continue
        if url.startswith("/"):
            url = "https://www.naukri.com" + url
        company = (it.get("companyName") or "Employer").strip()
        loc = (it.get("locationLabel") or location).strip()
        salary = (it.get("salaryLabel") or "Not disclosed").strip()
        jobs.append({
            "title": title,
            "company": company,
            "location": loc,
            "job_type": "Full-time",
            "salary": salary,
            "source": "Naukri (Live)",
            "url": url,
            "recruiter_email": "",
            "description": (f"Posted {it.get('postedAt', 'recently')} on Naukri: {title} at {company} ({loc}). "
                            f"Experience: {it.get('experienceLabel', 'Not specified')}. "
                            f"Skills: {it.get('tagsAndSkills', '')}."),
            "matched_skills": keyword,
            "match_score": 95 - idx,
            "is_24h": 1,
            "country": "India",
        })
    return jobs


def scrape_foundit(keyword: str = "Java", location: str = "India", limit: int = 15) -> List[Dict]:
    """Real live Foundit (Monster India) listings via the crawloop/foundit-jobs-scraper actor."""
    payload = {
        "position": keyword,
        "location": "" if location.lower() == "india" else location,
        "maxItems": limit,
        "postedWithinDays": "7",
    }
    items, err = _run_actor_sync(FOUNDIT_ACTOR, payload)
    LAST_ERROR["Foundit"] = err or ""
    if err:
        logger.error(f"Foundit scraping error: {err}")
        return []

    jobs = []
    for idx, it in enumerate(items):
        title = (it.get("title") or "").strip()
        url = (it.get("url") or it.get("applyUrl") or "").strip()
        if not title or not url:
            continue
        company = (it.get("employer") or "Employer").strip()
        loc = (it.get("location") or location).strip()
        salary = (it.get("salaryNote") or "Not disclosed").strip()
        skills = ", ".join(it.get("skills") or [])
        jobs.append({
            "title": title,
            "company": company,
            "location": loc,
            "job_type": (it.get("employmentType") or "Full-time"),
            "salary": salary,
            "source": "Foundit / Monster India (Live)",
            "url": url,
            "recruiter_email": "",
            "description": (f"Posted on Foundit: {title} at {company} ({loc}). "
                            f"Experience: {it.get('experienceText', 'Not specified')}. Skills: {skills}."),
            "matched_skills": keyword,
            "match_score": 95 - idx,
            "is_24h": 1,
            "country": "India",
        })
    return jobs


def scrape_linkedin_india(keyword: str = "Java", location: str = "India", limit: int = 15) -> List[Dict]:
    """Reuses the existing direct LinkedIn public-job-search scraper (us_job_scrapers.py) -
    that endpoint is generic to any location, not US-specific, so India needs no new code."""
    LAST_ERROR["LinkedIn"] = ""
    try:
        jobs = scrape_linkedin_us(keyword, location=location, contract_only=False, limit=limit)
    except Exception as ex:
        LAST_ERROR["LinkedIn"] = f"LinkedIn could not be read ({ex.__class__.__name__})"
        return []
    for j in jobs:
        j["country"] = "India"
    return jobs


def run_multi_source_india_scrape(keywords: Optional[List[str]] = None, location: str = "India",
                                  save_to_db: bool = True, sources: Optional[List[str]] = None) -> Dict:
    """Mirrors run_multi_source_us_scrape in us_job_scrapers.py, for the India market.
    sources: only these portals (e.g. ["Naukri"] when the recruiter picked Naukri) - no Apify credit is
    spent on portals that weren't asked for. Returns {count, saved_to_db, jobs, sources: {portal: {count, error}}}."""
    import models  # local import: avoids a hard dependency for callers that only need scrape_*

    if not keywords:
        keywords = ["Java", "Python Developer", "Data Analyst", "React Developer"]
    wanted = [x for x in SOURCES if not sources or x in sources]
    scrapers = {"Naukri": scrape_naukri, "Foundit": scrape_foundit, "LinkedIn": scrape_linkedin_india}

    all_jobs = []
    seen_keys = set()
    status = {name: {"count": 0, "error": ""} for name in wanted}
    for kw in keywords:
        for name in wanted:
            found = scrapers[name](kw, location=location, limit=8)
            status[name]["count"] += len(found)
            if not found and LAST_ERROR.get(name):
                status[name]["error"] = LAST_ERROR[name]
            for j in found:
                key = f"{j['title'].lower()}|{j['company'].lower()}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_jobs.append(j)

    saved_count = 0
    if save_to_db:
        for job_data in all_jobs:
            try:
                job_id = models.save_or_update_scraped_job(job_data)
                job_data["id"] = job_id
                saved_count += 1
            except Exception as e:
                logger.error(f"Error saving India job {job_data.get('title')}: {e}")

    return {"count": len(all_jobs), "saved_to_db": saved_count, "jobs": all_jobs, "sources": status}
