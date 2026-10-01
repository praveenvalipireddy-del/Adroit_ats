"""LinkedIn profile ingestion for the education filters (Filter A / Filter B).

Every source is converted by a LinkedInDataProvider into ONE normalised profile shape, then
ingest_profiles() stores it in linkedin_profiles + profile_education (deduplicated on the
canonical LinkedIn profile URL). Each education entry's institution is matched against
institution_aliases to get its canonical id and COUNTRY; unmatched schools keep country NULL
(so the filters exclude them - nothing is guessed) and are logged to unmapped_institutions.

Providers today:
  - ApifyProfileProvider : raw Full-mode items from the Apify LinkedIn search (full education list).
  - SourcedPoolProvider  : rows already in sourced_candidates (Apify + the old People Data Labs
                           finds). Only the two verified entries (Bachelor's + Master's) exist there,
                           so these profiles are marked education_complete = 0 and are upgraded as
                           soon as a full Apify profile for the same person comes in.
A licensed data provider can be added later by subclassing LinkedInDataProvider - the filter
logic only reads the normalised tables.

Normalised profile:
  {linkedin_url, name, headline, location, current_company, current_title,
   education: [{institution_name, degree, field_of_study, start_year, end_year, degree_level_hint?}],
   education_complete: bool}
"""
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional

import education_match

logger = logging.getLogger("linkedin_ingest")

_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#\s]+)", re.I)


def canonical_linkedin_url(url: Optional[str]) -> Optional[str]:
    """https://www.linkedin.com/in/<slug> (lowercase slug, no query/trailing slash), or None."""
    m = _SLUG_RE.search(url or "")
    if not m:
        return None
    slug = m.group(1).strip().strip("/").lower()
    return f"https://www.linkedin.com/in/{slug}" if slug else None


def _int_year(value) -> Optional[int]:
    if isinstance(value, dict):
        value = value.get("year")
    try:
        y = int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return None
    return y if 1950 <= y <= 2100 else None


class LinkedInDataProvider(ABC):
    source = "provider"

    @abstractmethod
    def to_profiles(self, raw_items: Iterable) -> List[Dict]:
        """Convert this provider's records into normalised profile dicts."""


class ApifyProfileProvider(LinkedInDataProvider):
    """harvestapi/linkedin-profile-search Full-mode items (see linkedin_sourcing._PROFILE_FIELDS)."""
    source = "apify"

    def to_profiles(self, raw_items):
        out = []
        for item in raw_items or []:
            if not isinstance(item, dict):
                continue
            url = canonical_linkedin_url(item.get("linkedinUrl"))
            name = f"{(item.get('firstName') or '').strip()} {(item.get('lastName') or '').strip()}".strip()
            if not url or not name:
                continue
            loc = item.get("location") or {}
            current = item.get("currentPosition") or []
            current = current[0] if current and isinstance(current[0], dict) else {}
            education = []
            for e in item.get("education") or []:
                if not isinstance(e, dict) or not (e.get("schoolName") or "").strip():
                    continue
                education.append({
                    "institution_name": e.get("schoolName").strip(),
                    "degree": (e.get("degree") or "").strip(),
                    "field_of_study": (e.get("fieldOfStudy") or "").strip(),
                    "start_year": _int_year(e.get("startDate")),
                    "end_year": _int_year(e.get("endDate")),
                })
            out.append({
                "linkedin_url": url,
                "name": name,
                "headline": (item.get("headline") or "").strip(),
                "location": (loc.get("linkedinText") or (loc.get("parsed") or {}).get("text") or "").strip(),
                "current_company": (current.get("companyName") or "").strip(),
                "current_title": (current.get("position") or "").strip(),
                "education": education,
                "education_complete": True,
            })
        return out


def _split_degree(text: str):
    """'BTech - Computer Science (2019)' -> ('BTech', 'Computer Science')."""
    t = re.sub(r"\s*\((?:\d{4}|year not listed)\)\s*$", "", (text or "").strip())
    if " - " in t:
        deg, fld = t.split(" - ", 1)
        return deg.strip(), fld.strip()
    return t, ""


class SourcedPoolProvider(LinkedInDataProvider):
    """Rows of sourced_candidates (already verified India Bachelor's + non-Indian Master's)."""
    source = "sourced_pool"

    def to_profiles(self, rows):
        out = []
        for r in rows or []:
            url = canonical_linkedin_url(r.get("profile_url"))
            if not url or not (r.get("name") or "").strip():
                continue
            education = []
            if (r.get("bachelor_college") or "").strip():
                deg, fld = _split_degree(r.get("bachelor_degree"))
                education.append({"institution_name": r["bachelor_college"].strip(), "degree": deg, "field_of_study": fld,
                                  "start_year": None, "end_year": _int_year(r.get("bachelor_year")),
                                  "degree_level_hint": education_match.BACHELORS})
            if (r.get("master_university") or "").strip():
                deg, fld = _split_degree(r.get("master_degree"))
                education.append({"institution_name": r["master_university"].strip(), "degree": deg, "field_of_study": fld,
                                  "start_year": None, "end_year": _int_year(r.get("master_year")),
                                  "degree_level_hint": education_match.MASTERS})
            out.append({
                "linkedin_url": url,
                "name": r["name"].strip(),
                "headline": (r.get("headline") or "").strip(),
                "location": (r.get("location") or "").strip(),
                "current_company": "",
                "current_title": "",
                "education": education,
                "education_complete": False,
                "_source": r.get("source") or "apify",
                "_user_id": r.get("found_by_user_id"),
            })
        return out


def _store_education(cur, conn, profile_id: int, education: List[Dict]):
    cur.execute("DELETE FROM profile_education WHERE profile_id = ?", (profile_id,))
    for e in education:
        match = education_match.match_and_record(conn, e["institution_name"])
        level = education_match.degree_level(e.get("degree"))
        # Pool rows were verified as Bachelor's/Master's by the search that found them; their stored
        # degree text can be terse ("Bachelors"), so the verified role wins when the text is unclear.
        if level == education_match.OTHER and e.get("degree_level_hint"):
            level = e["degree_level_hint"]
        matched = match.get("status") == "matched"
        cur.execute("""INSERT INTO profile_education
                       (profile_id, institution_name, institution_norm, institution_canonical_id, degree, degree_level,
                        field_of_study, country, start_year, end_year, match_method)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (profile_id, e["institution_name"], education_match.normalize(e["institution_name"]),
                     match.get("canonical_id") if matched else None, e.get("degree") or "", level,
                     e.get("field_of_study") or "", match.get("country") if matched else None,
                     e.get("start_year"), e.get("end_year"), match.get("method") if matched else match.get("status")))


def ingest_profiles(profiles: List[Dict], source: str, user_id: Optional[int], conn=None, log_action: str = "capture") -> Dict:
    """Upsert normalised profiles. New profile -> inserted (+ capture_log row). Existing profile ->
    fields refreshed; its education is replaced only when the new data is complete or the stored
    data isn't (a full Apify profile upgrades a pool-only one, never the other way round).
    Commits per profile, so one bad record can't roll back the rest."""
    import models
    own = conn is None
    conn = conn or models.get_db_connection()
    stats = {"added": 0, "updated": 0, "skipped": 0}
    try:
        cur = conn.cursor()
        for p in profiles or []:
            url = canonical_linkedin_url(p.get("linkedin_url"))
            if not url:
                stats["skipped"] += 1
                continue
            p_source = p.get("_source") or source
            p_user = p.get("_user_id") if p.get("_user_id") is not None else user_id
            complete = 1 if p.get("education_complete") else 0
            try:
                cur.execute("SELECT id, education_complete FROM linkedin_profiles WHERE linkedin_url = ?", (url,))
                row = cur.fetchone()
                if row:
                    profile_id = row[0]
                    cur.execute("""UPDATE linkedin_profiles SET
                                     name = COALESCE(NULLIF(?, ''), name), headline = COALESCE(NULLIF(?, ''), headline),
                                     location = COALESCE(NULLIF(?, ''), location),
                                     current_company = COALESCE(NULLIF(?, ''), current_company),
                                     current_title = COALESCE(NULLIF(?, ''), current_title),
                                     education_complete = ?, updated_at = CURRENT_TIMESTAMP
                                   WHERE id = ?""",
                                (p.get("name") or "", p.get("headline") or "", p.get("location") or "",
                                 p.get("current_company") or "", p.get("current_title") or "",
                                 max(complete, int(row[1] or 0)), profile_id))
                    if complete or not int(row[1] or 0):
                        _store_education(cur, conn, profile_id, p.get("education") or [])
                    stats["updated"] += 1
                else:
                    cur.execute("""INSERT INTO linkedin_profiles
                                   (linkedin_url, name, headline, location, current_company, current_title,
                                    experience_json, source, captured_by, education_complete)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (url, p.get("name") or "", p.get("headline") or "", p.get("location") or "",
                                 p.get("current_company") or "", p.get("current_title") or "",
                                 json.dumps(p.get("experience")) if p.get("experience") else None,
                                 p_source, p_user, complete))
                    profile_id = cur.lastrowid
                    _store_education(cur, conn, profile_id, p.get("education") or [])
                    cur.execute("INSERT INTO capture_log (user_id, action, source, profile_id, details) VALUES (?, ?, ?, ?, ?)",
                                (p_user, log_action, p_source, profile_id, url))
                    stats["added"] += 1
                conn.commit()
            except Exception as ex:
                try:
                    conn.rollback()
                except Exception:
                    pass
                stats["skipped"] += 1
                logger.warning("ingest: could not store %s: %s", url, ex)
        return stats
    finally:
        if own:
            conn.close()


def import_sourced_pool(conn=None) -> Dict:
    """Bring every sourced_candidates row whose profile isn't in linkedin_profiles yet into the
    education tables. Idempotent and free (no API calls) - safe to run at every startup."""
    import models
    own = conn is None
    conn = conn or models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT linkedin_url FROM linkedin_profiles")
        have = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT * FROM sourced_candidates ORDER BY id ASC")
        rows = [dict(r) for r in cur.fetchall()]
        todo = [r for r in rows if canonical_linkedin_url(r.get("profile_url")) not in have]
        if not todo:
            return {"added": 0, "updated": 0, "skipped": 0}
        profiles = SourcedPoolProvider().to_profiles(todo)
        stats = ingest_profiles(profiles, "apify", None, conn=conn, log_action="import_pool")
        logger.info("Education filters: imported %s profile(s) from the sourcing pool.", stats)
        return stats
    finally:
        if own:
            conn.close()


def mark_verified(conn, matches) -> int:
    """Record the Bachelor's year the LinkedIn search VERIFIED for each match (evaluate_profile: an
    Indian college's Bachelor's ending that year + a non-Indian, non-foreign Master's, person in the
    USA). The Passout tab lists these people even when their college isn't on the college list -
    otherwise people the recruiter paid to verify were hidden (4 verified for 2019, 1 shown).
    Caller commits."""
    cur = conn.cursor()
    n = 0
    for m in matches or []:
        url = canonical_linkedin_url((m or {}).get("profile_url") or (m or {}).get("linkedin_url"))
        year = _int_year((m or {}).get("bachelor_year"))
        if url and year:
            cur.execute("UPDATE linkedin_profiles SET verified_bachelor_year = ? WHERE linkedin_url = ?", (year, url))
            n += 1
    return n


def sync_verified_years(conn=None) -> int:
    """Fill verified_bachelor_year from the team's verified pool (sourced_candidates) for profiles
    that don't have it yet. Free, idempotent; runs at startup so existing results show too."""
    import models
    own = conn is None
    conn = conn or models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT profile_url, bachelor_year FROM sourced_candidates WHERE bachelor_year IS NOT NULL AND bachelor_year <> ''")
        verified = {}
        for r in cur.fetchall():
            url, year = canonical_linkedin_url(r[0]), _int_year(r[1])
            if url and year:
                verified[url] = year
        if not verified:
            return 0
        cur.execute("SELECT id, linkedin_url FROM linkedin_profiles WHERE verified_bachelor_year IS NULL")
        todo = [(r[0], verified[r[1]]) for r in cur.fetchall() if r[1] in verified]
        for pid, year in todo:
            cur.execute("UPDATE linkedin_profiles SET verified_bachelor_year = ? WHERE id = ?", (year, pid))
        conn.commit()
        if todo:
            logger.info("Education filters: marked %d profile(s) with their search-verified Bachelor's year.", len(todo))
        return len(todo)
    finally:
        if own:
            conn.close()


def rematch_institution(conn, name_norm: str) -> int:
    """After an admin maps a school name, apply it to every stored education entry with that name.
    Returns rows updated. Caller commits."""
    result = education_match.load_matcher(conn, force=True).match(name_norm)
    if result.get("status") != "matched":
        return 0
    cur = conn.cursor()
    cur.execute("""UPDATE profile_education SET institution_canonical_id = ?, country = ?, match_method = ?
                   WHERE institution_norm = ?""",
                (result["canonical_id"], result["country"], "admin_alias", name_norm))
    cur.execute("SELECT COUNT(*) FROM profile_education WHERE institution_norm = ? AND institution_canonical_id = ?",
                (name_norm, result["canonical_id"]))
    return int(cur.fetchone()[0])
