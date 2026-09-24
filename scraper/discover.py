"""Find company job boards on public ATS platforms using the Common Crawl index.

Every Ashby / Greenhouse / Lever / Workable / Recruitee board that Common
Crawl has seen gets added to data/companies.json, so the scraper polls
thousands of companies instead of a hand-made list. Runs weekly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .common import UA

log = logging.getLogger("discover")
DATA = Path(__file__).resolve().parent.parent / "data"

PATTERNS = {
    "ashby": [("jobs.ashbyhq.com/*", "prefix")],
    "greenhouse": [("boards.greenhouse.io/*", "prefix"), ("job-boards.greenhouse.io/*", "prefix")],
    "lever": [("jobs.lever.co/*", "prefix")],
    "workable": [("apply.workable.com/*", "prefix")],
    "recruitee": [("recruitee.com", "domain")],
}
SKIP = {"", "embed", "api", "j", "jobs", "careers", "static", "assets", "robots.txt", "favicon.ico", "www", "app", "blog", "help", "support"}
SLUG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def slug_of(ats: str, url: str) -> str | None:
    try:
        u = urlparse(url if "://" in url else "https://" + url)
    except Exception:
        return None
    if ats == "recruitee":
        sub = (u.hostname or "").split(".")[0]
        return sub if sub not in SKIP and (u.hostname or "").endswith(".recruitee.com") else None
    parts = [p for p in u.path.split("/") if p]
    if not parts:
        return None
    s = parts[0]
    return s if s.lower() not in SKIP and SLUG_OK.match(s) else None


async def crawl_ids(c: httpx.AsyncClient, n: int = 2) -> list[str]:
    r = await c.get("https://index.commoncrawl.org/collinfo.json")
    return [x["cdx-api"] for x in r.json()[:n]]


async def query(c, api, pattern, match, max_pages):
    params = {"url": pattern, "output": "json", "fl": "url", "filter": "status:200"}
    if match == "domain":
        params["matchType"] = "domain"
    try:
        r = await c.get(api, params={**params, "showNumPages": "true"})
        pages = min(int(r.json().get("pages", 1)), max_pages)
    except Exception as e:
        log.warning("pages %s %s: %s", api, pattern, e)
        return []
    urls = []
    for p in range(pages):
        for attempt in range(3):
            try:
                r = await c.get(api, params={**params, "page": p}, timeout=90)
                if r.status_code == 200:
                    urls += [json.loads(l)["url"] for l in r.text.splitlines() if l.strip()]
                    break
                await asyncio.sleep(5 * (attempt + 1))
            except Exception:
                await asyncio.sleep(5 * (attempt + 1))
    return urls


async def main(max_pages: int = 40):
    path = DATA / "companies.json"
    companies = json.loads(path.read_text()) if path.exists() else {}
    before = {k: len(v) for k, v in companies.items()}
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as c:
        apis = await crawl_ids(c)
        for ats, pats in PATTERNS.items():
            found = set(companies.get(ats, []))
            for api in apis:
                for pat, match in pats:
                    for url in await query(c, api, pat, match, max_pages):
                        s = slug_of(ats, url)
                        if s:
                            found.add(s)
            companies[ats] = sorted(found, key=str.lower)
            log.info("discover %-10s %d -> %d boards", ats, before.get(ats, 0), len(companies[ats]))
    path.write_text(json.dumps(companies, indent=1))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
