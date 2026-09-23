import logging
logger = logging.getLogger('apify_service')
"""
ADROIT ATS - High-Performance Talent Sourcing & Live Bench Candidate Service
100% Direct Authentic LinkedIn Profile URLs (https://www.linkedin.com/in/...)
Zero Search Clutter, Zero 404s, Zero Search Result Pages.
"""

import os
import re
import time
import json
import threading
import urllib.parse
from typing import List, Dict, Any, Optional
import config

SEARCH_CACHE = {}
CACHE_TTL = 600  # 10 minutes


# =========================================================================
# NOTE: The previously hardcoded VERIFIED_REAL_TALENT_POOL (fabricated
# candidate profiles) and its filter_verified_pool() lookup have been
# removed. Sourcing now returns ONLY genuine, live-searched profiles from
# live_candidate_scraper.py, or an honest empty result if none are found.
# =========================================================================

LIVE_SEARCH_TIMEOUT_SECONDS = 25  # a genuine web search cannot be forced to finish in ~3s


def scrape_bench_candidates(category="all", intent="ready_to_market", start_year=2012, end_year=2020, location="United States", max_items=25, keyword=None, bachelor_max_year=None, bachelor_min_year=None, bachelor_year=None, college=None, us_college=None, settlement=None) -> List[Dict]:
    """
    Genuine US IT Talent Sourcing Engine.
    - Every call performs a REAL live search (Google/SerpAPI + DuckDuckGo X-Ray,
      via live_candidate_scraper.py). There is no fabricated fallback pool.
    - Uses a short-lived in-memory cache only to avoid re-hitting search engines
      for an identical query in quick succession.
    - Allows up to LIVE_SEARCH_TIMEOUT_SECONDS for the search to complete.
    - Returns an HONEST empty list if no genuine profiles are found — nothing
      is ever substituted or invented to "fill" a search.
    """
    search_keyword = (keyword or category or "Computer Science").strip()
    cache_key = f"{search_keyword.lower()}_{start_year}_{end_year}_{bachelor_year}_{college}_{us_college}_{settlement}_{location.lower()}"

    # Return cached results if available within TTL (avoids re-hitting search
    # engines for the exact same query in quick succession)
    if cache_key in SEARCH_CACHE:
        cached_time, cached_data = SEARCH_CACHE[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            return cached_data[:max_items]

    live_results = []
    import concurrent.futures
    try:
        from live_candidate_scraper import scrape_live_linkedin_candidates
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                scrape_live_linkedin_candidates,
                keyword=search_keyword,
                start_year=start_year,
                end_year=end_year,
                location=location,
                max_items=max_items,
                force_fresh=True,
                bachelor_year=bachelor_year,
                college=college
            )
            live_results = future.result(timeout=LIVE_SEARCH_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        logger.warning(f"Live candidate search exceeded {LIVE_SEARCH_TIMEOUT_SECONDS}s; returning no results (no fake fallback).")
        live_results = []
    except Exception as ex:
        logger.error(f"Live candidate search failed: {ex}")
        live_results = []

    # STRICT EXACT YEAR GUARD: if a recruiter specifies a year (e.g. 2019),
    # return ONLY candidates who genuinely matched that EXACT year.
    if bachelor_year is not None and live_results:
        try:
            target_by = int(bachelor_year)
            live_results = [c for c in live_results if int(c.get("bachelor_year") or c.get("grad_year") or 0) == target_by]
        except (ValueError, TypeError):
            pass

    final_results = live_results[:max_items]
    SEARCH_CACHE[cache_key] = (time.time(), final_results)
    return final_results

def scrape_linkedin_students(keyword="Computer Science", start_year=2018, end_year=2026, location="United States", max_items=25):
    return scrape_bench_candidates(
        category=keyword,
        keyword=keyword,
        intent="ready_to_market",
        start_year=start_year,
        end_year=end_year,
        location=location,
        max_items=max_items
    )
