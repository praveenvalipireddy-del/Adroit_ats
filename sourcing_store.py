"""Shared, team-wide storage for Sourcing results.

Why this exists: PDL and Apify both charge per record. Without this, every one
of the (up to ~30) recruiters who opens Sourcing and clicks Search pays to
re-scan the same LinkedIn population from page 1. This module makes the FIRST
search for a given (source, search_year) pay for a scan, and every recruiter
after that read the verified matches straight from Postgres/SQLite for free.
It also keeps the pagination cursor server-side (per source+year, not per
browser) so the next search continues where the LAST recruiter's left off
instead of re-scanning already-seen pages.

Nothing here changes WHAT counts as a match - evaluate_profile /
evaluate_pdl_person in linkedin_sourcing.py still make that call; this only
avoids paying twice for the same answer.
"""
import logging
from typing import Dict, List, Optional

from models import get_db_connection, is_postgres

logger = logging.getLogger("sourcing_store")

# Columns copied verbatim from a candidate dict (same shape for both PDL and
# Apify matches - see evaluate_pdl_person / evaluate_profile).
_CANDIDATE_COLS = [
    "profile_url", "name", "headline", "bachelor_year", "bachelor_degree", "bachelor_college",
    "master_degree", "master_university", "master_year", "location",
    "status_tag", "settlement_badge", "settlement_sub", "quality", "degree",
]


def _year_key(year) -> str:
    """Normalises the filter value to a stable, non-NULL cache key ('' = All)."""
    return str(year).strip() if year else ""


def get_cached_matches(source: str, search_year) -> List[Dict]:
    """Every verified match any recruiter has already found for this search - free to read."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM sourced_candidates WHERE source = ? AND search_year = ? ORDER BY id ASC",
            (source, _year_key(search_year)),
        )
        rows = cur.fetchall()
        out = []
        for row in rows:
            cand = {col: row[col] for col in _CANDIDATE_COLS}
            cand["linkedin_url"] = cand["profile_url"]
            cand["grad_year"] = cand["bachelor_year"]
            cand["university"] = cand["master_university"]
            out.append(cand)
        return out
    except Exception as ex:
        logger.error(f"get_cached_matches failed: {ex}")
        return []
    finally:
        conn.close()


def save_matches(source: str, search_year, matches: List[Dict], user_id: Optional[int] = None) -> int:
    """Adds newly-verified matches to the shared pool. Safe to call with matches the pool
    already has - duplicates (same source + profile_url) are skipped, not double-stored.
    Returns how many were genuinely new."""
    if not matches:
        return 0
    year_key = _year_key(search_year)
    conn = get_db_connection()
    added = 0
    try:
        cur = conn.cursor()
        for m in matches:
            url = (m.get("profile_url") or "").strip()
            if not url:
                continue
            try:
                cur.execute(
                    f"""INSERT INTO sourced_candidates
                        (source, search_year, found_by_user_id, {", ".join(_CANDIDATE_COLS)})
                        VALUES ({", ".join(["?"] * (3 + len(_CANDIDATE_COLS)))})""",
                    [source, year_key, user_id] + [m.get(col, "") for col in _CANDIDATE_COLS],
                )
                added += 1
            except Exception:
                # Unique index (source, profile_url) rejected a duplicate - expected, not an error.
                pass
        conn.commit()
        return added
    except Exception as ex:
        logger.error(f"save_matches failed: {ex}")
        return 0
    finally:
        conn.close()


def get_cursor(source: str, search_year) -> Dict:
    """Where the team's shared search last left off. Defaults describe a fresh search."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT next_page, scroll_token, mode, exhausted FROM sourcing_cursor WHERE source = ? AND search_year = ?",
            (source, _year_key(search_year)),
        )
        row = cur.fetchone()
        if not row:
            return {"next_page": 1, "scroll_token": None, "mode": None, "exhausted": False}
        return {"next_page": row["next_page"] or 1, "scroll_token": row["scroll_token"],
                "mode": row["mode"], "exhausted": bool(row["exhausted"])}
    except Exception as ex:
        logger.error(f"get_cursor failed: {ex}")
        return {"next_page": 1, "scroll_token": None, "mode": None, "exhausted": False}
    finally:
        conn.close()


def save_cursor(source: str, search_year, next_page: Optional[int] = None,
                 scroll_token: Optional[str] = None, mode: Optional[str] = None,
                 exhausted: bool = False) -> None:
    year_key = _year_key(search_year)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        conflict_cols = "(source, search_year)"
        if is_postgres(conn):
            cur.execute(
                f"""INSERT INTO sourcing_cursor (source, search_year, next_page, scroll_token, mode, exhausted)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT {conflict_cols} DO UPDATE SET
                       next_page = EXCLUDED.next_page, scroll_token = EXCLUDED.scroll_token,
                       mode = EXCLUDED.mode, exhausted = EXCLUDED.exhausted, updated_at = CURRENT_TIMESTAMP""",
                (source, year_key, next_page or 1, scroll_token, mode, int(exhausted)),
            )
        else:
            cur.execute(
                f"""INSERT INTO sourcing_cursor (source, search_year, next_page, scroll_token, mode, exhausted)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT {conflict_cols} DO UPDATE SET
                       next_page = excluded.next_page, scroll_token = excluded.scroll_token,
                       mode = excluded.mode, exhausted = excluded.exhausted, updated_at = CURRENT_TIMESTAMP""",
                (source, year_key, next_page or 1, scroll_token, mode, int(exhausted)),
            )
        conn.commit()
    except Exception as ex:
        logger.error(f"save_cursor failed: {ex}")
    finally:
        conn.close()
