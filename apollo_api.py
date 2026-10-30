"""Super Apply's hiring-team finder - Apollo.io (APOLLO_API_KEY in the server environment, never in code or chat).

Two calls, and only the second one costs:
- search_hiring_team(): Apollo people search for recruiters / talent acquisition / HR / hiring managers at the job's
  company, in the job's country. FREE (no credits). Returns no emails; last names may be partly hidden.
- reveal(): Apollo people match for ONE person the recruiter picked. 1 credit when Apollo matches the person - even if
  it then has no verified email. Only an email Apollo marks "verified" is ever used: a guessed / unverified address
  bounces and hurts the consultant's Gmail, so it is reported and not returned.

Results are filtered to people whose current employer really is the job's company (Apollo's keyword search can match
the name elsewhere). Nothing is invented: no email patterns, no guessed addresses.
"""
import logging
import os
import re
from typing import Dict, List

import requests

logger = logging.getLogger("apollo_api")

BASE = "https://api.apollo.io/api/v1"
TIMEOUT = 25
HIRING_TITLES = ["recruiter", "technical recruiter", "talent acquisition", "talent acquisition partner", "talent acquisition specialist",
                 "hr manager", "human resources", "hr business partner", "hiring manager"]
_SUFFIXES = {"inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "pvt", "private", "plc", "gmbh", "the",
             "technologies", "technology", "group", "holdings", "india", "usa", "us"}


class ApolloError(RuntimeError):
    def __init__(self, message: str, plan_blocked: bool = False):
        super().__init__(message)
        self.plan_blocked = plan_blocked      # the Apollo plan has no API access (Free plan) - not something a retry fixes


def api_key() -> str:
    return (os.getenv("APOLLO_API_KEY") or "").strip()


def configured() -> bool:
    return bool(api_key())


def _post(path: str, body: Dict) -> Dict:
    key = api_key()
    if not key:
        raise ApolloError("Apollo isn't connected: add APOLLO_API_KEY in Render > Environment.")
    try:
        r = requests.post(f"{BASE}{path}", json=body, timeout=TIMEOUT,
                          headers={"X-Api-Key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"})
    except requests.RequestException as ex:
        raise ApolloError(f"Apollo didn't answer ({str(ex)[:120]}).")
    if r.status_code == 401:
        raise ApolloError("Apollo refused the API key - create a new one (Apollo > Settings > API Keys) and update APOLLO_API_KEY in Render.")
    if r.status_code in (402, 403):
        said = _message(r)
        if re.search(r"free plan|not included|upgrade|paid plan", said, re.I):
            raise ApolloError("Your Apollo account is on the Free plan, and Apollo doesn't allow API searches on it - so Job Hunt "
                              "can't look up the hiring team automatically. A paid Apollo plan (any tier) turns this on. "
                              "Until then, use the free way below.", plan_blocked=True)
        raise ApolloError("Your Apollo plan doesn't allow this API call (Apollo said: "
                          f"{said}). Use a master API key, or the plan needs an upgrade.")
    if r.status_code == 422:
        raise ApolloError(f"Apollo couldn't use that request ({_message(r)}).")
    if r.status_code == 429:
        raise ApolloError("Apollo's rate limit was reached - try again in a minute.")
    if r.status_code >= 400:
        raise ApolloError(f"Apollo error {r.status_code}: {_message(r)}")
    try:
        return r.json()
    except ValueError:
        raise ApolloError("Apollo sent an unreadable answer.")


http_post = _post            # replaceable in tests


def _message(r) -> str:
    try:
        d = r.json()
        return str(d.get("error") or d.get("message") or d)[:200]
    except ValueError:
        return (r.text or "")[:200]


def _tokens(name: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t not in _SUFFIXES}


def same_company(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def search_hiring_team(company: str, country: str = "", per_page: int = 25) -> List[Dict]:
    """Recruiters / TA / HR / hiring managers at `company` (FREE). Each: {id, name, title, location, linkedin_url, has_email}."""
    company = (company or "").strip()
    if not company or company.lower() in ("(company not shown)", "(see post)", "confidential", "direct client"):
        raise ApolloError("This job doesn't name the company, so there is no hiring team to look up.")
    body = {"q_keywords": company, "person_titles": HIRING_TITLES, "include_similar_titles": True, "page": 1, "per_page": per_page}
    if country:
        body["person_locations"] = [country]
    data = http_post("/mixed_people/api_search", body)
    out = []
    for p in data.get("people") or []:
        org = (p.get("organization") or {}).get("name") or p.get("organization_name") or ""
        if not same_company(org, company):
            continue
        last = p.get("last_name") or p.get("last_name_obfuscated") or ""
        out.append({"id": p.get("id"), "name": f"{p.get('first_name') or ''} {last}".strip(), "title": p.get("title") or "",
                    "company": org, "location": ", ".join(x for x in (p.get("city"), p.get("state"), p.get("country")) if x),
                    "linkedin_url": p.get("linkedin_url") or "", "has_email": p.get("has_email")})     # None = Apollo didn't say
    # recruiters / TA first, then HR, then managers
    rank = lambda t: 0 if re.search(r"recruit|talent", t, re.I) else 1 if re.search(r"\bhr\b|human|people", t, re.I) else 2  # noqa: E731
    out.sort(key=lambda x: rank(x["title"]))
    return [x for x in out if x["id"]][:10]


def reveal(person_id: str) -> Dict:
    """The person's work email (1 credit when Apollo matches them). {name, title, company, email, email_status, verified, linkedin_url}."""
    if not re.fullmatch(r"[a-f0-9]{24}", person_id or ""):
        raise ApolloError("That isn't an Apollo person id.")
    data = http_post("/people/match", {"id": person_id, "reveal_personal_emails": False})
    p = data.get("person") or {}
    if not p:
        return {"matched": False, "verified": False, "email": "", "note": "Apollo couldn't match this person (no credit used)."}
    status = (p.get("email_status") or "").lower()
    email = (p.get("email") or "").strip()
    verified = bool(email) and status == "verified" and "email_not_unlocked" not in email
    org = p.get("organization") or {}
    return {"matched": True, "verified": verified, "email": email if verified else "", "email_status": status or "none",
            "name": p.get("name") or f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip(), "title": p.get("title") or "",
            "company": org.get("name") or "", "linkedin_url": p.get("linkedin_url") or "",
            "note": "" if verified else (f"Apollo has no verified email for this person (status: {status or 'none'}) - not used, to avoid a bounce."
                                         " Apollo still counts 1 credit for the match.")}
