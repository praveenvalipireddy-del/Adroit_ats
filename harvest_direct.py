"""HarvestAPI direct (api.harvestapi.io) as the LinkedIn sourcing provider - an alternative to running
HarvestAPI's scraper through Apify. Switched on with SOURCING_PROVIDER=harvestapi (+ HARVESTAPI_API_KEY);
remove the setting (or set it to "apify") to go back to Apify. linkedin_sourcing calls into this module,
so every route, filter, export and tracker card works the same with either provider.

One "run" = one LinkedIn result page, done in a background thread:
  1. GET /linkedin/lead-search  (schools, locations, years-of-experience, page) -> up to 25 short profiles
     (no education in them),
  2. GET /linkedin/profile for each one -> the full profile (education with years, location country),
  3. the full profiles are stored in harvest_runs and read back by linkedin_sourcing.fetch_run in the
     same shape as the Apify dataset items.

Cost: HarvestAPI doesn't publish per-call prices in its docs, so spend is ESTIMATED from
HARVESTAPI_SEARCH_PRICE_USD / HARVESTAPI_PROFILE_PRICE_USD (defaults = what the same scraper costs on
Apify) and counted against the same daily budget. Check the real usage in the HarvestAPI dashboard.
"""
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import requests

import models

logger = logging.getLogger("harvest_direct")

API = "https://api.harvestapi.io"
SEARCH_PRICE_USD = float(os.getenv("HARVESTAPI_SEARCH_PRICE_USD", "0.10"))
PROFILE_PRICE_USD = float(os.getenv("HARVESTAPI_PROFILE_PRICE_USD", "0.004"))
# Trial safety: pages per search request while on HarvestAPI (the free trial credit is only $1).
MAX_PAGES = int(os.getenv("HARVESTAPI_MAX_PAGES", "1"))
PROFILE_WORKERS = 5
RUN_PREFIX = "hv-"


def enabled() -> bool:
    return os.getenv("SOURCING_PROVIDER", "").strip().lower() == "harvestapi"


def _key() -> str:
    return (os.getenv("HARVESTAPI_API_KEY") or "").strip()


def configured() -> bool:
    return bool(_key())


def is_run(run_id: Optional[str]) -> bool:
    return str(run_id or "").startswith(RUN_PREFIX)


def _redact(text: str) -> str:
    k = _key()
    return text.replace(k, "***") if k else text


def _get(path: str, params: Dict) -> Dict:
    """(status_code, json-or-{}, error text)"""
    try:
        r = requests.get(f"{API}{path}", params=params, headers={"X-API-Key": _key()}, timeout=60)
    except Exception as ex:
        return {"status": 0, "data": {}, "error": _redact(f"could not reach HarvestAPI ({ex.__class__.__name__})")}
    try:
        data = r.json()
    except ValueError:
        data = {}
    err = ""
    if r.status_code != 200:
        err = (data.get("error") or data.get("message") if isinstance(data, dict) else "") or f"HTTP {r.status_code}"
    elif isinstance(data, dict) and data.get("error"):
        err = str(data["error"])
    return {"status": r.status_code, "data": data if isinstance(data, dict) else {}, "error": _redact(str(err))[:300]}


# ---------------------------------------------------------------- run storage

def _update(run_db_id: int, **fields):
    conn = models.get_db_connection()
    try:
        conn.cursor().execute(f"UPDATE harvest_runs SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                              list(fields.values()) + [run_db_id])
        conn.commit()
    finally:
        conn.close()


def _row(run_db_id: int) -> Optional[Dict]:
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM harvest_runs WHERE id = ?", (run_db_id,))
        r = cur.fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _db_id(run_id: str) -> int:
    return int(str(run_id)[len(RUN_PREFIX):])


def todays_spend_usd() -> float:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM harvest_runs WHERE created_at >= ?", (today,))
        return round(float(cur.fetchone()[0] or 0), 4)
    finally:
        conn.close()


# ---------------------------------------------------------------- the search

def search_params(schools: List[str], location: str, page: int, experience_ids: List[str]) -> Dict:
    p = {"schools": ",".join(schools[:50]), "locations": location, "page": page}
    if experience_ids:
        p["yearsOfExperienceIds"] = ",".join(experience_ids)
    return p


def _work(run_db_id: int, params: Dict):
    calls, items, error = 0, [], ""
    try:
        res = _get("/linkedin/lead-search", params)
        calls_search = 1
        elements = [e for e in (res["data"].get("elements") or []) if isinstance(e, dict)]
        total = (res["data"].get("pagination") or {}).get("totalElements")
        _update(run_db_id, search_total=total if isinstance(total, int) else None, search_found=len(elements),
                cost_usd=SEARCH_PRICE_USD, status="RUNNING")
        if res["error"]:
            error = f"HarvestAPI search failed: {res['error']}"
        urls = [e.get("linkedinUrl") for e in elements if e.get("linkedinUrl")][:25]

        def full(url):
            row = _row(run_db_id)
            if row and row.get("abort"):
                return None
            r = _get("/linkedin/profile", {"query": url})
            return r

        with ThreadPoolExecutor(max_workers=PROFILE_WORKERS) as pool:
            results = list(pool.map(full, urls))
        profile_errors = []
        for r in results:
            if r is None:
                continue
            calls += 1
            prof = r["data"].get("element") if isinstance(r["data"].get("element"), dict) else r["data"]
            if r["error"] or not isinstance(prof, dict) or not prof.get("linkedinUrl"):
                profile_errors.append(r["error"] or "empty profile")
                continue
            items.append({k: prof.get(k) for k in ("linkedinUrl", "firstName", "lastName", "headline", "location",
                                                   "education", "currentPosition", "openToWork", "experience")})
        if not items and profile_errors and not error:
            error = f"HarvestAPI profile lookups failed: {profile_errors[0]}"
        cost = round(SEARCH_PRICE_USD * calls_search + PROFILE_PRICE_USD * calls, 4)
        aborted = bool((_row(run_db_id) or {}).get("abort"))
        _update(run_db_id, items_json=json.dumps(items), profiles_ok=len(items), profile_calls=calls, cost_usd=cost,
                error=error, status="ABORTED" if aborted else ("FAILED" if error and not items else "SUCCEEDED"),
                finished_at=_now())
    except Exception as ex:   # never leave a run stuck in RUNNING
        logger.exception("HarvestAPI run failed")
        _update(run_db_id, status="FAILED", error=_redact(f"HarvestAPI run failed: {ex}")[:300], finished_at=_now(),
                items_json=json.dumps(items))


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def start_page_run(schools: List[str], location: str, page: int, experience_ids: List[str],
                   background: bool = True) -> Dict:
    """Start one page; returns {run_id, dataset_id, start_page} like an Apify run."""
    params = search_params(schools, location, page, experience_ids)
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO harvest_runs (status, start_page, params_json, cost_usd) VALUES ('READY', ?, ?, 0)",
                    (page, json.dumps(params)))
        run_db_id = cur.lastrowid
        conn.commit()
        if not run_db_id:
            cur.execute("SELECT MAX(id) FROM harvest_runs")
            run_db_id = cur.fetchone()[0]
    finally:
        conn.close()
    if background:
        threading.Thread(target=_work, args=(run_db_id, params), daemon=True).start()
    else:
        _work(run_db_id, params)
    rid = f"{RUN_PREFIX}{run_db_id}"
    return {"run_id": rid, "dataset_id": rid, "start_page": page}


def fetch_run(run_id: str, offset: int, batch: int) -> Dict:
    """{run, status, raw_items} in the shape linkedin_sourcing.fetch_run returns for Apify."""
    row = _row(_db_id(run_id))
    if not row:
        return {"error": "That HarvestAPI search is no longer available.", "code": 404}
    items = json.loads(row.get("items_json") or "[]")
    run = {"id": run_id, "status": row["status"], "statusMessage": row.get("error") or "",
           "usageTotalUsd": row.get("cost_usd"),
           "harvest": {"search_total": row.get("search_total"), "search_found": row.get("search_found"),
                       "profiles_ok": row.get("profiles_ok"), "profile_calls": row.get("profile_calls"),
                       "estimated_cost": True}}
    return {"run": run, "status": row["status"], "raw_items": items[offset:offset + batch]}


def abort(run_id: str) -> bool:
    try:
        _update(_db_id(run_id), abort=1)
        return True
    except Exception:
        return False


def problem(run_id: str):
    """(message, kind) for a finished run that returned nothing, like linkedin_sourcing._provider_problem."""
    row = _row(_db_id(run_id)) or {}
    if row.get("error"):
        return row["error"], "error"
    if row.get("search_found") == 0:
        return "", ""     # a genuinely empty result page
    return "", ""
