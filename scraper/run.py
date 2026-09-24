"""Flowhunt scraper entry point.

  python -m scraper.run --mode hot    # every 4h: big boards + remote boards + ATS boards known to hire Webflow people
  python -m scraper.run --mode full   # daily: also sweeps every ATS board in data/companies.json

Writes data/jobs.json (all live Webflow jobs seen in the last 30 days) and
data/meta.json. The Flowhunt board ingests jobs.json on its own schedule.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import time
from collections import Counter
from pathlib import Path

from . import sources_ats, sources_boards, sources_jobspy
from .common import client, merge, now_iso

log = logging.getLogger("run")
DATA = Path(__file__).resolve().parent.parent / "data"
KEEP_DAYS = 30        # drop jobs not seen for this long
MAX_POST_AGE = 60     # ignore postings older than this


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1))


async def gather(mode: str, skip_jobspy: bool):
    companies = load("companies.json", {})
    hot = load("hot.json", {})
    ats_set = companies if mode == "full" else hot
    # Seeds are always polled, so a fresh repo produces results on its first run.
    seeds = load("seed_companies.json", {})
    for k, v in seeds.items():
        ats_set = {**ats_set, k: sorted(set(ats_set.get(k, [])) | set(v))}

    async with client() as c:
        t = time.time()
        ats_task = sources_ats.run(ats_set, c)
        boards_task = sources_boards.run(c)
        jobspy_task = asyncio.to_thread(sources_jobspy.run) if not skip_jobspy else asyncio.sleep(0, result=[])
        (ats_jobs, hits), board_jobs, jobspy_jobs = await asyncio.gather(ats_task, boards_task, jobspy_task)
        log.info("gathered in %.0fs: ats=%d boards=%d jobspy=%d", time.time() - t, len(ats_jobs), len(board_jobs), len(jobspy_jobs))

    # Remember boards that had Webflow jobs so the 4-hourly run keeps polling them.
    for k, v in hits.items():
        hot[k] = sorted(set(hot.get(k, [])) | set(v), key=str.lower)
    # Any ATS board linked from a Webflow job (e.g. a LinkedIn job pointing at Greenhouse)
    # becomes a known company and a hot board, so coverage grows every run.
    from .discover import slugs_from_urls
    everything = ats_jobs + board_jobs + jobspy_jobs
    linked = slugs_from_urls(u for j in everything for u in j["links"])
    for k, v in linked.items():
        hot[k] = sorted(set(hot.get(k, [])) | v, key=str.lower)
        companies[k] = sorted(set(companies.get(k, [])) | v, key=str.lower)
    save("hot.json", hot)
    save("companies.json", companies)
    return everything


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["hot", "full"], default="hot")
    ap.add_argument("--skip-jobspy", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    DATA.mkdir(exist_ok=True)

    fresh = merge(asyncio.run(gather(args.mode, args.skip_jobspy)))
    cutoff_post = (dt.date.today() - dt.timedelta(days=MAX_POST_AGE)).isoformat()
    fresh = {k: v for k, v in fresh.items() if v["posted"] >= cutoff_post and v["url"]}

    prev = {j["id"]: j for j in load("jobs.json", [])}
    now = now_iso()
    new_ids = []
    for jid, j in fresh.items():
        old = prev.get(jid)
        if old:
            j["first_seen"] = old.get("first_seen", now)
            j["links"] = old["links"] + [u for u in j["links"] if u not in old["links"]]
            j["sources"] = old["sources"] + [s for s in j["sources"] if s not in old["sources"]]
            j["posted"] = min(old["posted"], j["posted"])
        else:
            j["first_seen"] = now
            new_ids.append(jid)
        j["last_seen"] = now
        prev[jid] = j

    cutoff_seen = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    jobs = [j for j in prev.values() if j.get("last_seen", now) >= cutoff_seen[:19]]
    jobs.sort(key=lambda j: (j["posted"], j["first_seen"]), reverse=True)
    save("jobs.json", jobs)

    src = Counter(s for j in jobs for s in j["sources"])
    save("meta.json", {
        "last_run": now, "mode": args.mode, "total": len(jobs), "new_this_run": len(new_ids),
        "seen_this_run": len(fresh), "sources": dict(src.most_common()),
        "boards": {k: len(v) for k, v in load("companies.json", {}).items()},
        "hot_boards": {k: len(v) for k, v in load("hot.json", {}).items()},
    })
    log.info("done: %d live jobs, %d new, %d seen this run", len(jobs), len(new_ids), len(fresh))


if __name__ == "__main__":
    main()
