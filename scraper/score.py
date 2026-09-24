"""Fit score (0-100) for a job against the owner's profile.

The profile (skills, location, preferences) is NOT stored in this public repo.
It lives in the Flowhunt board's private database (meta/profile) and is passed
in as a dict. Scoring is deterministic so every run ranks jobs the same way.

  role fit        0-45  how close the title is to a Webflow design/build role
  can I apply     0-35  can someone in the profile's country actually take it
  level           0-10  seniority matches experience
  freshness+      0-10  recent, salary shown, stack overlap
"""
from __future__ import annotations

import datetime as dt
import re

WF = re.compile(r"web\s?flow", re.I)

ROLE_CORE = re.compile(r"developer|engineer|designer|specialist|expert|builder|build|web|site|front|cms", re.I)
ROLE_WEB = re.compile(
    r"web ?(site)? ?(designer|developer|engineer|producer|manager)|website|design engineer|front[\s-]?end|"
    r"creative (developer|technologist)|no[\s-]?code|low[\s-]?code|framer|landing page|marketing (designer|developer|engineer|design technologist)|"
    r"digital designer|ui (designer|developer)|design technologist|web & |web and", re.I)
ROLE_DESIGN = re.compile(r"product designer|ux|ui\b|brand designer|visual designer|graphic designer|creative|designer", re.I)
ROLE_ADJACENT = re.compile(r"seo|cro|conversion|growth|marketing|content|digital experience|web ops|analytics|demand gen", re.I)

SENIOR_TOO_HIGH = re.compile(r"\b(staff|principal|director|head of|vp|vice president|chief)\b", re.I)
JUNIOR = re.compile(r"\b(intern|internship|junior|jr\.?|entry|graduate|trainee|werkstudent)\b", re.I)

ANYWHERE = re.compile(r"worldwide|anywhere|global|international|any location|emea|(?<!south )africa", re.I)
US_ONLY = re.compile(r"\b(us|usa|u\.s\.|united states|americas?|north america|canada|latam|latin america|mexico|brazil|argentina|colombia|[A-Z][a-z]+, [A-Z]{2}\b)", re.I)
EUROPE = re.compile(r"europe|\beu\b|european union|uk\b|united kingdom|england|london|germany|netherlands|france|spain|ireland|portugal|poland|belgium|austria|serbia|czech|sweden|denmark|italy", re.I)
ASIA_ETC = re.compile(r"south africa|india|philippines|pakistan|bangladesh|indonesia|vietnam|singapore|malaysia|australia|new zealand|uae|united arab emirates|dubai|costa rica", re.I)


def _days(posted: str) -> int:
    try:
        return (dt.date.today() - dt.date.fromisoformat(posted[:10])).days
    except Exception:
        return 99


def score(job: dict, profile: dict) -> tuple[int, str]:
    title = job.get("title", "")
    snippet = job.get("snippet", "") or ""
    loc = f"{job.get('location', '')} {job.get('workplace', '')}"
    typ = (job.get("type") or "").lower()
    home = [h.lower() for h in profile.get("home", ["nigeria", "lagos"])]
    home_re = re.compile(r"\b(" + "|".join(map(re.escape, home)) + r")\b", re.I)
    stack = [s.lower() for s in profile.get("stack", [])]
    why = []

    # 1. Role fit
    wf_title, wf_text = bool(WF.search(title)), bool(WF.search(snippet)) or "webflow.jobs" in job.get("sources", []) or "flowremote" in job.get("sources", []) or "flowroles" in job.get("sources", [])
    if wf_title and ROLE_CORE.search(title):
        role, why_role = 45, "Webflow build/design role"
    elif wf_title:
        role, why_role = 38, "Webflow-titled role"
    elif ROLE_WEB.search(title):
        role, why_role = (37, "web design/dev role using Webflow") if wf_text else (30, "web design/dev role")
    elif ROLE_DESIGN.search(title):
        role, why_role = (24, "design role that uses Webflow") if wf_text else (17, "design role, Webflow minor")
    elif ROLE_ADJACENT.search(title):
        role, why_role = (14, "marketing/SEO role that touches Webflow") if wf_text else (8, "marketing role, Webflow minor")
    else:
        role, why_role = 5, "role outside your lane"
    why.append(why_role)

    # 2. Can I apply from home?
    l = loc.lower()
    contract = any(k in typ for k in ("contract", "freelance"))
    if home_re.search(l):
        reach, why_reach = 35, "based in your country"
    elif ANYWHERE.search(l):
        reach, why_reach = 35, "open worldwide"
    elif job.get("workplace") == "remote":
        if US_ONLY.search(job.get("location", "")):
            reach, why_reach = 10, "remote but US/Americas-restricted"
        elif EUROPE.search(l):
            reach, why_reach = 15, "remote but Europe/UK-restricted"
        elif ASIA_ETC.search(l):
            reach, why_reach = 6, "remote for another country"
        else:
            reach, why_reach = 28, "remote"
        if contract and reach < 28:
            reach, why_reach = reach + 8, why_reach + " (contract, sometimes flexible)"
    elif job.get("workplace") in ("onsite", "hybrid") or re.search(r",", job.get("location", "")):
        reach, why_reach = 2, "on-site/hybrid abroad"
    else:
        reach, why_reach = 16, "location not stated"
    why.append(why_reach)

    # 3. Level
    if SENIOR_TOO_HIGH.search(title):
        level = 3
        why.append("more senior than your 3 yrs")
    elif JUNIOR.search(title):
        level = 5
    else:
        level = 9

    # 4. Freshness, pay, stack
    d = _days(job.get("posted", ""))
    extra = 6 if d <= 3 else 4 if d <= 7 else 2 if d <= 14 else 0
    if job.get("salary"):
        extra += 2
    hits = [s for s in stack if s and s in (title + " " + snippet).lower()]
    extra += min(2, len(hits))
    extra = min(10, extra)

    total = max(0, min(100, role + reach + level + extra))
    return total, " · ".join(why)
