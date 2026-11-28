"""
ADROIT ATS - Canada job market: LinkedIn, Indeed Canada, ZipRecruiter Canada and LinkedIn recruiter posts.

- LinkedIn: the same free public job search as the US and India (us_job_scrapers.scrape_linkedin_us) with
  location "Canada" (or a Canadian city) - no cost.
- Indeed Canada: the Indeed Apify actor (indeed_jobs.py) with country "ca" - about $0.004 per search (Apify credit).
- ZipRecruiter Canada: ziprecruiter.ca via zip_monster_jobs.py - about $0.08 per search, only when picked.
- Recruiter posts: LinkedIn posts read by the AI (linkedin_posts.py), keeping only Canadian requirements -
  about $0.10 per search, only when picked.

"All Canada portals" = LinkedIn + Indeed Canada. Each portal's result or reason ("Indeed: 0 jobs - <why>") is
reported per portal, like India (india_job_scrapers.run_portals does the searching and saving).
"""
from typing import Dict, List, Optional

import india_job_scrapers as ij
from us_job_scrapers import scrape_linkedin_us

SOURCES = ("LinkedIn", "Indeed", "ZipRecruiter", "Recruiter Post")
ALL_SOURCES = ("LinkedIn", "Indeed")
COUNTRY = "Canada"


def scrape_linkedin_canada(keyword: str = "Java", location: str = "Canada", limit: int = 15, job_type: str = "") -> List[Dict]:
    ij.LAST_ERROR["LinkedIn"] = ""
    try:
        jobs = scrape_linkedin_us(keyword, location=location or COUNTRY, contract_only=False, limit=limit, job_type=job_type)
    except Exception as ex:
        ij.LAST_ERROR["LinkedIn"] = f"LinkedIn could not be read ({ex.__class__.__name__})"
        return []
    for j in jobs:
        j["country"] = COUNTRY
    return jobs


def scrape_indeed_canada(keyword: str = "Java", location: str = "Canada", limit: int = 15, job_type: str = "") -> List[Dict]:
    import indeed_jobs
    return indeed_jobs.scrape_indeed(keyword, location=location, country=COUNTRY, limit=max(limit, 25), job_type=job_type)


def scrape_ziprecruiter_canada(keyword: str = "Java", location: str = "Canada", limit: int = 15, job_type: str = "") -> List[Dict]:
    import zip_monster_jobs
    return zip_monster_jobs.scrape_ziprecruiter(keyword, location=location, job_type=job_type, country=COUNTRY)


def run_multi_source_canada_scrape(keywords: Optional[List[str]] = None, location: str = "Canada",
                                   save_to_db: bool = True, sources: Optional[List[str]] = None, job_type: str = "") -> Dict:
    """sources: only these portals (None = LinkedIn + Indeed Canada). Returns {count, saved_to_db, jobs, sources}."""
    if not keywords:
        keywords = ["Software Engineer"]
    wanted = [x for x in SOURCES if (x in sources if sources else x in ALL_SOURCES)]
    scrapers = {"LinkedIn": scrape_linkedin_canada, "Indeed": scrape_indeed_canada, "ZipRecruiter": scrape_ziprecruiter_canada,
                "Recruiter Post": lambda kw, location=COUNTRY, limit=8, job_type="": ij.scrape_recruiter_posts(kw, COUNTRY, job_type)}
    return ij.run_portals(scrapers, wanted, keywords, location or COUNTRY, job_type, save_to_db, COUNTRY)
