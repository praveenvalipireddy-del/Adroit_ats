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
import requests
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


# =========================================================================
# Apify "LinkedIn People Search" (memo23/linkedin-people-search) integration
# =========================================================================
# Runs in the actor's cookie-free "public" mode: reads each person's actual
# public LinkedIn profile page (real name, real direct profile URL, best-
# effort school name) with zero LinkedIn login / ban risk.
#
# VERIFIED VIA LIVE TESTING: this mode does NOT return graduation years or
# degree types at all - only the actor's "cookie" mode (using the user's own
# LinkedIn session cookie) can retrieve that, since it's gated behind login.
# Per an explicit product decision, we use "public" mode only and NEVER guess
# a graduation year for these results - every candidate from this source is
# marked `year_verified: False` so the UI can flag it for manual confirmation
# rather than presenting a fabricated or assumed year.
APIFY_LINKEDIN_PEOPLE_SEARCH_ACTOR = "memo23~linkedin-people-search"

# A modest, cost-conscious spread of well-known Indian engineering colleges
# to search by "school" filter. Apify bills ~$0.005 per search start + ~$0.006
# per profile found (memo23/linkedin-people-search pricing as of Sep 2026), so
# keep this list and per-college result cap small rather than exhaustive.
INDIAN_COLLEGES_FOR_SCHOOL_SEARCH = [
    "Indian Institute of Technology",
    "National Institute of Technology",
    "BITS Pilani",
    "Anna University",
]


def search_linkedin_by_school(school: str, location: str = "United States", max_results: int = 5) -> List[Dict]:
    """
    One real Apify LinkedIn People Search call (public/cookie-free mode) for
    a given school-name filter. Returns genuine, real-profile candidates with
    NO graduation year (honestly marked unverified) - never fabricated.
    """
    if not config.APIFY_API_TOKEN:
        return []

    url = f"https://api.apify.com/v2/acts/{APIFY_LINKEDIN_PEOPLE_SEARCH_ACTOR}/run-sync-get-dataset-items"
    payload = {
        "mode": "public",
        "school": school,
        "location": location,
        "maxResults": max_results,
    }
    try:
        resp = requests.post(url, params={"token": config.APIFY_API_TOKEN}, json=payload, timeout=90)
        if resp.status_code not in (200, 201):
            logger.warning(f"Apify LinkedIn people search HTTP {resp.status_code} for school='{school}'")
            return []
        raw_items = resp.json()
    except Exception as ex:
        logger.error(f"Apify LinkedIn people search failed for school='{school}': {ex}")
        return []

    candidates = []
    for item in raw_items or []:
        profile_url = (item.get("profileUrl") or "").strip()
        name = (item.get("name") or "").strip()
        # Skip records with no usable profile URL. (NOTE: `partial: true` on
        # the raw item is common even for genuinely good matches - verified in
        # testing it does NOT mean "bad record", so it's not used as a filter.)
        if not profile_url or "linkedin.com/in/" not in profile_url:
            continue
        # Reject non-US LinkedIn domains (e.g. in.linkedin.com) - these are
        # either India-based profiles or, as seen in testing, sometimes the
        # school's own institutional LinkedIn page rather than a real person.
        if re.search(r'://(?:in|uk|ca|sg|au|ae)\.linkedin\.com', profile_url.lower()):
            continue
        # Reject records that are clearly the institution's own page, not a
        # person (seen in testing: name comes back identical to the school
        # searched for, e.g. name="BITS Pilani" when school="BITS Pilani").
        name_tokens = name.split()
        if not name or len(name_tokens) < 2:
            continue
        if name.lower().strip() == school.lower().strip():
            continue
        # Reject garbled/incomplete records: a single-letter first or last
        # name token (seen in testing, e.g. "Bq N") is a reliable sign the
        # actor couldn't parse a real name for this profile. (Generic "about"
        # boilerplate text alone is NOT used as a reject signal - verified in
        # testing that some genuinely good matches also have it, e.g. a real
        # candidate whose LinkedIn About section just isn't public.)
        if len(name_tokens[0]) <= 1 or len(name_tokens[-1]) <= 1:
            continue

        schools_list = item.get("schools") or []
        matched_school = schools_list[0] if schools_list else school
        headline = item.get("jobTitle") or item.get("roleFromSummary") or "Technical Professional"

        candidates.append({
            "name": name,
            "headline": headline,
            "title": headline,
            "bachelor_year": "",   # HONEST: this source has no year data - never guessed
            "grad_year": "",
            "bachelor_degree": "",
            "bachelor_college": matched_school,
            "master_degree": "",
            "master_university": "",
            "degree": f"Matched school: {matched_school} (graduation year not available - verify on profile)",
            "location": item.get("location") or location,
            "status_tag": "School-matched — year unverified",
            "status_badge": "⚠️ Verify year on profile",
            "quality": "[SCHOOL-MATCHED] Real profile - graduation year not available from this source",
            "profile_url": profile_url,
            "linkedin_url": profile_url,
            "year_verified": False,
            "source": "apify_linkedin_people_search",
        })
    return candidates


def search_linkedin_bench_candidates_by_schools(location: str = "United States", max_results_per_school: int = 5, colleges: Optional[List[str]] = None) -> List[Dict]:
    """
    Search across a small, cost-conscious spread of well-known Indian colleges
    via the Apify people-search actor and merge/dedupe the results. Returns
    [] immediately (no cost incurred) if APIFY_API_TOKEN isn't configured.
    """
    if not config.APIFY_API_TOKEN:
        return []

    colleges = colleges or INDIAN_COLLEGES_FOR_SCHOOL_SEARCH
    all_candidates = []
    seen_urls = set()
    for college in colleges:
        for c in search_linkedin_by_school(college, location=location, max_results=max_results_per_school):
            if c["profile_url"] not in seen_urls:
                seen_urls.add(c["profile_url"])
                all_candidates.append(c)
    return all_candidates


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
