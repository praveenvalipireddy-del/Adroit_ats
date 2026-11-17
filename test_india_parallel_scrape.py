"""The India portals are searched at the same time, so "All portals" stays inside the server's 240 s request limit.
Each portal is stubbed with a 1 s wait (no Apify calls, no cost): one after another took 4 s, together it takes ~1 s.
A portal that crashes is reported as that portal's error; the others' jobs still come back."""
import time

import india_job_scrapers as ijs


def check(cond, msg):
    if not cond:
        raise SystemExit("FAIL: " + msg)
    print("ok -", msg)


def slow(name):
    def run(kw, location="India", limit=8, job_type=""):
        time.sleep(1.0)
        return [{"title": f"{name} Data Analyst", "company": f"{name} Co", "url": f"https://example.test/{name}", "source": name}]
    return run


def boom(kw, location="India", limit=8, job_type=""):
    raise RuntimeError("portal down")


ijs.scrape_naukri = slow("Naukri")
ijs.scrape_foundit = slow("Foundit")
ijs.scrape_indeed_india = slow("Indeed")
ijs.scrape_linkedin_india = slow("LinkedIn")

t0 = time.monotonic()
res = ijs.run_multi_source_india_scrape(keywords=["Data analyst SQL"], save_to_db=False)
took = time.monotonic() - t0
check(took < 2.5, f"four 1-second portals finished together in {took:.1f}s (one after another would be 4s+)")
check(res["count"] == 4, f"all four portals' jobs came back ({res['count']})")
check(all(res["sources"][p]["count"] == 1 for p in ("Naukri", "Foundit", "Indeed", "LinkedIn")), "each portal's count is reported")

ijs.scrape_foundit = boom
ijs.LAST_ERROR.pop("Foundit", None)
res = ijs.run_multi_source_india_scrape(keywords=["Data analyst SQL"], save_to_db=False)
check(res["count"] == 3, "a crashing portal doesn't lose the other portals' jobs")
check("failed" in res["sources"]["Foundit"]["error"], f"the crashed portal shows its error: {res['sources']['Foundit']['error']!r}")

res = ijs.run_multi_source_india_scrape(keywords=["Data analyst SQL"], save_to_db=False, sources=["Naukri"])
check(list(res["sources"]) == ["Naukri"] and res["count"] == 1, "picking one portal still searches only that portal")

js = open("static/js/app.js", encoding="utf-8").read()
check("took too long and the server stopped it - pick one portal in Source" in js, "a cut-off request says so instead of a bare 'failed'")
print("ALL PASSED")
