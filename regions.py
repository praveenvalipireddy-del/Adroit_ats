"""USA / India / Canada teams in one app.

- Every user has users.region: their team(s). One team ('USA', 'India', 'Canada') or several, stored as a
  comma list in REGIONS order ('USA,Canada'). The older value 'Both' still means USA + India. A one-team
  recruiter is locked to it; admins and multi-team recruiters choose a view (one team or all of theirs)
  with the switcher in the top bar (kept in the session). A multi-team recruiter still sees only their own
  consultants.
- A consultant's region comes from their country: 'India' -> India team, 'Canada' -> Canada team, anything
  else -> USA team (no extra column - a consultant moves team when their country changes).
- What is split: consultants / bench (and everything reached through a consultant: drafts, resume
  optimizer, applications), the Jobs feed (US / India / Canada jobs) and the admins' Vendors view.
- backfill() gives existing users a region once: India when most of their consultants are in India,
  otherwise USA. Admins can change any recruiter's teams in Admin & Settings.
"""
from typing import Dict, Iterable, Optional, Set

REGIONS = ("USA", "India", "Canada")
JOB_COUNTRY = {"USA": "United States", "India": "India", "Canada": "Canada"}


def normalize(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v in ("india", "in"):
        return "India"
    if v in ("canada", "ca"):
        return "Canada"
    return "USA" if v in ("usa", "us", "united states") else ""


def team_set(value) -> Set[str]:
    """A user's teams as a set: 'Both' -> {USA, India}; 'USA,Canada' / ['USA', 'Canada'] -> {USA, Canada}."""
    if isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        v = (value or "").strip()
        if v.lower() in ("both", "usa+india", "usa + india", "india+usa", "both teams"):
            return {"USA", "India"}
        if v.lower() in ("all", "all teams"):
            return set(REGIONS)
        parts = v.replace("+", ",").split(",")
    return {r for r in (normalize(str(p)) for p in parts) if r}


def normalize_team(value) -> str:
    """A user's teams as stored: 'USA', 'India', 'Canada' or a comma list in REGIONS order ('' when unknown)."""
    teams = team_set(value)
    return ",".join(r for r in REGIONS if r in teams)


def team_label(value) -> str:
    """'USA', 'USA + Canada' - for messages."""
    return " + ".join(r for r in REGIONS if r in team_set(value)) or "USA"


def can_switch(user: Dict, stored_region: str) -> bool:
    """Admins and multi-team recruiters pick which team to look at."""
    return "Admin" in (user.get("role") or "") or len(team_set(stored_region)) > 1


def candidate_region(cand: Dict) -> str:
    c = (cand.get("country") or "").strip().lower()
    return "India" if c == "india" else ("Canada" if c == "canada" else "USA")


def job_region(country: Optional[str]) -> str:
    """A job's market from its country: India / Canada / USA (anything else)."""
    return candidate_region({"country": country})


def user_region(conn, user_id: int) -> str:
    cur = conn.cursor()
    cur.execute("SELECT region FROM users WHERE id = ?", (user_id,))
    r = cur.fetchone()
    return normalize_team(r[0] if r else "") or "USA"


def user_regions(conn, user_id: int) -> Set[str]:
    return team_set(user_region(conn, user_id))


def view_regions(user: Dict, stored_region: str, admin_view: Optional[str]) -> Set[str]:
    """Regions this user is looking at right now."""
    mine = set(REGIONS) if "Admin" in (user.get("role") or "") else (team_set(stored_region) or {"USA"})
    if len(mine) > 1:
        v = normalize(admin_view)
        return {v} if v in mine else mine
    return mine


def filter_candidates(cands: Iterable[Dict], regions: Set[str]):
    return [c for c in cands if candidate_region(c) in regions]


def backfill(conn) -> int:
    """Give users without a region one (India when most of their consultants are in India)."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE region IS NULL OR region = ''")
    ids = [r[0] for r in cur.fetchall()]
    for uid in ids:
        cur.execute("""SELECT SUM(CASE WHEN LOWER(COALESCE(country, '')) = 'india' THEN 1 ELSE 0 END),
                              SUM(CASE WHEN LOWER(COALESCE(country, '')) = 'india' THEN 0 ELSE 1 END)
                       FROM candidates WHERE assigned_user_id = ?""", (uid,))
        row = cur.fetchone()        # by position: a Postgres row (models.DbRow) iterates its column names
        india, other = row[0], row[1]
        cur.execute("UPDATE users SET region = ? WHERE id = ?", ("India" if (india or 0) > (other or 0) else "USA", uid))
    conn.commit()
    return len(ids)
