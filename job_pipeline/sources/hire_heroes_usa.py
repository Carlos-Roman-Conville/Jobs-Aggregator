"""Hire Heroes USA Job Board ingest via public RSS feed.

Hire Heroes USA (https://www.hireheroesusa.org) is a veteran-focused nonprofit
that operates a job board open to all veterans, including reservists.
The board exposes a public RSS feed at /jobs.rss with the latest postings.

Field mapping (RSS item -> posting):
  - <title>        -> "Job Title at Company"  -> parsed into title + company
  - <link>         -> job_url + apply_url     (canonical Hire Heroes job page)
  - <guid>         -> external_id (stable per posting)
  - <description>  -> description_text (HTML stripped)
  - <pubDate>      -> kept in raw_payload
  - <category>     -> location hint if present, else falls back to body parse

Filtering:
  - Optional keyword filter (case-insensitive substring match against title +
    description). Empty keyword = no filter.

Pagination: the RSS feed returns the latest ~50 postings; not pageable.
The `limit` parameter caps how many we ingest per run.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple

import requests

from job_pipeline.db import upsert_posting
from job_pipeline.normalize import normalize_posting_fields


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Pattern: "Senior Software Engineer at Acme Corp"
_TITLE_AT_COMPANY_RE = re.compile(r"^(.+?)\s+at\s+(.+?)$", re.IGNORECASE)


def _plain(s: str, max_len: int = 12000) -> str:
    if not s:
        return ""
    t = html.unescape(s)
    t = _TAG_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t[:max_len]


def _parse_title_company(raw_title: str) -> Tuple[str, str]:
    """Split 'Role at Company' into ('Role', 'Company'). Falls back to
    (raw, '(Hire Heroes listing)') if pattern doesn't match."""
    s = (raw_title or "").strip()
    m = _TITLE_AT_COMPANY_RE.match(s)
    if m:
        role = m.group(1).strip()
        company = m.group(2).strip()
        if role and company:
            return role, company
    return s, "(Hire Heroes listing)"


def ingest_hire_heroes_usa(
    *,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
    keyword: str = "",
    limit: int = 200,
) -> None:
    """Pull the Hire Heroes USA jobs RSS feed and upsert each posting."""
    url = "https://jobs.hireheroesusa.org/jobs.rss"
    try:
        r = session.get(url, timeout=60)
        r.raise_for_status()
    except Exception as e:
        errors.append(f"hire_heroes_usa:{e}")
        return

    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        errors.append(f"hire_heroes_usa:xml_parse:{e}")
        return

    channel = root.find("channel")
    items = (channel.findall("item") if channel is not None else []) or []

    kw_l = (keyword or "").strip().lower()
    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)
    max_n = max(1, int(limit))
    n = 0

    for it in items:
        if n >= max_n:
            break

        raw_title = _plain((it.findtext("title") or ""))
        guid = (it.findtext("guid") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc_html = it.findtext("description") or ""
        desc = _plain(desc_html, max_len=12000)
        pub = (it.findtext("pubDate") or "").strip()

        # category may carry location; multiple categories possible
        cats = [
            (c.text or "").strip() for c in it.findall("category") if (c.text or "").strip()
        ]
        loc_guess = ""
        for c in cats:
            cl = c.lower()
            if any(tok in cl for tok in (",", "remote", "united states", "usa", " - ")):
                loc_guess = c
                break

        title, company = _parse_title_company(raw_title)

        # keyword filter against title + description
        if kw_l:
            hay = (title + " " + desc).lower()
            if kw_l not in hay:
                continue

        # validation
        if len(title) < min_title or len(desc) < min_desc:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            continue
        if not link.startswith("http"):
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            continue

        ext = guid or link

        fld = normalize_posting_fields(company, title, loc_guess, "", desc)
        _, _, reused, dedup_reason = upsert_posting(
            source="hire_heroes_usa",
            external_id=f"hhu:{ext}"[:420],
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=link,
            job_url=link,
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text="",
            raw_payload={
                "guid": guid,
                "link": link,
                "pubDate": pub,
                "categories": cats,
                "raw_title": raw_title,
            },
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["hire_heroes_usa_jobs_touched"] = (
                stats.get("hire_heroes_usa_jobs_touched", 0) + 1
            )
        n += 1
