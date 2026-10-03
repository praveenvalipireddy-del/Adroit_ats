"""Daily LinkedIn sourcing budget must count ALL of today's Apify runs.

Root cause this guards against: todays_spend_usd() read only the latest 100 runs. One run = one
page of 25 profiles, so on a busy team day (more than 100 runs) the rest of today's spend was
ignored and searches kept starting past SOURCING_DAILY_BUDGET_USD.

Apify is never contacted: requests.get is replaced by a stub serving 250 of today's runs + older
ones, 100 per page, newest first (like Apify's API).
Run: python test_daily_budget.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["APIFY_API_TOKEN"] = "dummy-test-token-not-real"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.APIFY_API_TOKEN = "dummy-test-token-not-real"
import linkedin_sourcing as ls  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


now = datetime.now(timezone.utc)
midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
span = max((now - midnight).total_seconds() - 5, 1)
# 250 runs today at $0.20 each (newest first), then 30 runs from yesterday at $1.00 each
runs = [{"startedAt": (now - timedelta(seconds=span * i / 250)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "usageTotalUsd": 0.20} for i in range(250)]
runs += [{"startedAt": (midnight - timedelta(hours=1, minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "usageTotalUsd": 1.00} for i in range(30)]
calls = []


class Resp:
    def __init__(self, items):
        self._items = items

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": {"items": self._items}}


def fake_get(url, params=None, timeout=None):
    calls.append(dict(params or {}))
    off, lim = int(params.get("offset", 0)), int(params.get("limit", 100))
    return Resp(runs[off:off + lim])


ls.requests.get = fake_get
spent = ls.todays_spend_usd()
check(abs(spent - 50.0) < 0.01, f"today's spend should be 250 x $0.20 = $50.00 (yesterday excluded), got ${spent}")
check(len(calls) == 3 and [c.get("offset") for c in calls] == [0, 100, 200], f"pages read: {[c.get('offset') for c in calls]}")

calls.clear()
runs[:] = runs[:40]   # a quiet day: one page is enough
check(abs(ls.todays_spend_usd() - 8.0) < 0.01 and len(calls) == 1, f"quiet day: one page, $8.00 ({len(calls)} calls)")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: daily budget counts all of today's Apify runs (pages through them), excludes yesterday.")
