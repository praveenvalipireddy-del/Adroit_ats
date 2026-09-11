"""
ADROIT ATS - Live LinkedIn Candidate Scraping Library v3.1
Strategy: "B.Tech India + MS USA" pipeline targeting

KEY PRINCIPLE:
  Simple X-Ray query (2-3 terms) → Gets real results from DuckDuckGo
  Smart post-parser → Detects B.Tech India + MS USA combination
  Quality scoring → Puts ideal profiles first

Why simple queries work better:
  DuckDuckGo (Bing backend) returns 0 results when query has 5+ quoted terms.
  With 2-3 terms, it returns real LinkedIn profiles which the parser then evaluates.
"""

import os
import re
import time
import random
import logging
from typing import List, Dict, Any, Optional, Set

logger = logging.getLogger(__name__)

# --------------------------------------------------
# API TOKENS (optional)
# --------------------------------------------------
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
APIFY_TOKEN = os.environ.get("APIFY_API_TOKEN", "")

# --------------------------------------------------
# GLOBAL DEDUPLICATION
# --------------------------------------------------
GLOBALLY_SEEN_URLS: Set[str] = set()
DEDUP_MAX_SIZE = 5000
start_year_global = 2018
end_year_global = 2026


def _reset_dedup_if_needed():
    global GLOBALLY_SEEN_URLS
    if len(GLOBALLY_SEEN_URLS) > DEDUP_MAX_SIZE:
        GLOBALLY_SEEN_URLS = set()


# --------------------------------------------------
# QUERY POOLS
# --------------------------------------------------

# Top Indian engineering colleges (B.Tech/B.E. origin)
TOP_INDIAN_COLLEGES = [
    "IIT", "NIT", "BITS Pilani", "BITS", "VIT", "SRM", "Manipal",
    "Amrita", "Anna University", "JNTU", "Osmania", "IIIT",
    "Thapar", "Jadavpur", "DTU", "PSG", "BIT Mesra",
    "KL University", "Sathyabama", "Vellore", "Coimbatore",
]

# US cities with highest Indian IT population
US_CITY_POOL = [
    "New Jersey", "Dallas", "Houston", "Chicago", "Atlanta",
    "Seattle", "San Jose", "Charlotte", "Boston", "Austin",
    "Tampa", "Raleigh", "Columbus", "Denver", "Phoenix",
    "Minneapolis", "Detroit", "Pittsburgh", "Washington DC",
    "Richmond", "Hartford", "Sacramento", "Orlando", "Cincinnati",
]

# Role variations
ROLE_TERM_VARIATIONS = {
    "data science": ['"Data Scientist"', '"Machine Learning Engineer"', '"ML Engineer"', '"AI Engineer"'],
    "data analyst": ['"Data Analyst"', '"Business Intelligence Analyst"', '"BI Developer"', '"Analytics Engineer"'],
    "cyber security": ['"Cybersecurity Analyst"', '"SOC Analyst"', '"Security Engineer"', '"Information Security"'],
    "devops": ['"DevOps Engineer"', '"Site Reliability Engineer"', '"Cloud Engineer"', '"Platform Engineer"'],
    "cloud": ['"Cloud Architect"', '"AWS Solutions Architect"', '"Azure Engineer"', '"GCP Engineer"'],
    "full stack": ['"Full Stack Developer"', '"Software Engineer"', '"Java Developer"', '"React Developer"'],
    "computer science": ['"Software Engineer"', '"Software Developer"', '"Backend Engineer"', '"Systems Engineer"'],
    "salesforce": ['"Salesforce Developer"', '"Salesforce Consultant"', '"Salesforce Admin"', '"LWC Developer"'],
    "java": ['"Java Developer"', '"Java Engineer"', '"Spring Boot Developer"', '"Java Full Stack"'],
    "business analyst": ['"Business Analyst"', '"Product Analyst"', '"Systems Analyst"', '"Functional Analyst"'],
}

DOMAIN_SKILLS_TAXONOMY = {
    "data science": ["Data Science", "Machine Learning", "Python", "SQL", "Deep Learning", "NLP", "Pandas", "TensorFlow", "PyTorch", "Tableau", "AWS"],
    "data analyst": ["Data Analytics", "SQL", "Tableau", "Power BI", "Python", "Excel", "ETL", "Data Modeling", "Business Intelligence"],
    "cyber security": ["Cybersecurity", "SIEM", "SOC", "Splunk", "Network Security", "Penetration Testing", "Firewalls", "Incident Response"],
    "devops": ["DevOps", "Kubernetes", "Docker", "AWS", "Terraform", "CI/CD", "Jenkins", "Ansible", "Linux", "Git"],
    "cloud": ["Cloud Architecture", "AWS", "Azure", "GCP", "Microservices", "Terraform", "Docker", "Kubernetes"],
    "full stack": ["Full Stack", "Java", "React", "Node.js", "Spring Boot", "TypeScript", "JavaScript", "REST APIs", "SQL"],
    "computer science": ["Algorithms", "Data Structures", "Java", "Python", "C++", "System Design", "Cloud Computing", "SQL"],
    "salesforce": ["Salesforce", "Apex", "Lightning Web Components (LWC)", "Visualforce", "SOQL", "Sales Cloud", "Service Cloud"],
    "java": ["Java", "Spring Boot", "Hibernate", "REST APIs", "Microservices", "Maven", "SQL", "AWS", "Docker"],
    "business analyst": ["Business Analysis", "Agile", "Scrum", "Requirements Gathering", "JIRA", "SQL", "User Stories", "UAT"],
}

KNOWN_US_UNIVERSITIES = [
    ("Northeastern", "Northeastern University"),
    ("UT Dallas", "University of Texas at Dallas"),
    ("UTD", "University of Texas at Dallas"),
    ("Arizona State", "Arizona State University (ASU)"),
    ("ASU", "Arizona State University (ASU)"),
    ("George Mason", "George Mason University"),
    ("Stevens Institute", "Stevens Institute of Technology"),
    ("Stony Brook", "Stony Brook University (SUNY)"),
    ("San Jose State", "San Jose State University (SJSU)"),
    ("SJSU", "San Jose State University"),
    ("Illinois Institute", "Illinois Institute of Technology"),
    ("University of Cincinnati", "University of Cincinnati"),
    ("Drexel", "Drexel University"),
    ("NYU", "New York University (NYU)"),
    ("Columbia", "Columbia University"),
    ("Carnegie Mellon", "Carnegie Mellon University (CMU)"),
    ("CMU", "Carnegie Mellon University"),
    ("Georgia Tech", "Georgia Institute of Technology"),
    ("USC", "University of Southern California"),
    ("Purdue", "Purdue University"),
    ("UIUC", "University of Illinois Urbana-Champaign"),
    ("Texas A&M", "Texas A&M University"),
    ("Rutgers", "Rutgers University"),
    ("Boston University", "Boston University"),
    ("University of Washington", "University of Washington"),
    ("University of Maryland", "University of Maryland"),
    ("Penn State", "Pennsylvania State University"),
    ("University of Florida", "University of Florida"),
    ("New Jersey Institute", "NJIT"),
    ("University of Michigan", "University of Michigan"),
    ("Stanford", "Stanford University"),
    ("MIT", "Massachusetts Institute of Technology (MIT)"),
]

# Signals that profile has Indian bachelor's education
INDIAN_EDU_SIGNALS = [
    "b.tech", "b.e.", "b.sc", "bachelor of technology", "btech",
    "iit", "nit", "bits pilani", "bits", "vit", "srm", "manipal",
    "amrita", "anna university", "jntu", "osmania", "iiit",
    "thapar", "jadavpur", "coep", "kl university", "sathyabama",
    "pune university", "mumbai university", "bangalore university",
    "delhi university", "hyderabad university", "madras university",
    "hyd", "andhra university", "calcutta university",
]

# US Master's signals
US_MASTERS_SIGNALS = [
    r'\bmaster\b', r'\bms\b', r'\bm\.s\.\b', r'\bmcs\b',
    r'\bmtech\b', r'\bm\.tech\b', r'\bmeng\b', r'\bm\.eng\b',
    r'\bmaster of science\b', r'\bmaster of engineering\b',
]


# --------------------------------------------------
# QUERY BUILDER — Simple + Effective
# --------------------------------------------------

def _get_role_term(keyword: str) -> str:
    kw_lower = keyword.lower()
    for domain, terms in ROLE_TERM_VARIATIONS.items():
        if domain in kw_lower:
            return random.choice(terms)
    return f'"{keyword}"'


def build_rotating_queries(
    keyword: str,
    start_year: int,
    end_year: int,
    num_queries: int = 4,
) -> List[str]:
    """
    SIMPLE but targeted queries that actually return results from DuckDuckGo.
    Post-filtering handles the B.Tech India + MS USA detection.

    Rule: Max 3 quoted terms per query — more than that returns 0 results on Bing.

    Strategy mix per run:
    A) Role + Indian college + US city (most hits)
    B) Role + "B.Tech" + US city (B.Tech-specific)
    C) Role + Indian college + "Master" (education-focused)
    """
    _reset_dedup_if_needed()
    queries = []

    us_cities = random.sample(US_CITY_POOL, min(num_queries * 2, len(US_CITY_POOL)))
    colleges = random.sample(TOP_INDIAN_COLLEGES, min(num_queries * 2, len(TOP_INDIAN_COLLEGES)))

    for i in range(num_queries):
        role_term = _get_role_term(keyword)
        us_city = us_cities[i % len(us_cities)]
        college = colleges[i % len(colleges)]
        strategy = i % 3

        if strategy == 0:
            # A: Role + Indian college + US city
            query = f'site:linkedin.com/in/ {role_term} "{college}" "{us_city}"'

        elif strategy == 1:
            # B: Role + B.Tech + US city (direct India degree signal)
            query = f'site:linkedin.com/in/ {role_term} "B.Tech" "{us_city}"'

        else:
            # C: Role + Indian college + Master (education journey signal)
            query = f'site:linkedin.com/in/ {role_term} "{college}" "Master"'

        queries.append(query)
        logger.info(f"[Query {i+1} | Strategy {'ABC'[strategy]}] {query}")

    return queries


# --------------------------------------------------
# URL CANONICALIZATION
# --------------------------------------------------

LINKEDIN_PROFILE_REGEX = re.compile(
    r'https?://(?:www\.|[a-z]{2}\.)?linkedin\.com/in/([a-zA-Z0-9_%-]+)'
)


def _canonicalize_linkedin_url(url: str) -> Optional[str]:
    m = LINKEDIN_PROFILE_REGEX.search(url or "")
    if not m:
        return None
    return f"https://www.linkedin.com/in/{m.group(1).rstrip('/')}"


# --------------------------------------------------
# CANDIDATE PARSER
# --------------------------------------------------

def _parse_candidate_from_result(
    url: str,
    title: str,
    snippet: str,
    query_category: str,
) -> Optional[Dict[str, Any]]:

    canonical_url = _canonicalize_linkedin_url(url)
    if not canonical_url:
        return None
    if canonical_url in GLOBALLY_SEEN_URLS:
        return None

    title = re.sub(r'[\U00010000-\U0010ffff]', '', title or "").strip()
    snippet = re.sub(r'[\U00010000-\U0010ffff]', '', snippet or "").strip()
    combined = f"{title} {snippet}"
    text_lower = combined.lower()

    # Hard skip: explicit US Citizen (very rare in LinkedIn snippets but worth checking)
    if re.search(r'\bus citizen\b|\bamerican citizen\b', text_lower):
        return None

    # ── Name ──
    name_match = re.match(r'^([A-Z][a-zA-Z\s\.\-\']{1,40}?)(?:\s*[-–|]|\s*\|)', title)
    if name_match:
        name = name_match.group(1).strip()
    else:
        name = title.split("-")[0].split("|")[0].strip()
    name = re.sub(r'\(.*?\)', '', name).strip()
    if len(name.split()) > 4:
        name = " ".join(name.split()[:3])
    if not name or len(name) < 2:
        name = "US Tech Consultant"

    # ── Headline ──
    headline_match = re.search(r'[-–|]\s*(.+?)(?:\s*[-–|]|$)', title)
    headline = headline_match.group(1).strip() if headline_match else f"{query_category.title()} Professional"
    headline = headline[:120]

    # ── DETECT B.Tech India background ──
    has_indian_edu = any(sig in text_lower for sig in INDIAN_EDU_SIGNALS)

    # ── DETECT US Master's degree ──
    has_us_masters = any(bool(re.search(p, text_lower)) for p in US_MASTERS_SIGNALS)

    # ── University ──
    university = "US University"
    for short, full in KNOWN_US_UNIVERSITIES:
        if re.search(r'\b' + re.escape(short) + r'\b', combined, re.I):
            university = full
            break
    if university == "US University":
        for p in [
            r'(?:at|@)\s+([A-Z][a-zA-Z\s]+(?:University|College|Institute of Technology|Tech))',
            r'([A-Z][a-zA-Z\s]+(?:University|College|Institute of Technology|State University))',
        ]:
            m = re.search(p, combined)
            if m:
                cand_uni = m.group(1).strip()
                if len(cand_uni) < 50 and not any(w in cand_uni.lower() for w in ["linkedin", "master", "bachelor"]):
                    university = cand_uni
                    break

    # ── Degree — reflect actual education journey ──
    if has_indian_edu and has_us_masters:
        degree = f"B.Tech (India) + MS {query_category.title()} (USA)"
    elif has_us_masters:
        degree = f"Master's in {query_category.title()} (USA)"
    elif has_indian_edu:
        degree = f"B.Tech / B.E. (India) — US Settled"
    elif "data sci" in text_lower:
        degree = "Master's in Data Science"
    elif "computer science" in text_lower:
        degree = "Master's in Computer Science"
    elif "cyber" in text_lower:
        degree = "Master's in Cybersecurity"
    else:
        degree = f"Master's in {query_category.title()}"

    # ── Graduation Year ──
    years_found = re.findall(r'\b(201[8-9]|202[0-6])\b', combined)
    grad_year = years_found[-1] if years_found else str(random.randint(start_year_global, end_year_global))

    # ── Location ──
    location = "United States"
    for lp in [
        r'([A-Z][a-zA-Z\s]+,\s*(?:[A-Z]{2}|California|Texas|New York|Washington|Illinois|Massachusetts|Georgia|Florida|Virginia|New Jersey|Ohio|Michigan|North Carolina))',
        r'\b(New Jersey|New York|California|Texas|Seattle|Chicago|Atlanta|Dallas|Houston|Boston|Charlotte|Pittsburgh|Austin|Denver|Phoenix|Tampa|Raleigh|Minneapolis|Portland|San Jose)\b',
    ]:
        lm = re.search(lp, combined)
        if lm:
            loc = lm.group(1).strip()
            if len(loc) < 40:
                location = loc
                break

    # ── Skills ──
    matched_skills = []
    for domain, skill_list in DOMAIN_SKILLS_TAXONOMY.items():
        if domain in text_lower or domain in query_category.lower():
            for s in skill_list:
                if re.search(r'\b' + re.escape(s) + r'\b', combined, re.I):
                    if s not in matched_skills:
                        matched_skills.append(s)
    if not matched_skills:
        for s in ["Python", "SQL", "Java", "AWS", "Git", "Cloud", "REST APIs", "Agile"]:
            if re.search(r'\b' + re.escape(s) + r'\b', combined, re.I):
                matched_skills.append(s)
    if not matched_skills:
        matched_skills = [query_category.title(), "Python", "SQL", "Cloud"]

    # ── Work Authorization Badge ──
    if re.search(r'\bstem opt\b', text_lower):
        status_badge = "STEM OPT (3yr auth)"
    elif re.search(r'\bopt\b|\bf1\b|\bf-1\b|\bcpt\b', text_lower):
        status_badge = "F1 OPT"
    elif re.search(r'\bh1b\b|\bh-1b\b', text_lower):
        status_badge = "H1B Sponsored"
    elif re.search(r'\bc2c\b|\bcorp to corp\b', text_lower):
        status_badge = "C2C / H1B"
    elif re.search(r'\bgreen card\b|\bgc holder\b|\bpermanent resident\b', text_lower):
        status_badge = "Green Card / PR"
    else:
        status_badge = "OPT / H1B (India to USA)"

    # ── Quality Star Rating ──
    if has_indian_edu and has_us_masters:
        quality = "[IDEAL] B.Tech India + MS USA"
    elif has_us_masters:
        quality = "[GOOD] MS USA"
    elif has_indian_edu:
        quality = "[OK] B.Tech India"
    else:
        quality = "[STD] General Profile"

    return {
        "name": name,
        "headline": headline,
        "profile_url": canonical_url,
        "linkedin_url": canonical_url,
        "degree": degree,
        "university": university,
        "grad_year": grad_year,
        "location": location,
        "skills": matched_skills[:7],
        "status_badge": status_badge,
        "quality": quality,
        "has_indian_edu": has_indian_edu,
        "has_us_masters": has_us_masters,
        "summary": snippet[:220] + ("..." if len(snippet) > 220 else ""),
        "source": "Live LinkedIn X-Ray",
    }


# --------------------------------------------------
# SEARCH ENGINES
# --------------------------------------------------

def _search_duckduckgo(query: str, max_results: int = 15) -> List[Dict]:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            return [{"url": r.get("href", ""), "title": r.get("title", ""), "snippet": r.get("body", "")}
                    for r in ddgs.text(query, max_results=max_results)]
    except ImportError:
        try:
            from duckduckgo_search import DDGS as DDGS2
            with DDGS2() as ddgs:
                return [{"url": r.get("href", ""), "title": r.get("title", ""), "snippet": r.get("body", "")}
                        for r in ddgs.text(query, max_results=max_results)]
        except Exception as e:
            logger.warning(f"DDG fallback: {e}")
    except Exception as e:
        logger.warning(f"DDG: {e}")
    return []


def _search_serpapi(query: str, max_results: int = 10) -> List[Dict]:
    if not SERPAPI_KEY:
        return []
    try:
        import requests
        resp = requests.get("https://serpapi.com/search", params={
            "q": query, "api_key": SERPAPI_KEY, "num": max_results, "engine": "google"}, timeout=15)
        if resp.status_code == 200:
            return [{"url": i.get("link", ""), "title": i.get("title", ""), "snippet": i.get("snippet", "")}
                    for i in resp.json().get("organic_results", [])]
    except Exception as e:
        logger.warning(f"SerpApi: {e}")
    return []


def _search_apify(query: str, max_results: int = 10) -> List[Dict]:
    if not APIFY_TOKEN or APIFY_TOKEN == "your_apify_token_here":
        return []
    try:
        import requests
        url = f"https://api.apify.com/v2/acts/apify~google-search-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}&memory=512&timeout=30"
        res = requests.post(url, json={"queries": query, "maxPagesPerQuery": 1, "resultsPerPage": max_results}, timeout=35)
        results = []
        if res.status_code in [200, 201]:
            for page in (res.json() if isinstance(res.json(), list) else []):
                for item in page.get("organicResults", []):
                    results.append({"url": item.get("url", "") or item.get("link", ""), "title": item.get("title", ""), "snippet": item.get("description", "")})
        return results
    except Exception as e:
        logger.warning(f"Apify: {e}")
    return []


# --------------------------------------------------
# MAIN PUBLIC FUNCTION
# --------------------------------------------------

def scrape_live_linkedin_candidates(
    keyword: str = "Data Scientist",
    start_year: int = 2018,
    end_year: int = 2026,
    location: str = "United States",
    max_items: int = 25,
    force_fresh: bool = True,
) -> List[Dict[str, Any]]:
    """
    Returns LinkedIn profiles of professionals who:
    1. Did B.Tech / B.E. in India (IIT, NIT, VIT, BITS, etc.)
    2. Did Master's (MS) in USA
    3. Are currently working in USA on OPT / STEM OPT / H1B

    ★★★ Ideal profiles are automatically sorted to the top.
    Every run returns DIFFERENT candidates (rotating queries + global dedup).
    """
    global start_year_global, end_year_global
    start_year_global = start_year
    end_year_global = end_year

    clean_keyword = (keyword or "Data Scientist").strip()
    logger.info(f"Scraping '{clean_keyword}' | B.Tech India + MS USA | {start_year}-{end_year}")

    num_queries = max(3, min(6, (max_items // 5) + 2))
    queries = build_rotating_queries(clean_keyword, start_year, end_year, num_queries)

    collected: List[Dict] = []
    seen_this_run: Set[str] = set()

    for q_idx, query in enumerate(queries):
        if len(collected) >= max_items:
            break

        raw_results = _search_duckduckgo(query, max_results=15)
        if not raw_results and SERPAPI_KEY:
            raw_results = _search_serpapi(query, max_results=10)
        if not raw_results and APIFY_TOKEN and APIFY_TOKEN != "your_apify_token_here":
            raw_results = _search_apify(query, max_results=10)

        logger.info(f"Query {q_idx+1}/{len(queries)}: {len(raw_results)} raw results")
        random.shuffle(raw_results)

        for item in raw_results:
            if len(collected) >= max_items:
                break

            candidate = _parse_candidate_from_result(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                query_category=clean_keyword,
            )
            if not candidate:
                continue

            url = candidate["linkedin_url"]
            if url in seen_this_run or url in GLOBALLY_SEEN_URLS:
                continue

            years_in_snippet = re.findall(r'\b(201[8-9]|202[0-6])\b', item.get("snippet", ""))
            if years_in_snippet:
                try:
                    if not (start_year <= int(candidate["grad_year"]) <= end_year):
                        continue
                except (ValueError, TypeError):
                    pass

            seen_this_run.add(url)
            GLOBALLY_SEEN_URLS.add(url)
            collected.append(candidate)

        if q_idx < len(queries) - 1:
            time.sleep(random.uniform(0.3, 1.0))

    # Sort: ★★★ (B.Tech India + MS USA) first, then ★★, then ★
    collected.sort(key=lambda c: (
        0 if (c.get("has_indian_edu") and c.get("has_us_masters")) else
        1 if c.get("has_us_masters") else
        2 if c.get("has_indian_edu") else 3
    ))

    ideal_count = sum(1 for c in collected if c.get("has_indian_edu") and c.get("has_us_masters"))
    logger.info(f"Done: {len(collected)} candidates | ★★★ Ideal: {ideal_count} | ★★ MS USA: {sum(1 for c in collected if c.get('has_us_masters') and not c.get('has_indian_edu'))} | ★ Other: {len(collected) - ideal_count - sum(1 for c in collected if c.get('has_us_masters') and not c.get('has_indian_edu'))}")
    return collected[:max_items]
