"""Jobs straight from company career portals (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable).

Each company publishes its open jobs as a free public feed - no key, no Apify, no cost. Job Hunt keeps a
list of companies (table career_boards) and reads their feeds:

- The list grows three ways: a verified starter list (SEED - every entry answered with live jobs when it
  was added), automatically from the apply links of jobs we already have (discover), and by hand
  (a recruiter pastes any careers link - add_board checks it answers before saving it).
- sync_all() reads every active board. Only US and India jobs posted in the last DAYS days are kept
  (Workday / SmartRecruiters boards list thousands). A job's scraped_at is its POSTING time, so the Jobs
  "Last 24h" filter means "posted in the last 24h" for these jobs - an old posting never looks new.
- Saved as source "Company site (<portal>)"; the job type comes from the feed when it says it.

Nothing is invented: a company name we can't read is the board's own name (title-cased), a job without a
posting date is kept only once (its first-seen time) and never refreshed to look new.
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

import requests

logger = logging.getLogger("career_boards")

DAYS = 14                 # keep jobs posted in the last two weeks
PER_BOARD_CAP = 150       # at most this many new jobs per company per sync
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (JobHunt career-board reader)", "Accept": "application/json"}
PORTAL_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday",
                "smartrecruiters": "SmartRecruiters", "workable": "Workable"}

# Verified 2026-10-27: each answered its public feed with open jobs. (ats, slug, company name)
SEED = [
    ("greenhouse", "coherehealth", "Cohere Health"), ("greenhouse", "airbnb", "Airbnb"), ("greenhouse", "stripe", "Stripe"),
    ("greenhouse", "databricks", "Databricks"), ("greenhouse", "cloudflare", "Cloudflare"), ("greenhouse", "datadog", "Datadog"),
    ("greenhouse", "gitlab", "GitLab"), ("greenhouse", "robinhood", "Robinhood"), ("greenhouse", "instacart", "Instacart"),
    ("greenhouse", "dropbox", "Dropbox"), ("greenhouse", "figma", "Figma"), ("greenhouse", "twilio", "Twilio"),
    ("greenhouse", "okta", "Okta"), ("greenhouse", "elastic", "Elastic"), ("greenhouse", "mongodb", "MongoDB"),
    ("greenhouse", "pinterest", "Pinterest"), ("greenhouse", "lyft", "Lyft"), ("greenhouse", "reddit", "Reddit"),
    ("greenhouse", "discord", "Discord"), ("greenhouse", "asana", "Asana"), ("greenhouse", "affirm", "Affirm"),
    ("greenhouse", "brex", "Brex"), ("greenhouse", "chime", "Chime"), ("greenhouse", "gusto", "Gusto"),
    ("greenhouse", "samsara", "Samsara"), ("greenhouse", "toast", "Toast"), ("greenhouse", "coinbase", "Coinbase"),
    ("greenhouse", "squarespace", "Squarespace"), ("greenhouse", "purestorage", "Pure Storage"), ("greenhouse", "rubrik", "Rubrik"),
    ("greenhouse", "newrelic", "New Relic"), ("greenhouse", "anthropic", "Anthropic"), ("greenhouse", "groww", "Groww"),
    ("greenhouse", "druva", "Druva"), ("greenhouse", "hackerrank", "HackerRank"), ("greenhouse", "zscaler", "Zscaler"),
    ("greenhouse", "appian", "Appian"), ("greenhouse", "flexport", "Flexport"), ("greenhouse", "doordashusa", "DoorDash"),
    ("greenhouse", "cockroachlabs", "Cockroach Labs"),
    ("lever", "palantir", "Palantir"), ("lever", "spotify", "Spotify"), ("lever", "meesho", "Meesho"), ("lever", "cred", "CRED"),
    ("ashby", "openai", "OpenAI"), ("ashby", "ramp", "Ramp"), ("ashby", "notion", "Notion"), ("ashby", "linear", "Linear"),
    ("ashby", "perplexity", "Perplexity"), ("ashby", "cursor", "Cursor"),
    ("smartrecruiters", "BoschGroup", "Bosch"), ("smartrecruiters", "ServiceNow", "ServiceNow"), ("smartrecruiters", "Experian", "Experian"),
    ("workday", "nvidia|wd5|NVIDIAExternalCareerSite", "NVIDIA"), ("workday", "salesforce|wd12|External_Career_Site", "Salesforce"),
    ("workday", "adobe|wd5|external_experienced", "Adobe"), ("workday", "intel|wd1|External", "Intel"),
    ("workday", "capitalone|wd12|Capital_One", "Capital One"), ("workday", "mastercard|wd1|CorporateCareers", "Mastercard"),
    ("workday", "target|wd5|targetcareers", "Target"), ("workday", "workday|wd5|Workday", "Workday"), ("workday", "paypal|wd1|jobs", "PayPal"),
    ("workable", "huggingface", "Hugging Face"),
]


# ------------------------------------------------------------------ http (replaceable in tests)
def _http_json(url: str, body: Optional[dict] = None):
    if body is None:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    else:
        r = requests.post(url, headers={**HEADERS, "Content-Type": "application/json"}, data=json.dumps(body), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


http_json: Callable = _http_json


def _parallel(urls: List[str], parse: Callable, workers: int = 3) -> list:
    """GET each url (a few at a time, one retry) and keep only parse(answer) - the small piece we need, so a board's
    hundreds of full posting documents are never held in memory together. None where one still failed."""
    from concurrent.futures import ThreadPoolExecutor

    def one(u):
        for attempt in (1, 2):
            try:
                return parse(http_json(u))
            except Exception as ex:
                if attempt == 2:
                    logger.info(f"Career board detail skipped ({u[:80]}): {ex}")
                    return None
                time.sleep(1.5)
    if not urls:
        return []
    with ThreadPoolExecutor(min(workers, len(urls))) as pool:
        return list(pool.map(one, urls))


def _text(html: str) -> str:
    import job_description_fetch
    return job_description_fetch.html_to_text(html or "")


# ------------------------------------------------------------------ which portal a link belongs to
def parse_board_url(url: str) -> Optional[Dict]:
    """{'ats', 'slug', 'careers_url'} for a careers / job link on a supported portal, else None."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    try:
        u = urlparse(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    if re.fullmatch(r"(job-)?boards(\.eu)?\.greenhouse\.io", host):
        slug = (parse_qs(u.query).get("for") or [None])[0] if parts[:1] == ["embed"] else (parts[0] if parts else None)
        if slug and re.fullmatch(r"[A-Za-z0-9_-]+", slug):
            return {"ats": "greenhouse", "slug": slug.lower(), "careers_url": f"https://job-boards.greenhouse.io/{slug.lower()}"}
    if re.fullmatch(r"jobs(\.eu)?\.lever\.co", host) and parts and re.fullmatch(r"[A-Za-z0-9_.-]+", parts[0]):
        return {"ats": "lever", "slug": parts[0].lower(), "careers_url": f"https://jobs.lever.co/{parts[0].lower()}"}
    if host == "jobs.ashbyhq.com" and parts and re.fullmatch(r"[A-Za-z0-9_.%-]+", parts[0]):
        return {"ats": "ashby", "slug": parts[0], "careers_url": f"https://jobs.ashbyhq.com/{parts[0]}"}
    m = re.fullmatch(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com", host)
    if m and parts:
        site_parts = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        if site_parts and re.fullmatch(r"[A-Za-z0-9_-]+", site_parts[0]):
            slug = f"{m.group(1)}|{m.group(2)}|{site_parts[0]}"
            return {"ats": "workday", "slug": slug, "careers_url": f"https://{host}/{site_parts[0]}"}
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and parts and re.fullmatch(r"[A-Za-z0-9_-]+", parts[0]):
        return {"ats": "smartrecruiters", "slug": parts[0], "careers_url": f"https://jobs.smartrecruiters.com/{parts[0]}"}
    if host == "apply.workable.com" and parts and parts[0] not in ("api", "j") and re.fullmatch(r"[A-Za-z0-9_-]+", parts[0]):
        return {"ats": "workable", "slug": parts[0].lower(), "careers_url": f"https://apply.workable.com/{parts[0].lower()}/"}
    return None


_LINK_RE = re.compile(r"https?://(?:(?:job-)?boards(?:\.eu)?\.greenhouse\.io|jobs(?:\.eu)?\.lever\.co|jobs\.ashbyhq\.com|"
                      r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com|(?:jobs|careers)\.smartrecruiters\.com|apply\.workable\.com)/[^\s\"'<>)]+", re.I)


def find_board_links(text: str) -> List[Dict]:
    out, seen = [], set()
    for m in _LINK_RE.finditer(text or ""):
        b = parse_board_url(m.group(0))
        if b and (b["ats"], b["slug"]) not in seen:
            seen.add((b["ats"], b["slug"]))
            out.append(b)
    return out


# ------------------------------------------------------------------ where a job is
_US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI "
              "SC SD TN TX UT VT VA WA WV WI WY DC").split()
_US_WORDS = re.compile(r"\b(united states|u\.s\.a?\.?|usa|us-remote|remote[\s,-]*us|us[\s,-]*remote|alabama|alaska|arizona|arkansas|california|colorado|"
                       r"connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|"
                       r"massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|"
                       r"new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|"
                       r"tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|san francisco|seattle|austin|boston|"
                       r"chicago|los angeles|denver|atlanta|dallas|houston|palo alto|mountain view|sunnyvale|san jose|santa clara|san mateo|"
                       r"bay area|nyc|brooklyn|miami|phoenix|portland|salt lake|raleigh|charlotte|pittsburgh|philadelphia|minneapolis|"
                       r"san diego|irvine|plano|reston|mclean|arlington|richmond|tempe|nashville|detroit|columbus|indianapolis)\b", re.I)
_IN_WORDS = re.compile(r"\b(india|bengaluru|bangalore|hyderabad|pune|mumbai|gurgaon|gurugram|noida|chennai|delhi|new delhi|kolkata|"
                       r"ahmedabad|kochi|coimbatore|jaipur|chandigarh|indore|trivandrum|thiruvananthapuram|mysore|vadodara|nagpur|"
                       r"bhubaneswar|visakhapatnam|telangana|karnataka|maharashtra|tamil nadu|haryana|uttar pradesh|kerala)\b", re.I)
_US_STATE_ABBR = re.compile(r",\s*(" + "|".join(_US_STATES) + r")\b")


def country_of(location: str, code: str = "") -> Optional[str]:
    """'United States' / 'India' / None (another country, or nothing says where)."""
    c = (code or "").strip().upper()
    if c in ("US", "USA"):
        return "United States"
    if c in ("IN", "IND"):
        return "India"
    loc = location or ""
    us = _US_WORDS.search(loc) or _US_STATE_ABBR.search(loc)
    ind = _IN_WORDS.search(loc)
    if us and ind:
        return "United States" if us.start() < ind.start() else "India"
    if us:
        return "United States"
    if ind:
        return "India"
    return None


# ------------------------------------------------------------------ reading one board
def _iso(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s / 1000 if s > 1e11 else s, tz=timezone.utc)
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _workday_posted(text: str, now: datetime) -> Optional[datetime]:
    t = (text or "").lower()
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*days?", t)
    return now - timedelta(days=int(m.group(1))) if m else None


def fetch_board(ats: str, slug: str, company: str = "", days: int = DAYS, now: Optional[datetime] = None) -> List[Dict]:
    """The board's US / India jobs posted in the last `days` days, newest first, each:
    {title, company, location, country, url, posted (datetime|None), job_type, description}."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    company = company or slug.split("|")[0].replace("-", " ").title()
    jobs: List[Dict] = []

    def keep(title, location, url, posted, job_type, description, code=""):
        country = country_of(location, code)
        if not title or not url or not country:
            return
        if posted is not None and posted < cutoff:
            return
        jobs.append({"title": title.strip()[:200], "company": company, "location": (location or "").strip()[:200], "country": country,
                     "url": url, "posted": posted, "job_type": job_type or "", "description": (description or "").strip()})

    if ats == "greenhouse":
        d = http_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        for j in d.get("jobs", []):
            import html as html_lib
            keep(j.get("title"), (j.get("location") or {}).get("name"), j.get("absolute_url"),
                 _iso(j.get("first_published") or j.get("updated_at")), "", _text(html_lib.unescape(j.get("content") or "")))
    elif ats == "lever":
        d = http_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        for j in d if isinstance(d, list) else []:
            cat = j.get("categories") or {}
            locs = "; ".join(dict.fromkeys(x for x in [cat.get("location") or ""] + (cat.get("allLocations") or []) if x))
            body = "\n".join([j.get("descriptionPlain") or ""] + [f"{l.get('text')}\n{_text(l.get('content'))}" for l in (j.get("lists") or [])]
                             + [j.get("additionalPlain") or ""])
            keep(j.get("text"), locs, j.get("hostedUrl"), _iso(j.get("createdAt")), cat.get("commitment") or "", body, j.get("country") or "")
    elif ats == "ashby":
        d = http_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
        for j in d.get("jobs", []):
            if j.get("isListed") is False:
                continue
            addr = ((j.get("address") or {}).get("postalAddress") or {})
            locs = "; ".join(x for x in [j.get("location") or ""] + [s.get("location") or "" for s in (j.get("secondaryLocations") or [])]
                             + [addr.get("addressCountry") or ""] if x)
            keep(j.get("title"), locs, j.get("jobUrl"), _iso(j.get("publishedAt")), j.get("employmentType") or "",
                 j.get("descriptionPlain") or _text(j.get("descriptionHtml")))
    elif ats == "smartrecruiters":
        picked, offset = [], 0
        while offset < 1000 and len(picked) < PER_BOARD_CAP:
            d = http_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
            page = d.get("content", [])
            for j in page:
                code = ((j.get("location") or {}).get("country") or "").upper()
                posted = _iso(j.get("releasedDate"))
                if code in ("US", "IN") and not (posted and posted < cutoff):
                    picked.append((j, posted, code))
            offset += 100
            if len(page) < 100:
                break
        picked = [(j, posted, code) for j, posted, code in picked[:PER_BOARD_CAP]]

        def sr_body(det):
            secs = ((det.get("jobAd") or {}).get("sections") or {})
            return "\n".join(f"{(s or {}).get('title') or ''}\n{_text((s or {}).get('text'))}" for s in secs.values())[:20000]
        bodies = _parallel([f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j.get('id')}" for j, _, _ in picked], sr_body)
        for (j, posted, code), body in zip(picked, bodies):
            if body is None:
                continue
            loc = j.get("location") or {}
            keep(j.get("name"), ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
                 f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", posted, ((j.get("typeOfEmployment") or {}).get("label") or ""),
                 body, code)
    elif ats == "workday":
        tenant, wd, site = slug.split("|")
        base = f"https://{tenant}.{wd}.myworkdayjobs.com"
        picked, offset, stale_pages = [], 0, 0
        while offset < 600 and len(picked) < PER_BOARD_CAP * 2:
            d = http_json(f"{base}/wday/cxs/{tenant}/{site}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
            page = d.get("jobPostings", [])
            fresh = 0
            for j in page:
                posted = _workday_posted(j.get("postedOn"), now)
                if posted is not None and posted < cutoff:
                    continue
                fresh += 1
                lt = j.get("locationsText") or ""
                # a single named place outside the US / India: its details are never fetched
                if lt and not re.search(r"\d+\s+locations", lt, re.I) and country_of(lt) is None:
                    continue
                picked.append(({"title": j.get("title"), "externalPath": j.get("externalPath"), "locationsText": lt}, posted))
            stale_pages = stale_pages + 1 if not fresh else 0
            offset += 20
            if len(page) < 20 or stale_pages >= 3:
                break

        def wd_info(det):
            info = det.get("jobPostingInfo") or {}
            code = ((info.get("country") or {}).get("alpha2Code") or "") if isinstance(info.get("country"), dict) else ""
            return {"title": info.get("title"), "location": info.get("location"), "more": info.get("additionalLocations") or [],
                    "url": info.get("externalUrl"), "start": info.get("startDate"), "time": info.get("timeType") or "",
                    "text": _text(info.get("jobDescription"))[:20000], "code": code}
        details = _parallel([f"{base}/wday/cxs/{tenant}/{site}{j['externalPath']}" for j, _ in picked], wd_info)
        for (j, posted), info in zip(picked, details):
            if info is None:
                continue
            locs = "; ".join([info["location"] or j["locationsText"] or ""] + info["more"])
            keep(info["title"] or j["title"], locs, info["url"] or f"{base}/{site}{j['externalPath']}",
                 _iso(info["start"]) or posted, info["time"], info["text"], info["code"])
    elif ats == "workable":
        d = http_json(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs", {"query": "", "location": [], "department": [], "worktype": [], "remote": []})
        for j in d.get("results", []):
            loc = j.get("location") or {}
            posted = _iso(j.get("published"))
            if posted and posted < cutoff:
                continue
            det = http_json(f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{j.get('shortcode')}")
            body = "\n".join(_text(det.get(k)) for k in ("description", "requirements", "benefits") if det.get(k))
            keep(j.get("title"), ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
                 f"https://apply.workable.com/{slug}/j/{j.get('shortcode')}/", posted, j.get("type") or "", body, loc.get("countryCode") or "")
    else:
        raise ValueError(f"Unknown portal {ats}")
    jobs.sort(key=lambda x: x["posted"] or now, reverse=True)
    return jobs[:PER_BOARD_CAP]


# ------------------------------------------------------------------ the list of companies
def ensure_seed(conn) -> int:
    cur = conn.cursor()
    added = 0
    for ats, slug, company in SEED:
        cur.execute("SELECT id FROM career_boards WHERE ats = ? AND slug = ?", (ats, slug))
        if cur.fetchone():
            continue
        cur.execute("INSERT INTO career_boards (ats, slug, company, careers_url, found_via, active) VALUES (?, ?, ?, ?, 'starter list', 1)",
                    (ats, slug, company, _careers_url(ats, slug)))
        added += 1
    conn.commit()
    return added


def _careers_url(ats: str, slug: str) -> str:
    if ats == "workday":
        t, wd, site = slug.split("|")
        return f"https://{t}.{wd}.myworkdayjobs.com/{site}"
    return {"greenhouse": f"https://job-boards.greenhouse.io/{slug}", "lever": f"https://jobs.lever.co/{slug}",
            "ashby": f"https://jobs.ashbyhq.com/{slug}", "smartrecruiters": f"https://jobs.smartrecruiters.com/{slug}",
            "workable": f"https://apply.workable.com/{slug}/"}.get(ats, "")


def add_board(conn, url: str, company: str = "", found_via: str = "added by hand", check: bool = True) -> Dict:
    """Add a company by any of its careers / job links. With check, the feed must answer first."""
    b = parse_board_url(url)
    if not b:
        return {"status": "unsupported", "error": "That link isn't on a supported careers portal (Greenhouse, Lever, Ashby, Workday, "
                                                  "SmartRecruiters, Workable)."}
    cur = conn.cursor()
    cur.execute("SELECT id, company FROM career_boards WHERE ats = ? AND slug = ?", (b["ats"], b["slug"]))
    row = cur.fetchone()
    if row:
        return {"status": "exists", "id": row[0], "company": row[1], "ats": b["ats"]}
    name = (company or "").strip()[:150]
    if check:
        try:
            if b["ats"] == "greenhouse" and not name:
                name = (http_json(f"https://boards-api.greenhouse.io/v1/boards/{b['slug']}") or {}).get("name") or ""
            fetch_board(b["ats"], b["slug"], name, days=3650)       # any answer at all proves the board exists
        except Exception as ex:
            return {"status": "error", "error": f"The {PORTAL_NAMES[b['ats']]} careers feed for '{b['slug']}' didn't answer ({str(ex)[:120]})."}
    name = name or b["slug"].split("|")[0].replace("-", " ").title()
    cur.execute("INSERT INTO career_boards (ats, slug, company, careers_url, found_via, active) VALUES (?, ?, ?, ?, ?, 1)",
                (b["ats"], b["slug"], name, b["careers_url"], found_via))
    conn.commit()
    cur.execute("SELECT id FROM career_boards WHERE ats = ? AND slug = ?", (b["ats"], b["slug"]))
    return {"status": "added", "id": cur.fetchone()[0], "company": name, "ats": b["ats"]}


def discover(conn, limit_rows: int = 5000) -> int:
    """Companies found in the apply links / text of jobs we already have (Indeed, LinkedIn, Dice, emails ...)."""
    cur = conn.cursor()
    cur.execute("SELECT url, description FROM jobs ORDER BY id DESC LIMIT ?", (limit_rows,))
    found = {}
    for r in cur.fetchall():
        for b in find_board_links(f"{r[0] or ''} {r[1] or ''}"):
            found[(b["ats"], b["slug"])] = b
    added = 0
    for (ats, slug), b in found.items():
        cur.execute("SELECT id FROM career_boards WHERE ats = ? AND slug = ?", (ats, slug))
        if cur.fetchone():
            continue
        cur.execute("INSERT INTO career_boards (ats, slug, company, careers_url, found_via, active) VALUES (?, ?, ?, ?, 'found in a job link', 1)",
                    (ats, slug, slug.split("|")[0].replace("-", " ").title(), b["careers_url"]))
        added += 1
    conn.commit()
    return added


def list_boards(conn) -> List[Dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM career_boards ORDER BY company")
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["portal"] = PORTAL_NAMES.get(d["ats"], d["ats"])
        out.append(d)
    return out


# ------------------------------------------------------------------ saving jobs
def _upsert_job(conn, job: Dict, ats: str, now: datetime) -> bool:
    """Insert a new job (True) or leave an existing one alone (False). scraped_at = posting time, never refreshed."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM jobs WHERE url = ?", (job["url"],))
    if cur.fetchone():
        return False
    posted = (job["posted"] or now).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    desc = job["description"] or f"{job['title']} at {job['company']} ({job['location']})"
    cur.execute("""INSERT INTO jobs (title, company, location, job_type, salary, source, url, recruiter_email, recruiter_phone, recruiter_name,
                                     description, full_description, matched_skills, match_score, country, status, is_24h, is_seed_example, scraped_at)
                   VALUES (?, ?, ?, ?, '', ?, ?, '', '', '', ?, ?, '', 0, ?, 'Open', 1, 0, ?)""",
                (job["title"], job["company"], job["location"], job["job_type"], f"Company site ({PORTAL_NAMES[ats]})", job["url"],
                 desc[:1500], desc[:20000], job["country"], posted))
    return True


def sync_board(conn, board: Dict, now: Optional[datetime] = None, fetched: Optional[List[Dict]] = None, fetch_error: Optional[Exception] = None) -> Dict:
    """Read one board (or save what was already read for it - sync_all reads several at once) and record the result."""
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor()
    try:
        if fetch_error is not None:
            raise fetch_error
        jobs = fetched if fetched is not None else fetch_board(board["ats"], board["slug"], board.get("company") or "", now=now)
        new = sum(1 for j in jobs if _upsert_job(conn, j, board["ats"], now))
        cur.execute("UPDATE career_boards SET last_checked_at = ?, last_job_count = ?, last_new = ?, last_error = '' WHERE id = ?",
                    (now.strftime("%Y-%m-%d %H:%M:%S"), len(jobs), new, board["id"]))
        conn.commit()
        return {"company": board.get("company"), "jobs": len(jobs), "new": new}
    except Exception as ex:
        try:
            conn.rollback()
        except Exception:
            pass
        cur = conn.cursor()
        cur.execute("UPDATE career_boards SET last_checked_at = ?, last_error = ? WHERE id = ?",
                    (now.strftime("%Y-%m-%d %H:%M:%S"), str(ex)[:300], board["id"]))
        conn.commit()
        logger.warning(f"Career board {board.get('ats')}/{board.get('slug')} failed: {ex}")
        return {"company": board.get("company"), "jobs": 0, "new": 0, "error": str(ex)[:200]}


PARALLEL_BOARDS = 3          # the server has 512 MB: three companies' postings in memory at a time
KEEP_DAYS = 30               # company-site jobs older than this are removed (unless an application uses them)


def prune_old(conn, now: Optional[datetime] = None) -> int:
    """Remove company-site jobs posted more than KEEP_DAYS ago that no application points to (keeps the table small)."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    cur.execute("""DELETE FROM jobs WHERE source LIKE 'Company site%' AND scraped_at < ?
                   AND id NOT IN (SELECT job_id FROM applications)""", (cutoff,))
    n = cur.rowcount or 0
    conn.commit()
    return n


def sync_all(conn, time_budget_s: float = 1500) -> Dict:
    """Read every active board (oldest-checked first), PARALLEL_BOARDS at a time; each board's jobs are saved
    as soon as it has been read, so a restart part-way keeps what was done (and the next run starts with the rest)."""
    import gc
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    started = time.monotonic()
    try:
        discover(conn)
    except Exception as ex:
        logger.warning(f"Career board discovery failed: {ex}")
    try:
        prune_old(conn)
    except Exception as ex:
        logger.warning(f"Old company-site jobs not pruned: {ex}")
    cur = conn.cursor()
    cur.execute("SELECT * FROM career_boards WHERE active = 1 ORDER BY COALESCE(last_checked_at, '') ASC, id ASC")
    boards = [dict(r) for r in cur.fetchall()]
    totals = {"boards": 0, "jobs": 0, "new": 0, "errors": 0}
    now = datetime.now(timezone.utc)

    def read(b):
        try:
            return b, fetch_board(b["ats"], b["slug"], b.get("company") or "", now=now), None
        except Exception as ex:
            return b, None, ex

    # A rolling window of PARALLEL_BOARDS companies: a finished company is saved and dropped before the next
    # one starts, so at most three companies' postings are ever in memory.
    pool = ThreadPoolExecutor(PARALLEL_BOARDS)
    queue = list(boards)
    pending = set()
    try:
        while queue or pending:
            while queue and len(pending) < PARALLEL_BOARDS and time.monotonic() - started <= time_budget_s:
                pending.add(pool.submit(read, queue.pop(0)))
            if not pending:
                break
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                b, jobs, err = fut.result()
                r = sync_board(conn, b, now=now, fetched=jobs, fetch_error=err)
                totals["boards"] += 1
                totals["jobs"] += r["jobs"]
                totals["new"] += r["new"]
                totals["errors"] += 1 if r.get("error") else 0
            del done, fut, jobs
            gc.collect()            # free those companies' postings before the next ones
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return totals


LOCK_KEY = "career_sync_started"


def claim_sync(conn, minutes: int = 40) -> bool:
    """One sync at a time across the server's workers: True if this one may run (no other started in the last `minutes`)."""
    cur = conn.cursor()
    now = datetime.now(timezone.utc)
    cur.execute("SELECT meta_value FROM app_meta WHERE meta_key = ?", (LOCK_KEY,))
    row = cur.fetchone()
    if row and row[0]:
        try:
            if now - datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) < timedelta(minutes=minutes):
                return False
        except ValueError:
            pass
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    if row:
        cur.execute("UPDATE app_meta SET meta_value = ? WHERE meta_key = ?", (stamp, LOCK_KEY))
    else:
        cur.execute("INSERT INTO app_meta (meta_key, meta_value) VALUES (?, ?)", (LOCK_KEY, stamp))
    conn.commit()
    return True


def release_sync(conn):
    cur = conn.cursor()
    cur.execute("UPDATE app_meta SET meta_value = '' WHERE meta_key = ?", (LOCK_KEY,))
    conn.commit()
