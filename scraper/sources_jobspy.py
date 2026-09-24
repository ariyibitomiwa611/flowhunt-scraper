"""Big job boards via python-jobspy: LinkedIn, Indeed, Glassdoor, Google Jobs,
ZipRecruiter, Naukri and Bayt. Each (site, market) search is isolated so a
block or rate limit on one never stops the others."""
from __future__ import annotations

import logging
import math

from .common import fmt_salary, is_relevant, make_job

log = logging.getLogger("jobspy")

TERM = "webflow"

# LinkedIn: one search per market (location string).
LINKEDIN_LOCATIONS = [
    "Worldwide", "United States", "United Kingdom", "European Union", "Canada", "Australia",
    "India", "Nigeria", "South Africa", "Brazil", "Philippines", "United Arab Emirates", "Singapore",
]
# Indeed + Glassdoor: country markets they support.
INDEED_COUNTRIES = [
    "usa", "uk", "canada", "australia", "germany", "netherlands", "france", "spain", "ireland",
    "india", "singapore", "south africa", "nigeria", "brazil", "mexico", "philippines",
    "united arab emirates", "poland", "portugal", "new zealand",
]
GLASSDOOR_COUNTRIES = ["usa", "uk", "canada", "australia", "germany", "india", "france", "netherlands", "ireland", "singapore"]
GOOGLE_QUERIES = [
    "webflow developer jobs since last week",
    "webflow designer jobs since last week",
    "remote webflow jobs since last week",
    "webflow freelance contract jobs since last week",
]


def _rows_to_jobs(df, source_hint: str) -> list[dict]:
    out = []
    if df is None or len(df) == 0:
        return out
    for r in df.to_dict("records"):
        title = str(r.get("title") or "")
        company = str(r.get("company") or "")
        desc = r.get("description")
        desc = desc if isinstance(desc, str) else ""
        # Searches already matched "webflow"; with a description we can double-check,
        # without one we require a web-ish or Webflow title.
        if desc:
            if not is_relevant(title, desc, company):
                continue
        elif not is_relevant(title, "webflow", company):
            continue
        site = str(r.get("site") or source_hint)
        url = r.get("job_url_direct") if isinstance(r.get("job_url_direct"), str) and site == "google" else r.get("job_url")
        rem = r.get("is_remote")
        rem = rem if isinstance(rem, bool) else None
        posted = r.get("date_posted")
        if isinstance(posted, float) and math.isnan(posted):
            posted = None
        out.append(
            make_job(
                title=title,
                company=company,
                url=str(url or r.get("job_url") or ""),
                source=site.replace("zip_recruiter", "ziprecruiter"),
                location=str(r.get("location") or "") if not isinstance(r.get("location"), float) else "",
                remote=rem,
                type_=str(r.get("job_type") or "") if isinstance(r.get("job_type"), str) else "",
                posted=posted,
                salary=fmt_salary(r.get("min_amount"), r.get("max_amount"), r.get("currency") if isinstance(r.get("currency"), str) else "USD",
                                  r.get("interval") if isinstance(r.get("interval"), str) else ""),
                description=desc,
            )
        )
    return out


def _search(label: str, **kw) -> list[dict]:
    from jobspy import scrape_jobs  # imported lazily so the rest runs without it

    try:
        df = scrape_jobs(description_format="markdown", verbose=0, **kw)
        jobs = _rows_to_jobs(df, kw.get("site_name", ["?"])[0])
        log.info("jobspy %-38s raw=%-4d kept=%d", label, 0 if df is None else len(df), len(jobs))
        return jobs
    except Exception as e:
        log.warning("jobspy %s failed: %s", label, str(e)[:160])
        return []


def run(hours_old: int = 72) -> list[dict]:
    jobs: list[dict] = []
    for loc in LINKEDIN_LOCATIONS:
        jobs += _search(f"linkedin/{loc}", site_name=["linkedin"], search_term=TERM, location=loc,
                        results_wanted=100, hours_old=hours_old, linkedin_fetch_description=False)
    for c in INDEED_COUNTRIES:
        jobs += _search(f"indeed/{c}", site_name=["indeed"], search_term=TERM, country_indeed=c,
                        results_wanted=100, hours_old=hours_old)
    for c in GLASSDOOR_COUNTRIES:
        jobs += _search(f"glassdoor/{c}", site_name=["glassdoor"], search_term=TERM, country_indeed=c,
                        results_wanted=60, hours_old=hours_old)
    for q in GOOGLE_QUERIES:
        jobs += _search(f"google/{q[:24]}", site_name=["google"], google_search_term=q, results_wanted=60)
    jobs += _search("ziprecruiter/us", site_name=["zip_recruiter"], search_term=TERM, results_wanted=100, hours_old=hours_old)
    jobs += _search("naukri/in", site_name=["naukri"], search_term=TERM, results_wanted=60, hours_old=hours_old)
    jobs += _search("bayt/me", site_name=["bayt"], search_term=TERM, results_wanted=40)
    return jobs
