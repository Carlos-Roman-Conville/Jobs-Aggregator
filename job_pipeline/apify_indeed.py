"""
Indeed discovery via Apify actor `valig/indeed-jobs-scraper`.

Requires an Apify token in the environment: `APIFY_TOKEN`, `APIFY_API_TOKEN`, `APIFY_KEY`, or `APIFY_API_KEY`.

When ``use_search_preferences_seeds`` is true under the Apify block, ``job_pipeline.ingest``
fans out multiple actor runs (one per expanded title × location); each request uses
``limit = max(20, configured_limit // len(search_term_seeds))`` so total Apify churn stays bounded.

Docs: https://apify.com/valig/indeed-jobs-scraper
"""
from __future__ import annotations

import html
import os
import re
from typing import Any, Dict, List, MutableMapping, Optional, Tuple

import requests

from job_pipeline.db import upsert_posting
from job_pipeline.normalize import normalize_posting_fields

APIFY_RUN_SYNC_TMPL = "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
DEFAULT_ACTOR_ID = "valig~indeed-jobs-scraper"


def apify_api_token() -> str:
    for key in (
        "APIFY_TOKEN",
        "APIFY_API_TOKEN",
        "APIFY_KEY",
        "APIFY_API_KEY",
    ):
        v = (os.getenv(key) or "").strip()
        if v:
            return v
    return ""


def _strip_html(s: str, max_len: int = 12000) -> str:
    if not s:
        return ""
    t = html.unescape(s)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_len]


def _format_location(loc: Any) -> str:
    if isinstance(loc, dict):
        parts = [
            loc.get("city"),
            loc.get("admin1Code"),
            loc.get("countryName") or loc.get("countryCode"),
        ]
        return ", ".join(str(p) for p in parts if p)
    return str(loc or "").strip()


def _format_salary(item: Dict[str, Any]) -> str:
    bs = item.get("baseSalary")
    if not isinstance(bs, dict):
        return ""
    parts: List[str] = []
    if bs.get("min") is not None:
        parts.append(str(bs["min"]))
    if bs.get("max") is not None:
        parts.append(str(bs["max"]))
    if not parts:
        return ""
    rng = " - ".join(parts)
    cur = str(bs.get("currencyCode") or "").strip()
    unit = str(bs.get("unitOfWork") or "").strip()
    suf = " ".join(x for x in (cur, unit) if x)
    return f"{rng} {suf}".strip()


def _indeed_item_to_fields(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map Apify Indeed record → upsert_posting kwargs (normalized)."""
    key = str(item.get("key") or "").strip()
    indeed_url = (item.get("url") or "").strip()
    apply_url = (item.get("jobUrl") or item.get("job_url") or "").strip() or indeed_url
    if not apply_url and not indeed_url:
        return None
    apply_u = apply_url or indeed_url
    indeed_u = indeed_url or apply_url

    employer = item.get("employer") if isinstance(item.get("employer"), dict) else {}
    company = str(employer.get("name") or "").strip()
    title = str(item.get("title") or "").strip()

    desc_obj = item.get("description")
    if isinstance(desc_obj, dict):
        desc = (desc_obj.get("text") or "").strip()
        if not desc and desc_obj.get("html"):
            desc = _strip_html(str(desc_obj.get("html") or ""))
    else:
        desc = str(desc_obj or "")[:12000]

    location = _format_location(item.get("location"))
    salary_text = _format_salary(item)

    ext = key if key else ""
    if not ext:
        import hashlib

        ext = hashlib.sha256(f"{apply_u}|{title}|{company}".encode("utf-8")).hexdigest()[:24]

    fld = normalize_posting_fields(company, title, location, salary_text, desc)
    return {
        "external_id": ext[:128],
        "company_name": fld["company_name"],
        "title": fld["title"],
        "apply_url": apply_u,
        "job_url": indeed_u,
        "location": fld["location"],
        "description_text": fld["description_text"],
        "salary_text": fld["salary_text"],
        "raw_payload": {"source": "indeed_apify", "indeed": item},
    }


def _validation_skip_reason(
    title: str,
    description_text: str,
    apply_url: str,
    settings: Dict[str, Any],
) -> Optional[str]:
    t = (title or "").strip()
    if len(t) < settings["min_title_length"] or t == "(no title)":
        return "title_too_short"
    d = (description_text or "").strip()
    if len(d) < settings["min_description_length"]:
        return "description_too_short"
    if settings["skip_without_http_url"]:
        u = (apply_url or "").strip().lower()
        if not u.startswith(("http://", "https://")):
            return "no_http_url"
    return None


def run_apify_indeed_actor(
    apify_cfg: Dict[str, Any],
    *,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: MutableMapping[str, int],
) -> None:
    """
    Query the Apify Indeed actor once for a single ``title`` + ``location`` pair.

    Ingest expands titles (from ``search_preferences`` seeds when nested
    ``use_search_preferences_seeds`` is true) and locations (``locations`` array).
    ``limit`` in the POST body is split across seed count when that flag is on.
    """
    token = apify_api_token()
    if not token:
        raise RuntimeError("missing Apify token: set APIFY_TOKEN or APIFY_API_TOKEN in .env")

    actor_id = str(apify_cfg.get("actor_id") or DEFAULT_ACTOR_ID).strip()
    actor_id = actor_id.replace("/", "~")

    title = str(apify_cfg.get("title") or "").strip()
    location = str(apify_cfg.get("location") or "").strip()
    if not title or not location:
        raise ValueError("indeed.apify.title and indeed.apify.location must be non-empty per run")

    full_limit = max(1, int(apify_cfg.get("limit") or 100))
    if bool(apify_cfg.get("use_search_preferences_seeds")):
        from job_pipeline.search_preferences import search_term_seeds

        seeds = search_term_seeds()
        n = max(1, len(seeds)) if seeds else 1
        per_seed = max(20, full_limit // n)
    else:
        per_seed = full_limit

    body: Dict[str, Any] = {
        "country": str(apify_cfg.get("country") or "us").strip().lower(),
        "title": title,
        "location": location,
        "limit": max(1, min(1000, per_seed)),
    }
    dp = apify_cfg.get("datePosted")
    if dp is not None and str(dp).strip() != "":
        body["datePosted"] = str(dp).strip()

    timeout_sec = max(60, min(300, int(apify_cfg.get("timeout_sec") or 300)))
    params: Dict[str, Any] = {
        "token": token,
        "timeout": timeout_sec,
        "format": "json",
        "clean": "true",
    }
    if apify_cfg.get("max_total_charge_usd") is not None:
        params["maxTotalChargeUsd"] = float(apify_cfg["max_total_charge_usd"])
    if apify_cfg.get("memory_mbytes") is not None:
        params["memory"] = int(apify_cfg["memory_mbytes"])

    url = APIFY_RUN_SYNC_TMPL.format(actor_id=actor_id)

    read_timeout = timeout_sec + 120
    r = requests.post(url, params=params, json=body, timeout=(30, read_timeout))
    if r.status_code == 401:
        raise RuntimeError("apify_auth_failed: check APIFY_TOKEN")
    if r.status_code == 404:
        raise RuntimeError(f"apify_actor_not_found: {actor_id}")
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:500]
        raise RuntimeError(f"apify_run_failed:{r.status_code}:{detail}")

    items = r.json()
    if not isinstance(items, list):
        raise RuntimeError("apify_unexpected_response_shape")

    for item in items:
        if not isinstance(item, dict):
            continue
        mapped = _indeed_item_to_fields(item)
        if not mapped:
            stats["indeed_skipped_bad_row"] = stats.get("indeed_skipped_bad_row", 0) + 1
            continue
        skip = _validation_skip_reason(
            mapped["title"],
            mapped["description_text"],
            mapped["apply_url"],
            ingest_settings,
        )
        if skip:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            stats[f"skip_{skip}"] = stats.get(f"skip_{skip}", 0) + 1
            continue
        _, _, reused, dedup_reason = upsert_posting(
            source="indeed",
            external_id=mapped["external_id"],
            company_name=mapped["company_name"],
            title=mapped["title"],
            apply_url=mapped["apply_url"],
            job_url=mapped["job_url"],
            location=mapped["location"],
            description_text=mapped["description_text"],
            salary_text=mapped["salary_text"],
            raw_payload=mapped["raw_payload"],
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["indeed_jobs_touched"] = stats.get("indeed_jobs_touched", 0) + 1
