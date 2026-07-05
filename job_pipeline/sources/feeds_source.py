"""Thin JSON-first job feeds (RemoteOK, Arbeitnow, Remotive, The Muse, Jobicy, Working Nomads, WWR RSS)."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

import requests

from job_pipeline.db import upsert_posting
from job_pipeline.normalize import normalize_posting_fields


def _plain(x: Any) -> str:
    return html.unescape(str(x or "")).strip()


def _strip_html(s: str, max_len: int = 12000) -> str:
    if not s:
        return ""
    t = html.unescape(s)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_len]


def ingest_remoteok(
    *,
    slug: str,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
    limit: int = 80,
) -> None:
    url = "https://remoteok.com/api"
    try:
        r = session.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        errors.append(f"remoteok:{e}")
        return
    if not isinstance(data, list):
        errors.append("remoteok:unexpected_json_shape")
        return

    slug_l = slug.lower().strip()
    max_n = max(1, limit)
    n = 0
    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)

    for row in data:
        if not isinstance(row, dict):
            continue
        if row.get("id") == 0:
            continue
        pos = _plain(row.get("position") or row.get("title"))
        if slug_l and slug_l not in pos.lower():
            continue
        company = _plain(row.get("company"))
        desc = _plain(row.get("description"))
        loc = ""
        tags = row.get("tags") or []
        if isinstance(tags, list):
            loc = ", ".join(_plain(t) for t in tags[:6])
        job_url = _plain(row.get("url"))
        if job_url and not job_url.startswith("http"):
            job_url = "https://remoteok.com" + job_url
        ext = _plain(row.get("id") or row.get("date") or job_url)
        if not job_url.startswith("http") or len(pos) < min_title or len(desc) < min_desc:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            continue
        fld = normalize_posting_fields(company, pos, loc, "", desc[:12000])
        _, _, reused, dedup_reason = upsert_posting(
            source="remoteok",
            external_id=f"rok:{ext}"[:420],
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=job_url,
            job_url=job_url,
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text="",
            raw_payload=row,
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["remoteok_jobs_touched"] = stats.get("remoteok_jobs_touched", 0) + 1
        n += 1
        if n >= max_n:
            break


def ingest_arbeitnow(
    *,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
    keyword: str = "",
    limit: int = 60,
) -> None:
    url = "https://arbeitnow.com/api/job-board-api"
    try:
        r = session.get(url, timeout=60)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        errors.append(f"arbeitnow:{e}")
        return
    rows = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        errors.append("arbeitnow:unexpected_json_shape")
        return

    kw = keyword.lower().strip()
    max_n = max(1, limit)
    got = 0
    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)

    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _plain(row.get("title"))
        if kw and kw not in title.lower():
            continue
        company = _plain(row.get("company_name") or row.get("company"))
        desc = _plain(row.get("description"))
        loc = _plain(row.get("location"))
        job_url = _plain(row.get("url") or row.get("apply_url"))
        ext = _plain(row.get("slug") or row.get("id") or job_url)
        if not job_url.startswith("http") or len(title) < min_title or len(desc) < min_desc:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            continue
        fld = normalize_posting_fields(company, title, loc, "", desc[:12000])
        _, _, reused, dedup_reason = upsert_posting(
            source="arbeitnow",
            external_id=f"an:{ext}"[:420],
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=job_url,
            job_url=job_url,
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text="",
            raw_payload=row,
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["arbeitnow_jobs_touched"] = stats.get("arbeitnow_jobs_touched", 0) + 1
        got += 1
        if got >= max_n:
            break


def ingest_remotive(
    *,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
    category: str = "",
    search: str = "",
    limit: int = 80,
) -> None:
    """Remotive public API: https://remotive.com/api/remote-jobs"""
    params: Dict[str, Any] = {}
    if category:
        params["category"] = category
    if search:
        params["search"] = search
    if limit:
        params["limit"] = max(1, int(limit))
    try:
        r = session.get("https://remotive.com/api/remote-jobs", params=params, timeout=60)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        errors.append(f"remotive:{e}")
        return
    rows = payload.get("jobs") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        errors.append("remotive:unexpected_json_shape")
        return

    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)

    for row in rows[: max(1, limit)]:
        if not isinstance(row, dict):
            continue
        title = _plain(row.get("title"))
        company = _plain(row.get("company_name"))
        desc = _strip_html(_plain(row.get("description")))
        loc = _plain(row.get("candidate_required_location") or "Remote")
        salary = _plain(row.get("salary"))
        job_url = _plain(row.get("url"))
        ext = _plain(row.get("id") or job_url)
        if not job_url.startswith("http") or len(title) < min_title or len(desc) < min_desc:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            continue
        fld = normalize_posting_fields(company, title, loc, salary, desc)
        _, _, reused, dedup_reason = upsert_posting(
            source="remotive",
            external_id=f"rmtv:{ext}"[:420],
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=job_url,
            job_url=job_url,
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text=fld["salary_text"],
            raw_payload=row,
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["remotive_jobs_touched"] = stats.get("remotive_jobs_touched", 0) + 1


def ingest_themuse(
    *,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
    categories: List[str] | None = None,
    levels: List[str] | None = None,
    location: str = "",
    max_pages: int = 2,
    per_page_cap: int = 60,
) -> None:
    """The Muse public jobs API: https://www.themuse.com/api/public/jobs"""
    cats = [c for c in (categories or ["Computer and IT"]) if c]
    lvls = [l for l in (levels or []) if l]

    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)

    for pg in range(1, max(1, int(max_pages)) + 1):
        params: List[tuple] = [("page", pg), ("descending", "true")]
        for c in cats:
            params.append(("category", c))
        for l in lvls:
            params.append(("level", l))
        if location:
            params.append(("location", location))
        try:
            r = session.get(
                "https://www.themuse.com/api/public/jobs",
                params=params,
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            errors.append(f"themuse:page_{pg}:{e}")
            return

        results = data.get("results") if isinstance(data, dict) else []
        if not isinstance(results, list) or not results:
            return
        n_this_page = 0
        for row in results:
            if not isinstance(row, dict):
                continue
            title = _plain(row.get("name"))
            company_obj = row.get("company") if isinstance(row.get("company"), dict) else {}
            company = _plain(company_obj.get("name"))
            desc = _strip_html(_plain(row.get("contents")))
            locs = row.get("locations") if isinstance(row.get("locations"), list) else []
            loc = _plain(locs[0].get("name")) if locs and isinstance(locs[0], dict) else ""
            refs = row.get("refs") if isinstance(row.get("refs"), dict) else {}
            job_url = _plain(refs.get("landing_page"))
            ext = _plain(row.get("id") or row.get("short_name") or job_url)
            if not job_url.startswith("http") or len(title) < min_title or len(desc) < min_desc:
                stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
                continue
            fld = normalize_posting_fields(company, title, loc, "", desc)
            _, _, reused, dedup_reason = upsert_posting(
                source="themuse",
                external_id=f"muse:{ext}"[:420],
                company_name=fld["company_name"],
                title=fld["title"],
                apply_url=job_url,
                job_url=job_url,
                location=fld["location"],
                description_text=fld["description_text"],
                salary_text="",
                raw_payload=row,
                dedupe_by_normalized_url=dedupe_by_normalized_url,
            )
            if reused:
                if dedup_reason == "company_title":
                    stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
                else:
                    stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
            else:
                stats["themuse_jobs_touched"] = stats.get("themuse_jobs_touched", 0) + 1
            n_this_page += 1
            if n_this_page >= per_page_cap:
                break


def ingest_jobicy(
    *,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
    geo: str = "usa",
    industry: str = "",
    tag: str = "",
    count: int = 50,
) -> None:
    """Jobicy public API: https://jobicy.com/api/v2/remote-jobs"""
    params: Dict[str, Any] = {"count": max(1, min(100, int(count)))}
    if geo:
        params["geo"] = geo
    if industry:
        params["industry"] = industry
    if tag:
        params["tag"] = tag
    try:
        r = session.get("https://jobicy.com/api/v2/remote-jobs", params=params, timeout=60)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        errors.append(f"jobicy:{e}")
        return
    rows = payload.get("jobs") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        errors.append("jobicy:unexpected_json_shape")
        return

    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)

    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _plain(row.get("jobTitle"))
        company = _plain(row.get("companyName"))
        desc = _strip_html(_plain(row.get("jobDescription") or row.get("jobExcerpt")))
        loc = _plain(row.get("jobGeo") or "Remote")
        job_url = _plain(row.get("url"))
        ext = _plain(row.get("id") or job_url)
        if not job_url.startswith("http") or len(title) < min_title or len(desc) < min_desc:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            continue
        fld = normalize_posting_fields(company, title, loc, "", desc)
        _, _, reused, dedup_reason = upsert_posting(
            source="jobicy",
            external_id=f"jcy:{ext}"[:420],
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=job_url,
            job_url=job_url,
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text="",
            raw_payload=row,
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["jobicy_jobs_touched"] = stats.get("jobicy_jobs_touched", 0) + 1


def ingest_working_nomads(
    *,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
    category_filter: str = "",
    keyword: str = "",
    limit: int = 100,
) -> None:
    """Working Nomads public listing: https://www.workingnomads.com/api/exposed_jobs/"""
    try:
        r = session.get("https://www.workingnomads.com/api/exposed_jobs/", timeout=60)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        errors.append(f"working_nomads:{e}")
        return
    if not isinstance(rows, list):
        errors.append("working_nomads:unexpected_json_shape")
        return

    cat_l = (category_filter or "").lower().strip()
    kw_l = (keyword or "").lower().strip()
    max_n = max(1, int(limit))

    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)

    n = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _plain(row.get("title"))
        if kw_l and kw_l not in title.lower():
            continue
        category = _plain(row.get("category_name"))
        if cat_l and cat_l not in category.lower():
            continue
        company = _plain(row.get("company_name"))
        desc = _strip_html(_plain(row.get("description")))
        loc = _plain(row.get("location") or "Remote")
        job_url = _plain(row.get("url"))
        ext = _plain(row.get("id") or job_url)
        if not job_url.startswith("http") or len(title) < min_title or len(desc) < min_desc:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            continue
        fld = normalize_posting_fields(company, title, loc, "", desc)
        _, _, reused, dedup_reason = upsert_posting(
            source="working_nomads",
            external_id=f"wn:{ext}"[:420],
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=job_url,
            job_url=job_url,
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text="",
            raw_payload=row,
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["working_nomads_jobs_touched"] = stats.get("working_nomads_jobs_touched", 0) + 1
        n += 1
        if n >= max_n:
            break


def ingest_weworkremotely_rss(
    *,
    session: requests.Session,
    ingest_settings: Dict[str, Any],
    dedupe_by_normalized_url: bool,
    stats: Dict[str, int],
    errors: List[str],
    categories: List[str] | None = None,
    keyword: str = "",
    limit_per_feed: int = 60,
) -> None:
    """We Work Remotely category RSS feeds.

    Categories are URL slugs. Common ones:
      - remote-customer-support-jobs
      - remote-devops-sysadmin-jobs
      - remote-back-end-programming-jobs
      - remote-front-end-programming-jobs
      - remote-full-stack-programming-jobs
      - remote-product-jobs
    """
    cats = [c for c in (categories or ["remote-customer-support-jobs", "remote-devops-sysadmin-jobs"]) if c]
    kw_l = (keyword or "").lower().strip()
    max_n = max(1, int(limit_per_feed))

    min_desc = int(ingest_settings.get("min_description_length") or 35)
    min_title = int(ingest_settings.get("min_title_length") or 2)

    for slug in cats:
        feed_url = f"https://weworkremotely.com/categories/{slug}.rss"
        try:
            r = session.get(feed_url, timeout=60)
            r.raise_for_status()
            xml_text = r.text
        except Exception as e:
            errors.append(f"wwr:{slug}:{e}")
            continue
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            errors.append(f"wwr:{slug}:xml_parse:{e}")
            continue
        items = root.findall(".//item")[:max_n]
        for it in items:
            title_raw = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            desc_raw = (it.findtext("description") or "").strip()
            region = (it.findtext("region") or "").strip()
            company = ""
            title = title_raw
            if ":" in title_raw:
                company, _, title = title_raw.partition(":")
                company = company.strip()
                title = title.strip()
            if kw_l and kw_l not in title.lower() and kw_l not in desc_raw.lower():
                continue
            desc = _strip_html(desc_raw)
            loc = region or "Remote"
            ext = link
            if not link.startswith("http") or len(title) < min_title or len(desc) < min_desc:
                stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
                continue
            fld = normalize_posting_fields(company, title, loc, "", desc)
            _, _, reused, dedup_reason = upsert_posting(
                source=f"wwr_rss:{slug}",
                external_id=f"wwr:{ext}"[:420],
                company_name=fld["company_name"],
                title=fld["title"],
                apply_url=link,
                job_url=link,
                location=fld["location"],
                description_text=fld["description_text"],
                salary_text="",
                raw_payload={"slug": slug, "title_raw": title_raw, "region": region},
                dedupe_by_normalized_url=dedupe_by_normalized_url,
            )
            if reused:
                if dedup_reason == "company_title":
                    stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
                else:
                    stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
            else:
                stats["wwr_jobs_touched"] = stats.get("wwr_jobs_touched", 0) + 1
