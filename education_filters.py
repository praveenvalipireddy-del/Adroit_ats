"""Filter A / Filter B over the education tables (linkedin_profiles + profile_education).

Filter A - "Indian College -> US Masters": a Bachelors entry at the chosen Indian college AND a
           Masters entry at any institution whose country is USA.
Filter B - "US University -> Indian Undergrad": a Masters entry at the chosen US university AND a
           Bachelors entry at any institution whose country is India.

- Year range (optional) applies to the entry at the CHOSEN institution (A: the Bachelor's end year,
  B: the Master's end year). When a range is set, entries with no end year don't qualify - a
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

FILTERS = {
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
        raise FilterError("filter must be 'A' or 'B'")
    inst = str(args.get("institution_id") or "").strip()
    if not inst:
        raise FilterError("Choose an institution first.")
    y_from, y_to = _year(args.get("year_from")), _year(args.get("year_to"))
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


def _like(text: str) -> str:
    return "%" + text.lower().replace("\\", "").replace("%", "").replace("_", "") + "%"


def _where(p: Dict):
    spec = FILTERS[p["filter"]]
    clauses = ["""EXISTS (SELECT 1 FROM profile_education c WHERE c.profile_id = p.id AND c.degree_level = ?
                   AND c.institution_canonical_id = ? AND c.country = ?{years})""",
               """EXISTS (SELECT 1 FROM profile_education o WHERE o.profile_id = p.id AND o.degree_level = ?
                   AND o.country = ?)"""]
    params = [spec["chosen_level"], p["institution_id"], spec["chosen_country"]]
    years = ""
    if p["year_from"]:
        years += " AND c.end_year >= ?"
        params.append(p["year_from"])
    if p["year_to"]:
        years += " AND c.end_year <= ?"
        params.append(p["year_to"])
    clauses[0] = clauses[0].format(years=years)
    params += [spec["other_level"], spec["other_country"]]
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
        chosen = [e for e in edu if e["degree_level"] == spec["chosen_level"] and e["institution_canonical_id"] == p["institution_id"]
                  and e["country"] == spec["chosen_country"] and _in_range(e["end_year"], p)]
        other = [e for e in edu if e["degree_level"] == spec["other_level"] and e["country"] == spec["other_country"]]
        indian, us = (chosen, other) if p["filter"] == "A" else (other, chosen)
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
                    p.captured_by, p.captured_at, u.name AS captured_by_name
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
    cur.execute("""SELECT c.institution_canonical_id AS cid, COUNT(DISTINCT c.profile_id) AS n FROM profile_education c
                   WHERE c.country = ? AND c.degree_level = ? AND c.institution_canonical_id IS NOT NULL
                     AND EXISTS (SELECT 1 FROM profile_education o WHERE o.profile_id = c.profile_id
                                 AND o.degree_level = ? AND o.country = ?)
                   GROUP BY c.institution_canonical_id""", (country, level, spec["other_level"], spec["other_country"]))
    for r in cur.fetchall():
        if r["cid"] in insts:
            insts[r["cid"]]["profiles"] = int(r["n"])
    return sorted(insts.values(), key=lambda d: (-d["profiles"], d["name"]))
