import html
import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, MutableMapping, Optional, Tuple

IngestProgressFn = Callable[[float, str, Dict[str, int]], None]

import requests

from job_pipeline.db import upsert_posting
from job_pipeline.normalize import normalize_apply_url, normalize_posting_fields

logger = logging.getLogger(__name__)

CONFIG_ENV = "JOB_PIPELINE_CONFIG"
DEFAULT_CONFIG = "job_pipeline_config.json"


def _base_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_pipeline_config() -> Dict[str, Any]:
    name = os.getenv(CONFIG_ENV, DEFAULT_CONFIG)
    path = name if os.path.isabs(name) else os.path.join(_base_dir(), name)
    if not os.path.isfile(path):
        return _normalize_config(
            {
                "greenhouse_board_tokens": [],
                "lever_companies": [],
                "lever_max_postings": 60,
            }
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raw = {}
    return _normalize_config(raw)


def _normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Merge nested `sources.*` with legacy top-level keys (backward compatible)."""
    out: Dict[str, Any] = dict(raw)
    src = raw.get("sources") or {}
    gh = src.get("greenhouse") if isinstance(src.get("greenhouse"), dict) else {}
    lv = src.get("lever") if isinstance(src.get("lever"), dict) else {}

    tokens = out.get("greenhouse_board_tokens")
    if not tokens:
        out["greenhouse_board_tokens"] = [str(t).strip() for t in (gh.get("board_tokens") or []) if str(t).strip()]
    out["_greenhouse_enabled"] = bool(gh.get("enabled", True))

    levers = out.get("lever_companies")
    if not levers:
        out["lever_companies"] = [str(s).strip() for s in (lv.get("companies") or []) if str(s).strip()]
    out["_lever_enabled"] = bool(lv.get("enabled", False))

    if out.get("lever_max_postings") is None:
        out["lever_max_postings"] = int(lv.get("max_postings") or 60)

    ind = src.get("indeed") if isinstance(src.get("indeed"), dict) else {}
    out["_indeed_enabled"] = bool(ind.get("enabled", False))
    ap = ind.get("apify")
    out["_indeed_apify"] = ap if isinstance(ap, dict) else {}

    js = src.get("jobspy") if isinstance(src.get("jobspy"), dict) else {}
    out["_jobspy_enabled"] = bool(js.get("enabled", False))
    out["_jobspy_cfg"] = js

    uj = src.get("usajobs") if isinstance(src.get("usajobs"), dict) else {}
    out["_usajobs_enabled"] = bool(uj.get("enabled", False))
    out["_usajobs_cfg"] = uj

    feeds = src.get("feeds") if isinstance(src.get("feeds"), dict) else {}
    out["_feeds_cfg"] = feeds

    hn = src.get("hn_whoishiring") if isinstance(src.get("hn_whoishiring"), dict) else {}
    out["_hn_whoishiring_enabled"] = bool(hn.get("enabled", False))
    out["_hn_whoishiring_cfg"] = hn

    idl = src.get("idealist") if isinstance(src.get("idealist"), dict) else {}
    out["_idealist_enabled"] = bool(idl.get("enabled", False))
    out["_idealist_cfg"] = idl

    ngv = src.get("neogov") if isinstance(src.get("neogov"), dict) else {}
    out["_neogov_enabled"] = bool(ngv.get("enabled", False))
    out["_neogov_cfg"] = ngv

    hhu = src.get("hire_heroes_usa") if isinstance(src.get("hire_heroes_usa"), dict) else {}
    out["_hire_heroes_usa_enabled"] = bool(hhu.get("enabled", False))
    out["_hire_heroes_usa_cfg"] = hhu

    return out


def matching_thresholds(cfg: Dict[str, Any]) -> Dict[str, Any]:
    m = cfg.get("matching") if isinstance(cfg.get("matching"), dict) else {}
    f = cfg.get("filters") if isinstance(cfg.get("filters"), dict) else {}
    return {
        "auto_close_combined_below": float(m.get("auto_close_combined_below", 0.26)),
        "auto_close_pass_verdict_combined_below": float(
            m.get("auto_close_pass_verdict_combined_below", 0.48)
        ),
        "explain_scores": bool(m.get("explain_scores", True)),
        "min_salary_usd": max(0, int(f.get("min_salary_usd") or 0)),
        "salary_hard_gate": bool(m.get("salary_hard_gate", False)),
    }


def parse_salary_low_usd(text: str) -> Optional[int]:
    """Rough lower-bound USD from a salary string; None if not parseable."""
    if not (text or "").strip():
        return None
    s = text.lower().replace(",", "")
    m = re.search(r"(\d+)\s*k\b", s)
    if m:
        return int(m.group(1)) * 1000
    nums: List[int] = []
    for m in re.finditer(r"\b(\d{5,6})\b", s):
        nums.append(int(m.group(1)))
    if nums:
        return min(nums)
    return None


def salary_hard_gate(row: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[bool, str]:
    th = matching_thresholds(cfg)
    if not th["salary_hard_gate"] or th["min_salary_usd"] <= 0:
        return False, ""
    blob = " ".join(
        str(row.get(k) or "")
        for k in ("salary_text", "title", "description_text")
    )
    low = parse_salary_low_usd(str(row.get("salary_text") or "")) or parse_salary_low_usd(blob)
    if low is None:
        return False, ""
    if low < th["min_salary_usd"]:
        return True, f"salary_below_min_usd:{low}<{th['min_salary_usd']}"
    return False, ""


def ingest_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    ing = cfg.get("ingest") if isinstance(cfg.get("ingest"), dict) else {}
    return {
        "dedupe_normalized_url": bool(ing.get("dedupe_normalized_url", True)),
        "max_greenhouse_listings_per_board": max(1, int(ing.get("max_greenhouse_listings_per_board", 300))),
        "skip_without_http_url": bool(ing.get("skip_without_http_url", True)),
        "min_description_length": max(0, int(ing.get("min_description_length", 35))),
        "min_title_length": max(1, int(ing.get("min_title_length", 2))),
    }


def expand_search_terms_for_ingest(
    cfg_block: Dict[str, Any],
    fallback: str,
    *,
    label: str = "ingest",
) -> List[str]:
    """
    Expand one configured search token into many phrases when ``use_search_preferences_seeds`` is true.

    Falls back to the literal configured term when seeds are unavailable or the toggle is off.
    """
    fb = (fallback or "").strip()
    if not isinstance(cfg_block, dict) or not bool(cfg_block.get("use_search_preferences_seeds", False)):
        return [fb] if fb else [""]

    from job_pipeline.search_preferences import search_term_seeds

    seeds = search_term_seeds()
    if not seeds:
        logger.warning(
            "%s: use_search_preferences_seeds enabled but seed list empty; using fallback %r",
            label,
            fb or "(empty)",
        )
        return [fb] if fb else [""]
    return seeds


def strip_html_to_text(s: str, max_len: int = 12000) -> str:
    if not s:
        return ""
    t = html.unescape(s)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_len]


def _get(session: requests.Session, url: str) -> Any:
    r = session.get(url, timeout=45)
    r.raise_for_status()
    return r.json()


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
    # Hard ingest pre-screen: drop any posting whose title doesn't match
    # Carlos's narrow target list (Help Desk / Customer Support / Jr IT /
    # Desktop Support / Tier 1 NOC + Ops Manager backup). Cuts DB rows,
    # summarize LLM calls, and review time on guaranteed-not-a-fit jobs.
    # Best-effort: never block ingest if search_preferences fails to load.
    try:
        from job_pipeline.search_preferences import passes_target_title_filter
        if not passes_target_title_filter(t):
            return "title_off_target"
    except Exception:
        pass
    return None


def ingest_greenhouse_board(
    session: requests.Session,
    board_token: str,
    settings: Dict[str, Any],
    stats: MutableMapping[str, int],
    *,
    dedupe_by_normalized_url: bool,
    on_job_progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    base = f"https://boards-api.greenhouse.io/v1/boards/{board_token}"
    data = _get(session, f"{base}/jobs")
    jobs = data.get("jobs") or []
    cap = settings["max_greenhouse_listings_per_board"]
    jobs = jobs[:cap]
    job_ids = [j.get("id") for j in jobs if j.get("id") is not None]
    n_jobs = len(job_ids)
    for j_i, j in enumerate(jobs, start=1):
        jid = j.get("id")
        if jid is None:
            continue
        ext = str(jid)
        detail = _get(session, f"{base}/jobs/{ext}")
        title = (detail.get("title") or j.get("title") or "").strip()
        company = (detail.get("company_name") or j.get("company_name") or "").strip()
        loc = ""
        loc_obj = detail.get("location") or j.get("location")
        if isinstance(loc_obj, dict):
            loc = str(loc_obj.get("name") or "")
        content = detail.get("content") or ""
        desc = strip_html_to_text(str(content))
        url = (detail.get("absolute_url") or j.get("absolute_url") or "").strip()
        fld = normalize_posting_fields(company, title, loc, "", desc)
        skip = _validation_skip_reason(fld["title"], fld["description_text"], url, settings)
        if skip:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            stats[f"skip_{skip}"] = stats.get(f"skip_{skip}", 0) + 1
            continue
        _, _, reused, dedup_reason = upsert_posting(
            source="greenhouse",
            external_id=f"{board_token}:{ext}",
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=url,
            job_url=url,
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text="",
            raw_payload={"board": board_token, "job": detail},
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["greenhouse_jobs_touched"] = stats.get("greenhouse_jobs_touched", 0) + 1
        if on_job_progress is not None and n_jobs > 0:
            try:
                on_job_progress(j_i, n_jobs)
            except Exception:
                pass


def ingest_lever_company(
    session: requests.Session,
    site: str,
    max_n: int,
    settings: Dict[str, Any],
    stats: MutableMapping[str, int],
    *,
    dedupe_by_normalized_url: bool,
) -> None:
    data = _get(session, f"https://api.lever.co/v0/postings/{site}?mode=json")
    if not isinstance(data, list):
        return
    for posting in data[:max_n]:
        pid = posting.get("id")
        if not pid:
            continue
        title = (posting.get("text") or "").strip()
        desc_plain = (posting.get("descriptionPlain") or "")[:12000]
        hosted = (posting.get("hostedUrl") or "").strip()
        apply_u = (posting.get("applyUrl") or hosted).strip()
        loc = ""
        cats = posting.get("categories") or {}
        if isinstance(cats, dict):
            loc = (cats.get("location") or "") or ""
            if isinstance(loc, dict):
                loc = str(loc.get("name") or "")
            else:
                loc = str(loc)
        company = site
        fld = normalize_posting_fields(company, title, loc, "", desc_plain)
        skip = _validation_skip_reason(fld["title"], fld["description_text"], apply_u, settings)
        if skip:
            stats["skipped_validation"] = stats.get("skipped_validation", 0) + 1
            stats[f"skip_{skip}"] = stats.get(f"skip_{skip}", 0) + 1
            continue
        _, _, reused, dedup_reason = upsert_posting(
            source="lever",
            external_id=f"{site}:{pid}",
            company_name=fld["company_name"],
            title=fld["title"],
            apply_url=apply_u,
            job_url=hosted or apply_u,
            location=fld["location"],
            description_text=fld["description_text"],
            salary_text="",
            raw_payload={"site": site, "posting": posting},
            dedupe_by_normalized_url=dedupe_by_normalized_url,
        )
        if reused:
            if dedup_reason == "company_title":
                stats["dedup_by_company_title"] = stats.get("dedup_by_company_title", 0) + 1
            else:
                stats["skipped_duplicate_url"] = stats.get("skipped_duplicate_url", 0) + 1
        else:
            stats["lever_jobs_touched"] = stats.get("lever_jobs_touched", 0) + 1


def plan_ingest_steps(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Human-readable step labels for progress UI (matches ``run_ingest_all`` order)."""
    cfg = cfg or load_pipeline_config()
    steps: List[str] = []

    tokens = [str(t).strip() for t in (cfg.get("greenhouse_board_tokens") or []) if str(t).strip()]
    levers = [str(s).strip() for s in (cfg.get("lever_companies") or []) if str(s).strip()]
    if not cfg.get("_greenhouse_enabled", True):
        tokens = []
    if not cfg.get("_lever_enabled", False):
        levers = []
    for t in tokens:
        steps.append(f"greenhouse:{t}")
    for s in levers:
        steps.append(f"lever:{s}")

    if cfg.get("_usajobs_enabled"):
        steps.append("usajobs")

    feeds_cfg = cfg.get("_feeds_cfg") or {}
    if isinstance(feeds_cfg, dict):
        rok = feeds_cfg.get("remoteok") if isinstance(feeds_cfg.get("remoteok"), dict) else {}
        if bool(rok.get("enabled", False)):
            for term in expand_search_terms_for_ingest(
                rok, str(rok.get("title_slug_filter") or rok.get("slug") or ""), label="remoteok"
            ):
                steps.append(f"remoteok:{term or 'all'}")

        an = feeds_cfg.get("arbeitnow") if isinstance(feeds_cfg.get("arbeitnow"), dict) else {}
        if bool(an.get("enabled", False)):
            for kw in expand_search_terms_for_ingest(an, str(an.get("keyword") or ""), label="arbeitnow"):
                steps.append(f"arbeitnow:{kw or 'all'}")

        rmtv = feeds_cfg.get("remotive") if isinstance(feeds_cfg.get("remotive"), dict) else {}
        if bool(rmtv.get("enabled", False)):
            for search_kw in expand_search_terms_for_ingest(
                rmtv, str(rmtv.get("search") or ""), label="remotive"
            ):
                steps.append(f"remotive:{search_kw or 'all'}")

        muse = feeds_cfg.get("themuse") if isinstance(feeds_cfg.get("themuse"), dict) else {}
        if bool(muse.get("enabled", False)):
            steps.append("themuse")

        jcy = feeds_cfg.get("jobicy") if isinstance(feeds_cfg.get("jobicy"), dict) else {}
        if bool(jcy.get("enabled", False)):
            for tag_kw in expand_search_terms_for_ingest(
                jcy, str(jcy.get("tag") or ""), label="jobicy"
            ):
                steps.append(f"jobicy:{tag_kw or 'all'}")

        wn = feeds_cfg.get("working_nomads") if isinstance(feeds_cfg.get("working_nomads"), dict) else {}
        if bool(wn.get("enabled", False)):
            for kw in expand_search_terms_for_ingest(
                wn, str(wn.get("keyword") or ""), label="working_nomads"
            ):
                steps.append(f"working_nomads:{kw or 'all'}")

        wwr = feeds_cfg.get("wwr_rss") if isinstance(feeds_cfg.get("wwr_rss"), dict) else {}
        if bool(wwr.get("enabled", False)):
            for kw in expand_search_terms_for_ingest(
                wwr, str(wwr.get("keyword") or ""), label="wwr_rss"
            ):
                steps.append(f"wwr:{kw or 'all'}")

    if cfg.get("_hn_whoishiring_enabled"):
        hn_cfg = cfg.get("_hn_whoishiring_cfg") or {}
        if isinstance(hn_cfg, dict):
            for kw in expand_search_terms_for_ingest(
                hn_cfg, str(hn_cfg.get("keyword") or ""), label="hn_whoishiring"
            ):
                steps.append(f"hn:{kw or 'all'}")

    if cfg.get("_hire_heroes_usa_enabled"):
        hhu_cfg = cfg.get("_hire_heroes_usa_cfg") or {}
        if isinstance(hhu_cfg, dict):
            for kw in expand_search_terms_for_ingest(
                hhu_cfg, str(hhu_cfg.get("keyword") or ""), label="hire_heroes_usa"
            ):
                steps.append(f"hire_heroes:{kw or 'all'}")

    if cfg.get("_jobspy_enabled"):
        js = cfg.get("_jobspy_cfg") or {}
        if isinstance(js, dict):
            for sterm in expand_search_terms_for_ingest(
                js, str(js.get("search_term") or ""), label="jobspy"
            ):
                steps.append(f"jobspy:{sterm or 'all'}")

    if cfg.get("_indeed_enabled"):
        from job_pipeline.apify_indeed import apify_api_token

        if apify_api_token():
            ap_cfg = cfg.get("_indeed_apify") or {}
            ap_cfg_dict = ap_cfg if isinstance(ap_cfg, dict) else {}
            titles = expand_search_terms_for_ingest(
                ap_cfg_dict,
                str(ap_cfg_dict.get("title") or ""),
                label="indeed_apify",
            )
            locations_raw = ap_cfg_dict.get("locations")
            if isinstance(locations_raw, list) and locations_raw:
                location_list = [str(x).strip() for x in locations_raw if str(x).strip()]
            else:
                location_list = [str(ap_cfg_dict.get("location") or "").strip()]
            location_list = [loc for loc in location_list if loc]
            for title_q in titles:
                tstrip = str(title_q or "").strip()
                if not tstrip:
                    continue
                for loc in location_list:
                    steps.append(f"indeed:{tstrip}@{loc}")

    return steps


class _IngestProgressReporter:
    def __init__(
        self,
        steps: List[str],
        stats: MutableMapping[str, int],
        on_progress: Optional[IngestProgressFn],
    ) -> None:
        self._steps = steps
        self._total = max(1, len(steps))
        self._stats = stats
        self._on_progress = on_progress
        self._step = 0
        self._label = "starting"
        self._started = time.monotonic()

    def _touched_total(self) -> int:
        return sum(
            int(v)
            for k, v in self._stats.items()
            if k.endswith("_jobs_touched") and isinstance(v, int)
        )

    def _emit(self, fraction: float, label: str) -> None:
        if self._on_progress is None:
            return
        try:
            self._on_progress(
                min(1.0, max(0.0, fraction)),
                label,
                dict(self._stats),
            )
        except Exception:
            pass

    def begin_step(self, label: str) -> None:
        self._label = label
        self._emit(self._step / self._total, label)

    def substep(self, done: int, total: int) -> None:
        if total <= 0:
            return
        sub = min(1.0, max(0.0, done / total))
        self._emit((self._step + sub) / self._total, f"{self._label} ({done}/{total})")

    def end_step(self, label: str) -> None:
        self._step += 1
        self._emit(self._step / self._total, label)

    def finish(self) -> None:
        self._emit(1.0, "ingest complete")


def _ingest_jobs_touched(stats: MutableMapping[str, int]) -> int:
    return sum(
        int(v)
        for k, v in stats.items()
        if k.endswith("_jobs_touched") and isinstance(v, (int, float))
    )


def run_ingest_all(*, on_progress: Optional[IngestProgressFn] = None) -> Dict[str, Any]:
    cfg = load_pipeline_config()
    settings = ingest_settings(cfg)
    dedupe = settings["dedupe_normalized_url"]
    tokens = [str(t).strip() for t in (cfg.get("greenhouse_board_tokens") or []) if str(t).strip()]
    levers = [str(s).strip() for s in (cfg.get("lever_companies") or []) if str(s).strip()]
    max_lv = int(cfg.get("lever_max_postings") or 60)
    err: List[str] = []
    stats: Dict[str, int] = {
        "greenhouse_jobs_touched": 0,
        "lever_jobs_touched": 0,
        "indeed_jobs_touched": 0,
        "jobspy_jobs_touched": 0,
        "usajobs_jobs_touched": 0,
        "remoteok_jobs_touched": 0,
        "arbeitnow_jobs_touched": 0,
        "remotive_jobs_touched": 0,
        "themuse_jobs_touched": 0,
        "jobicy_jobs_touched": 0,
        "working_nomads_jobs_touched": 0,
        "wwr_jobs_touched": 0,
        "hn_whoishiring_jobs_touched": 0,
        "hire_heroes_usa_jobs_touched": 0,
        "skipped_validation": 0,
        "skipped_duplicate_url": 0,
        "dedup_by_company_title": 0,
    }
    if not cfg.get("_greenhouse_enabled", True):
        tokens = []
    if not cfg.get("_lever_enabled", False):
        levers = []

    rep = _IngestProgressReporter(plan_ingest_steps(cfg), stats, on_progress)

    with requests.Session() as session:
        session.headers.update({"User-Agent": "AI-JobPipeline/1.1"})
        for t in tokens:
            lbl = f"greenhouse:{t}"
            rep.begin_step(lbl)
            try:
                ingest_greenhouse_board(
                    session,
                    t,
                    settings,
                    stats,
                    dedupe_by_normalized_url=dedupe,
                    on_job_progress=lambda d, n: rep.substep(d, n),
                )
            except Exception as e:
                err.append(f"greenhouse:{t}: {e}")
            rep.end_step(lbl)
        for s in levers:
            lbl = f"lever:{s}"
            rep.begin_step(lbl)
            try:
                ingest_lever_company(session, s, max_lv, settings, stats, dedupe_by_normalized_url=dedupe)
            except Exception as e:
                err.append(f"lever:{s}: {e}")
            rep.end_step(lbl)

        if cfg.get("_usajobs_enabled"):
            from job_pipeline.sources.usajobs_source import run_usajobs_ingest

            rep.begin_step("usajobs")
            try:
                run_usajobs_ingest(
                    cfg.get("_usajobs_cfg") or {},
                    session=session,
                    ingest_settings=settings,
                    dedupe_by_normalized_url=dedupe,
                    stats=stats,
                    errors=err,
                )
            except Exception as e:
                err.append(f"usajobs:{e}")
            rep.end_step("usajobs")

        feeds_cfg = cfg.get("_feeds_cfg") or {}
        if isinstance(feeds_cfg, dict):
            rok = feeds_cfg.get("remoteok") if isinstance(feeds_cfg.get("remoteok"), dict) else {}
            if bool(rok.get("enabled", False)):
                from job_pipeline.sources.feeds_source import ingest_remoteok

                slug_fallback = str(rok.get("title_slug_filter") or rok.get("slug") or "")
                for slug_term in expand_search_terms_for_ingest(
                    rok, slug_fallback, label="remoteok"
                ):
                    lbl = f"remoteok:{slug_term or 'all'}"
                    rep.begin_step(lbl)
                    try:
                        ingest_remoteok(
                            slug=slug_term,
                            session=session,
                            ingest_settings=settings,
                            dedupe_by_normalized_url=dedupe,
                            stats=stats,
                            errors=err,
                            limit=int(rok.get("limit") or 80),
                        )
                    except Exception as e:
                        err.append(f"remoteok:{slug_term}:{e}")
                    rep.end_step(lbl)

            an = feeds_cfg.get("arbeitnow") if isinstance(feeds_cfg.get("arbeitnow"), dict) else {}
            if bool(an.get("enabled", False)):
                from job_pipeline.sources.feeds_source import ingest_arbeitnow

                for kw in expand_search_terms_for_ingest(
                    an, str(an.get("keyword") or ""), label="arbeitnow"
                ):
                    lbl = f"arbeitnow:{kw or 'all'}"
                    rep.begin_step(lbl)
                    try:
                        ingest_arbeitnow(
                            session=session,
                            ingest_settings=settings,
                            dedupe_by_normalized_url=dedupe,
                            stats=stats,
                            errors=err,
                            keyword=kw,
                            limit=int(an.get("limit") or 60),
                        )
                    except Exception as e:
                        err.append(f"arbeitnow:{kw}:{e}")
                    rep.end_step(lbl)

            rmtv = feeds_cfg.get("remotive") if isinstance(feeds_cfg.get("remotive"), dict) else {}
            if bool(rmtv.get("enabled", False)):
                from job_pipeline.sources.feeds_source import ingest_remotive

                for search_kw in expand_search_terms_for_ingest(
                    rmtv, str(rmtv.get("search") or ""), label="remotive"
                ):
                    lbl = f"remotive:{search_kw or 'all'}"
                    rep.begin_step(lbl)
                    try:
                        ingest_remotive(
                            session=session,
                            ingest_settings=settings,
                            dedupe_by_normalized_url=dedupe,
                            stats=stats,
                            errors=err,
                            category=str(rmtv.get("category") or ""),
                            search=search_kw,
                            limit=int(rmtv.get("limit") or 80),
                        )
                    except Exception as e:
                        err.append(f"remotive:{search_kw}:{e}")
                    rep.end_step(lbl)

            muse = feeds_cfg.get("themuse") if isinstance(feeds_cfg.get("themuse"), dict) else {}
            if bool(muse.get("enabled", False)):
                from job_pipeline.sources.feeds_source import ingest_themuse

                rep.begin_step("themuse")
                try:
                    ingest_themuse(
                        session=session,
                        ingest_settings=settings,
                        dedupe_by_normalized_url=dedupe,
                        stats=stats,
                        errors=err,
                        categories=list(muse.get("categories") or ["Computer and IT"]),
                        levels=list(muse.get("levels") or []),
                        location=str(muse.get("location") or ""),
                        max_pages=int(muse.get("max_pages") or 2),
                        per_page_cap=int(muse.get("per_page_cap") or 60),
                    )
                except Exception as e:
                    err.append(f"themuse:{e}")
                rep.end_step("themuse")

            jcy = feeds_cfg.get("jobicy") if isinstance(feeds_cfg.get("jobicy"), dict) else {}
            if bool(jcy.get("enabled", False)):
                from job_pipeline.sources.feeds_source import ingest_jobicy

                for tag_kw in expand_search_terms_for_ingest(
                    jcy, str(jcy.get("tag") or ""), label="jobicy"
                ):
                    lbl = f"jobicy:{tag_kw or 'all'}"
                    rep.begin_step(lbl)
                    try:
                        ingest_jobicy(
                            session=session,
                            ingest_settings=settings,
                            dedupe_by_normalized_url=dedupe,
                            stats=stats,
                            errors=err,
                            geo=str(jcy.get("geo") or "usa"),
                            industry=str(jcy.get("industry") or ""),
                            tag=tag_kw,
                            count=int(jcy.get("count") or 50),
                        )
                    except Exception as e:
                        err.append(f"jobicy:{tag_kw}:{e}")
                    rep.end_step(lbl)

            wn = feeds_cfg.get("working_nomads") if isinstance(feeds_cfg.get("working_nomads"), dict) else {}
            if bool(wn.get("enabled", False)):
                from job_pipeline.sources.feeds_source import ingest_working_nomads

                for kw in expand_search_terms_for_ingest(
                    wn, str(wn.get("keyword") or ""), label="working_nomads"
                ):
                    lbl = f"working_nomads:{kw or 'all'}"
                    rep.begin_step(lbl)
                    try:
                        ingest_working_nomads(
                            session=session,
                            ingest_settings=settings,
                            dedupe_by_normalized_url=dedupe,
                            stats=stats,
                            errors=err,
                            category_filter=str(wn.get("category_filter") or ""),
                            keyword=kw,
                            limit=int(wn.get("limit") or 100),
                        )
                    except Exception as e:
                        err.append(f"working_nomads:{kw}:{e}")
                    rep.end_step(lbl)

            wwr = feeds_cfg.get("wwr_rss") if isinstance(feeds_cfg.get("wwr_rss"), dict) else {}
            if bool(wwr.get("enabled", False)):
                from job_pipeline.sources.feeds_source import ingest_weworkremotely_rss

                for kw in expand_search_terms_for_ingest(
                    wwr, str(wwr.get("keyword") or ""), label="wwr_rss"
                ):
                    lbl = f"wwr:{kw or 'all'}"
                    rep.begin_step(lbl)
                    try:
                        ingest_weworkremotely_rss(
                            session=session,
                            ingest_settings=settings,
                            dedupe_by_normalized_url=dedupe,
                            stats=stats,
                            errors=err,
                            categories=list(wwr.get("categories") or [
                                "remote-customer-support-jobs",
                                "remote-devops-sysadmin-jobs",
                            ]),
                            keyword=kw,
                            limit_per_feed=int(wwr.get("limit_per_feed") or 60),
                        )
                    except Exception as e:
                        err.append(f"wwr:{kw}:{e}")
                    rep.end_step(lbl)

        if cfg.get("_hn_whoishiring_enabled"):
            from job_pipeline.sources.hn_whoishiring import ingest_hn_whoishiring

            hn_cfg = cfg.get("_hn_whoishiring_cfg") or {}
            with requests.Session() as hn_session:
                hn_session.headers.update({"User-Agent": "AI-JobPipeline/1.1"})
                hn_keywords = expand_search_terms_for_ingest(
                    hn_cfg if isinstance(hn_cfg, dict) else {},
                    str(hn_cfg.get("keyword") or ""),
                    label="hn_whoishiring",
                )
                lim_total = max(1, int(hn_cfg.get("limit") or 200))
                per_kw = max(1, lim_total // max(1, len(hn_keywords)))
                for kw in hn_keywords:
                    lbl = f"hn:{kw or 'all'}"
                    rep.begin_step(lbl)
                    try:
                        ingest_hn_whoishiring(
                            session=hn_session,
                            ingest_settings=settings,
                            dedupe_by_normalized_url=dedupe,
                            stats=stats,
                            errors=err,
                            keyword=kw,
                            require_remote=bool(hn_cfg.get("require_remote", True)),
                            require_url=bool(hn_cfg.get("require_url", True)),
                            limit=per_kw,
                        )
                    except Exception as e:
                        err.append(f"hn:{kw}:{e}")
                    rep.end_step(lbl)

        if cfg.get("_hire_heroes_usa_enabled"):
            from job_pipeline.sources.hire_heroes_usa import ingest_hire_heroes_usa

            hhu_cfg = cfg.get("_hire_heroes_usa_cfg") or {}
            hhu_keywords = expand_search_terms_for_ingest(
                hhu_cfg if isinstance(hhu_cfg, dict) else {},
                str(hhu_cfg.get("keyword") or ""),
                label="hire_heroes_usa",
            )
            lim_total = max(1, int(hhu_cfg.get("limit") or 200))
            per_kw = max(1, lim_total // max(1, len(hhu_keywords)))
            for kw in hhu_keywords:
                lbl = f"hire_heroes:{kw or 'all'}"
                rep.begin_step(lbl)
                try:
                    ingest_hire_heroes_usa(
                        session=session,
                        ingest_settings=settings,
                        dedupe_by_normalized_url=dedupe,
                        stats=stats,
                        errors=err,
                        keyword=kw,
                        limit=per_kw,
                    )
                except Exception as e:
                    err.append(f"hire_heroes:{kw}:{e}")
                rep.end_step(lbl)

    if cfg.get("_jobspy_enabled"):
        from job_pipeline.sources.jobspy_source import run_jobspy_ingest

        js_cfg = cfg.get("_jobspy_cfg") or {}
        if isinstance(js_cfg, dict):
            js_terms = expand_search_terms_for_ingest(
                js_cfg,
                str(js_cfg.get("search_term") or ""),
                label="jobspy",
            )
            for sterm in js_terms:
                lbl = f"jobspy:{sterm or 'all'}"
                rep.begin_step(lbl)
                one_cfg = dict(js_cfg)
                one_cfg["use_search_preferences_seeds"] = False
                one_cfg["search_term"] = sterm
                try:
                    run_jobspy_ingest(
                        one_cfg,
                        ingest_settings=settings,
                        dedupe_by_normalized_url=dedupe,
                        stats=stats,
                        errors=err,
                    )
                except Exception as e:
                    err.append(f"jobspy:{sterm}:{e}")
                rep.end_step(lbl)

    if cfg.get("_indeed_enabled"):
        from job_pipeline.apify_indeed import apify_api_token, run_apify_indeed_actor

        if apify_api_token():
            ap_cfg = cfg.get("_indeed_apify") or {}
            ap_cfg_dict = ap_cfg if isinstance(ap_cfg, dict) else {}
            titles = expand_search_terms_for_ingest(
                ap_cfg_dict,
                str(ap_cfg_dict.get("title") or ""),
                label="indeed_apify",
            )
            locations_raw = ap_cfg_dict.get("locations")
            if isinstance(locations_raw, list) and locations_raw:
                location_list = [str(x).strip() for x in locations_raw if str(x).strip()]
            else:
                location_list = [str(ap_cfg_dict.get("location") or "").strip()]
            location_list = [loc for loc in location_list if loc]
            if not location_list:
                logger.warning("indeed_apify: no valid locations in job_pipeline_config; skipping Indeed")
            else:
                for title_q in titles:
                    tstrip = str(title_q or "").strip()
                    if not tstrip:
                        continue
                    for loc in location_list:
                        lbl = f"indeed:{tstrip}@{loc}"
                        rep.begin_step(lbl)
                        try:
                            cfg_run = dict(ap_cfg_dict)
                            cfg_run["title"] = tstrip
                            cfg_run["location"] = loc
                            run_apify_indeed_actor(
                                cfg_run,
                                ingest_settings=settings,
                                dedupe_by_normalized_url=dedupe,
                                stats=stats,
                            )
                        except Exception as e:
                            err.append(f"indeed:{tstrip}@{loc}:{e}")
                        rep.end_step(lbl)
        else:
            err.append("indeed_apify:missing_token_set_APIFY_TOKEN_or_APIFY_API_TOKEN_in_env")

    rep.finish()

    out = {
        "ok": len(err) == 0,
        "greenhouse_jobs_touched": stats.get("greenhouse_jobs_touched", 0),
        "lever_jobs_touched": stats.get("lever_jobs_touched", 0),
        "indeed_jobs_touched": stats.get("indeed_jobs_touched", 0),
        "jobspy_jobs_touched": stats.get("jobspy_jobs_touched", 0),
        "usajobs_jobs_touched": stats.get("usajobs_jobs_touched", 0),
        "remoteok_jobs_touched": stats.get("remoteok_jobs_touched", 0),
        "arbeitnow_jobs_touched": stats.get("arbeitnow_jobs_touched", 0),
        "remotive_jobs_touched": stats.get("remotive_jobs_touched", 0),
        "themuse_jobs_touched": stats.get("themuse_jobs_touched", 0),
        "jobicy_jobs_touched": stats.get("jobicy_jobs_touched", 0),
        "working_nomads_jobs_touched": stats.get("working_nomads_jobs_touched", 0),
        "wwr_jobs_touched": stats.get("wwr_jobs_touched", 0),
        "hn_whoishiring_jobs_touched": stats.get("hn_whoishiring_jobs_touched", 0),
        "hire_heroes_usa_jobs_touched": stats.get("hire_heroes_usa_jobs_touched", 0),
        "skipped_validation": stats.get("skipped_validation", 0),
        "skipped_duplicate_url": stats.get("skipped_duplicate_url", 0),
        "dedup_by_company_title": stats.get("dedup_by_company_title", 0),
        "errors": err,
    }
    for k, v in list(stats.items()):
        if k.startswith("skip_") and v:
            out[k] = v
    return out


def add_manual_posting(
    company_name: str,
    title: str,
    apply_url: str,
    description_text: str,
    location: str = "",
    salary_text: str = "",
) -> Tuple[int, int]:
    import hashlib

    cfg = load_pipeline_config()
    settings = ingest_settings(cfg)
    fld = normalize_posting_fields(company_name, title, location, salary_text, description_text)
    skip = _validation_skip_reason(fld["title"], fld["description_text"], apply_url, settings)
    if skip:
        raise ValueError(f"manual_posting_rejected:{skip}")
    h = hashlib.sha256(
        f"{apply_url}|{fld['title']}|{fld['company_name']}".encode("utf-8")
    ).hexdigest()[:24]
    pid, iid, _, _ = upsert_posting(
        source="manual",
        external_id=h,
        company_name=fld["company_name"],
        title=fld["title"],
        apply_url=apply_url,
        job_url=apply_url,
        location=fld["location"],
        description_text=fld["description_text"],
        salary_text=fld["salary_text"],
        raw_payload={},
        dedupe_by_normalized_url=settings["dedupe_normalized_url"],
    )
    return pid, iid
