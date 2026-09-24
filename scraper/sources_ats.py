"""Poll company job boards on public ATS APIs and keep Webflow roles.

Covers Ashby, Greenhouse, Lever, Workable and Recruitee. The company list
lives in data/companies.json and grows weekly via discover.py.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .common import WEBFLOW, WEBISH, fmt_salary, is_relevant, make_job, strip_html

log = logging.getLogger("ats")


async def _get(c: httpx.AsyncClient, url: str, **kw):
    try:
        r = await c.get(url, **kw)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


async def ashby(c, slug):
    d = await _get(c, f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"})
    out = []
    for j in (d or {}).get("jobs", []) or []:
        desc = j.get("descriptionPlain") or strip_html(j.get("descriptionHtml"))
        company = (d.get("organizationName") if isinstance(d, dict) else None) or slug
        company = j.get("organizationName") or company
        if not is_relevant(j.get("title", ""), desc, company):
            continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(
            make_job(
                title=j.get("title"),
                company=_pretty(slug) if company == slug else company,
                url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}",
                source="ashby",
                location=j.get("location") or "",
                remote=j.get("isRemote"),
                type_=j.get("employmentType") or "",
                posted=j.get("publishedAt"),
                salary=comp,
                description=desc,
            )
        )
    return out


async def greenhouse(c, slug):
    # List without content (light), then fetch the full posting only for web-ish titles.
    d = await _get(c, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    out = []
    for j in (d or {}).get("jobs", []) or []:
        title = j.get("title", "")
        company = j.get("company_name") or _pretty(slug)
        if not (WEBFLOW.search(title) or WEBISH.search(title)):
            continue
        full = await _get(c, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j.get('id')}")
        desc = strip_html((full or {}).get("content"))
        if not is_relevant(title, desc, company):
            continue
        out.append(
            make_job(
                title=j.get("title"),
                company=company,
                url=j.get("absolute_url"),
                source="greenhouse",
                location=(j.get("location") or {}).get("name", ""),
                posted=j.get("first_published") or j.get("updated_at"),
                description=desc,
            )
        )
    return out


async def lever(c, slug):
    d = await _get(c, f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    out = []
    for j in d or []:
        desc = (j.get("descriptionPlain") or "") + " " + " ".join(
            strip_html(x.get("content")) for x in j.get("lists", []) or []
        )
        if not is_relevant(j.get("text", ""), desc, slug):
            continue
        cat = j.get("categories") or {}
        sr = j.get("salaryRange") or {}
        out.append(
            make_job(
                title=j.get("text"),
                company=_pretty(slug),
                url=j.get("hostedUrl"),
                source="lever",
                location=cat.get("location") or "",
                remote=True if j.get("workplaceType") == "remote" else None,
                type_=cat.get("commitment") or "",
                posted=j.get("createdAt"),
                salary=fmt_salary(sr.get("min"), sr.get("max"), sr.get("currency"), (sr.get("interval") or "").replace("per-", "")),
                description=desc,
            )
        )
    return out


async def workable(c, slug):
    d = await _get(c, f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
    out = []
    company = (d or {}).get("name") or _pretty(slug)
    for j in (d or {}).get("jobs", []) or []:
        desc = strip_html(j.get("description"))
        if not is_relevant(j.get("title", ""), desc, company):
            continue
        loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
        out.append(
            make_job(
                title=j.get("title"),
                company=company,
                url=j.get("url") or j.get("shortlink") or f"https://apply.workable.com/{slug}/",
                source="workable",
                location=loc,
                remote=j.get("telecommuting"),
                type_=j.get("employment_type") or "",
                posted=j.get("published_on") or j.get("created_at"),
                description=desc,
            )
        )
    return out


async def recruitee(c, slug):
    d = await _get(c, f"https://{slug}.recruitee.com/api/offers/")
    out = []
    for j in (d or {}).get("offers", []) or []:
        desc = strip_html((j.get("description") or "") + " " + (j.get("requirements") or ""))
        company = j.get("company_name") or _pretty(slug)
        if not is_relevant(j.get("title", ""), desc, company):
            continue
        out.append(
            make_job(
                title=j.get("title"),
                company=company,
                url=j.get("careers_url") or f"https://{slug}.recruitee.com/",
                source="recruitee",
                location=j.get("location") or "",
                remote=j.get("remote"),
                type_=j.get("employment_type_code") or "",
                posted=j.get("published_at") or j.get("created_at"),
                description=desc,
            )
        )
    return out


def _pretty(slug: str) -> str:
    s = slug.split(".")[0].replace("-", " ").replace("_", " ")
    return s.title() if s.islower() else s


FETCHERS = {"ashby": ashby, "greenhouse": greenhouse, "lever": lever, "workable": workable, "recruitee": recruitee}


async def run(companies: dict[str, list[str]], client: httpx.AsyncClient, concurrency: int = 30):
    """Returns (jobs, hits) where hits = {ats: [slugs that had a Webflow job]}."""
    sem = asyncio.Semaphore(concurrency)
    stats = {k: [0, 0] for k in FETCHERS}  # boards polled, jobs kept
    hits: dict[str, set] = {k: set() for k in FETCHERS}

    async def one(ats, slug):
        async with sem:
            try:
                res = await FETCHERS[ats](client, slug)
            except Exception as e:  # never let one board kill the run
                log.debug("%s/%s failed: %s", ats, slug, e)
                res = []
            stats[ats][0] += 1
            stats[ats][1] += len(res)
            if res:
                hits[ats].add(slug)
            return res

    tasks = [one(ats, slug) for ats, slugs in companies.items() if ats in FETCHERS for slug in slugs]
    results = await asyncio.gather(*tasks)
    for ats, (n, k) in stats.items():
        log.info("ATS %-10s boards=%-6d webflow_jobs=%d", ats, n, k)
    return [j for r in results for j in r], {k: sorted(v) for k, v in hits.items()}
