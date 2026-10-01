"""Filter A / Filter B over the education tables (linkedin_profiles + profile_education).

Passout  - an Indian Bachelor's ending in the chosen year (any Indian college) AND a US Master's.
Filter A - "Indian College -> US Masters": a Bachelors entry at the chosen Indian college AND a
           Masters entry at any institution whose country is USA.
Filter B - "US University -> Indian Undergrad": a Masters entry at the chosen US university AND a
           Bachelors entry at any institution whose country is India.

- Year range (optional) applies to the entry at the CHOSEN institution (A: the Bachelor's end year,
  B: the Master's end year). A single year (either box) means exactly that year. When a range is set, entries with no end year don't qualify - a
  missing year is never assumed to be inside the range.
- A candidate with several degrees matches if ANY qualifying pair exists.
- Entries whose country is unknown (school not matched to the alias table) never qualify; those
  schools are listed for admins in unmapped_institutions.
- Optional location / keyword text filters AND-combine with the above. (LinkedIn profiles carry no
  visa status, so there is no visa filter.)
- Profiles are a team-wide pool (like the rest of Sourcing): every logged-in recruiter sees them.
"""
import io
from typing import Dict, List, Optional

from institutions_seed import GROUPS
from linkedin_sourcing import is_indian_institution

_GROUPS = {g[0]: {"id": g[0], "name": g[1], "country": g[2], "words": g[3], "members": g[4]} for g in GROUPS}

# Passout ("P"): an Indian Bachelor's ending in the chosen year (any Indian college) AND a US Master's.
# "All" covers PASSOUT_MIN..PASSOUT_MAX, the same range the LinkedIn search uses.
PASSOUT_MIN, PASSOUT_MAX = 2015, 2023

FILTERS = {
    "P": {"chosen_level": "Bachelors", "chosen_country": "India", "other_level": "Masters", "other_country": "USA"},
    "A": {"chosen_level": "Bachelors", "chosen_country": "India", "other_level": "Masters", "other_country": "USA"},
    "B": {"chosen_level": "Masters", "chosen_country": "USA", "other_level": "Bachelors", "other_country": "India"},
}
MAX_PAGE_SIZE = 100
MAX_EXPORT_ROWS = 10000


class FilterError(ValueError):
    pass


def _year(v) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        y = int(str(v).strip())
    except ValueError:
        raise FilterError(f"Invalid year: {v!r}")
    if not 1950 <= y <= 2100:
        raise FilterError(f"Invalid year: {v!r}")
    return y


def parse_params(args: Dict) -> Dict:
    f = str(args.get("filter") or "").strip().upper()
    if f not in FILTERS:
        raise FilterError("filter must be 'P', 'A' or 'B'")
    if f == "P":
        inst = None
        py = str(args.get("passout_year") or "").strip()
        if py.lower() == "all":
            y_from, y_to = PASSOUT_MIN, PASSOUT_MAX
        else:
            y_from = y_to = _year(py)
            if not y_from:
                raise FilterError("Choose a passout year.")
    else:
        inst = str(args.get("institution_id") or "").strip()
        if not inst:
            raise FilterError("Choose an institution first.")
        y_from, y_to = _year(args.get("year_from")), _year(args.get("year_to"))
        # One year typed (in either box) means exactly that passout year - "2023" must not also
        # return 2016-2022 graduates. Both boxes = a range.
        if bool(y_from) != bool(y_to):
            y_from = y_to = y_from or y_to
    if y_from and y_to and y_from > y_to:
        raise FilterError("'Year from' is after 'Year to'.")
    try:
        page = max(1, int(args.get("page") or 1))
        page_size = max(1, min(int(args.get("page_size") or 25), MAX_PAGE_SIZE))
    except (TypeError, ValueError):
        raise FilterError("Invalid page.")
    return {"filter": f, "institution_id": inst, "year_from": y_from, "year_to": y_to,
            "location": str(args.get("location") or "").strip(), "keyword": str(args.get("keyword") or "").strip(),
            "page": page, "page_size": page_size}


def member_ids(institution_id: Optional[str]) -> Optional[List[str]]:
    """A group ("JNTU - any campus") stands for all of its member institutions. None = any institution."""
    if institution_id is None:
        return None
    group = _GROUPS.get(institution_id)
    return list(group["members"]) if group else [institution_id]


def display_name(conn, institution_id: Optional[str]) -> str:
    if institution_id is None:
        return "any Indian college"
    if institution_id in _GROUPS:
        return _GROUPS[institution_id]["name"]
    cur = conn.cursor()
    cur.execute("SELECT MIN(canonical_name) FROM institution_aliases WHERE canonical_id = ?", (institution_id,))
    row = cur.fetchone()
    return (row[0] if row else None) or institution_id


def _like(text: str) -> str:
    return "%" + text.lower().replace("\\", "").replace("%", "").replace("_", "") + "%"


def _where(p: Dict):
    spec = FILTERS[p["filter"]]
    members = member_ids(p["institution_id"])
    clauses = ["""EXISTS (SELECT 1 FROM profile_education c WHERE c.profile_id = p.id AND c.degree_level = ?
                   {inst}AND c.country = ?{years})""",
               """EXISTS (SELECT 1 FROM profile_education o WHERE o.profile_id = p.id AND o.degree_level = ?
                   AND o.country = ?)"""]
    params = [spec["chosen_level"], *(members or []), spec["chosen_country"]]
    years = ""
    if p["year_from"]:
        years += " AND c.end_year >= ?"
        params.append(p["year_from"])
    if p["year_to"]:
        years += " AND c.end_year <= ?"
        params.append(p["year_to"])
    inst_sql = f"AND c.institution_canonical_id IN ({', '.join(['?'] * len(members))}) " if members else ""
    clauses[0] = clauses[0].format(years=years, inst=inst_sql)
    params += [spec["other_level"], spec["other_country"]]
    if p["filter"] == "P":
        # Everyone the paid LinkedIn search verified for these years, plus college-list matches.
        edu_sql, edu_params = " AND ".join(clauses), params
        clauses = [f"((p.verified_bachelor_year >= ? AND p.verified_bachelor_year <= ?) OR ({edu_sql}))"]
        params = [p["year_from"], p["year_to"], *edu_params]
    else:
        # ...or verified for this institution by a Filter A/B LinkedIn search.
        edu_sql, edu_params = " AND ".join(clauses), params
        vyears, vparams = "", []
        if p["year_from"]:
            vyears += " AND v.chosen_year >= ?"
            vparams.append(p["year_from"])
        if p["year_to"]:
            vyears += " AND v.chosen_year <= ?"
            vparams.append(p["year_to"])
        clauses = [f"""(({edu_sql}) OR EXISTS (SELECT 1 FROM profile_verifications v WHERE v.profile_id = p.id
                        AND v.filter_key = ? AND v.institution_id IN ({", ".join(["?"] * len(members))}){vyears}))"""]
        params = [*edu_params, p["filter"], *members, *vparams]
    if p["location"]:
        clauses.append("LOWER(COALESCE(p.location, '')) LIKE ?")
        params.append(_like(p["location"]))
    if p["keyword"]:
        clauses.append("(LOWER(COALESCE(p.headline, '')) LIKE ? OR LOWER(COALESCE(p.current_title, '')) LIKE ? "
                       "OR LOWER(COALESCE(p.current_company, '')) LIKE ?)")
        params += [_like(p["keyword"])] * 3
    return " AND ".join(clauses), params


def _fmt_entries(entries: List[Dict], with_degree: bool) -> str:
    parts = []
    for e in entries:
        label = e["canonical_name"] or e["institution_name"]
        if with_degree and e.get("degree"):
            label += f" - {e['degree']}" + (f", {e['field_of_study']}" if e.get("field_of_study") else "")
        label += f" ({e['end_year']})" if e.get("end_year") else " (year not listed)"
        parts.append(label)
    return "; ".join(parts)


def _in_range(year, p: Dict) -> bool:
    """No range set -> any entry. Range set -> the entry needs a year inside it (missing year never counts)."""
    if not (p["year_from"] or p["year_to"]):
        return True
    if not year:
        return False
    return (not p["year_from"] or year >= p["year_from"]) and (not p["year_to"] or year <= p["year_to"])


def _decorate(conn, p: Dict, rows: List[Dict]) -> List[Dict]:
    """Attach the qualifying education entries to each profile row."""
    if not rows:
        return []
    spec = FILTERS[p["filter"]]
    members = member_ids(p["institution_id"])
    ids = [r["id"] for r in rows]
    cur = conn.cursor()
    cur.execute(f"""SELECT e.profile_id, e.institution_name, e.institution_canonical_id, e.degree, e.degree_level,
                           e.field_of_study, e.country, e.end_year,
                           (SELECT MIN(a.canonical_name) FROM institution_aliases a WHERE a.canonical_id = e.institution_canonical_id) AS canonical_name
                    FROM profile_education e WHERE e.profile_id IN ({", ".join(["?"] * len(ids))})
                    ORDER BY e.end_year""", ids)
    by_profile: Dict[int, List[Dict]] = {}
    for e in cur.fetchall():
        by_profile.setdefault(e["profile_id"], []).append(dict(e))

    out = []
    for r in rows:
        edu = by_profile.get(r["id"], [])
        chosen = [e for e in edu if e["degree_level"] == spec["chosen_level"] and (members is None or e["institution_canonical_id"] in members)
                  and e["country"] == spec["chosen_country"] and _in_range(e["end_year"], p)]
        other = [e for e in edu if e["degree_level"] == spec["other_level"] and e["country"] == spec["other_country"]]
        if p["filter"] in ("A", "B") and members and (not chosen or not other):
            # Verified by a Filter A/B LinkedIn search but the OTHER school isn't on the college list:
            # show the profile's entries the search verified.
            if not chosen:
                chosen = [e for e in edu if e["degree_level"] == spec["chosen_level"] and e["institution_canonical_id"] in members
                          and _in_range(e["end_year"], p)]
            if not other:
                if p["filter"] == "A":
                    other = [e for e in edu if e["degree_level"] == "Masters" and e["country"] not in ("India",)
                             and (e["country"] == "USA" or not is_indian_institution(e["institution_name"]))]
                else:
                    other = [e for e in edu if e["degree_level"] == "Bachelors"
                             and (e["country"] == "India" or (not e["country"] and is_indian_institution(e["institution_name"])))]
        vy = r.get("verified_bachelor_year")
        if p["filter"] == "P" and vy and _in_range(vy, p):
            # Search-verified person whose college/university isn't on the college list: show the
            # entries the search verified (Bachelor's ending that year; a Master's not from India).
            if not chosen:
                chosen = [e for e in edu if e["degree_level"] == "Bachelors" and e["end_year"] == vy and e["country"] != "USA"]
            if not other:
                other = [e for e in edu if e["degree_level"] == "Masters" and e["country"] != "India"]
        # A and Passout choose on the Indian Bachelor's side; B chooses on the US Master's side.
        indian, us = (other, chosen) if p["filter"] == "B" else (chosen, other)
        out.append({
            "id": r["id"],
            "name": r["name"],
            "headline": r["current_title"] or r["headline"] or "",
            "current_company": r["current_company"] or "",
            "location": r["location"] or "",
            "indian_college": _fmt_entries(indian, with_degree=False),
            "us_masters": _fmt_entries(us, with_degree=True),
            "linkedin_url": r["linkedin_url"],
            "captured_by": r["captured_by_name"] or ("Sourcing search" if not r["captured_by"] else f"User #{r['captured_by']}"),
            "captured_at": str(r["captured_at"] or "")[:10],
        })
    return out


_SELECT = """SELECT p.id, p.name, p.headline, p.current_title, p.current_company, p.location, p.linkedin_url,
                    p.captured_by, p.captured_at, p.verified_bachelor_year, u.name AS captured_by_name
             FROM linkedin_profiles p LEFT JOIN users u ON u.id = p.captured_by"""


def search(conn, p: Dict) -> Dict:
    where, params = _where(p)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM linkedin_profiles p WHERE {where}", params)
    total = int(cur.fetchone()[0])
    offset = (p["page"] - 1) * p["page_size"]
    cur.execute(f"{_SELECT} WHERE {where} ORDER BY p.captured_at DESC, p.id DESC LIMIT ? OFFSET ?",
                params + [p["page_size"], offset])
    rows = [dict(r) for r in cur.fetchall()]
    return {"total": total, "page": p["page"], "page_size": p["page_size"],
            "pages": max(1, -(-total // p["page_size"])), "results": _decorate(conn, p, rows)}


def export_rows(conn, p: Dict) -> List[Dict]:
    where, params = _where(p)
    cur = conn.cursor()
    cur.execute(f"{_SELECT} WHERE {where} ORDER BY p.captured_at DESC, p.id DESC LIMIT ?", params + [MAX_EXPORT_ROWS])
    return _decorate(conn, p, [dict(r) for r in cur.fetchall()])


EXPORT_COLUMNS = [
    ("name", "Name"), ("headline", "Headline / Current Title"), ("current_company", "Current Company"),
    ("location", "Location"), ("indian_college", "Indian College (Bachelor's year)"),
    ("us_masters", "US University - Master's (year)"), ("linkedin_url", "LinkedIn URL"),
    ("captured_by", "Captured By"), ("captured_at", "Captured Date"),
]


def build_xlsx(rows: List[Dict], title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Candidates"
    ws.append([label for _, label in EXPORT_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1D4ED8")
    for r in rows:
        ws.append([r.get(key, "") for key, _ in EXPORT_COLUMNS])
        link = ws.cell(row=ws.max_row, column=[k for k, _ in EXPORT_COLUMNS].index("linkedin_url") + 1)
        if link.value:
            link.hyperlink = link.value
            link.font = Font(color="1D4ED8", underline="single")
    widths = {"name": 24, "headline": 40, "current_company": 26, "location": 26, "indian_college": 46,
              "us_masters": 56, "linkedin_url": 48, "captured_by": 20, "captured_at": 14}
    for i, (key, _) in enumerate(EXPORT_COLUMNS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = widths.get(key, 20)
    ws.freeze_panes = "A2"
    info = wb.create_sheet("Search")
    info.append(["Search", title])
    info.append(["Rows", len(rows)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def institutions_for(conn, filter_key: str) -> List[Dict]:
    """The chosen-side institutions of one filter for the autocomplete, with every alias (so
    "JNTUH" finds it) and how many stored profiles this filter would return for it (no year range)."""
    spec = FILTERS[filter_key]
    country, level = spec["chosen_country"], spec["chosen_level"]
    cur = conn.cursor()
    cur.execute("SELECT canonical_id, canonical_name, city, alias FROM institution_aliases WHERE country = ? ORDER BY canonical_name",
                (country,))
    insts: Dict[str, Dict] = {}
    for r in cur.fetchall():
        d = insts.setdefault(r["canonical_id"], {"id": r["canonical_id"], "name": r["canonical_name"],
                                                 "city": r["city"] or "", "aliases": [], "profiles": 0})
        if r["alias"] != r["canonical_name"]:
            d["aliases"].append(r["alias"])
    # Who this filter would return per institution (no year range): college-list matches plus people
    # a Filter A/B LinkedIn search verified for it. Kept as sets so groups count distinct people.
    people: Dict[str, set] = {}
    cur.execute("""SELECT DISTINCT c.institution_canonical_id AS cid, c.profile_id AS pid FROM profile_education c
                   WHERE c.country = ? AND c.degree_level = ? AND c.institution_canonical_id IS NOT NULL
                     AND EXISTS (SELECT 1 FROM profile_education o WHERE o.profile_id = c.profile_id
                                 AND o.degree_level = ? AND o.country = ?)""",
                (country, level, spec["other_level"], spec["other_country"]))
    for r in cur.fetchall():
        people.setdefault(r["cid"], set()).add(r["pid"])
    cur.execute("SELECT DISTINCT institution_id AS cid, profile_id AS pid FROM profile_verifications WHERE filter_key = ?",
                (filter_key,))
    for r in cur.fetchall():
        people.setdefault(r["cid"], set()).add(r["pid"])
    for cid, pids in people.items():
        if cid in insts:
            insts[cid]["profiles"] = len(pids)
    out = sorted(insts.values(), key=lambda d: (-d["profiles"], d["name"]))
    # "Any campus" groups go first; their count is distinct people across all member campuses.
    groups = []
    for g in _GROUPS.values():
        if g["country"] != country:
            continue
        members = [m for m in g["members"] if m in insts]
        if not members:
            continue
        group_people = set().union(*(people.get(m, set()) for m in members))
        groups.append({"id": g["id"], "name": g["name"], "city": "", "aliases": list(g["words"]),
                       "profiles": len(group_people), "group": True,
                       "members": [insts[m]["name"] for m in members]})
    return groups + out
