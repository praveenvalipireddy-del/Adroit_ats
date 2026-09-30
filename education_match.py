"""Institution + degree normalisation for the education-based Sourcing filters (Filter A / B).

- normalize()          : lowercase, '&' -> 'and', strip punctuation, collapse spaces, drop "the".
- degree_level()       : LinkedIn degree string -> Bachelors / Masters / PhD / Other.
- InstitutionMatcher   : school name -> canonical institution (+ country) from institution_aliases.
      1. exact alias match on the normalised name
      2. exact match on a known name followed only by region words and/or that institution's own
         city ("Osmania University, Hyderabad, Telangana", "Northeastern University Boston MA")
      3. rapidfuzz fallback (token_sort_ratio). Score >= FUZZY_ACCEPT -> matched. Score between
         FUZZY_REVIEW and FUZZY_ACCEPT -> NOT matched, sent to the admin review list with the
         suggestion. A fuzzy candidate is also rejected outright when each side has a distinctive
         word the other lacks ("NIT Warangal" vs "NIT Trichy", "IIT Mandi" vs "IIT Madras") -
         that is a different campus, not a typo.
  Anything not matched is recorded in unmapped_institutions so an admin can add an alias.
  Nothing is ever guessed: an unmatched school has country None, and the filters exclude it.
"""
import logging
import re
import time
from typing import Dict, Iterable, List, Optional

from rapidfuzz import fuzz, process

logger = logging.getLogger("education_match")

FUZZY_ACCEPT = 92
FUZZY_REVIEW = 85
MIN_FUZZY_ALIAS_LEN = 8   # acronyms ("utd", "jntuh") are exact-match only - fuzzy on them is noise

BACHELORS, MASTERS, PHD, OTHER = "Bachelors", "Masters", "PhD", "Other"

# Words that don't distinguish one institution from another (ignored by the campus guard).
_GENERIC_WORDS = {
    "of", "and", "at", "in", "for", "the", "university", "college", "institute", "engineering",
    "technology", "technological", "science", "sciences", "school", "campus", "state", "national",
    "indian", "deemed", "be", "to", "education", "higher", "research", "studies", "center", "centre",
}

# Region words allowed after an institution's name ("..., Telangana, India", "... Boston MA").
# Deliberately NO city names here: the only city accepted is the matched institution's OWN city -
# otherwise "University of North Texas, Dallas" (UNT Dallas, a separate university) would match UNT.
_REGION_WORDS = {
    "india", "usa", "us", "united", "states", "america",
    "telangana", "andhra", "pradesh", "tamil", "nadu", "karnataka", "kerala", "maharashtra",
    "gujarat", "punjab", "rajasthan", "odisha", "west", "bengal", "uttar", "haryana",
    "texas", "tx", "california", "ca", "new", "york", "ny", "jersey", "nj", "illinois", "il",
    "missouri", "mo", "ohio", "oh", "massachusetts", "ma", "florida", "fl", "georgia", "ga",
    "north", "south", "carolina", "nc", "sc", "virginia", "va", "maryland", "md", "arizona", "az",
    "pennsylvania", "pa", "michigan", "mi", "kansas", "ks", "oklahoma", "ok", "kentucky", "ky",
    "indiana", "in", "oregon", "or", "tennessee", "tn", "arkansas", "ar", "connecticut", "ct",
    "hampshire", "nh",
}

# Unambiguous short forms, expanded on BOTH sides (input and alias) so they can't create a false match.
_ABBREVIATIONS = {"univ": "university", "inst": "institute", "engg": "engineering", "coll": "college"}


def normalize(text: Optional[str]) -> str:
    s = (text or "").lower().replace("&", " and ")
    s = re.sub(r"['’`]", "", s)             # "vignan's" -> "vignans", not "vignan s"
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [_ABBREVIATIONS.get(t, t) for t in s.split() if t != "the"]
    return " ".join(tokens)


# ---------------------------------------------------------------- degree level

_PHD_RE = re.compile(r"\bph\.?\s?d\b|doctor of philosophy|\bdoctorate\b", re.I)
_MASTERS_RE = re.compile(
    r"\bmaster|\bm\.?\s?s\b\.?|\bm\.?\s?sc\b|\bm\.?\s?eng\b|\bm\.?\s?tech\b|\bm\.?\s?e\b\.?"
    r"|\bmba\b|\bm\.?\s?b\.?\s?a\b|\bmca\b|\bm\.?\s?c\.?\s?a\b|\bmcs\b|\bmis\b", re.I)
_BACHELORS_RE = re.compile(
    r"\bbachelor|\bb\.?\s?tech\b|\bb\.?\s?e\b\.?|\bb\.?\s?sc\b|\bb\.?\s?s\b\.?|\bbca\b|\bb\.?\s?c\.?\s?a\b"
    r"|\bb\.?\s?com\b|\bb\.?\s?eng\b|\bbba\b|\bb\.?\s?a\b\.?|\bb\.?\s?arch\b|\bb\.?\s?pharm", re.I)
_DUAL_RE = re.compile(r"\bdual degree\b|\bintegrated\b", re.I)


def degree_level(degree: Optional[str]) -> str:
    """Map a LinkedIn degree string to Bachelors / Masters / PhD / Other.

    A dual/integrated degree (e.g. "Integrated B.Tech + M.Tech", or a string naming both a
    Bachelor's and a Master's) is "Other": it is neither a clean Bachelor's nor a clean Master's,
    and counting it as either would put the candidate in a filter on a guess."""
    d = (degree or "").strip()
    if not d:
        return OTHER
    if _PHD_RE.search(d):
        return PHD
    is_m = bool(_MASTERS_RE.search(d))
    is_b = bool(_BACHELORS_RE.search(d))
    if _DUAL_RE.search(d) or (is_m and is_b):
        return OTHER
    if is_m:
        return MASTERS
    if is_b:
        return BACHELORS
    return OTHER


# ---------------------------------------------------------------- institution matching

def _distinct_words(text_norm: str) -> List[str]:
    return [t for t in text_norm.split() if len(t) >= 4 and t not in _GENERIC_WORDS]


def _different_campus(input_norm: str, alias_norm: str) -> bool:
    """True when each side has a distinctive word the other has no close spelling of."""
    a, b = _distinct_words(input_norm), _distinct_words(alias_norm)

    def unmatched(xs, ys):
        return [x for x in xs if not any(fuzz.ratio(x, y) >= 80 for y in ys)]

    return bool(unmatched(a, b)) and bool(unmatched(b, a))


class InstitutionMatcher:
    """rows: iterables of dicts with canonical_id, canonical_name, alias, country, city."""

    def __init__(self, rows: Iterable[Dict]):
        self.by_alias: Dict[str, Dict] = {}
        for r in rows:
            norm = normalize(r.get("alias"))
            if norm and norm not in self.by_alias:
                self.by_alias[norm] = {
                    "canonical_id": r["canonical_id"], "canonical_name": r["canonical_name"],
                    "country": r.get("country"), "city": r.get("city") or "",
                }
        self._fuzzy_keys = [k for k in self.by_alias if len(k) >= MIN_FUZZY_ALIAS_LEN]

    def _result(self, norm_key, method, score=100):
        return dict(self.by_alias[norm_key], status="matched", method=method, score=score)

    def match(self, name: Optional[str]) -> Dict:
        """{'status': 'matched'|'review'|'unmapped'|'empty', canonical_id, canonical_name, country,
        city, method, score, suggestion}. Only status 'matched' carries a country."""
        norm = normalize(name)
        if not norm:
            return {"status": "empty", "country": None}

        if norm in self.by_alias:
            return self._result(norm, "exact")

        # "Osmania University, Hyderabad, Telangana" / "Northeastern University Boston MA": a known
        # name followed only by region words and/or that institution's own city.
        tokens = norm.split()
        for cut in range(len(tokens) - 1, 0, -1):
            head = " ".join(tokens[:cut])
            if head in self.by_alias:
                allowed = _REGION_WORDS | set(normalize(self.by_alias[head]["city"]).split())
                if set(tokens[cut:]) <= allowed:
                    return self._result(head, "exact_location_suffix")
                break

        best = None
        if len(norm) >= 4 and self._fuzzy_keys:
            for key, score, _ in process.extract(norm, self._fuzzy_keys, scorer=fuzz.token_sort_ratio, limit=5):
                if score < FUZZY_REVIEW:
                    break
                if _different_campus(norm, key):
                    continue
                best = (key, score)
                break

        if best and best[1] >= FUZZY_ACCEPT:
            return self._result(best[0], "fuzzy", round(best[1]))
        if best:
            sugg = self.by_alias[best[0]]
            return {"status": "review", "country": None, "score": round(best[1]),
                    "suggestion": {"canonical_id": sugg["canonical_id"], "canonical_name": sugg["canonical_name"]}}
        return {"status": "unmapped", "country": None}


# ---------------------------------------------------------------- DB glue

_cache = {"matcher": None, "at": 0.0}
CACHE_SECONDS = 60


def load_matcher(conn=None, force: bool = False) -> InstitutionMatcher:
    """Matcher built from the institution_aliases table (cached for CACHE_SECONDS so admin edits
    show up within a minute without a restart)."""
    if not force and _cache["matcher"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["matcher"]
    import models
    own = conn is None
    conn = conn or models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT canonical_id, canonical_name, alias, country, city FROM institution_aliases ORDER BY id")
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()
    m = InstitutionMatcher(rows)
    _cache.update(matcher=m, at=time.time())
    return m


def invalidate_cache():
    _cache.update(matcher=None, at=0.0)


def record_unmatched(conn, name: str, result: Dict) -> None:
    """Add/refresh an entry in the admin 'unmapped institutions' list. Caller commits."""
    norm = normalize(name)
    if not norm or result.get("status") not in ("review", "unmapped"):
        return
    sugg = result.get("suggestion") or {}
    cur = conn.cursor()
    cur.execute("SELECT id FROM unmapped_institutions WHERE name_norm = ?", (norm,))
    row = cur.fetchone()
    if row:
        cur.execute("""UPDATE unmapped_institutions
                       SET seen_count = seen_count + 1, last_seen_at = CURRENT_TIMESTAMP, status = ?,
                           suggested_canonical_id = ?, suggested_score = ?
                       WHERE id = ?""",
                    (result["status"], sugg.get("canonical_id"), result.get("score"), row[0]))
    else:
        cur.execute("""INSERT INTO unmapped_institutions
                       (name, name_norm, status, suggested_canonical_id, suggested_score, seen_count)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (name.strip(), norm, result["status"], sugg.get("canonical_id"), result.get("score")))
    logger.info("Institution not matched (%s): %r", result["status"], name)


def match_and_record(conn, name: str) -> Dict:
    """Match a school name; unmatched names are logged to unmapped_institutions. Caller commits."""
    result = load_matcher(conn).match(name)
    if result.get("status") in ("review", "unmapped"):
        record_unmatched(conn, name, result)
    return result
