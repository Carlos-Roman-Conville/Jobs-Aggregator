"""Freshness + location re-verification against the LIVE posting.

Why this exists
---------------
The pipeline scores a job at ingest/summarize time using the STORED
description, which is frequently incomplete:

  * it misses the real work-mode ("In-House Operations / On-Site, Stockton CA"
    lived on the Lever page, not in the scraped blurb), and
  * it misses eligibility limits ("open to candidates based outside of the
    United States"), and
  * it never re-checks whether the posting is even still live.

So a job can sit in ``pending_review`` looking applyable while actually being
expired, onsite, or non-US-only. `backfill_location_classify` re-runs the
location policy but only on the same stale stored text, so it can't catch any
of this. This module RE-FETCHES the live posting and closes the job when there
is POSITIVE evidence it is dead or location-blocked.

Conservative by construction
----------------------------
We only ever close on positive evidence. If the page can't be fetched (e.g.
Indeed returns HTTP 403 to bots) or renders client-side to an empty shell, the
verdict is ``undetermined`` and the item is LEFT in the queue with a flag — we
never close a job we couldn't actually read.

Measured source behaviour (2026-06-21):
  * Lever / Greenhouse / Workday / RemoteOK / WeWorkRemotely → fetchable (200),
    onsite + non-US markers detected reliably.
  * Indeed (indeed.com) → HTTP 403 to non-browser clients; cannot verify over
    HTTP. Indeed jobs are flagged ``http_blocked`` and must be checked in the
    browser at apply time.

Result is recorded on ``summary_json.freshness`` for every checked row (audit +
idempotency); dead/blocked rows are additionally moved to ``status='closed'``
with a human-readable ``user_notes`` reason. Nothing is deleted.

Usage:
    python -m job_pipeline.freshness_check --category operations --dry-run
    python -m job_pipeline.freshness_check --category operations --limit 60
    python -m job_pipeline.freshness_check --item-id 10704
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(override=True)
except ImportError:
    pass

import requests

from job_pipeline.db import pg_connect, update_item_status
from job_pipeline.location_policy import evaluate_location_policy

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Hosts known to hard-block non-browser clients (HTTP 403). We don't even bother
# scoring their tiny error body — flagged http_blocked, left in queue.
_BOT_BLOCKED_HOSTS = ("indeed.com", "ziprecruiter.com", "glassdoor.com", "linkedin.com")

# Positive "this posting is gone" evidence in the rendered page text.
_EXPIRY_MARKERS = (
    "this job has expired",
    "job has expired",
    "posting has expired",
    "no longer accepting applications",
    "no longer accepting application",
    "we are no longer accepting",
    "applications are now closed",
    "application period has closed",
    "this job is no longer available",
    "this position is no longer available",
    "this opportunity is no longer available",
    "this job is no longer active",
    "this job is no longer open",
    "this posting is no longer",
    "this position has been filled",
    "this role has been filled",
    "the position has been filled",
    "job is no longer accepting",
    "the job you're looking for is no longer",
    "the job you are looking for is no longer",
    "this requisition is closed",
)

# "outside the United States" near eligibility/restriction framing → non-US-only.
_OUTSIDE_US_RE = re.compile(
    r"outside (?:of )?the (?:united states|u\.?s\.?a?\b|us\b)", re.IGNORECASE
)
_ELIGIBILITY_NEAR_RE = re.compile(
    r"candidat|applicant|\byou\b|\byou'?re\b|based|located|reside|residen|"
    r"\bonly\b|must be|open to|this role|this position|work\s*from|employ",
    re.IGNORECASE,
)
# Affirmative "US-based" framing that should override a stray outside-US mention.
_US_REQUIRED_RE = re.compile(
    r"must (?:be|reside|live)[^.]{0,40}united states|"
    r"(?:authorized|eligible) to work in the (?:united states|u\.?s)|"
    r"u\.?s\.?\s*(?:based|residents?|citizens?)\s*(?:only|required)|"
    r"based in the united states",
    re.IGNORECASE,
)


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)/?", url or "", re.IGNORECASE)
    return (m.group(1) if m else "").lower()


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(html)).strip()


def _is_non_us_only(text_low: str) -> bool:
    if _US_REQUIRED_RE.search(text_low):
        return False
    for m in _OUTSIDE_US_RE.finditer(text_low):
        a, b = max(0, m.start() - 90), min(len(text_low), m.end() + 50)
        if _ELIGIBILITY_NEAR_RE.search(text_low[a:b]):
            return True
    return False


def fetch_live(url: str, session: requests.Session, timeout: int = 15) -> Tuple[int, str]:
    """GET the posting. Returns (status_code, plain_text). status_code 0 on error."""
    try:
        r = session.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        return r.status_code, _html_to_text(r.text or "")
    except Exception:
        return 0, ""


def assess(
    title: str,
    location: str,
    fresh_text: str,
    status_code: int,
    url: str,
    cfg: Dict[str, Any],
) -> Tuple[str, str]:
    """Classify a freshly-fetched posting.

    Returns (verdict, reason) where verdict is one of:
      dead | blocked_location | blocked_nonus | live_ok | undetermined
    Only ``dead`` / ``blocked_*`` should trigger a close.
    """
    host = _host(url)

    # Hard 404/410 = gone, regardless of source.
    if status_code in (404, 410):
        return "dead", f"http_{status_code}"

    # Bot-blocked or no body → cannot verify; never close.
    if status_code in (401, 403, 429) or any(h in host for h in _BOT_BLOCKED_HOSTS):
        return "undetermined", "http_blocked"
    if status_code == 0:
        return "undetermined", "fetch_error"
    if len(fresh_text) < 400:
        # Client-rendered shell or near-empty page; not enough to judge.
        return "undetermined", "thin_body"

    text_low = fresh_text.lower()

    # Expired / filled markers.
    for mk in _EXPIRY_MARKERS:
        if mk in text_low:
            return "dead", "expired_marker"

    # Non-US-only eligibility.
    if _is_non_us_only(text_low):
        return "blocked_nonus", "non_us_only"

    # Location policy on the FRESH full text. Only a hard reject on a definite
    # onsite/hybrid classification counts — never act on 'unknown' (could be a
    # partial fetch).
    action, _mult, cls, code = evaluate_location_policy(title, location, fresh_text, cfg)
    if action == "reject" and cls in ("onsite", "hybrid"):
        return "blocked_location", code or f"{cls}_outside_metro"

    return "live_ok", ""


def _load_cfg() -> Dict[str, Any]:
    try:
        from job_pipeline.states import load_merged_config

        return load_merged_config()
    except Exception:
        try:
            with open("job_pipeline_config.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def _fetch_pending(
    limit: Optional[int], category: Optional[str], item_id: Optional[int], recheck_days: int
) -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            where = ["i.status = 'pending_review'"]
            params: List[Any] = []
            if item_id is not None:
                where = ["i.id = %s"]
                params = [int(item_id)]
            else:
                if category:
                    where.append("i.category = %s")
                    params.append(category)
                # Skip rows already freshness-checked within recheck window.
                where.append(
                    "COALESCE((i.summary_json->'freshness'->>'checked_at'), '') "
                    "< to_char(NOW() - (%s || ' days')::interval, 'YYYY-MM-DD')"
                )
                params.append(int(recheck_days))
            sql = (
                "SELECT i.id, i.summary_json, p.title, p.location, p.apply_url, p.job_url "
                "FROM job_pipeline_items i JOIN job_postings p ON p.id = i.posting_id "
                "WHERE " + " AND ".join(where) + " ORDER BY i.list_rank DESC NULLS LAST"
            )
            if limit:
                sql += " LIMIT %s"
                params.append(int(limit))
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "summary_json": r[1],
                "title": r[2] or "",
                "location": r[3] or "",
                "url": (r[4] or r[5] or "").strip(),
            }
            for r in rows
        ]
    finally:
        conn.close()


def _write_freshness(item_id: int, info: Dict[str, Any]) -> None:
    """Record the freshness result on summary_json (non-destructive merge)."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE job_pipeline_items "
                "SET summary_json = jsonb_set("
                "      COALESCE(summary_json, '{}'::jsonb), '{freshness}', %s::jsonb, true), "
                "    updated_at = NOW() "
                "WHERE id = %s",
                (json.dumps(info, ensure_ascii=False), int(item_id)),
            )
        conn.commit()
    finally:
        conn.close()


def verify_item(
    item_id: int,
    session: requests.Session,
    cfg: Dict[str, Any],
    *,
    today: str,
    dry_run: bool = False,
    row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if row is None:
        got = _fetch_pending(None, None, item_id, 0)
        if not got:
            return {"id": item_id, "verdict": "missing", "reason": "no_row"}
        row = got[0]
    url = row["url"]
    if not url or not url.lower().startswith("http"):
        return {"id": item_id, "verdict": "undetermined", "reason": "no_url"}

    status_code, text = fetch_live(url, session)
    verdict, reason = assess(row["title"], row["location"], text, status_code, url, cfg)

    info = {
        "checked_at": today,
        "verdict": verdict,
        "reason": reason,
        "http": status_code,
        "host": _host(url),
    }
    result = {"id": item_id, "title": row["title"][:50], "verdict": verdict, "reason": reason}

    if not dry_run:
        _write_freshness(item_id, info)
        if verdict in ("dead", "blocked_location", "blocked_nonus"):
            note = f"auto-closed [{today}] freshness: {verdict} ({reason})"
            update_item_status(item_id, "closed", note)
    return result


def verify_pending(
    *,
    limit: Optional[int] = None,
    category: Optional[str] = None,
    item_id: Optional[int] = None,
    recheck_days: int = 5,
    dry_run: bool = False,
    polite_sleep: float = 0.4,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify a batch of pending_review items against their live postings.

    ``today`` must be supplied by the caller (the workflow runtime forbids
    Date.now); CLI passes the system date.
    """
    if today is None:
        from datetime import date

        today = date.today().isoformat()
    cfg = _load_cfg()
    rows = _fetch_pending(limit, category, item_id, recheck_days)
    counts: Dict[str, int] = {}
    closed: List[Dict[str, Any]] = []
    with requests.Session() as session:
        for i, row in enumerate(rows):
            res = verify_item(
                row["id"], session, cfg, today=today, dry_run=dry_run, row=row
            )
            v = res["verdict"]
            counts[v] = counts.get(v, 0) + 1
            if v in ("dead", "blocked_location", "blocked_nonus"):
                closed.append(res)
            if polite_sleep and i + 1 < len(rows):
                time.sleep(polite_sleep)
    return {
        "checked": len(rows),
        "counts": counts,
        "closed": closed,
        "dry_run": dry_run,
    }


def main(argv: Optional[List[str]] = None) -> int:
    from datetime import date

    ap = argparse.ArgumentParser(description="Freshness + location re-verification")
    ap.add_argument("--category", default=None, help="lane category filter (e.g. operations)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--item-id", type=int, default=None)
    ap.add_argument("--recheck-days", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    out = verify_pending(
        limit=args.limit,
        category=args.category,
        item_id=args.item_id,
        recheck_days=args.recheck_days,
        dry_run=args.dry_run,
        today=date.today().isoformat(),
    )
    print(
        f"{'DRY-RUN: ' if out['dry_run'] else ''}checked {out['checked']} | "
        f"counts={out['counts']}",
        file=sys.stderr,
    )
    for c in out["closed"]:
        print(f"  CLOSE {c['id']}: {c['verdict']} ({c['reason']})  {c['title']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
