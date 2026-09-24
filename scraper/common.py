"""Shared helpers: job normalisation, ids, Webflow relevance, HTTP client."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
from typing import Any, Iterable

import httpx

UA = "Mozilla/5.0 (compatible; FlowhuntBot/1.0; personal job alerts)"

# Titles that plausibly involve building or running websites.
WEBISH = re.compile(
    r"web|site|front[\s-]?end|no[\s-]?code|low[\s-]?code|landing|\bcro\b|conversion|"
    r"digital (?:design|experience|marketing)|marketing (?:design|engineer|developer|ops)|"
    r"brand design|visual design|ui\b|ux\b|product design|creative|design engineer|growth|"
    r"content|seo|cms|framer",
    re.I,
)
WEBFLOW = re.compile(r"web\s?flow", re.I)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


def is_relevant(title: str, description: str | None, company: str = "") -> bool:
    """Title says Webflow, or description does and the role is web-ish.

    Jobs *at* Webflow Inc mention Webflow everywhere, so for them we also
    require a web-ish title (keeps design/dev roles, drops sales, finance...).
    """
    title = title or ""
    if WEBFLOW.search(title):
        return True
    if not WEBISH.search(title):
        return False
    if company and company.strip().lower() == "webflow":
        return True
    return bool(description and WEBFLOW.search(description))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)|- remote|remote", "", s.lower()))


def _cnorm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().split("|")[0].split("—")[0].replace("inc", ""))


def job_id(company: str, title: str) -> str:
    """Must match the id used by the Flowhunt board (dedupe key)."""
    return hashlib.sha1((_cnorm(company) + "|" + _norm(title)).encode()).hexdigest()[:12]


def workplace_of(location: str, remote_flag: Any = None, hint: str = "") -> str:
    loc = f"{location} {hint}".lower()
    if remote_flag is True or any(k in loc for k in ("remote", "anywhere", "worldwide", "work from home")):
        return "remote"
    if "hybrid" in loc:
        return "hybrid"
    if "on-site" in loc or "onsite" in loc or "in office" in loc or remote_flag is False:
        return "onsite"
    return "unknown"


def to_date(value: Any) -> str:
    """Accepts ISO strings, epoch seconds/ms, date objects; returns YYYY-MM-DD."""
    if value is None or value == "":
        return today()
    try:
        if isinstance(value, (int, float)):
            v = value / 1000 if value > 1e11 else value
            return dt.datetime.fromtimestamp(v, dt.timezone.utc).strftime("%Y-%m-%d")
        if isinstance(value, (dt.date, dt.datetime)):
            return value.strftime("%Y-%m-%d")
        s = str(value)
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        if m:
            return m.group(1)
    except Exception:
        pass
    return today()


def fmt_salary(lo: Any, hi: Any, currency: str | None = "USD", interval: str | None = "") -> str:
    def f(x):
        try:
            x = float(x)
        except (TypeError, ValueError):
            return None
        if x != x:  # NaN
            return None
        return f"{x/1000:.0f}k" if x >= 1000 else f"{x:.0f}"

    a, b = f(lo), f(hi)
    if not a and not b:
        return ""
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "CAD": "CA$", "AUD": "A$"}.get((currency or "").upper(), (currency or "") + " ")
    per = {"yearly": "/yr", "year": "/yr", "monthly": "/mo", "month": "/mo", "hourly": "/hr", "hour": "/hr", "weekly": "/wk", "daily": "/day"}.get(
        (interval or "").lower(), ""
    )
    rng = f"{a}–{b}" if a and b and a != b else (a or b)
    return f"{sym}{rng}{per}"


def make_job(
    *,
    title: str,
    company: str,
    url: str,
    source: str,
    location: str = "",
    remote: Any = None,
    type_: str = "",
    posted: Any = None,
    salary: str = "",
    description: str = "",
) -> dict:
    title = re.sub(r"\s+", " ", (title or "").strip())
    company = re.sub(r"\s+", " ", (company or "").strip()) or "Unknown"
    location = re.sub(r"\s+", " ", (location or "").strip())
    return {
        "id": job_id(company, title),
        "title": title,
        "company": company,
        "location": location or "—",
        "workplace": workplace_of(location, remote, type_),
        "type": normalise_type(type_),
        "posted": to_date(posted),
        "salary": salary or "",
        "url": url,
        "links": [url],
        "sources": [source],
        "snippet": snippet(description),
    }


def normalise_type(t: str | None) -> str:
    t = (t or "").lower().replace("_", " ")
    if not t:
        return "Full-time"
    if "intern" in t:
        return "Internship"
    if "freelance" in t:
        return "Freelance"
    if "contract" in t or "temp" in t:
        return "Contract"
    if "part" in t:
        return "Part-time"
    return "Full-time"


def snippet(desc: str | None, n: int = 280) -> str:
    """A short excerpt around the first Webflow mention (useful for scoring later)."""
    text = re.sub(r"\s+", " ", strip_html(desc)).strip()
    if not text:
        return ""
    m = WEBFLOW.search(text)
    start = max(0, (m.start() - n // 2) if m else 0)
    return text[start : start + n]


def merge(jobs: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for j in jobs:
        cur = out.get(j["id"])
        if not cur:
            out[j["id"]] = j
            continue
        for u in j["links"]:
            if u not in cur["links"]:
                cur["links"].append(u)
        for s in j["sources"]:
            if s not in cur["sources"]:
                cur["sources"].append(s)
        if not cur.get("salary") and j.get("salary"):
            cur["salary"] = j["salary"]
        if cur["workplace"] == "unknown" and j["workplace"] != "unknown":
            cur["workplace"] = j["workplace"]
        if cur["location"] == "—" and j["location"] != "—":
            cur["location"] = j["location"]
        if not cur.get("snippet") and j.get("snippet"):
            cur["snippet"] = j["snippet"]
        # Prefer a direct company/ATS link over an aggregator as the main URL.
        if _is_aggregator(cur["url"]) and not _is_aggregator(j["url"]):
            cur["url"] = j["url"]
        cur["posted"] = min(cur["posted"], j["posted"])
    return out


AGGREGATORS = ("linkedin.", "indeed.", "glassdoor.", "ziprecruiter.", "google.", "himalayas.", "jobicy.", "remotive.", "remoteok.")


def _is_aggregator(url: str) -> bool:
    return any(a in url for a in AGGREGATORS)


def client(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept": "application/json, text/html;q=0.9"},
        timeout=timeout,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
    )
