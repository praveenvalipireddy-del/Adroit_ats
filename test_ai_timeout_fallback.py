"""Resume Optimizer when Gemini is too slow: fall back instead of giving up.

Root cause this guards against: a Gemini timeout (the free tier sometimes takes > 80 s) was not
treated as a retryable error, so the optimizer gave up at once - the configured backup (Grok /
OpenRouter) was never tried, the result was "keyword check only" after 80.8 s, and the draft panel
didn't say why. Now: 45 s per Gemini call, a timeout goes straight to the backup, and the reason is
shown. No network: the Gemini client and the backup are stubs. Labelled test fixtures.
Run: python test_ai_timeout_fallback.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.GEMINI_API_KEY = "test-key-not-real"
import resume_bot as rb  # noqa: E402
from google.genai import types  # noqa: E402

failures, gemini_calls, backup_calls = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


class ReadTimeout(Exception):
    pass


class FakeModels:
    def generate_content(self, model, contents, config):
        gemini_calls.append(model)
        raise ReadTimeout("The read operation timed out")


class FakeClient:
    models = FakeModels()


rb._gemini_client = lambda: (FakeClient(), types)
rb.gemini_configured = lambda: True
ANALYSIS = {"initial_match_percentage": 72, "match_breakdown": {"mandatory_skills": 29, "recent_project_relevance": 18, "domain_experience": 12,
            "tools_frameworks_cloud": 6, "certifications_education": 5, "location_work_authorization": 2}, "domain_detected": "Cloud",
            "strong_match_skills": ["Terraform"], "partial_match_skills": [], "mandatory_missing_skills": [], "preferred_missing_skills": [],
            "risky_skills_avoided": [], "skills_to_add": {"summary": [], "technical_skills": ["Ansible"], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 80}


def backup(system, prompt, want_json, max_tokens):
    backup_calls.append("analysis" if want_json else "rewrite")
    return (json.dumps(ANALYSIS), False, "grok-backup") if want_json else ("[3] Tools: Terraform, Ansible, Jenkins", False, "grok-backup")


RESUME = "RAVI TIMEOUTTEST\nSUMMARY\nDevOps engineer with 8 years.\nTools: Terraform, Jenkins\n"
JD = "DevOps Engineer: Terraform, Ansible and Jenkins required."

check(rb.GEMINI_TIMEOUT_MS == 45000, f"45 s per Gemini call: {rb.GEMINI_TIMEOUT_MS}")

# ---- backup configured: a timeout goes straight to it (no second slow Gemini wait), resume optimized
rb.xai_configured = lambda: True
rb.openrouter_configured = lambda: False
rb._openrouter_or_xai_generate = backup
res = rb.optimize_resume_for_jd(RESUME, JD)
check(res.get("ai_powered") and res.get("optimized"), f"optimized via the backup: {res.get('not_optimized_reason')} / {res.get('ai_unavailable_reason')}")
check(backup_calls == ["analysis", "rewrite"], f"backup used for both steps: {backup_calls}")
check(len(gemini_calls) == 2 and len(set(gemini_calls)) == 1, f"one Gemini attempt per step, no retry after a timeout: {gemini_calls}")
check("grok-backup" in res.get("ai_model", ""), f"result names the model that answered: {res.get('ai_model')}")

# ---- no backup: the reason says what happened
rb._ANALYSIS_CACHE.clear()
rb.xai_configured = lambda: False
res = rb.optimize_resume_for_jd(RESUME, JD + " ")
why = res.get("ai_unavailable_reason", "")
check(not res.get("optimized") and "took too long" in why and "45 seconds" in why and "no backup AI is configured" in why, f"reason: {why!r}")

# ---- the draft panel shows the reason (front-end code)
js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "js", "app.js"), encoding="utf-8").read()
check("Why the AI didn't run:" in js and "data.ai_unavailable_reason" in js.split("async function draftOptimizeRun")[1], "draft panel shows the reason")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: Gemini timeout -> straight to the backup (both steps optimized), clear reason without a backup, shown in the draft panel.")
