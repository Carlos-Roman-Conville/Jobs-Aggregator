"""JobSpy multi-board scraper → upsert_posting."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from job_pipeline.db import upsert_posting
from job_pipeline.normalize import normalize_posting_fields
from job_pipeline.search_preferences import search_term_seeds

logger = logging.getLogger(__name__)


def _df_to_rows(df) -> List[Dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    return df.to_dict("records")


def _num(v) -> Any:
    """Coerce a jobspy amount cell to int, treating NaN/blank as None."""
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f <= 0:  # NaN or non-positive
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def _salary_from_jobspy_row(row: Dict[str, Any]) -> str:
    """JobSpy returns pay as separate ``min_amount`` / ``max_amount`` /
    ``interval`` columns, NOT a ``salary`` string. The ingest was only reading a
    nonexistent ``salary`` key, so every JobSpy posting stored blank pay. Build a
    human salary string from the real columns (e.g. '55000 - 60000 yearly')."""
    mn, mx = _num(row.get("min_amount")), _num(row.get("max_amount"))
    if mn is None and mx is None:
        return ""
    interval = str(row.get("interval") or "").strip()
    if mn and mx:
        body = f"{mn} - {mx}"
    else:
        body = str(mn or mx)
    return f"{body} {interval}".strip()


def run_jobspy_ingest(
    cfg: Dict[str, Any],
    *,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
) -> None:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.error(
            "jobspy: python-jobspy not installed — run `pip install python-jobspy`; skipping JobSpy ingest."
        )
        errors.append("jobspy:pip install python-jobspy (package missing)")
        return

    site_names = cfg.get("site_name") or cfg.get("site_names") or ["indeed", "glassdoor", "google"]
    if isinstance(site_names, str):
        site_names = [s.strip() for s in site_names.split(",") if s.strip()]

    search_term_fallback = str(cfg.get("search_term") or cfg.get("title") or "it support").strip()
    use_seeds = bool(cfg.get("use_search_preferences_seeds", False))
    if use_seeds:
        search_terms = search_term_seeds()
        if not search_terms:
            logger.warning(
                "jobspy: use_search_preferences_seeds enabled but seed list empty; using %r",
                search_term_fallback,
            )
            search_terms = [search_term_fallback]
    else:
        search_terms = [search_term_fallback]

    location = str(cfg.get("location") or "United States").strip()
    results_wanted = max(1, int(cfg.get("results_wanted") or cfg.get("limit") or 40))
    hours_old = int(cfg.get("hours_old") or 720)
    country_indeed = str(cfg.get("country_indeed") or "usa")

    try:
        kwargs: Dict[str, Any] = {
            "site_name": site_names,
            "location": location,
            "results_wanted": results_wanted,
            "hours_old": hours_old,
        }
        gs = str(cfg.get("google_search_term") or "").strip()
        if gs:
            kwargs["google_search_term"] = gs
        if country_indeed:
            kwargs["country_indeed"] = country_indeed
        rows_accum: List[Dict[str, Any]] = []
        for sterm in search_terms:
            kwargs["search_term"] = sterm
            try:
                df = scrape_jobs(**kwargs)
            except Exception as e:
                errors.append(f"jobspy:{sterm}:{e}")
                continue
            rows_accum.extend(_df_to_rows(df))
    except Exception as e:
        errors.append(f"jobspy:{e}")
        return

    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)

    for row in rows_accum:
        company = str(row.get("company") or row.get("company_name") or "").strip()
        title = str(row.get("title") or row.get("job_title") or "").strip()
        loc = str(row.get("location") or row.get("job_location") or "").strip()
        desc = str(row.get("description") or row.get("job_description") or "").strip()
        salary = str(row.get("salary") or row.get("salary_text") or "").strip()
        if not salary:
            salary = _salary_from_jobspy_row(row)
        job_url = str(
            row.get("job_url") or row.get("url") or row.get("link") or row.get("apply_link") or ""
        ).strip()
        site = str(row.get("site") or row.get("source") or "jobspy").strip().lower() or "jobspy"
        ext = str(row.get("id") or row.get("job_id") or "") or job_url[:120]
        if not job_url or len(title) < min_title or len(desc) < min_desc:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            continue
        fld = normalize_posting_fields(company, title, loc, salary, desc)
        _, _, reused, dedup_reason = upsert_posting(
            source=f"jobspy_{site}",
            external_id=f"{site}:{ext}"[:500],
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=job_url,
            job_url=job_url,
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text=fld["salary_text"],
            raw_payload=row if isinstance(row, dict) else {},
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["jobspy_jobs_touched"] = stats.get("jobspy_jobs_touched", 0) + 1
