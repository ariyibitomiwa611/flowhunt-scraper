"""Plan the Flowhunt board update for one refresh run. Deterministic: no network.

The refresh task (Claude, every 4h) does the fetching and database calls; this
script decides WHAT to write so the rules are code, not judgement:

  python -m scraper.ingest --existing existing/jobs --feed feed.json \
      [--extra extra.json] --profile profile.json --out plan

Inputs
  existing/  one JSON file per job doc already on the board (ArtifactData list out_dir)
  feed.json  data/jobs.json from this repo (the scraper's output)
  extra.json optional list of jobs found by the refresh task's own sweep, same shape
  profile    the owner's scoring profile (read from the board's meta/profile doc)

Outputs (in --out)
  new/<id>.json   full docs for jobs not yet on the board, already scored
  batches.json    list of ArtifactData batch write lists (<=50 each), op=set
  stale.json      {"at":..., "ids":[...]} jobs that have closed (for meta/stale)
  summary.json    counts for meta/status
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
from collections import Counter

from .common import merge
from .score import score

# Sources whose listings disappear when the job closes, so "not seen lately" means closed.
TRACKABLE = {"ashby", "greenhouse", "lever", "workable", "recruitee", "himalayas", "jobicy", "remotive", "remoteok", "arbeitnow", "workingnomads"}
MAX_POST_AGE = 60      # never add postings older than this
UNTRACKED_TTL = 35     # jobs we can't track are treated as closed after this many days
TRACKED_GRACE = 2      # tracked jobs unseen for this many days are closed

KEEP = ["id", "title", "company", "location", "workplace", "type", "posted", "salary", "url", "links", "sources", "snippet"]


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--existing", required=True)
    ap.add_argument("--feed", required=True)
    ap.add_argument("--extra")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    existing = {}
    for f in glob.glob(os.path.join(a.existing, "*.json")):
        d = json.load(open(f))
        d = d.get("data", d)
        existing[d["id"]] = d
    feed = json.load(open(a.feed)) if os.path.exists(a.feed) else []
    extra = json.load(open(a.extra)) if a.extra and os.path.exists(a.extra) else []
    profile = json.load(open(a.profile))
    profile = profile.get("data", profile)

    t = now()
    cutoff = (t.date() - dt.timedelta(days=MAX_POST_AGE)).isoformat()
    incoming = merge([{**j, "links": list(j.get("links") or [j["url"]]), "sources": list(j.get("sources") or [])} for j in feed + extra])
    feed_by_id = {j["id"]: j for j in feed}

    os.makedirs(os.path.join(a.out, "new"), exist_ok=True)
    writes = []
    for jid, j in incoming.items():
        if jid in existing or j.get("posted", "") < cutoff or not j.get("url"):
            continue
        doc = {k: j.get(k, "") for k in KEEP}
        s, why = score(doc, profile)
        doc.update(score=s, score_reason=why, found_at=iso(t), status="new")
        path = os.path.abspath(os.path.join(a.out, "new", f"{jid}.json"))
        json.dump(doc, open(path, "w"), ensure_ascii=False)
        writes.append({"op": "set", "collection": "jobs", "doc_id": jid, "file_path": path})
    json.dump([writes[i:i + 50] for i in range(0, len(writes), 50)], open(os.path.join(a.out, "batches.json"), "w"), indent=1)

    # Closed jobs: never touch saved/applied ones.
    stale = []
    for jid, j in existing.items():
        if j.get("status") in ("saved", "applied"):
            continue
        srcs = set(j.get("sources") or [])
        f = feed_by_id.get(jid)
        if srcs and srcs <= TRACKABLE:
            seen = (f or {}).get("last_seen")
            if not seen or (t - dt.datetime.fromisoformat(seen.replace("Z", "+00:00"))).days >= TRACKED_GRACE:
                stale.append(jid)
        else:
            try:
                age = (t.date() - dt.date.fromisoformat(j.get("posted", "")[:10])).days
            except ValueError:
                age = 0
            if age > UNTRACKED_TTL:
                stale.append(jid)
    json.dump({"at": iso(t), "ids": sorted(stale)}, open(os.path.join(a.out, "stale.json"), "w"))

    total = len(existing) + len(writes)
    src = Counter(s for j in list(existing.values()) + [incoming[w["doc_id"]] for w in writes] for s in j.get("sources", []))
    json.dump({"added": len(writes), "total": total, "stale": len(stale), "sources": dict(src.most_common()),
               "top_new": sorted(([json.load(open(w["file_path"]))["score"], incoming[w["doc_id"]]["title"]] for w in writes), reverse=True)[:5]},
              open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    print(f"new={len(writes)} batches={len(writes) and (len(writes) - 1) // 50 + 1} stale={len(stale)} total={total}")


if __name__ == "__main__":
    main()
