"""Individual plan: one login markets ONE profile - their own.

An Individual is a users row with role "Individual" and exactly one consultant (candidates.assigned_user_id = their
id), created together by an admin. They get Jobs (free sources only), their profile, their applications, resume
tailoring (AI), the Apollo hiring team on a job and the Autofill extension (with AI answers) - no paid job portals, no Gmail outreach,
no recruiter / admin screens, and nothing of anyone else's.

Per-person settings live in app_meta (no schema change):
  individual_limit:<uid>     live job searches allowed per day (default DEFAULT_DAILY_LIMIT)
  individual_disabled:<uid>  "1" = can't log in
  individual_searches:<uid>  "YYYY-MM-DD:<count>" - today's live searches
"""
from datetime import datetime, timezone

ROLE = "Individual"
DEFAULT_DAILY_LIMIT = 20
MAX_DAILY_LIMIT = 500

# Routes an Individual may use (Flask endpoint -> methods). Everything else is refused (403) - an allowlist, so a
# route added later stays closed to them until it is listed here.
ALLOWED = {
    "home": {"GET"}, "login": {"GET", "POST"}, "logout": {"GET"}, "dashboard": {"GET"}, "static": {"GET"},
    "api_stats": {"GET"},
    "api_consultants": {"GET"},                                  # no POST: one profile only
    "api_consultant_detail": {"GET", "PUT"},                     # no DELETE
    "api_consultant_upload_resume": {"POST"},
    "api_consultant_resume_docx": {"GET"},
    "api_consultants_overview": {"GET"},
    "api_jobs_search": {"POST"},
    "api_job_role_counts": {"POST"},
    "api_job_full_description": {"POST"},
    "api_job_page": {"GET"},
    "api_job_save": {"POST"},
    "api_job_apply_status": {"POST"},
    "api_applications": {"GET", "POST"},
    "api_application_update": {"PUT"},
    "api_application_events": {"GET"},
    "api_applications_export": {"GET"},
    # Resume tailoring (Resume Optimizer, job page "Tailor resume") - each route checks the profile is theirs
    "api_resume_bot_optimize": {"POST"}, "api_resume_bot_extract_text": {"POST"}, "api_resume_bot_download_docx": {"POST"},
    "api_optimized_resumes": {"GET", "POST"}, "api_optimized_resume_for_job": {"GET"}, "api_optimized_resume_download": {"GET"},
    "api_resume_editor_edit": {"POST"}, "api_jd_from_url": {"POST"}, "api_jd_cloud": {"POST"},
    "api_read_requirement": {"POST"}, "api_match_check": {"POST"},
    "api_consultant_rules": {"GET", "POST"}, "api_consultant_rule": {"PUT", "DELETE"}, "api_consultant_rule_check": {"POST"},
    # Apollo hiring team on a job (search is free; each new email is 1 Apollo credit). Not the shared Contacts list.
    "api_super_apply_check": {"POST"}, "api_super_apply_search": {"POST"}, "api_super_apply_reveal": {"POST"},
    "api_extension_key": {"POST"}, "extension_zip": {"GET"},
    "api_ext_me": {"GET"}, "api_ext_consultant": {"GET"}, "api_ext_resume": {"GET"},
    "api_ext_tailor": {"POST"}, "api_ext_match": {"POST"},
    "api_ext_answers": {"POST"},                                 # AI answers to the form's extra questions
    "api_ext_job_status": {"POST"}, "api_ext_save_job": {"POST"}, "api_ext_applied": {"POST"},
    "api_individual_me": {"GET"},
}

# Job sources that cost nothing, per market. "All" for the US means the free portals (Dice + LinkedIn) already.
FREE_SOURCES = {
    "united states": ("all", "dice", "linkedin"),
    "india": ("linkedin",),
    "canada": ("linkedin",),
}


def is_individual(user) -> bool:
    return bool(user) and (user.get("role") or "").strip() == ROLE


def allowed(endpoint: str, method: str) -> bool:
    if method in ("OPTIONS", "HEAD"):
        return True
    return method in ALLOWED.get(endpoint or "", set())


def free_source(country: str, source: str) -> str:
    """The source an Individual's search may use: theirs when it is free, else the market's free one
    (company sites are always fine - read from the stored career-board jobs, never a paid scrape)."""
    s = (source or "All").strip()
    if s.lower().startswith("company site"):
        return s
    free = FREE_SOURCES.get((country or "United States").strip().lower(), ("linkedin",))
    if s.lower().replace(".com", "") in free:
        return s
    return "All" if "all" in free else "LinkedIn"


# --- app_meta settings ---------------------------------------------------------------------------------------

def _get(conn, key):
    cur = conn.cursor()
    cur.execute("SELECT meta_value FROM app_meta WHERE meta_key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def _set(conn, key, value):
    cur = conn.cursor()
    cur.execute("SELECT meta_value FROM app_meta WHERE meta_key = ?", (key,))
    if cur.fetchone():
        cur.execute("UPDATE app_meta SET meta_value = ? WHERE meta_key = ?", (value, key))
    else:
        cur.execute("INSERT INTO app_meta (meta_key, meta_value) VALUES (?, ?)", (key, value))
    conn.commit()


def _delete(conn, uid):
    cur = conn.cursor()
    for k in ("individual_limit", "individual_disabled", "individual_searches"):
        cur.execute("DELETE FROM app_meta WHERE meta_key = ?", (f"{k}:{int(uid)}",))
    conn.commit()


def clean_limit(value):
    """A daily limit from a form: a whole number 1..MAX_DAILY_LIMIT, else None."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= MAX_DAILY_LIMIT else None


def daily_limit(conn, uid) -> int:
    return clean_limit(_get(conn, f"individual_limit:{int(uid)}")) or DEFAULT_DAILY_LIMIT


def set_daily_limit(conn, uid, limit) -> None:
    _set(conn, f"individual_limit:{int(uid)}", str(int(limit)))


def is_disabled(conn, uid) -> bool:
    return _get(conn, f"individual_disabled:{int(uid)}") == "1"


def set_disabled(conn, uid, disabled: bool) -> None:
    _set(conn, f"individual_disabled:{int(uid)}", "1" if disabled else "0")


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def searches_today(conn, uid) -> int:
    raw = _get(conn, f"individual_searches:{int(uid)}") or ""
    day, _, n = raw.partition(":")
    return int(n) if day == _today() and n.isdigit() else 0


def count_search(conn, uid) -> int:
    """Record one live search for today; returns today's total."""
    n = searches_today(conn, uid) + 1
    _set(conn, f"individual_searches:{int(uid)}", f"{_today()}:{n}")
    return n


def usage(conn, uid) -> dict:
    return {"searches_today": searches_today(conn, uid), "daily_limit": daily_limit(conn, uid)}
