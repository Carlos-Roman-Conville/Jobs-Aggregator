"""USAJobs Search API → upsert_posting (requires API key + email in env)."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import requests

from job_pipeline.db import upsert_posting
from job_pipeline.normalize import normalize_posting_fields
from job_pipeline.search_preferences import search_term_seeds

logger = logging.getLogger(__name__)


def usajobs_credentials() -> tuple[str, str]:
    key = (os.getenv("USAJOBS_AUTHORIZATION_KEY") or os.getenv("USAJOBS_API_KEY") or "").strip()
    ua = (os.getenv("USAJOBS_USER_AGENT") or "").strip()
    # API requires contact email in User-Agent
    if not ua:
        ua = (
            os.getenv("USAJOBS_REQUEST_EMAIL")
            or os.getenv("JOB_PIPELINE_OWNER_EMAIL")
            or "you@example.com"
        )
    return key, ua


def run_usajobs_ingest(
    cfg: Dict[str, Any],
    *,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
) -> None:
    key, ua = usajobs_credentials()
    if not key:
        errors.append("usajobs:missing_USAJOBS_AUTHORIZATION_KEY")
        return

    keyword_fallback = str(cfg.get("keyword") or "IT Specialist").strip()
    use_seeds = bool(cfg.get("use_search_preferences_seeds", False))
    if use_seeds:
        keywords = search_term_seeds()
        if not keywords:
            logger.warning(
                "usajobs: use_search_preferences_seeds enabled but seed list empty; using %r",
                keyword_fallback,
            )
            keywords = [keyword_fallback]
    else:
        keywords = [keyword_fallback]

    location_name = str(cfg.get("location_name") or "United States").strip()
    per_page = max(1, min(500, int(cfg.get("results_per_page") or 25)))
    pages = max(1, int(cfg.get("max_pages") or 1))

    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)

    hdrs = {
        "Host": "data.usajobs.gov",
        "User-Agent": ua,
        "Authorization-Key": key,
    }
    base = "https://data.usajobs.gov/api/search"

    for keyword in keywords:
        for pg in range(1, pages + 1):
            try:
                resp = session.get(
                    base,
                    headers=hdrs,
                    params={
                        "Keyword": keyword,
                        "LocationName": location_name,
                        "ResultsPerPage": per_page,
                        "Page": pg,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                errors.append(f"usajobs:{keyword}:page_{pg}:{e}")
                break

            arr = (
                (data.get("SearchResult") or {}).get("SearchResultItems")
                if isinstance(data.get("SearchResult"), dict)
                else []
            )
            if not arr:
                break
            for item in arr:
                if not isinstance(item, dict):
                    continue
                desc_obj = (
                    item.get("MatchedObjectDescriptor")
                    if isinstance(item.get("MatchedObjectDescriptor"), dict)
                    else {}
                )
                title = str(desc_obj.get("PositionTitle") or "").strip()
                company = str(desc_obj.get("OrganizationName") or "US Federal").strip()
                locs = (
                    desc_obj.get("PositionLocationDisplay")
                    if isinstance(desc_obj.get("PositionLocationDisplay"), list)
                    else []
                )
                loc = ""
                if locs:
                    loc = (
                        str(locs[0].get("LocationName") or "")
                        if isinstance(locs[0], dict)
                        else str(locs[0])
                    )
                usr = desc_obj.get("UserArea") if isinstance(desc_obj.get("UserArea"), dict) else {}
                det = usr.get("Details") if isinstance(usr.get("Details"), dict) else {}
                desc_plain = str(det.get("JobSummary") or desc_obj.get("QualificationSummary") or "")

                apply_uri = ""
                refs = desc_obj.get("ApplyURI") if isinstance(desc_obj.get("ApplyURI"), list) else []
                if refs:
                    apply_uri = str(refs[0])
                urls = desc_obj.get("PositionURI") if isinstance(desc_obj.get("PositionURI"), str) else ""
                job_url = apply_uri or urls or ""

                ext = str(desc_obj.get("PositionID") or item.get("MatchedObjectId") or "")
                if not ext or len(title) < min_title:
                    stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
                    continue
                if len(desc_plain) < min_desc:
                    desc_plain = (
                        desc_plain
                        + " "
                        + str(desc_obj.get("QualificationSummary") or "")
                        + str(desc_obj.get("UserArea") or "")
                    ).strip()

                fld = normalize_posting_fields(company, title, loc, "", desc_plain[:12000])
                skip_url = ingest_settings.get("skip_without_http_url", True)
                if skip_url and not (job_url or "").startswith("http"):
                    stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
                    continue

                _, _, reused, dedup_reason = upsert_posting(
                    source="usajobs",
                    external_id=ext[:400],
                    company_name=fld["company_name"],
                    title=fld["title"],
                    apply_url=job_url or fld["company_name"],
                    job_url=job_url or "",
                    location=fld["location"],
                    description_text=fld["description_text"],
                    salary_text="",
                    raw_payload=item,
                    dedupe_by_normalized_url=dedupe_by_normalized_url,
                )
                if reused:
                    if dedup_reason == "company_title":
                        stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
                    else:
                        stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
                else:
                    stats["usajobs_jobs_touched"] = stats.get("usajobs_jobs_touched", 0) + 1
