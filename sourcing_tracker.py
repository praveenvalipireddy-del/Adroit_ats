"""Sourcing tracker card: what a recruiter learns about a sourced LinkedIn candidate.

One card per candidate (linkedin_profiles row), shared by the whole team:
- Status (New / Contacted / No response / Interested / Not interested / Added to bench).
- Contact + details the recruiter collects: email, phone, visa status, current location, open to
  relocate, expected rate, availability, next follow-up date. Nothing is filled in or guessed by the
  app - except that the current location starts from the LinkedIn profile (editable).
- Owner: the first recruiter who moves the status past "New" (or adds the candidate to the bench)
  owns the candidate, so two recruiters don't call the same person. Others can still view, edit
  and comment; admins can reassign.
- Activity: every status change, owner change, field change (who changed which fields) and comment,
  newest first (table sourcing_comments, kinds: comment / status / field / owner / bench).
- Duplicate warnings: an email or phone that already belongs to another sourced candidate or to a
  bench consultant is reported when saving.
- Add to Bench: creates the consultant from the card (owned by the recruiter) and links them.
"""
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

STATUSES = ["New", "Contacted", "No response", "Interested", "Not interested", "Added to bench"]
# Same US work-authorization list as the consultant form (sourced candidates are in the USA), plus
# "Needs sponsorship" for people who have none yet.
VISA_OPTIONS = ["US Citizen", "Green Card", "H1B", "H1B Transfer", "H4 EAD", "L1", "L2", "OPT", "STEM OPT",
                "CPT", "TN", "B1/B2", "Needs sponsorship"]
RELOCATE_OPTIONS = ["Yes", "No"]
AVAILABILITY_OPTIONS = ["Immediately", "1 week", "2 weeks", "1 month", "Not looking"]

FIELDS = {   # column -> label used in the activity history
    "contact_email": "email", "contact_phone": "phone", "visa_status": "visa", "current_location": "location",
    "open_to_relocate": "open to relocate", "expected_rate": "expected rate", "availability": "availability",
    "follow_up_date": "follow-up date",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TrackerError(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """'+1 (469) 555-0100' -> '+14695550100'. Must start with + and a country code (7-15 digits)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if not raw.startswith("+"):
        raise TrackerError("Phone needs a country code, e.g. +1 469 555 0100 or +91 98765 43210.")
    if not 7 <= len(digits) <= 15:
        raise TrackerError("That phone number doesn't look complete.")
    return "+" + digits


def clean_fields(data: Dict) -> Dict:
    """Validated values for the fields present in `data` ('' clears a field)."""
    out = {}
    for key in FIELDS:
        if key not in data:
            continue
        v = str(data.get(key) or "").strip()
        if key == "contact_email" and v:
            if not _EMAIL_RE.match(v) or len(v) > 200:
                raise TrackerError("That email address doesn't look right.")
            v = v.lower()
        elif key == "contact_phone":
            v = normalize_phone(v)
        elif key == "visa_status" and v and v not in VISA_OPTIONS:
            raise TrackerError("Pick a visa status from the list.")
        elif key == "open_to_relocate" and v and v not in RELOCATE_OPTIONS:
            raise TrackerError("Open to relocate must be Yes or No.")
        elif key == "availability" and v and v not in AVAILABILITY_OPTIONS:
            raise TrackerError("Pick availability from the list.")
        elif key == "follow_up_date" and v:
            if not _DATE_RE.match(v):
                raise TrackerError("Follow-up date must be a date.")
            try:
                date.fromisoformat(v)
            except ValueError:
                raise TrackerError("Follow-up date must be a real date.")
        elif key in ("current_location", "expected_rate") and len(v) > 120:
            raise TrackerError("That value is too long.")
        out[key] = v
    return out


def _log(cur, profile_id: int, user_id: Optional[int], kind: str, text: str) -> None:
    cur.execute("INSERT INTO sourcing_comments (profile_id, user_id, kind, comment) VALUES (?, ?, ?, ?)",
                (profile_id, user_id, kind, text))


def _profile(cur, profile_id: int) -> Optional[Dict]:
    cur.execute(f"""SELECT p.id, p.name, p.headline, p.current_title, p.current_company, p.location, p.linkedin_url,
                           p.tracking_status, p.owner_user_id, p.bench_candidate_id, o.name AS owner_name,
                           {", ".join("p." + k for k in FIELDS)}
                    FROM linkedin_profiles p LEFT JOIN users o ON o.id = p.owner_user_id WHERE p.id = ?""", (profile_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def thread(cur, profile_id: int) -> List[Dict]:
    cur.execute("""SELECT c.id, c.kind, c.comment, c.created_at, c.user_id, u.name AS author
                   FROM sourcing_comments c LEFT JOIN users u ON u.id = c.user_id
                   WHERE c.profile_id = ? ORDER BY c.id DESC""", (profile_id,))
    return [{"id": r["id"], "kind": r["kind"], "text": r["comment"], "author": r["author"] or "",
             "user_id": r["user_id"], "at": str(r["created_at"] or "")[:16]} for r in cur.fetchall()]


def card(conn, profile_id: int) -> Optional[Dict]:
    cur = conn.cursor()
    p = _profile(cur, profile_id)
    if not p:
        return None
    cur.execute("""SELECT institution_name, degree, degree_level, field_of_study, end_year, country
                   FROM profile_education WHERE profile_id = ? ORDER BY end_year""", (profile_id,))
    education = [dict(r) for r in cur.fetchall()]
    fields = {k: p.get(k) or "" for k in FIELDS}
    if not fields["current_location"]:
        fields["current_location"] = p.get("location") or ""   # starts from LinkedIn, editable
    return {
        "id": p["id"], "name": p["name"], "linkedin_url": p["linkedin_url"],
        "headline": p.get("current_title") or p.get("headline") or "", "company": p.get("current_company") or "",
        "linkedin_location": p.get("location") or "", "education": education,
        "status": p.get("tracking_status") or "New",
        "owner_user_id": p.get("owner_user_id"), "owner_name": p.get("owner_name") or "",
        "bench_candidate_id": p.get("bench_candidate_id"),
        "fields": fields, "thread": thread(cur, profile_id),
        "options": {"statuses": STATUSES, "visa": VISA_OPTIONS, "relocate": RELOCATE_OPTIONS,
                    "availability": AVAILABILITY_OPTIONS},
    }


def _claim(cur, p: Dict, user: Dict) -> None:
    """Owner rule: the first recruiter to work the candidate owns them."""
    if not p.get("owner_user_id"):
        cur.execute("UPDATE linkedin_profiles SET owner_user_id = ? WHERE id = ?", (user["id"], p["id"]))
        _log(cur, p["id"], user["id"], "owner", f"Owner: {user['name']}")
        p["owner_user_id"] = user["id"]


def set_status(conn, profile_id: int, user: Dict, status: str) -> Dict:
    if status not in STATUSES:
        raise TrackerError("Unknown status.")
    cur = conn.cursor()
    p = _profile(cur, profile_id)
    if not p:
        raise LookupError("Candidate not found")
    old = p.get("tracking_status") or "New"
    if old != status:
        cur.execute("UPDATE linkedin_profiles SET tracking_status = ? WHERE id = ?", (status, profile_id))
        _log(cur, profile_id, user["id"], "status", f"Status: {old} → {status}")
        if status != "New":
            _claim(cur, p, user)
        conn.commit()
    return card(conn, profile_id)


def duplicates(cur, profile_id: int, email: str, phone: str) -> List[str]:
    """Warnings when this email / phone already belongs to another sourced candidate or a bench consultant."""
    out = []
    phone_tail = re.sub(r"\D", "", phone or "")[-10:]
    if email:
        cur.execute("""SELECT p.name, o.name AS owner FROM linkedin_profiles p LEFT JOIN users o ON o.id = p.owner_user_id
                       WHERE p.id <> ? AND LOWER(COALESCE(p.contact_email, '')) = ?""", (profile_id, email.lower()))
        out += [f"Email also saved on sourced candidate {r['name']}" + (f" (owner {r['owner']})" if r["owner"] else "") for r in cur.fetchall()]
        cur.execute("""SELECT c.name, u.name AS recruiter FROM candidates c LEFT JOIN users u ON u.id = c.assigned_user_id
                       WHERE LOWER(COALESCE(c.email, '')) = ? OR LOWER(COALESCE(c.gmail_account, '')) = ?""",
                    (email.lower(), email.lower()))
        out += [f"Already on the bench as {r['name']}" + (f" ({r['recruiter']})" if r["recruiter"] else "") for r in cur.fetchall()]
    if len(phone_tail) >= 7:
        cur.execute("SELECT id, name, contact_phone FROM linkedin_profiles WHERE id <> ? AND contact_phone IS NOT NULL AND contact_phone <> ''",
                    (profile_id,))
        out += [f"Phone also saved on sourced candidate {r['name']}" for r in cur.fetchall()
                if re.sub(r"\D", "", r["contact_phone"])[-10:] == phone_tail]
        cur.execute("""SELECT c.name, c.phone, u.name AS recruiter FROM candidates c LEFT JOIN users u ON u.id = c.assigned_user_id
                       WHERE c.phone IS NOT NULL AND c.phone <> ''""")
        out += [f"Phone matches bench consultant {r['name']}" + (f" ({r['recruiter']})" if r["recruiter"] else "") for r in cur.fetchall()
                if re.sub(r"\D", "", r["phone"])[-10:] == phone_tail]
    seen, uniq = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq


def save_fields(conn, profile_id: int, user: Dict, data: Dict) -> Tuple[Dict, List[str]]:
    values = clean_fields(data)
    cur = conn.cursor()
    p = _profile(cur, profile_id)
    if not p:
        raise LookupError("Candidate not found")
    changed = [k for k, v in values.items() if (p.get(k) or "") != v]
    if changed:
        cur.execute(f"UPDATE linkedin_profiles SET {', '.join(k + ' = ?' for k in changed)} WHERE id = ?",
                    [values[k] for k in changed] + [profile_id])
        _log(cur, profile_id, user["id"], "field", "Updated " + ", ".join(FIELDS[k] for k in changed))
        conn.commit()
    warnings = duplicates(cur, profile_id, values.get("contact_email", p.get("contact_email") or ""),
                          values.get("contact_phone", p.get("contact_phone") or ""))
    return card(conn, profile_id), warnings


def reassign(conn, profile_id: int, admin: Dict, new_owner_id: Optional[int]) -> Dict:
    cur = conn.cursor()
    p = _profile(cur, profile_id)
    if not p:
        raise LookupError("Candidate not found")
    name = "nobody"
    if new_owner_id:
        cur.execute("SELECT id, name FROM users WHERE id = ?", (new_owner_id,))
        u = cur.fetchone()
        if not u:
            raise TrackerError("Unknown recruiter.")
        name = u["name"]
    cur.execute("UPDATE linkedin_profiles SET owner_user_id = ? WHERE id = ?", (new_owner_id or None, profile_id))
    _log(cur, profile_id, admin["id"], "owner", f"Owner changed to {name} by {admin['name']}")
    conn.commit()
    return card(conn, profile_id)


def add_to_bench(conn, profile_id: int, user: Dict) -> Tuple[Dict, int]:
    """Create the bench consultant from the card (owned by this recruiter). Needs an email (Gmail
    drafts are sent from/for it). Returns (card, candidate_id); never creates a second copy."""
    import models

    cur = conn.cursor()
    p = _profile(cur, profile_id)
    if not p:
        raise LookupError("Candidate not found")
    if p.get("bench_candidate_id"):
        cur.execute("SELECT id FROM candidates WHERE id = ?", (p["bench_candidate_id"],))
        if cur.fetchone():
            raise TrackerError("This candidate is already on the bench.")
    email = (p.get("contact_email") or "").strip()
    if not email:
        raise TrackerError("Add the candidate's email first - the bench needs it for Gmail drafts.")
    cur.execute("SELECT name FROM candidates WHERE LOWER(email) = ?", (email.lower(),))
    existing = cur.fetchone()
    if existing:
        raise TrackerError(f"A bench consultant with this email already exists: {existing['name']}.")
    cand_id = models.create_candidate(
        name=p["name"], email=email, phone=p.get("contact_phone") or "",
        title=p.get("current_title") or p.get("headline") or "", primary_skills="",
        target_rate=p.get("expected_rate") or "", visa_status=p.get("visa_status") or "",
        location=p.get("current_location") or p.get("location") or "", country="United States",
        resume_summary=f"LinkedIn: {p['linkedin_url']}", gmail_account=email, assigned_user_id=user["id"])
    cur.execute("UPDATE linkedin_profiles SET bench_candidate_id = ?, tracking_status = 'Added to bench' WHERE id = ?",
                (cand_id, profile_id))
    old = p.get("tracking_status") or "New"
    if old != "Added to bench":
        _log(cur, profile_id, user["id"], "status", f"Status: {old} → Added to bench")
    _claim(cur, p, user)
    _log(cur, profile_id, user["id"], "bench", f"Added to bench by {user['name']}")
    conn.commit()
    return card(conn, profile_id), cand_id
