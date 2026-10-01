"""Fetch the FULL job description of a scraped posting, on demand (one posting per click).

Scraped jobs only store a one-line summary, which is too little for the Resume Optimizer. When a
recruiter clicks "Optimize Resume" on a LinkedIn or Dice job, this reads that one public posting:

- LinkedIn: the public guest job-posting page (no login), description section
  `div.show-more-less-html__markup`. Same kind of public endpoint the job search already uses.
- Dice: the posting page's schema.org JobPosting JSON-LD `description`.

Free (plain HTTP, no Apify). Only linkedin.com / dice.com are ever contacted, and the request URL
is rebuilt from the job id - a stored URL is never fetched as-is (no arbitrary hosts). Nothing is
invented: anything that can't be read returns (None, reason).
"""
import json
import logging
import re
import urllib.parse
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_description_fetch")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 15
MIN_CHARS = 200
MAX_CHARS = 30000

_LINKEDIN_ID_RES = (
    re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})(?:[/?#]|$)"),
    re.compile(r"[?&](?:currentJobId|jobId)=(\d{6,})"),
)
_DICE_ID_RE = re.compile(r"/job-detail/([0-9a-fA-F-]{16,64})(?:[/?#]|$)")
_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_host(url: str, domain: str) -> bool:
    h = _host(url)
    return h == domain or h.endswith("." + domain)


def linkedin_job_id(url: str) -> Optional[str]:
    if not _is_host(url, "linkedin.com"):
        return None
    for rx in _LINKEDIN_ID_RES:
        m = rx.search(url)
        if m:
            return m.group(1)
    return None


def dice_job_id(url: str) -> Optional[str]:
    if not _is_host(url, "dice.com"):
        return None
    m = _DICE_ID_RE.search(url)
    return m.group(1) if m else None


def supported(url: Optional[str]) -> bool:
    return bool(url) and bool(linkedin_job_id(url) or dice_job_id(url))


def html_to_text(html: str) -> str:
    """Readable plain text: one line per paragraph/heading, "- " for list items, no blank-line runs."""
    soup = BeautifulSoup(html or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert(0, "\n- ")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")
    text = soup.get_text("")
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if not ln:
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(ln)
        blank = False
    return "\n".join(out).strip()[:MAX_CHARS]


def _get(url: str) -> Tuple[Optional[str], str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as ex:
        logger.warning("job description fetch failed for %s: %s", url, ex)
        return None, "Could not reach the job site."
    if r.status_code in (404, 410):
        return None, "The posting is no longer available."
    if r.status_code == 429:
        return None, "The job site is limiting requests right now - try again in a minute."
    if r.status_code != 200:
        return None, f"The job site returned an error ({r.status_code})."
    return r.content.decode("utf-8", errors="replace"), ""


def _linkedin(job_id: str) -> Tuple[Optional[str], str]:
    html, err = _get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    if html is None:
        return None, err
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one("div.show-more-less-html__markup") or soup.select_one("div.description__text")
    if not el:
        return None, "The posting page has no job description section (LinkedIn may have changed its page)."
    return html_to_text(str(el)), ""


def _job_posting_description(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or sc.get_text() or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else (data.get("@graph") if isinstance(data, dict) and "@graph" in data else [data])
        for it in items or []:
            if isinstance(it, dict) and it.get("@type") == "JobPosting" and it.get("description"):
                return str(it["description"])
    return None


def _dice(job_id: str) -> Tuple[Optional[str], str]:
    html, err = _get(f"https://www.dice.com/job-detail/{job_id}")
    if html is None:
        return None, err
    desc = _job_posting_description(html)
    if not desc:
        return None, "The posting page has no job description data (Dice may have changed its page)."
    return html_to_text(desc), ""


def fetch_full_description(url: Optional[str]) -> Tuple[Optional[str], str]:
    """(full description text, "") or (None, reason a recruiter can read)."""
    if not url:
        return None, "This job has no posting link."
    li_id, dice_id = linkedin_job_id(url), dice_job_id(url)
    if li_id:
        text, err = _linkedin(li_id)
        site = "LinkedIn"
    elif dice_id:
        text, err = _dice(dice_id)
        site = "Dice"
    else:
        return None, "Automatic reading only works for LinkedIn and Dice postings."
    if text is None:
        return None, err
    if len(text) < MIN_CHARS:
        return None, f"The {site} posting's description is too short to be the full job description."
    return text, ""
