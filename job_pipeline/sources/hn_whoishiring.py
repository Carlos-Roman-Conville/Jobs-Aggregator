"""HackerNews 'Ask HN: Who is hiring?' monthly thread parser via Algolia.

Each top-level comment in the latest thread by user `whoishiring` is treated as a
job posting. Comment bodies are free-form text — we extract:
  - the first non-empty line as a candidate "title" (often "COMPANY | ROLE | LOC")
  - all http(s) URLs (the first one becomes apply_url)
  - the whole comment as description
Keyword filter narrows to relevant roles (e.g. "support", "sysadmin", "remote").
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List

import requests

from job_pipeline.db import upsert_posting
from job_pipeline.normalize import normalize_posting_fields


_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_REMOTE_RE = re.compile(r"\b(remote|wfh|work from home|fully remote)\b", re.IGNORECASE)


def _plain(s: str, max_len: int = 12000) -> str:
    if not s:
        return ""
    t = html.unescape(s)
    t = _TAG_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t[:max_len]


def _split_first_line(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    first = text.strip().split("\n", 1)[0].strip()
    if " | " in first:
        head = first.split(" | ", 1)[0].strip()
    elif " - " in first:
        head = first.split(" - ", 1)[0].strip()
    else:
        head = first
    return head[:120], first[:300]


def _find_latest_thread_id(session: requests.Session) -> tuple[str, str]:
    """Returns (thread_id, title)."""
    try:
        r = session.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": "Ask HN Who is hiring",
                "tags": "story,author_whoishiring",
                "hitsPerPage": 5,
            },
            timeout=45,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return "", ""
    hits = data.get("hits") or []
    for h in hits:
        title = str(h.get("title") or "")
        if "who is hiring" in title.lower() and "ask hn" in title.lower():
            return str(h.get("objectID") or ""), title
    return "", ""


def _fetch_comments(session: requests.Session, thread_id: str, max_n: int = 800) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    page = 0
    per = 500
    while len(out) < max_n and page < 4:
        try:
            r = session.get(
                "https://hn.algolia.com/api/v1/search",
                params={
                    "tags": f"comment,story_{thread_id}",
                    "hitsPerPage": per,
                    "page": page,
                },
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            break
        hits = data.get("hits") or []
        if not hits:
            break
        out.extend(hits)
        if len(hits) < per:
            break
        page += 1
    return out[:max_n]


def ingest_hn_whoishiring(
    *,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
    keyword: str = "",
    require_remote: bool = True,
    require_url: bool = True,
    limit: int = 200,
) -> None:
    thread_id, thread_title = _find_latest_thread_id(session)
    if not thread_id:
        errors.append("hn_whoishiring:no_thread_found")
        return
    comments = _fetch_comments(session, thread_id, max_n=max(50, int(limit) * 4))
    if not comments:
        errors.append(f"hn_whoishiring:no_comments_for_{thread_id}")
        return

    kw_l = (keyword or "").lower().strip()
    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)
    max_n = max(1, int(limit))
    n = 0

    for c in comments:
        if n >= max_n:
            break
        if not isinstance(c, dict):
            continue
        # Top-level only: parent_id should equal the thread id
        parent_id = str(c.get("parent_id") or c.get("story_id") or "")
        if c.get("parent_id") and str(c.get("parent_id")) != thread_id:
            continue
        body_html = c.get("comment_text") or ""
        body = _plain(body_html)
        if len(body) < min_desc:
            continue
        if kw_l and kw_l not in body.lower():
            continue
        if require_remote and not _REMOTE_RE.search(body):
            continue
        urls = _URL_RE.findall(body)
        # Strip trailing punctuation that often glues onto URLs in plaintext
        urls = [u.rstrip(".,);]") for u in urls]
        urls = [u for u in urls if u.startswith("http") and "news.ycombinator.com" not in u]
        apply_url = urls[0] if urls else ""
        if require_url and not apply_url:
            continue

        head, first_line = _split_first_line(body)
        company = head if head else "(HN listing)"
        title = first_line[:200] if len(first_line) > len(head) + 3 else f"{head} (HN listing)"
        if len(title) < min_title:
            title = head or "(HN listing)"

        # location: keep raw 'Remote' tag if present, else blank
        loc = "Remote" if _REMOTE_RE.search(body) else ""
        ext = str(c.get("objectID") or c.get("created_at") or apply_url)

        fld = normalize_posting_fields(company, title, loc, "", body)
        _, _, reused, dedup_reason = upsert_posting(
            source="hn_whoishiring",
            external_id=f"hn:{thread_id}:{ext}"[:420],
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=apply_url or "",
            job_url=apply_url or f"https://news.ycombinator.com/item?id={ext}",
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text="",
            raw_payload={"thread_id": thread_id, "thread_title": thread_title, "hn_object": c},
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["hn_whoishiring_jobs_touched"] = stats.get("hn_whoishiring_jobs_touched", 0) + 1
        n += 1
