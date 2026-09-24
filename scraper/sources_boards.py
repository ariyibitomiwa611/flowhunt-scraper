"""Remote job boards with public JSON APIs, plus Hacker News "Who is hiring"."""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .common import fmt_salary, is_relevant, make_job, strip_html

log = logging.getLogger("boards")


async def _json(c: httpx.AsyncClient, url: str, **kw):
    try:
        r = await c.get(url, **kw)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def himalayas(c):
    out = []
    for page in range(1, 8):
        d = await _json(c, "https://himalayas.app/jobs/api/search", params={"q": "webflow", "page": page})
        rows = (d or {}).get("jobs") or []
        for j in rows:
            desc = strip_html(j.get("description") or j.get("excerpt"))
            if not is_relevant(j.get("title", ""), desc or "webflow", j.get("companyName", "")):
                continue
            out.append(make_job(
                title=j.get("title"), company=j.get("companyName"),
                url=j.get("applicationLink") or j.get("guid"), source="himalayas",
                location=", ".join(j.get("locationRestrictions") or []) or "Anywhere", remote=True,
                type_=j.get("employmentType"), posted=j.get("pubDate"),
                salary=fmt_salary(j.get("minSalary"), j.get("maxSalary"), j.get("currency")), description=desc))
        if len(rows) < 20:
            break
    return out


async def jobicy(c):
    d = await _json(c, "https://jobicy.com/api/v2/remote-jobs", params={"count": 100, "tag": "webflow"})
    out = []
    for j in (d or {}).get("jobs") or []:
        desc = strip_html(j.get("jobDescription"))
        if not is_relevant(j.get("jobTitle", ""), desc, j.get("companyName", "")):
            continue
        out.append(make_job(
            title=j.get("jobTitle"), company=j.get("companyName"), url=j.get("url"), source="jobicy",
            location=j.get("jobGeo") or "", remote=True, type_=(j.get("jobType") or [""])[0] if isinstance(j.get("jobType"), list) else j.get("jobType"),
            posted=j.get("pubDate"), salary=fmt_salary(j.get("annualSalaryMin"), j.get("annualSalaryMax"), j.get("salaryCurrency"), "yearly"),
            description=desc))
    return out


async def remotive(c):
    d = await _json(c, "https://remotive.com/api/remote-jobs", params={"search": "webflow"})
    out = []
    for j in (d or {}).get("jobs") or []:
        desc = strip_html(j.get("description"))
        if not is_relevant(j.get("title", ""), desc, j.get("company_name", "")):
            continue
        out.append(make_job(
            title=j.get("title"), company=j.get("company_name"), url=j.get("url"), source="remotive",
            location=j.get("candidate_required_location") or "Anywhere", remote=True, type_=j.get("job_type"),
            posted=j.get("publication_date"), salary=j.get("salary") or "", description=desc))
    return out


async def remoteok(c):
    d = await _json(c, "https://remoteok.com/api", params={"tag": "webflow"})
    out = []
    for j in (d or [])[1:] if isinstance(d, list) else []:
        desc = strip_html(j.get("description"))
        if not is_relevant(j.get("position", ""), desc or " ".join(j.get("tags") or []), j.get("company", "")):
            continue
        out.append(make_job(
            title=j.get("position"), company=j.get("company"), url=j.get("url") or j.get("apply_url"), source="remoteok",
            location=j.get("location") or "Anywhere", remote=True, posted=j.get("date"),
            salary=fmt_salary(j.get("salary_min"), j.get("salary_max"), "USD", "yearly"), description=desc))
    return out


async def arbeitnow(c):
    out = []
    for page in range(1, 6):
        d = await _json(c, "https://www.arbeitnow.com/api/job-board-api", params={"page": page})
        rows = (d or {}).get("data") or []
        for j in rows:
            desc = strip_html(j.get("description"))
            if not is_relevant(j.get("title", ""), desc, j.get("company_name", "")):
                continue
            out.append(make_job(
                title=j.get("title"), company=j.get("company_name"), url=j.get("url"), source="arbeitnow",
                location=j.get("location") or "", remote=j.get("remote"), type_=" ".join(j.get("job_types") or []),
                posted=j.get("created_at"), description=desc))
        if not rows:
            break
    return out


async def workingnomads(c):
    d = await _json(c, "https://www.workingnomads.com/api/exposed_jobs/")
    out = []
    for j in d or []:
        desc = strip_html(j.get("description"))
        if not is_relevant(j.get("title", ""), desc + " " + (j.get("tags") or ""), j.get("company_name", "")):
            continue
        out.append(make_job(
            title=j.get("title"), company=j.get("company_name"), url=j.get("url"), source="workingnomads",
            location=j.get("location") or "Anywhere", remote=True, posted=j.get("pub_date"), description=desc))
    return out


async def hackernews(c):
    """Webflow mentions in the latest 'Who is hiring?' threads (top-level comments only)."""
    s = await _json(c, "https://hn.algolia.com/api/v1/search_by_date",
                    params={"tags": "story,author_whoishiring", "query": "who is hiring", "hitsPerPage": 2})
    out = []
    for story in (s or {}).get("hits") or []:
        sid = story.get("objectID")
        d = await _json(c, "https://hn.algolia.com/api/v1/search",
                        params={"query": "webflow", "tags": f"comment,story_{sid}", "hitsPerPage": 100})
        for h in (d or {}).get("hits") or []:
            if str(h.get("parent_id")) != str(sid):
                continue  # replies, not job posts
            text = strip_html(h.get("comment_text"))
            first = re.split(r"\n|\. ", text, 1)[0]
            parts = [p.strip() for p in first.split("|")]
            company = parts[0][:80] if parts else "HN company"
            role = next((p for p in parts[1:] if re_role.search(p)), "Webflow role (see post)")
            loc = next((p for p in parts[1:] if re_loc.search(p)), "")
            out.append(make_job(
                title=role[:120], company=company, url=f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                source="hackernews", location=loc, posted=h.get("created_at"), description=text))
    return out


re_role = re.compile(r"engineer|developer|designer|web|front|marketing|webflow", re.I)
re_loc = re.compile(r"remote|onsite|on-site|hybrid|[A-Z][a-z]+,\s?[A-Z]{2}", re.I)

SOURCES = [himalayas, jobicy, remotive, remoteok, arbeitnow, workingnomads, hackernews]


async def run(c: httpx.AsyncClient) -> list[dict]:
    async def safe(fn):
        try:
            res = await fn(c)
        except Exception as e:
            log.warning("%s failed: %s", fn.__name__, e)
            res = []
        log.info("board %-14s kept=%d", fn.__name__, len(res))
        return res

    results = await asyncio.gather(*(safe(f) for f in SOURCES))
    return [j for r in results for j in r]
