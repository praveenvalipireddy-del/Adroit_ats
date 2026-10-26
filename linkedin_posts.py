"""LinkedIn recruiter posts as job requirements ("Hiring a DevOps Engineer - C2C - share resumes to ...").

Many recruiters post a requirement in their feed instead of (or before) a job listing. This reads those posts:

1. harvestapi/linkedin-post-search (Apify, no LinkedIn login - nobody's account is used) searches posts
   for the keyword together with hiring words, newest first, from the last 24 hours.
2. Posts that are clearly not requirements are dropped without AI ("#OpenToWork" job seekers, posts
   without a single hiring word).
3. The AI (the Gemini-first chain) reads EVERY remaining post in full, in small batches, and says whether
   it is a real requirement and reads out title, company, location, market, type, experience, skills and
   the recruiter's email / phone. An email or phone only counts if it is written in the post.
4. Real requirements are saved as jobs (source "Recruiter Post") with the recruiter's name, headline,
   profile link and the post link - so Optimize Resume, drafts and the tracker work on them as on any job.

Cost (Apify store, Oct 2026): $0.002 per post read (+ $0.001 for a search that finds nothing). The default
50 posts per search cost about $0.10. LINKEDIN_POSTS_MAX changes the number of posts per search.
"""
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

import india_job_scrapers as ij
import resume_bot

logger = logging.getLogger("linkedin_posts")

ACTOR = "harvestapi~linkedin-post-search"
SOURCE = "Recruiter Post"
PRICE_PER_POST = 0.002
MAX_POSTS = int(os.getenv("LINKEDIN_POSTS_MAX", "50") or 50)
BATCH = 6

_SEEKER = re.compile(r"#?open\s?to\s?work|\bi\s*(?:am|'m)\s+(?:actively\s+)?(?:looking|seeking|searching)\s+for\s+(?:a\s+|new\s+)*(?:job|role|position|opportunit)"
                     r"|\bhire\s+me\b|\bmy\s+(?:resume|cv)\b", re.I)
_HIRING = re.compile(r"hiring|requirement|opening|vacanc|position|\brole\b|looking\s+for|we\s+need|need\s+(?:a|an)\b|urgent|c2c|corp\s*to\s*corp"
                     r"|\bw2\b|1099|contract|full[\s-]?time|share\s+(?:your\s+)?(?:resume|cv|profile)|send\s+(?:your\s+)?(?:resume|cv)|apply|\bjd\b", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")

AI_SYSTEM = """You read LinkedIn posts found by a recruiting team. For EACH post decide whether it is a JOB
REQUIREMENT: a recruiter, employer, vendor or hiring manager announcing an open position and asking for
candidates / resumes. NOT requirements: job seekers ("open to work", "I am looking for a role"), training or
course ads, motivational posts, news, event posts, and posts that only celebrate someone's new job.
Read the whole post. Never invent anything: a field the post doesn't state is "".
Return ONLY JSON: {"posts": [{"n": <the post number>, "is_requirement": true/false, "why": "a few words",
"title": "", "company": "", "location": "", "market": "USA" | "India" | "Other" | "Unknown",
"employment_type": "C2C / W2 / Contract / Full-time / Part-time / unknown", "experience": "",
"skills": [""], "recruiter_email": "only an address written in the post", "recruiter_phone": "only a number written in the post",
"how_to_apply": "", "summary": "the requirement in 2-4 short lines"}]}"""


def build_query(keyword: str) -> str:
    """The LinkedIn search: the keyword together with hiring words (LinkedIn's search takes AND / OR / quotes)."""
    kw = keyword.strip().replace('"', "")
    return f'"{kw}" AND (hiring OR requirement OR opening OR "looking for" OR "share resume" OR C2C OR urgent)'


def estimated_cost(max_posts: int = None) -> float:
    return round((max_posts or MAX_POSTS) * PRICE_PER_POST, 3)


def _worth_reading(content: str) -> bool:
    """Cheap first pass, before any AI: a seeker's post, or a post without one hiring word, is not a requirement."""
    if _SEEKER.search(content) and not re.search(r"\bwe(?:'re| are)\s+hiring\b|\bi(?:'m| am)\s+hiring\b", content, re.I):
        return False
    return bool(_HIRING.search(content))


def _ai_batch(posts: List[Dict]) -> Dict[int, Dict]:
    """One AI call for a few posts: {post number: verdict}. A failed call returns {} (those posts are skipped and counted)."""
    listing = "\n\n".join(f"=== POST {p['n']}\nAUTHOR: {p['author']} - {p['headline']}\n{p['content'][:3500]}" for p in posts)
    try:
        client, types = resume_bot._gemini_client() if resume_bot.gemini_configured() else (None, None)
        raw, _, _ = resume_bot._generate(client, types, AI_SYSTEM, listing, want_json=True, max_tokens=900 * len(posts) + 500)
        data = resume_bot._parse_json_object(raw) or {}
    except Exception as ex:
        logger.warning(f"AI could not read a batch of LinkedIn posts: {resume_bot._explain_gemini_error(ex)}")
        return {}
    out = {}
    for v in data.get("posts") or []:
        try:
            out[int(v.get("n"))] = v
        except (TypeError, ValueError):
            continue
    return out


def _only_if_written(value: str, content: str, pattern) -> str:
    """An email / phone the AI gives only counts if it is really in the post."""
    value = (value or "").strip()
    if value and value.lower() in content.lower():
        return value
    m = pattern.search(content)
    return m.group(0).strip() if m else ""


def search(keyword: str, market: str = "United States", max_posts: int = None, posted: str = "24h") -> Dict:
    """Returns {jobs, read, worth_reading, requirements, not_requirements, other_market, ai_failed, cost_usd, error}."""
    max_posts = max(5, min(int(max_posts or MAX_POSTS), 200))
    out = {"jobs": [], "read": 0, "worth_reading": 0, "requirements": 0, "not_requirements": 0, "other_market": 0,
           "ai_failed": 0, "cost_usd": 0.0, "error": ""}
    items, err = ij._run_actor_sync(ACTOR, {"searchQueries": [build_query(keyword)], "maxPosts": max_posts, "postedLimit": posted,
                                            "sortBy": "date", "profileScraperMode": "short"}, timeout=200)
    ij.LAST_ERROR[SOURCE] = err or ""
    if err:
        out["error"] = err
        return out
    posts = []
    for it in items:
        content = (it.get("content") or it.get("text") or "").strip()
        if not content or (it.get("type") not in (None, "post")):
            continue
        a = it.get("author") if isinstance(it.get("author"), dict) else {}
        posted_at = it.get("postedAt") if isinstance(it.get("postedAt"), dict) else {}
        posts.append({"n": len(posts) + 1, "content": content, "url": ij._first(it, "linkedinUrl", "url", "postUrl"),
                      "author": a.get("name") or ij._first(it, "authorName", default="LinkedIn member"),
                      "headline": a.get("info") or a.get("headline") or "",
                      "profile": (a.get("linkedinUrl") or "").split("?")[0], "posted": posted_at.get("postedAgoShort") or posted_at.get("date") or ""})
    out["read"] = len(posts)
    out["cost_usd"] = round(len(items) * PRICE_PER_POST, 3) if items else 0.001
    todo = [p for p in posts if _worth_reading(p["content"])]
    out["worth_reading"] = len(todo)
    out["not_requirements"] = len(posts) - len(todo)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    verdicts = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for part in pool.map(_ai_batch, batches):
            verdicts.update(part)
    want_india = market.lower() == "india"
    for p in todo:
        v = verdicts.get(p["n"])
        if v is None:
            out["ai_failed"] += 1
            continue
        if not v.get("is_requirement"):
            out["not_requirements"] += 1
            continue
        mk = (v.get("market") or "Unknown").strip().lower()
        if mk == "other" or (mk == "india" and not want_india) or (mk == "usa" and want_india):
            out["other_market"] += 1
            continue
        out["requirements"] += 1
        email = _only_if_written(v.get("recruiter_email"), p["content"], _EMAIL)
        phone = _only_if_written(v.get("recruiter_phone"), p["content"], _PHONE)
        skills = [s for s in (v.get("skills") or []) if isinstance(s, str) and s.strip()]
        head = f"Recruiter post by {p['author']}" + (f" ({p['headline']})" if p["headline"] else "")
        out["jobs"].append({
            "title": (v.get("title") or keyword)[:200],
            "company": (v.get("company") or "").strip()[:200] or "(see post)",
            "location": (v.get("location") or "").strip()[:200],
            "job_type": v.get("employment_type") if (v.get("employment_type") or "").lower() != "unknown" else "",
            "salary": "",
            "source": SOURCE,
            "url": p["url"],
            "recruiter_email": email,
            "recruiter_phone": phone,
            "recruiter_name": p["author"][:120],
            "description": (f"{head}\nProfile: {p['profile']}\nPosted: {p['posted']} on LinkedIn\n"
                            + (f"Experience: {v['experience']}\n" if v.get("experience") else "")
                            + (f"How to apply: {v['how_to_apply']}\n" if v.get("how_to_apply") else "")
                            + f"\n{p['content'][:8000]}"),
            "matched_skills": ", ".join(skills)[:500] or keyword,
            "match_score": 90,
            "is_24h": 1,
            "country": "India" if want_india else "United States",
        })
    logger.info(f"LinkedIn posts for {keyword!r} ({market}): {json.dumps({k: v for k, v in out.items() if k != 'jobs'})}")
    return out
