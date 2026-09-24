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
URL_RE = re.compile(r'"url":\s*"([^"]+)"')
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


async def _get(c, url, params=None, timeout=120, tries=6):
    """Common Crawl's index is often overloaded (503/504). Back off and retry."""
    wait = 15
    for attempt in range(tries):
        try:
            r = await c.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                await asyncio.sleep(1.5)  # be polite: roughly one request per second or two
                return r
            log.info("  %s -> %s, retry %d in %ds", url.split("/")[-1], r.status_code, attempt + 1, wait)
        except Exception as e:
            log.info("  %s -> %s, retry %d in %ds", url.split("/")[-1], type(e).__name__, attempt + 1, wait)
        await asyncio.sleep(wait)
        wait = min(wait * 2, 120)
    return None


async def crawl_ids(c: httpx.AsyncClient, n: int = 3) -> list[str]:
    r = await _get(c, "https://index.commoncrawl.org/collinfo.json")
    return [x["cdx-api"] for x in r.json()[:n]] if r else []


async def query(c, api, pattern, match, max_pages):
    params = {"url": pattern, "output": "json", "fl": "url"}
    if match == "domain":
        params["matchType"] = "domain"
    r = await _get(c, api, {**params, "showNumPages": "true"})
    if not r:
        return None  # index unavailable
    try:
        pages = min(int(r.json().get("pages", 1)), max_pages)
    except Exception:
        pages = 1
    urls = []
    for p in range(pages):
        r = await _get(c, api, {**params, "page": p}, timeout=180, tries=4)
        if r:
            # Pages sometimes arrive truncated: pull URLs out line by line and skip broken ones.
            urls += URL_RE.findall(r.text)
    return urls


def slugs_from_urls(urls) -> dict[str, set]:
    """Company boards referenced by any job link we've seen (used by run.py too)."""
    out = {k: set() for k in PATTERNS}
    hosts = {"jobs.ashbyhq.com": "ashby", "boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse",
             "jobs.lever.co": "lever", "apply.workable.com": "workable"}
    for u in urls:
        try:
            h = (urlparse(u).hostname or "").lower()
        except Exception:
            continue
        ats = hosts.get(h) or ("recruitee" if h.endswith(".recruitee.com") else None)
        if ats:
            s = slug_of(ats, u)
            if s:
                out[ats].add(s)
    return out


async def main(max_pages: int = 40):
    path = DATA / "companies.json"
    companies = json.loads(path.read_text()) if path.exists() else {}
    before = {k: len(v) for k, v in companies.items()}
    found = {k: set(companies.get(k, [])) for k in PATTERNS}

    # 1. Boards already referenced by jobs we've scraped (always works).
    jobs_path = DATA / "jobs.json"
    if jobs_path.exists():
        for k, v in slugs_from_urls(u for j in json.loads(jobs_path.read_text()) for u in j.get("links", [])).items():
            found[k] |= v

    # 2. Common Crawl (big, but often overloaded).
    cc_ok = False
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as c:
        for api in await crawl_ids(c):
            for ats, pats in PATTERNS.items():
                for pat, match in pats:
                    try:
                        urls = await query(c, api, pat, match, max_pages)
                    except Exception as e:  # never lose the whole run to one bad response
                        log.warning("query %s failed: %s", pat, e)
                        urls = None
                    if urls is None:
                        continue
                    cc_ok = True
                    for url in urls:
                        s = slug_of(ats, url)
                        if s:
                            found[ats].add(s)
                # Save progress after each ATS so a later failure keeps what we found.
                path.write_text(json.dumps({k: sorted(v, key=str.lower) for k, v in found.items()}, indent=1))
                log.info("  %s: %d boards so far", ats, len(found[ats]))
            if cc_ok and sum(len(v) for v in found.values()) > 2000:
                break  # one good crawl is plenty

    for ats in PATTERNS:
        companies[ats] = sorted(found[ats], key=str.lower)
        log.info("discover %-10s %d -> %d boards", ats, before.get(ats, 0), len(companies[ats]))
    path.write_text(json.dumps(companies, indent=1))
    if not cc_ok:
        log.error("Common Crawl index was unavailable for every query; only boards from scraped job links were added. Re-run later.")
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
