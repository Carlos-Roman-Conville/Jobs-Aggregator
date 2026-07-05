"""
Apify Scholarship Scraper Actor ingestion source.

Calls the community-maintained `majestic_fund/the-scholarship-scraper-actor`
which scrapes Scholarships.com, Fastweb, and College Board. ~$0.35 per 1,000
results. Reuses Carlos's existing APIFY_TOKEN env var.

Actor docs: https://apify.com/majestic_fund/the-scholarship-scraper-actor

Usage:
    from scholarship_pipeline.sources.apify_scholarship_actor import run_ingestion
    n_new, n_updated = run_ingestion(
        education_level="Undergraduate",
        field_of_study="Cybersecurity",
        min_award_amount=500,
        country="USA",
        max_results=200,
    )
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import urllib.parse
import urllib.request

from scholarship_pipeline.db import upsert_posting

APIFY_BASE = "https://api.apify.com/v2"
APIFY_ACTOR_ID = "majestic_fund~the-scholarship-scraper-actor"
DEFAULT_TIMEOUT_SEC = 600


def _apify_token() -> str:
    tok = (os.environ.get("APIFY_TOKEN") or "").strip()
    if not tok:
        raise RuntimeError(
            "APIFY_TOKEN env var not set. Add it to .env or export it for this session."
        )
    return tok


def _http_post_json(url: str, body: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, timeout: int = 60) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _trigger_run(input_payload: Dict[str, Any]) -> str:
    token = _apify_token()
    url = f"{APIFY_BASE}/acts/{APIFY_ACTOR_ID}/runs?token={urllib.parse.quote(token)}"
    body = {"input": input_payload}
    resp = _http_post_json(url, body, timeout=60)
    return str(resp["data"]["id"])


def _wait_for_run(run_id: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> Dict[str, Any]:
    token = _apify_token()
    url = f"{APIFY_BASE}/actor-runs/{run_id}?token={urllib.parse.quote(token)}"
    start = time.time()
    while time.time() - start < timeout_sec:
        resp = _http_get_json(url, timeout=30)
        data = resp["data"]
        status = data.get("status")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return data
        time.sleep(5)
    raise TimeoutError(f"Apify run {run_id} did not finish within {timeout_sec}s")


def _fetch_results(dataset_id: str, limit: int = 1000) -> List[Dict[str, Any]]:
    token = _apify_token()
    url = (
        f"{APIFY_BASE}/datasets/{dataset_id}/items"
        f"?token={urllib.parse.quote(token)}&clean=true&limit={int(limit)}"
    )
    return _http_get_json(url, timeout=60)


def _parse_deadline(raw: Optional[str]) -> Tuple[Optional[datetime], bool]:
    """Returns (datetime or None, rolling_deadline_flag)."""
    if not raw:
        return None, True
    raw = raw.strip()
    if not raw:
        return None, True
    rolling_signals = ("rolling", "varies", "ongoing", "open year", "any time")
    if any(s in raw.lower() for s in rolling_signals):
        return None, True
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc), False
        except ValueError:
            continue
    return None, False


def _parse_amount(raw: Any) -> Tuple[Optional[int], Optional[int]]:
    """Returns (min, max) in USD. Handles strings like '$1,000', '$1,000-$5,000', '$500'."""
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        v = int(raw)
        return v, v
    s = str(raw).replace("$", "").replace(",", "").strip().lower()
    if not s or s in ("varies", "unspecified", "n/a"):
        return None, None
    if "-" in s or "to" in s:
        parts = [p.strip() for p in s.replace("to", "-").split("-") if p.strip()]
        try:
            lo = int(float(parts[0]))
            hi = int(float(parts[1])) if len(parts) > 1 else lo
            return lo, hi
        except (ValueError, IndexError):
            return None, None
    try:
        v = int(float(s))
        return v, v
    except ValueError:
        return None, None


def run_ingestion(
    *,
    education_level: str = "Undergraduate",
    field_of_study: Optional[str] = None,
    min_award_amount: Optional[int] = None,
    country: str = "USA",
    max_results: int = 200,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> Tuple[int, int]:
    """Run the Apify scholarship actor with the given filters and upsert
    results. Returns (n_inserted_or_updated, n_skipped)."""
    input_payload: Dict[str, Any] = {
        "educationLevel": education_level,
        "country": country,
        "maxResults": int(max_results),
    }
    if field_of_study:
        input_payload["fieldOfStudy"] = field_of_study
    if min_award_amount:
        input_payload["minAwardAmount"] = int(min_award_amount)

    run_id = _trigger_run(input_payload)
    run = _wait_for_run(run_id, timeout_sec=timeout_sec)
    if run.get("status") != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run_id} ended with status={run.get('status')}")

    dataset_id = run["defaultDatasetId"]
    items = _fetch_results(dataset_id, limit=max_results)

    n_loaded = 0
    n_skipped = 0
    for item in items:
        try:
            title = (item.get("title") or "").strip()
            if not title:
                n_skipped += 1
                continue
            external_id = (
                item.get("id")
                or item.get("scholarshipId")
                or item.get("url")
                or title
            )
            apply_url = item.get("url") or item.get("applyUrl") or item.get("link")
            deadline_at, rolling = _parse_deadline(item.get("deadline"))
            amt_min, amt_max = _parse_amount(item.get("amount") or item.get("award"))
            upsert_posting(
                source="apify",
                external_id=str(external_id)[:255],
                title=title[:500],
                provider=(item.get("provider") or item.get("sponsor") or "")[:500] or None,
                description_text=item.get("description"),
                apply_url=apply_url,
                award_amount_min=amt_min,
                award_amount_max=amt_max,
                deadline_at=deadline_at,
                rolling_deadline=rolling,
                degree_level=(item.get("educationLevel") or education_level)[:50],
                field_of_study=(item.get("fieldOfStudy") or field_of_study or "")[:255] or None,
                geographic_restriction=(item.get("location") or country)[:255],
                eligibility_criteria=item.get("eligibility"),
                essay_required=bool(item.get("essayRequired", False)),
                essay_prompt=item.get("essayPrompt"),
                raw_payload=item,
            )
            n_loaded += 1
        except Exception as e:
            n_skipped += 1
            print(f"[apify] skipped item due to error: {e}")
    return n_loaded, n_skipped
