"""
ADROIT ATS - Live LinkedIn Candidate Scraping Library
Author: Adroit Engineering Team
Description:
    Real-time live candidate sourcing engine targeting verified LinkedIn profile URLs
    (https://www.linkedin.com/in/...). Performs precision X-Ray Boolean queries to source
    US Master's/OPT/CPT graduates, bench consultants, and job seekers across any domain.
"""

import os
import re
import time
import json
import logging
import urllib.parse
from typing import List, Dict, Any, Optional
import requests

logger = logging.getLogger(__name__)

# Default Apify Token from environment or fallback
APIFY_TOKEN = os.environ.get("APIFY_API_TOKEN", "your_apify_token_here")

# In-memory search cache to provide sub-second responses on repeated searches
LIVE_SEARCH_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 900  # 15 minutes

# Top recognized US Universities & Tech Institutes
KNOWN_US_UNIVERSITIES = [
    ("NYU", "New York University (NYU)"),
    ("New York University", "New York University"),
    ("Columbia", "Columbia University"),
    ("UT Dallas", "University of Texas at Dallas"),
    ("UTD", "University of Texas at Dallas"),
    ("University of Texas", "University of Texas"),
    ("San Jose State", "San Jose State University (SJSU)"),
    ("SJSU", "San Jose State University (SJSU)"),
    ("Arizona State", "Arizona State University (ASU)"),
    ("ASU", "Arizona State University (ASU)"),
    ("Northeastern", "Northeastern University"),
    ("Georgia Tech", "Georgia Institute of Technology"),
    ("Carnegie Mellon", "Carnegie Mellon University (CMU)"),
    ("CMU", "Carnegie Mellon University"),
    ("USC", "University of Southern California"),
    ("Purdue", "Purdue University"),
    ("UIUC", "University of Illinois Urbana-Champaign"),
    ("University of Illinois", "University of Illinois"),
    ("Stanford", "Stanford University"),
    ("Harvard", "Harvard University"),
    ("MIT", "Massachusetts Institute of Technology (MIT)"),
    ("Texas A&M", "Texas A&M University"),
    ("Rutgers", "Rutgers University"),
    ("George Mason", "George Mason University"),
    ("Northwestern", "Northwestern University"),
    ("Boston University", "Boston University"),
    ("University of Washington", "University of Washington"),
    ("University of Maryland", "University of Maryland"),
    ("Penn State", "Pennsylvania State University"),
    ("University of Florida", "University of Florida"),
    ("Stevens Institute", "Stevens Institute of Technology"),
    ("Illinois Tech", "Illinois Institute of Technology (IIT)"),
]

DOMAIN_SKILLS_TAXONOMY = {
    "data science": ["Data Science", "Machine Learning", "Python", "SQL", "Deep Learning", "NLP", "Pandas", "Scikit-Learn", "PyTorch", "TensorFlow", "Tableau", "AWS"],
    "data analyst": ["Data Analytics", "SQL", "Tableau", "Power BI", "Python", "Excel", "ETL", "Data Modeling", "Business Intelligence", "PostgreSQL"],
    "cyber security": ["Cybersecurity", "SIEM", "SOC", "Splunk", "Network Security", "Penetration Testing", "Vulnerability Assessment", "Firewalls", "Incident Response", "CISSP"],
    "devops": ["DevOps", "Kubernetes", "Docker", "AWS", "Terraform", "CI/CD", "Jenkins", "Ansible", "Linux", "CloudFormation", "Git"],
    "cloud": ["Cloud Architecture", "AWS", "Azure", "GCP", "Microservices", "Terraform", "Docker", "Kubernetes", "Linux"],
    "full stack": ["Full Stack", "Java", "React", "Node.js", "Spring Boot", "TypeScript", "JavaScript", "REST APIs", "Microservices", "SQL"],
    "computer science": ["Algorithms", "Data Structures", "Java", "Python", "C++", "System Design", "Cloud Computing", "Distributed Systems", "SQL"],
    "salesforce": ["Salesforce", "Apex", "Lightning Web Components (LWC)", "Visualforce", "SOQL", "Sales Cloud", "Service Cloud", "Workflows", "Triggers"],
}


def build_xray_query(
    keyword: str = "Data Scientist",
    start_year: int = 2018,
    end_year: int = 2026,
    location: str = "United States"
) -> str:
    """
    Builds clean, high-yield Google / SERP X-Ray search queries targeting verified LinkedIn profiles.
    Example: site:linkedin.com/in/ "Data Scientist" ("Master" OR "MS") "United States"
    """
    clean_kw = (keyword or "Computer Science").strip()
    kw_lower = clean_kw.lower()

    if any(k in kw_lower for k in ["data sci", "machine learning", "ai"]):
        core_term = '"Data Scientist"'
        edu_term = '("Master" OR "MS" OR "Student")'
    elif any(k in kw_lower for k in ["data anal", "analytics"]):
        core_term = '"Data Analyst"'
        edu_term = '("Master" OR "MS" OR "Student" OR "Analyst")'
    elif any(k in kw_lower for k in ["cyber", "security", "infosec", "soc"]):
        core_term = '("Cybersecurity" OR "Cyber Security" OR "Information Security")'
        edu_term = '("Master" OR "MS" OR "Analyst" OR "Engineer" OR "Student")'
    elif any(k in kw_lower for k in ["devops", "sre", "cloud"]):
        core_term = '("DevOps" OR "Cloud Engineer")'
        edu_term = '("Master" OR "MS" OR "Engineer" OR "Student")'
    elif "salesforce" in kw_lower:
        core_term = '"Salesforce"'
        edu_term = '("Developer" OR "Consultant")'
    elif any(k in kw_lower for k in ["computer", "software", "full stack", "java"]):
        core_term = '"Software Engineer"'
        edu_term = '("Master" OR "MS" OR "Student")'
    else:
        core_term = f'"{clean_kw}"'
        edu_term = '("Master" OR "MS" OR "Student")'

    clean_loc = location if location and location.lower() != "all" else "United States"
    query = f'site:linkedin.com/in/ {core_term} {edu_term} "{clean_loc}"'
    return query


def parse_candidate_from_serp(item: Dict[str, Any], query_category: str = "Tech") -> Optional[Dict[str, Any]]:
    """
    Intelligently parses Google/Apify search organic result items into rich, structured Candidate profiles.
    Extracts: Name, Headline, verified Profile URL, University, Degree, Grad Year, Location, Skills, and Status.
    """
    url = (item.get("url") or item.get("link") or "").strip()
    if not url or "linkedin.com/in/" not in url:
        return None

    # Sanitize URL to canonical direct profile URL
    url_match = re.search(r'(https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[a-zA-Z0-9_-]+)', url)
    if not url_match:
        return None
    canonical_url = url_match.group(1).replace("http://", "https://")

    title = item.get("title", "").strip()
    desc = item.get("description") or item.get("snippet") or ""
    
    # Remove emojis from title & desc to prevent console and display issues
    title = re.sub(r'[\U00010000-\U0010ffff]', '', title).strip()
    desc = re.sub(r'[\U00010000-\U0010ffff]', '', desc).strip()
    combined_text = f"{title} {desc}"

    # 1. Clean Title & Extract Name + Headline
    clean_title = re.sub(r'\s*[-|•–—]\s*LinkedIn.*$', '', title, flags=re.I).strip()
    
    name = "US Tech Consultant"
    headline = clean_title
    for sep in [" - ", " – ", " — ", " | ", " • "]:
        if sep in clean_title:
            parts = clean_title.split(sep, 1)
            candidate_name_part = parts[0].strip()
            # If name part is reasonable length (1 to 4 words)
            if 1 <= len(candidate_name_part.split()) <= 4 and not any(w in candidate_name_part.lower() for w in ["hiring", "jobs", "top", "view", "profiles", "looking"]):
                name = candidate_name_part
                headline = parts[1].strip()
            break

    # Clean Name of academic suffixes or extra symbols
    name = re.sub(r'\(.*?\)', '', name).strip()
    if len(name.split()) > 4:
        name = " ".join(name.split()[:3])
    if not name or len(name) < 2:
        name = "US Tech Consultant"

    # 2. Extract US University
    university = "US University"
    for short_name, full_name in KNOWN_US_UNIVERSITIES:
        if re.search(r'\b' + re.escape(short_name) + r'\b', combined_text, re.I):
            university = full_name
            break
    
    if university == "US University":
        uni_patterns = [
            r'([A-Z][a-zA-Z\s]+(?:University|College|Institute of Technology|State University))',
            r'at\s+([A-Z][a-zA-Z\s]+(?:University|College|Tech))',
            r'@\s+([A-Z][a-zA-Z\s]+(?:University|College|Tech))'
        ]
        for p in uni_patterns:
            m = re.search(p, combined_text)
            if m:
                cand_uni = m.group(1).strip()
                if len(cand_uni) < 40 and not any(w in cand_uni.lower() for w in ["linkedin", "master", "bachelor", "read more", "science"]):
                    university = cand_uni
                    break

    # 3. Extract Degree
    degree = f"Master's in {query_category.title()}"
    text_lower = combined_text.lower()
    if "data sci" in text_lower:
        degree = "Master's in Data Science"
    elif "data anal" in text_lower:
        degree = "Master's in Data Analytics"
    elif "computer science" in text_lower or "cs student" in text_lower:
        degree = "Master's in Computer Science"
    elif "cyber" in text_lower or "security" in text_lower:
        degree = "Master's in Cybersecurity"
    elif "information systems" in text_lower or "mis" in text_lower:
        degree = "Master's in Information Systems (MIS)"
    elif re.search(r'Master(?:\s+of\s+Science)?|\bMS\b', combined_text, re.I):
        degree = f"Master of Science in {query_category.title()}"
    elif re.search(r'Bachelor|B\.Tech|B\.E\.', combined_text, re.I):
        degree = f"Bachelor's in {query_category.title()}"

    # 4. Extract Graduation Year
    grad_year = "2024"
    years = re.findall(r'\b(201[8-9]|202[0-6])\b', combined_text)
    if years:
        grad_year = years[-1]

    # 5. Extract Location
    location = "United States"
    loc_match = re.search(r'([A-Z][a-zA-Z\s]+,\s*(?:[A-Z]{2}|California|Texas|New York|Washington|Illinois|Massachusetts|Georgia|Florida|Virginia|New Jersey|Ohio|Michigan|North Carolina|United States))', desc)
    if loc_match:
        candidate_loc = loc_match.group(1).strip()
        if len(candidate_loc) < 35 and not any(w in candidate_loc.lower() for w in ["read more", "aug", "sep", "jan", "may", "experience"]):
            location = candidate_loc

    # 6. Extract Relevant Skills
    matched_skills = []
    # Check domain taxonomy
    for domain, skill_list in DOMAIN_SKILLS_TAXONOMY.items():
        if domain in text_lower or domain in query_category.lower():
            for s in skill_list:
                if re.search(r'\b' + re.escape(s) + r'\b', combined_text, re.I):
                    if s not in matched_skills:
                        matched_skills.append(s)

    if not matched_skills:
        # Fallback general skills
        for s in ["Python", "SQL", "Java", "AWS", "Git", "Cloud Computing", "REST APIs", "Agile"]:
            if re.search(r'\b' + re.escape(s) + r'\b', combined_text, re.I):
                matched_skills.append(s)

    if not matched_skills:
        matched_skills = [query_category.title(), "Python", "SQL", "Cloud", "Analytics"]

    # 7. Status / Intent Badge
    if any(k in text_lower for k in ["opt", "cpt", "f1"]):
        status_badge = "F1 OPT / STEM OPT (Ready to Market)"
    elif any(k in text_lower for k in ["open to work", "actively seeking", "looking for"]):
        status_badge = "Actively Seeking / Ready to Market"
    elif any(k in text_lower for k in ["h1b", "c2c", "corp"]):
        status_badge = "H1B / C2C Eligible"
    else:
        status_badge = "US Tech Talent (Ready to Market)"

    return {
        "name": name,
        "headline": headline,
        "profile_url": canonical_url,
        "linkedin_url": canonical_url,
        "degree": degree,
        "university": university,
        "grad_year": grad_year,
        "location": location,
        "skills": matched_skills[:6],
        "status_badge": status_badge,
        "summary": desc[:200] + ("..." if len(desc) > 200 else ""),
        "source": "Live LinkedIn X-Ray"
    }


def scrape_live_linkedin_candidates(
    keyword: str = "Data Scientist",
    start_year: int = 2018,
    end_year: int = 2026,
    location: str = "United States",
    max_items: int = 25,
    force_fresh: bool = False
) -> List[Dict[str, Any]]:
    """
    Performs real-time live LinkedIn scraping for candidate sourcing.
    Uses Apify Google Search X-Ray Actor with caching and fallback.
    """
    clean_keyword = (keyword or "Data Scientist").strip()
    cache_key = f"{clean_keyword.lower()}_{start_year}_{end_year}_{location.lower()}"

    # Check cache unless force_fresh requested
    if not force_fresh and cache_key in LIVE_SEARCH_CACHE:
        cached_entry = LIVE_SEARCH_CACHE[cache_key]
        if time.time() - cached_entry["timestamp"] < CACHE_TTL_SECONDS:
            logger.info(f"Returning {len(cached_entry['results'])} cached live candidate results for '{clean_keyword}'")
            return cached_entry["results"][:max_items]

    xray_query = build_xray_query(
        keyword=clean_keyword,
        start_year=start_year,
        end_year=end_year,
        location=location
    )
    logger.info(f"Executing Live LinkedIn X-Ray Query: {xray_query}")

    live_candidates = []
    seen_urls = set()

    # --- Tier 1: Apify Google Search X-Ray Actor ---
    if APIFY_TOKEN:
        try:
            actor_url = f"https://api.apify.com/v2/acts/apify~google-search-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}&memory=512&timeout=30"
            payload = {
                "queries": xray_query,
                "maxPagesPerQuery": 1,
                "resultsPerPage": min(20, max_items + 5)
            }
            res = requests.post(actor_url, json=payload, timeout=32)
            if res.status_code in [200, 201]:
                data = res.json()
                if isinstance(data, list):
                    for page in data:
                        organic_results = page.get("organicResults", [])
                        for item in organic_results:
                            candidate = parse_candidate_from_serp(item, query_category=clean_keyword)
                            if candidate and candidate["profile_url"] not in seen_urls:
                                try:
                                    y = int(candidate.get("grad_year", 2024))
                                    if not (start_year <= y <= end_year):
                                        continue
                                except (ValueError, TypeError):
                                    pass
                                seen_urls.add(candidate["profile_url"])
                                live_candidates.append(candidate)
                                if len(live_candidates) >= max_items:
                                    break
                        if len(live_candidates) >= max_items:
                            break
                logger.info(f"Apify live scraping returned {len(live_candidates)} verified LinkedIn candidate profiles.")
        except Exception as e:
            logger.error(f"Apify live candidate scraper error: {e}")

    # If live scraper yielded results, store in cache and return
    if live_candidates:
        LIVE_SEARCH_CACHE[cache_key] = {
            "timestamp": time.time(),
            "results": live_candidates
        }
        return live_candidates[:max_items]

    logger.warning(f"Live scraping returned 0 results for '{clean_keyword}'.")
    return []
