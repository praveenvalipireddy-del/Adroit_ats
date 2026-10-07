# Adroit ATS: Code Review Report

Date: 2026-10-05 · Scope: the whole repository at commit `0162938` (main) · Reviewer: Claude

## 1. What the project is

Adroit ATS ("Hiring Flow") is a Flask web app for a US IT bench-sales team. Recruiters use it to:

- **Bench**: keep consultants with their resumes, visa status and expected rate.
- **Jobs**: find fresh contract jobs (Dice, LinkedIn, Naukri/Foundit via Apify), filtered by experience. A 1-Click Gmail Draft submits a consultant with the resume attached, and the recruiter's known vendors are BCC'd automatically.
- **Paste Requirement & Draft**: turn a pasted requirement into a Gmail draft (To = the poster, BCC = vendor contacts).
- **Sourcing**: find new candidates on LinkedIn (Indian Bachelor's → US Master's, by passout year or by college) through Apify or HarvestAPI. A tracker card per candidate holds contact details, status, owner and follow-up, plus an Add to Bench step.
- **Vendors**: each recruiter's private vendor contacts, uploaded from Excel.
- **Resume Optimizer**: tailor a Word resume to a job description with Gemini (with fallbacks).
- **Reporting / Admin**: drafts pipeline, recruiter accounts, college list.

Deployment: Render (gunicorn, 2 workers), Postgres in production, SQLite locally, auto-deploy from GitHub `main`.

## 2. Overall assessment

| Area | Rating | Summary |
|---|---|---|
| Functionality | Good | Rich, working features driven by real recruiter needs |
| Testing discipline | Good | 34 test scripts, including 17 real-browser (Edge) tests, and no paid calls in tests |
| Data honesty | Good | Strong "genuine data only" rule; recently removed invented defaults |
| **Security** | **Poor (urgent)** | Public repo with personal data and a DB password in history, an unauthenticated paid endpoint, weak password fallbacks |
| Architecture / structure | Needs work | A few very large files; dead code; duplicated templates |
| Maintainability | Fair | Clear module docstrings, but large files and ad-hoc migrations |
| Ops / deployment | Fair | Three conflicting start configs, no CI, unpinned dependencies |

**What's done well (keep doing it):**
- Every bug fix comes with a test that names its root cause. The browser tests check the real UI in Edge.
- Paid APIs are guarded: a daily budget, per-run caps, stubbed in tests, costs shown to the user.
- SQL is parameterised everywhere; candidate updates whitelist field names; passwords are hashed with werkzeug.
- Front-end output goes through `escapeHtml`, and toasts use `innerText`.
- Privacy rules are explicit: vendor lists per recruiter, contact columns in Excel exports for admins only.
- Module docstrings explain *why*, not just *what*.

## 3. Findings

Severity: **Critical** = fix now; **High** = fix this week; **Medium** = plan it; **Low** = when convenient.

### 3.1 Security and privacy

| # | Severity | Finding | Evidence |
|---|---|---|---|
| S1 | **Critical** | The **GitHub repo is public** and contains **3 real resumes** (personal data): `data/resumes/Praveen_Valipireddy_Resume.docx`, `Resume_KarunKumar_CRG_JuniorBA.docx`, `Sai_Teja_Resume.pdf`. `.gitignore` lists `data/resumes/`, but these files were committed before that rule. | `git ls-files data/`; anonymous GitHub API returns 200 |
| S2 | **Critical** | The **production Postgres URL including its password** is in the public git history (an old `render.yaml`). Render's internal host name limits who can connect, but it is still a leaked credential. | `git log -p` (password not reproduced here) |
| S3 | **Critical** | **`POST /api/jobs/search` needs no login.** An anonymous request with `country: India` (or `live_scrape: true`) triggers a live scrape. The India one runs on **paid Apify**, so anyone on the internet can spend your credit. | Verified by calling every `/api/*` route logged out |
| S4 | High | **Default admin password `Admin@2026` is in the public source.** It is the seeded admin password and the dev-login password. If `ADMIN_PASSWORD` isn't set on Render, anyone can log in as admin. The app already logs a warning about this. | `models.py:679`, `app.py:165` |
| S5 | High | **Password fallback:** an account with no stored hash accepts `"password"`, `"Password@123"` or `"Admin@2026"` and saves it. Only old accounts are affected, but it's a backdoor. | `models.py:967-970` |
| S6 | High | **No login rate-limiting or lockout.** Passwords can be guessed without limit. | `app.py` `/login` |
| S7 | Medium | `Access-Control-Allow-Origin: *` on every response. It isn't needed (one origin), and it widens the attack surface. | `app.py:96` |
| S8 | Medium | **No CSRF protection** on cookie-authenticated form/multipart POSTs (e.g. `/api/vendors/upload-preview`). The JSON endpoints are partly protected by CORS preflight. | |
| S9 | Medium | Session cookie flags are not set explicitly (`SESSION_COOKIE_SECURE`, `SAMESITE`, `HTTPONLY`). | `app.py:39` |
| S10 | Medium | **LinkedIn data and personal contact data** (emails, phones, visa status) are stored without a retention or deletion policy. Scraping LinkedIn is against its Terms. Decide your policy and document it. | `linkedin_profiles`, `sourcing_*` |
| S11 | Low | Recruiter `avatar_url` is put into `<img src>` without escaping. | `app.js:2644` |

### 3.2 Architecture and folder structure

| # | Severity | Finding |
|---|---|---|
| A1 | High | **Everything is flat in the repo root**: 30 Python modules, 34 `test_*.py`, deployment files and docs together. There's no package, no `tests/` folder, no `scripts/`. |
| A2 | High | **Very large files:** `app.py` 2,630 lines / 63 routes / 80 functions; `static/js/app.js` 4,497 lines / 136 functions; `models.py` 1,679 lines; `dashboard.html` 1,413 lines with heavy inline styles. Hard to review and easy to merge-conflict. |
| A3 | High | **Dead / leftover code** (about 360 KB): `templates_bundle.py` (266 KB copy of the templates, used only as a fallback that's already out of date), `app_assets.py` (70 KB, imported by nothing), `live_job_scraper.py` (imported by nothing), `push_to_github.py` (needs `dulwich`, not in requirements), `PUSH_TO_GITHUB.bat`, `java_spring_boot/` + `java_spring_boot.zip`, `react_components/` (not used by the app), an empty `ats.db`. |
| A4 | Medium | **Database layer:** raw SQL with a home-made Postgres/SQLite wrapper (`?`→`%s`, auto `RETURNING id`) and ad-hoc `migrate_db` / `CREATE TABLE IF NOT EXISTS`. No migration history, and the two dialects behave differently. Example: until today, SQLite was silently used when Postgres was unreachable. |
| A5 | Medium | Long-running work happens inside web requests or daemon threads in gunicorn workers (HarvestAPI runs, auto-drafts that make up to 10 IMAP logins in one request). A worker restart loses in-flight work. |
| A6 | Low | `templates/dashboard.html` holds every tab's markup in one file; small Jinja partials per tab would help. |

### 3.3 Code quality and best practices

| # | Severity | Finding |
|---|---|---|
| Q1 | Medium | **Logging:** 146 `print()` calls versus 60 `logger.*`. Render logs lose level, time and module for prints. |
| Q2 | Medium | **27 broad `except Exception:` blocks.** Some swallow errors silently (`except: pass`), which hides real failures. |
| Q3 | Medium | **Gmail draft logging hard-codes `user_id=1`, "Recruiter"** (`gmail_multi_manager.py` `mark_job_drafted` / `log_activity`). Reporting can't tell which recruiter drafted. |
| Q4 | Medium | **Invented placeholder data remains** in places, against the project's own rule: `live_job_scraper.py` "Rate on Discussion (C2C)" (dead file); default avatar URLs pointing to stock photos of strangers (`models.create_user`, dev-login). |
| Q5 | Low | 26 pyflakes warnings (unused imports such as `json`, `Path`, `msal`, `sys`; unused variables; f-strings without placeholders). |
| Q6 | Low | The cache-buster (`app.js?v=5.56.0`) is bumped by hand and asserted in about 15 tests, so every UI change edits all of them. |
| Q7 | Low | Mixed naming in the UI and docs: "Hiring Flow", "Adroit AI", "Aventra AI" (Dockerfile), "Mymulya" (README). |

### 3.4 Testing

| # | Severity | Finding |
|---|---|---|
| T1 | Medium | The tests are standalone scripts with their own `check()` helper, not pytest. The full suite runs serially for about 8 minutes, with no parallelism, fixtures or coverage report. |
| T2 | Medium | **No CI.** Tests run only when someone remembers to run them locally; nothing blocks a broken push from auto-deploying to Render. |
| T3 | Low | Browser tests are Edge-only and include fixed `wait_for_timeout` sleeps. One test (`test_education_filters_browser.py`) flaked once today. |
| T4 | Low | There's no Postgres test run. The Postgres wrapper is only covered by targeted stubs, yet production runs on Postgres. |

### 3.5 Deployment and operations

| # | Severity | Finding |
|---|---|---|
| D1 | Medium | **Three conflicting start configs:** `Procfile` (2 workers, *no* timeout), `render.yaml` (2 workers, 240 s), `Dockerfile` (3 workers, 240 s). Only one is really used; the others mislead. |
| D2 | Medium | `requirements.txt` uses `>=` only, so a new release of any library can break a deploy. Pin exact versions with a lock file. |
| D3 | Medium | `render.yaml` sets `SECRET_KEY`, but your Render service uses `FLASK_SECRET_KEY`. Both work (the config accepts either), but the blueprint doesn't match reality, and `ADMIN_PASSWORD` isn't declared. |
| D4 | Low | No health-check endpoint, no error tracking (e.g. Sentry), no uptime alerts. |
| D5 | Low | Docs are out of date: `README.md` (Mymulya, Indeed), `API_DOCS.md`, `README_FOR_DEVS.md` don't describe Vendors, Sourcing, HarvestAPI, or the env vars. |

## 4. Action items (prioritised)

### Now (today): security
1. **Make the GitHub repo private** (GitHub → Settings → Danger zone → Change visibility). This is the fastest way to stop S1, S2 and S4 exposure. *(5 min, you)*
2. **Rotate the Postgres password** on Render (Database → Rotate password), then update `DATABASE_URL` on the web service. *(10 min, you)*
3. **Set `ADMIN_PASSWORD`** on Render to a strong new password, and confirm the "default password" warning is gone from the logs. *(5 min, you)*
4. **Require login on `/api/jobs/search`** and on every other `/api/*` route by default (one `before_request` allow-list instead of per-route checks), with a test that calls every API route logged out. *(1 h, me)*
5. **Remove the password fallback** in `authenticate_user` and the hard-coded `Admin@2026` paths, including dev-login. *(30 min, me)*
6. **Remove the 3 resumes from git** (`git rm --cached`) and purge them from history (`git filter-repo`), or at least keep the repo private. Ask the people whose resumes they are if they were exposed. *(1 h, me + you)*

### This week: stability
7. Add **login rate-limiting** (e.g. 5 failed tries per 15 min per email/IP) and secure cookie flags; drop `Access-Control-Allow-Origin: *`. *(2 h)*
8. **Add CI** (GitHub Actions): run the non-browser tests on every push, and block Render auto-deploy on failure (Render "Auto-Deploy: After CI checks pass"). *(2 h)*
9. **Pin dependencies** (`pip freeze` → `requirements.txt`, or `pip-tools`), and keep **one** start command (render.yaml); delete or align the `Procfile` and `Dockerfile`. *(1 h)*
10. Record the **real user** on Gmail drafts and activity (Q3). *(1 h)*

### This month: structure
11. **Delete dead code**: `templates_bundle.py`, `app_assets.py`, `live_job_scraper.py`, `push_to_github.py`, `PUSH_TO_GITHUB.bat`, `java_spring_boot/`, `java_spring_boot.zip`, `react_components/`, `ats.db`. About 360 KB less to read. *(1 h)*
12. **Restructure into a package:**
    ```
    adroit_ats/
      app.py              # create_app(), blueprints registered
      config.py
      db.py               # connection + dialect wrapper
      routes/             # auth, candidates, jobs, outreach, sourcing, vendors, admin, resume
      services/           # gmail, vendors, sourcing (apify, harvest), scrapers, resume_bot
      models/             # users, candidates, jobs, vendors, sourcing
      templates/partials/ # one partial per tab
      static/js/          # app.js split per tab: jobs.js, sourcing.js, vendors.js ...
    tests/
      unit/  browser/  conftest.py
    scripts/  docs/
    ```
    Do it one blueprint at a time, running the suite after each move. *(2–4 days)*
13. **Adopt Alembic migrations**, optionally with SQLAlchemy Core, to replace `migrate_db` and the dialect wrapper. *(2 days)*
14. Move **long jobs to a worker** (Render Background Worker + a simple DB-backed queue, or RQ/Redis) for HarvestAPI runs, auto-drafts and scrapes. *(2 days)*

### Ongoing: quality
15. Convert tests to **pytest** (keep the same checks; add fixtures for app, DB and users; `pytest -n auto` for speed); replace fixed sleeps with waits for a condition. *(1–2 days)*
16. Replace `print()` with `logger`; narrow the broad `except` blocks; fix the pyflakes warnings. *(half a day)*
17. Add a `/healthz` endpoint, error tracking (Sentry free tier) and an uptime monitor. *(2 h)*
18. **Update the docs**: README (what the app does, setup, env var list), and a short data-handling note (what personal data is kept, for how long, who can see it). *(half a day)*
19. Generate the cache-buster from the file's hash at startup, so tests stop asserting a version number. *(1 h)*

## 5. Suggested order

Items **1–3** are settings changes only you can make on GitHub/Render. They take about 20 minutes and remove most of the risk. Items **4–6** are code changes I can make next, each tested and pushed like the recent fixes. After that, items 7–10, then the restructuring (11–14) in small, tested steps.
