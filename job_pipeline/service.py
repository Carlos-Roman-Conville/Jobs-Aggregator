import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from application_assets import get_cover_letter_template, get_default_apply_asset_ids, resolve_resume_path
from job_pipeline.attached_resume import parse_attached_resume
from job_pipeline.bootstrap_resume_profile import load_consolidated_profile
from job_pipeline.card_view import card_for_queue_row, digest_line
from job_pipeline.cover_letter_export import (
    cover_letter_plain_text_for_storage,
    export_cover_letter_markdown,
    render_cover_letter_pdf,
)
from job_pipeline.cover_letter_tailor import tailor_cover_letter_from_jd
from job_pipeline.db import (
    analytics_by_resume_template_outcome,
    clear_all_pipeline_jobs,
    count_completed_jobs,
    count_closed_by_reason,
    count_items_by_status,
    count_pending_review_above_rank,
    count_queue_items,
    category_counts,
    fetch_gap_answers_for_requirements,
    get_item,
    init_job_pipeline_schema,
    list_completed_jobs,
    list_queue,
    list_queue_source_counts,
    list_queue_source_counts_for_statuses,
    set_item_outcome,
    set_item_package,
    update_item_status,
)
from job_pipeline.ingest import add_manual_posting, run_ingest_all
from job_pipeline.auto_apply.browser_agent import run_job_apply_agent
from job_pipeline.inbox_sync import sync_rejection_emails
from job_pipeline.ats_parser_check import check_resume_pdf, experience_companies_from_content
from job_pipeline.cover_letter_optimizer import optimize_cover_letter_content
from job_pipeline.package_build import build_package_metadata, extract_resume_bullets_from_content
from job_pipeline.rendercv_export import render_tailored_resume_pdf
from job_pipeline.resume_gaps import answers_to_extra_facts, detect_gaps
from job_pipeline.resume_tailor import _load_grounded_profile_text, tailor_resume_from_jd
from job_pipeline.summarize import run_summarize_batch, run_summarize_all

VALID_OUTCOMES = frozenset(
    {"interview", "offer", "rejection", "ghosted", "withdrawn", "unknown"}
)
VALID_BUILD_MODES = frozenset({"both", "resume_only", "cover_letter_only"})

logger = logging.getLogger(__name__)


def ensure_schema() -> Dict[str, Any]:
    ok, err = init_job_pipeline_schema()
    return {"ok": ok, "error": err}


def svc_ingest(*, on_progress=None) -> Dict[str, Any]:
    return run_ingest_all(on_progress=on_progress)


def svc_summarize(limit: int = 15, *, on_progress=None) -> Dict[str, Any]:
    return run_summarize_batch(limit=limit, on_progress=on_progress)


def svc_verify_freshness(
    *,
    category: Optional[str] = None,
    limit: Optional[int] = None,
    recheck_days: int = 5,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Re-fetch pending_review postings and close dead / location-blocked ones.

    See job_pipeline.freshness_check. Closes (status='closed', non-destructive)
    jobs that are expired, onsite/hybrid outside the home metro, or non-US-only,
    based on the LIVE posting text. Indeed and other bot-blocked hosts come back
    'undetermined' and are left in the queue.
    """
    from datetime import date

    from job_pipeline.freshness_check import verify_pending

    return verify_pending(
        category=category,
        limit=limit,
        recheck_days=recheck_days,
        dry_run=dry_run,
        today=date.today().isoformat(),
    )


def svc_summarize_all(
    *,
    batch_size: int = 50,
    max_batches: int = 100,
    max_minutes: float = 45.0,
    should_stop: Optional[Any] = None,
    on_progress=None,
) -> Dict[str, Any]:
    return run_summarize_all(
        batch_size=batch_size,
        max_batches=max_batches,
        max_minutes=max_minutes,
        should_stop=should_stop,
        on_progress=on_progress,
    )


def svc_daily_run(
    ingest: bool = True,
    summarize_limit: int = 25,
    *,
    summarize_drain: bool = False,
) -> Dict[str, Any]:
    """
    Single call for n8n: optional ingest, summarize new rows, return digest text for Slack/email.
    When summarize_drain=True, summarize until ingested backlog is 0 (or cap/time hit).
    """
    out: Dict[str, Any] = {"ok": True}
    if ingest:
        out["ingest"] = run_ingest_all()
    if summarize_drain:
        out["summarize"] = run_summarize_all(batch_size=max(1, int(summarize_limit)))
    else:
        out["summarize"] = run_summarize_batch(limit=max(1, int(summarize_limit)))
    dig = svc_digest_pending(12)
    lines = dig.get("lines") or []
    out["digest_lines"] = lines
    out["digest_text"] = (
        "\n".join(lines)
        if lines
        else "(No pending_review items — ingest new jobs and summarize, or queue is clear.)"
    )
    out["digest"] = dig
    return out


def sort_items_recent_first(
    items: List[Dict[str, Any]],
    recent_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Pin session-recent item ids to the top while preserving relative order elsewhere."""
    if not items or not recent_ids:
        return items
    rank = {int(iid): idx for idx, iid in enumerate(recent_ids)}
    return sorted(
        items,
        key=lambda row: rank.get(int(row.get("item_id") or 0), 10_000),
    )


def svc_queue(
    status: Optional[str] = None,
    limit: int = 50,
    min_list_rank: Optional[float] = None,
    order_by_rank: bool = True,
    order_by: str = "rank",
    with_card: bool = False,
    source: Optional[str] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    rows = list_queue(
        status=status,
        limit=limit,
        min_list_rank=min_list_rank,
        order_by_rank=order_by_rank,
        order_by=order_by,
        source=source,
        category=category,
    )
    if with_card:
        for r in rows:
            c = card_for_queue_row(r.get("summary_json"))
            if not c.get("recommended_resume_id"):
                c["recommended_resume_id"] = r.get("recommended_resume_id") or ""
            r["card"] = c
    return {"ok": True, "items": rows}


def svc_digest_pending(limit: int = 7) -> Dict[str, Any]:
    rows = list_queue(status="pending_review", limit=limit, order_by_rank=True)
    lines = [digest_line(r) for r in rows]
    return {"ok": True, "lines": lines, "items": rows}


def svc_get_item(item_id: int) -> Dict[str, Any]:
    row = get_item(item_id)
    if not row:
        return {"ok": False, "error": "not found"}
    out = {"ok": True, "item": row, "card": card_for_queue_row(row.get("summary_json"))}
    return out


def svc_decide(item_id: int, action: str, notes: str = "") -> Dict[str, Any]:
    a = (action or "").strip().lower()
    mapping = {
        "apply": "approved",
        "approve": "approved",
        "skip": "closed",
        "later": "closed",
        "defer": "closed",
        "needs_edits": "drafted",
        "mark_applied": "submitted",
        "mark_submitted": "submitted",
        "supervised_done": "submitted",
        "mark_responded": "responded",
        "mark_rejected": "rejected",
        "close": "closed",
    }
    if a not in mapping:
        return {
            "ok": False,
            "error": (
                f"unknown action: {action}. Use apply|approve|skip|later|defer|needs_edits|"
                "mark_applied|mark_submitted|supervised_done|mark_responded|mark_rejected|close"
            ),
        }
    st = mapping[a]
    if not update_item_status(item_id, st, notes):
        return {"ok": False, "error": "update failed (bad item_id?)"}
    return {"ok": True, "status": st}


def svc_record_outcome(item_id: int, outcome: str, notes: str = "") -> Dict[str, Any]:
    o = (outcome or "").strip().lower()
    if o not in VALID_OUTCOMES:
        return {
            "ok": False,
            "error": f"outcome must be one of: {', '.join(sorted(VALID_OUTCOMES))}",
        }
    if not set_item_outcome(item_id, o, notes):
        return {"ok": False, "error": "update failed (bad item_id?)"}
    return {"ok": True, "outcome": o}


def svc_analytics() -> Dict[str, Any]:
    return {"ok": True, **analytics_by_resume_template_outcome()}


def svc_clear_all_jobs() -> Dict[str, Any]:
    """Remove every job posting and pipeline item from Postgres."""
    try:
        counts = clear_all_pipeline_jobs()
        return {"ok": True, **counts}
    except Exception as exc:
        logger.exception("svc_clear_all_jobs failed")
        return {"ok": False, "error": str(exc)}


def svc_pipeline_stats() -> Dict[str, Any]:
    """Lightweight queue counters for dashboard banners."""
    try:
        ingested = count_items_by_status("ingested")
        pending = count_items_by_status("pending_review")
        closed = count_items_by_status("closed")
        package_ready = count_items_by_status("package_ready")
        completed = count_completed_jobs()
        return {
            "ok": True,
            "ingested": ingested,
            "pending_review": pending,
            "closed": closed,
            "package_ready": package_ready,
            "completed": completed,
            "total_items": count_items_by_status(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def svc_count_pending_review(
    min_list_rank: float = 0.0,
    *,
    source: Optional[str] = None,
) -> int:
    try:
        return count_pending_review_above_rank(float(min_list_rank), source=source)
    except Exception:
        return 0


def svc_source_counts(
    status: str,
    *,
    min_list_rank: Optional[float] = None,
    statuses: Optional[List[str]] = None,
) -> Dict[str, Any]:
    try:
        if statuses:
            rows = list_queue_source_counts_for_statuses(statuses, min_list_rank=min_list_rank)
        else:
            rows = list_queue_source_counts(status, min_list_rank=min_list_rank)
        return {"ok": True, "sources": rows, "total": sum(int(r["count"]) for r in rows)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "sources": [], "total": 0}


def svc_list_completed(
    *,
    limit: int = 100,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        rows = list_completed_jobs(limit=max(1, int(limit)), source=source)
        return {"ok": True, "items": rows, "total": len(rows)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "items": []}


def svc_count_completed(*, source: Optional[str] = None) -> int:
    try:
        return count_completed_jobs(source=source)
    except Exception:
        return 0


def svc_count_queue(
    status: str,
    *,
    min_list_rank: Optional[float] = None,
    source: Optional[str] = None,
) -> int:
    try:
        return count_queue_items(status, min_list_rank=min_list_rank, source=source)
    except Exception:
        return 0


def svc_category_counts(
    status: str = "pending_review",
    *,
    min_list_rank: Optional[float] = None,
    source: Optional[str] = None,
) -> Dict[str, int]:
    try:
        return category_counts(status, min_list_rank=min_list_rank, source=source)
    except Exception:
        return {}


def svc_closed_reason_breakdown() -> Dict[str, Any]:
    try:
        reasons = count_closed_by_reason()
        closed_total = count_items_by_status("closed")
        return {"ok": True, "closed_total": closed_total, "by_category": reasons}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def svc_rescore_domain_fit(limit: int = 200) -> Dict[str, Any]:
    """Re-apply domain fit (career_profile.json) to pending_review rows without rerunning LLM summarization."""
    from job_pipeline.rescore_domain import rescore_pending_review_batch

    return rescore_pending_review_batch(limit=max(1, int(limit)))


def svc_imap_rejection_sync(dry_run: bool = True, max_messages: int = 120) -> Dict[str, Any]:
    return sync_rejection_emails(dry_run=bool(dry_run), max_messages=max(1, int(max_messages)))


def svc_browser_apply_preview(item_id: int, dry_run: bool = True) -> Dict[str, Any]:
    row = get_item(item_id)
    if not row:
        return {"ok": False, "error": "not found"}
    url = (row.get("apply_url") or row.get("job_url") or "").strip()
    if not url.startswith("http"):
        return {"ok": False, "error": "missing_apply_http_url"}
    out = run_job_apply_agent(url, dry_run=bool(dry_run))
    out["item_id"] = item_id
    return out


def svc_autofill_profile() -> Dict[str, Any]:
    from job_pipeline.autofill_profile import build_autofill_profile

    try:
        profile = build_autofill_profile()
        return {"ok": True, "profile": profile}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def svc_write_autofill_profile() -> Dict[str, Any]:
    from job_pipeline.autofill_profile import write_autofill_profile_json

    try:
        path = write_autofill_profile_json()
        return {"ok": True, "path": str(path)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _load_credentials_ledger() -> Dict[str, Any]:
    from job_pipeline.autofill_profile import default_ats_credentials_json_path

    path = default_ats_credentials_json_path()
    if not path.is_file():
        return {"version": 1, "credentials": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "credentials": {}}
    if not isinstance(raw, dict):
        return {"version": 1, "credentials": {}}
    creds = raw.get("credentials")
    if not isinstance(creds, dict):
        creds = {}
    return {"version": raw.get("version", 1), "credentials": creds}


def _save_credentials_ledger(ledger: Dict[str, Any]) -> None:
    from job_pipeline.autofill_profile import default_ats_credentials_json_path

    path = default_ats_credentials_json_path()
    payload = {
        "version": int(ledger.get("version") or 1),
        "_comment": "Local-only ledger of ATS account-creation credentials, keyed by domain. Populated automatically by the browser extension when you create a new account.",
        "credentials": ledger.get("credentials") or {},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_domain(domain: str) -> str:
    raw = (domain or "").strip().lower()
    if not raw:
        return ""
    # Strip scheme + path if a URL was passed.
    if "://" in raw:
        try:
            from urllib.parse import urlparse

            raw = urlparse(raw).hostname or ""
        except Exception:
            raw = raw.split("://", 1)[1].split("/", 1)[0]
    return raw.strip("/").strip().lower()


def svc_get_credential(domain: str) -> Dict[str, Any]:
    norm = _normalize_domain(domain)
    if not norm:
        return {"ok": False, "error": "missing_domain"}
    ledger = _load_credentials_ledger()
    record = (ledger.get("credentials") or {}).get(norm)
    return {"ok": True, "domain": norm, "credential": record or None}


def svc_save_credential(
    domain: str,
    email: str,
    password: str,
    application_url: str = "",
) -> Dict[str, Any]:
    norm = _normalize_domain(domain)
    if not norm:
        return {"ok": False, "error": "missing_domain"}
    if not email or not password:
        return {"ok": False, "error": "missing_email_or_password"}
    from datetime import datetime, timezone

    ledger = _load_credentials_ledger()
    creds = ledger.get("credentials") or {}
    existing = creds.get(norm) or {}
    record = {
        "email": email.strip(),
        "password": password,
        "created_at": existing.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "first_application_url": existing.get("first_application_url") or application_url.strip(),
    }
    creds[norm] = record
    ledger["credentials"] = creds
    try:
        _save_credentials_ledger(ledger)
    except OSError as exc:
        return {"ok": False, "error": f"write_failed: {exc}"}
    return {"ok": True, "domain": norm, "credential": record}


def _parse_tailored_yaml(yaml_path: "Path") -> Dict[str, Any]:
    """Pull skills + summary + top bullets out of a rendercv YAML file.

    rendercv YAML is YAML-like but we wrote it hand-formatted; using a real
    YAML parser is more reliable than regex for this. Falls back gracefully.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None  # type: ignore

    text = yaml_path.read_text(encoding="utf-8")
    skills_tech: str = ""
    skills_soft: str = ""
    summary: str = ""
    bullets: List[str] = []
    if yaml is not None:
        try:
            doc = yaml.safe_load(text) or {}
            sections = (doc.get("cv") or {}).get("sections") or {}
            summ = sections.get("summary") or []
            if isinstance(summ, list) and summ:
                summary = str(summ[0]).strip()
            skills_block = sections.get("skills") or []
            if isinstance(skills_block, list):
                for entry in skills_block:
                    if not isinstance(entry, dict):
                        continue
                    label = str(entry.get("label") or "").lower()
                    details = str(entry.get("details") or "")
                    if "technical" in label:
                        skills_tech = details
                    elif "soft" in label:
                        skills_soft = details
            exp_block = sections.get("experience") or []
            if isinstance(exp_block, list) and exp_block:
                first = exp_block[0]
                if isinstance(first, dict):
                    h = first.get("highlights") or []
                    bullets = [str(b).strip() for b in h if isinstance(b, str) and str(b).strip()][:6]
        except Exception:
            pass

    if not (summary or skills_tech):
        # Regex fallback for environments without PyYAML.
        m = re.search(r'summary:\s*\n\s+-\s+"([^"]+)"', text)
        if m:
            summary = m.group(1).strip()
        for m in re.finditer(
            r'label:\s*"([^"]+)"\s*\n\s+details:\s*"([^"]+)"',
            text,
        ):
            label = m.group(1).lower()
            details = m.group(2)
            if "technical" in label and not skills_tech:
                skills_tech = details
            elif "soft" in label and not skills_soft:
                skills_soft = details

    return {
        "summary": summary,
        "skills_technical": skills_tech,
        "skills_soft": skills_soft,
        "experience_bullets": bullets,
    }


def svc_tailored_for_url(url: str = "", filename: str = "") -> Dict[str, Any]:
    """Return parsed tailored content (skills + summary + bullets) for the
    package that best matches the given application URL.

    Match precedence:
      1. exact filename if provided
      2. URL hostname token vs filename substring
      3. most-recent tailored YAML
    """
    from pathlib import Path
    from urllib.parse import urlparse

    root = Path(__file__).resolve().parents[1] / "generated_resumes"
    if not root.is_dir():
        return {"ok": False, "error": "no_generated_resumes_dir"}

    yamls = [p for p in root.iterdir() if p.suffix.lower() == ".yaml"]
    # Exclude cover letter yamls.
    yamls = [p for p in yamls if "cover_letter" not in p.stem.lower()]
    if not yamls:
        return {"ok": True, "match": None}

    yamls.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    chosen: Optional["Path"] = None
    if filename:
        cand = root / filename
        if cand.is_file():
            chosen = cand
    if chosen is None and url:
        try:
            host = urlparse(url).hostname or ""
            host = host.lower()
            # Drop TLD then split on '.'
            host_no_tld = re.sub(r"\.(com|net|org|io|co|us|ai)$", "", host)
            tokens = [
                t
                for t in host_no_tld.split(".")
                if t and len(t) >= 3 and t not in {"www", "jobs", "careers", "apply", "app", "boards"}
            ]
            for tok in tokens:
                for p in yamls:
                    if tok in p.stem.lower():
                        chosen = p
                        break
                if chosen:
                    break
        except Exception:
            pass

    if chosen is None:
        chosen = yamls[0]

    parsed = _parse_tailored_yaml(chosen)
    return {
        "ok": True,
        "match": {
            "filename": chosen.name,
            "path": str(chosen.resolve()),
            "mtime": chosen.stat().st_mtime,
            **parsed,
        },
    }


def svc_recent_resumes(*, limit: int = 12) -> Dict[str, Any]:
    """List recent tailored resume PDFs/MDs from generated_resumes/.

    Filters to per-job tailored artifacts (skips orphan / non-matching files),
    sorts by mtime descending. The extension popup uses this to surface the
    right filename to attach.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "generated_resumes"
    if not root.is_dir():
        return {"ok": True, "resumes": [], "root": str(root)}

    items: List[Dict[str, Any]] = []
    for p in root.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".pdf", ".md"):
            continue
        # Skip cover letters from the primary list (still useful but secondary).
        is_cover = "cover_letter" in p.stem.lower()
        try:
            stat = p.stat()
            items.append(
                {
                    "filename": p.name,
                    "path": str(p.resolve()),
                    "mtime": stat.st_mtime,
                    "size": stat.st_size,
                    "kind": "cover_letter" if is_cover else "resume",
                }
            )
        except OSError:
            continue

    items.sort(key=lambda d: d["mtime"], reverse=True)
    # Limit primary resumes; cover letters returned alongside for context.
    resumes = [i for i in items if i["kind"] == "resume"][: max(1, int(limit))]
    cover_letters = [i for i in items if i["kind"] == "cover_letter"][: max(1, int(limit))]
    return {
        "ok": True,
        "root": str(root.resolve()),
        "resumes": resumes,
        "cover_letters": cover_letters,
    }


def _normalize_build_mode(mode: str) -> str:
    m = (mode or "both").strip().lower().replace("-", "_")
    aliases = {
        "resume": "resume_only",
        "cover_letter": "cover_letter_only",
        "both": "both",
    }
    m = aliases.get(m, m)
    if m not in VALID_BUILD_MODES:
        raise ValueError(f"mode must be one of: {', '.join(sorted(VALID_BUILD_MODES))}")
    return m


def _parse_summary_json(raw: Any) -> Dict[str, Any]:
    summary = raw or {}
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except Exception:
            summary = {}
    return summary if isinstance(summary, dict) else {}


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _gap_extra_facts(
    jd: str,
    *,
    profile_text: str,
    tailored_content: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
) -> List[str]:
    gaps = detect_gaps(
        jd,
        profile_text=profile_text,
        tailored_content=tailored_content,
        use_llm=use_llm,
    )
    if not gaps:
        return []
    reqs = [(g.get("requirement") or "").strip() for g in gaps]
    try:
        saved = fetch_gap_answers_for_requirements(reqs)
    except Exception:
        saved = {}
    answers = []
    for g, req in zip(gaps, reqs):
        sug = (g.get("suggested_answer") or "").strip()
        answers.append((saved.get(req) or "").strip() or sug)
    return answers_to_extra_facts(gaps, answers)


def _resume_text_from_tailor_result(result: Dict[str, Any]) -> str:
    md_path = (result.get("markdown_path") or "").strip()
    if md_path and os.path.isfile(md_path):
        try:
            return Path(md_path).read_text(encoding="utf-8")[:8000]
        except OSError:
            pass
    content = result.get("content") if isinstance(result.get("content"), dict) else {}
    chunks: List[str] = []
    summary = str(content.get("summary") or "").strip()
    if summary:
        chunks.append(summary)
    for exp in content.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        hdr = f"{exp.get('title', '')} @ {exp.get('company', '')}".strip()
        if hdr.strip("@"):
            chunks.append(hdr)
        for b in exp.get("bullets") or []:
            t = str(b).strip()
            if t:
                chunks.append(t)
    return "\n".join(chunks)[:8000]


def build_application_artifacts(
    *,
    mode: str = "both",
    tailor_resume: bool = True,
    title: str = "",
    company: str = "",
    location: str = "",
    description: str = "",
    summary_json: Optional[Dict[str, Any]] = None,
    resume_id: str = "",
    template_id: str = "",
    item_id: int = 0,
    attached_resume_path: Optional[str] = None,
    strategy_level: str = "balanced",
    render_cover_pdf: bool = True,
    render_resume_pdf: bool = True,
    theme: str = "classic",
    extra_facts: Optional[List[str]] = None,
    outputs_root: Optional[str] = None,
    gap_use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Shared builder for queue packages and ad-hoc /jobs/pipeline/tailor calls.
    Writes artifacts under generated_resumes/; does not touch Postgres.
    """
    mode_n = _normalize_build_mode(mode)
    summary = _parse_summary_json(summary_json)
    artifacts: Dict[str, Any] = {"warnings": []}
    warnings: List[str] = artifacts["warnings"]
    letter = ""
    letter_content: Dict[str, Any] = {}
    resume_bullets: List[str] = []
    tailored_result: Optional[Dict[str, Any]] = None
    resume_text_for_cl = ""

    profile = load_consolidated_profile()
    contact = profile.get("contact") if isinstance(profile.get("contact"), dict) else {}
    profile_text = _load_grounded_profile_text()
    out_root = outputs_root or _repo_root()

    need_resume = mode_n in ("both", "resume_only")
    need_cover = mode_n in ("both", "cover_letter_only")

    facts = (
        list(extra_facts)
        if extra_facts is not None
        else _gap_extra_facts(description, profile_text=profile_text, use_llm=gap_use_llm)
    )

    if need_resume and tailor_resume:
        tailored_result = tailor_resume_from_jd(
            description,
            job_title=title,
            company=company,
            location=location,
            strategy_level=strategy_level,
            extra_facts=facts,
            summary_json=summary,
            export_markdown=True,
            item_id=item_id or None,
        )
        if not tailored_result.get("ok"):
            content_err = tailored_result.get("content") or {}
            err = content_err.get("error", "resume_tailor_failed")
            detail = (content_err.get("detail") or "").strip()
            if err == "json_parse_failed" and detail:
                err = f"json_parse_failed: {detail}"
            return {"ok": False, "error": err, "tailored_resume": tailored_result}
        content = tailored_result.get("content") or {}
        resume_bullets = extract_resume_bullets_from_content(content)
        if tailored_result.get("markdown_path"):
            artifacts["resume_md"] = tailored_result["markdown_path"]
        if render_resume_pdf:
            pdf_path, diag = render_tailored_resume_pdf(
                content,
                contact=contact,
                name=str(profile.get("name") or ""),
                headline=str(profile.get("headline") or ""),
                job_title=str(tailored_result.get("job_title") or title or ""),
                company=str(tailored_result.get("company") or company or ""),
                item_id=int(item_id or 0),
                military_service=profile.get("military_service") or [],
                education=profile.get("education") or [],
                certifications=profile.get("certifications") or [],
                outputs_root=out_root,
                theme=theme,
                strategy_level=strategy_level,
            )
            if pdf_path:
                artifacts["resume_pdf"] = pdf_path
                parser_result = check_resume_pdf(
                    pdf_path,
                    expected_companies=experience_companies_from_content(content),
                )
                artifacts["ats_parser_check"] = parser_result
                for issue in parser_result.get("issues") or []:
                    warnings.append(f"ATS parser: {issue}")
                for issue in parser_result.get("warnings") or []:
                    warnings.append(f"ATS parser: {issue}")
            else:
                warnings.append(f"Resume PDF render skipped: {diag}")
        resume_text_for_cl = _resume_text_from_tailor_result(tailored_result)

    elif need_resume and not tailor_resume:
        link_path = (attached_resume_path or "").strip()
        if not link_path:
            try:
                link_path = resolve_resume_path(resume_id)
            except Exception as exc:
                return {"ok": False, "error": f"resume link failed: {exc}"}
        artifacts["resume_file"] = link_path
        parsed, warn = parse_attached_resume(link_path)
        if parsed:
            resume_text_for_cl = parsed
            resume_bullets = [ln.strip() for ln in parsed.splitlines() if ln.strip()][:40]
        elif warn:
            warnings.append(warn)

    if need_cover:
        if mode_n == "cover_letter_only":
            if attached_resume_path:
                parsed, warn = parse_attached_resume(attached_resume_path)
                if parsed:
                    resume_text_for_cl = parsed
                    resume_bullets = [ln.strip() for ln in parsed.splitlines() if ln.strip()][:40]
                    artifacts["resume_file"] = str(Path(attached_resume_path).resolve())
                elif warn:
                    warnings.append(warn)
            elif not resume_text_for_cl:
                warnings.append(
                    "No attached resume — cover letter grounded in profile only."
                )

        try:
            template_hint = get_cover_letter_template(template_id)
        except Exception as exc:
            return {"ok": False, "error": f"template load failed: {exc}"}

        cl_extra = (
            facts
            if extra_facts is not None
            else _gap_extra_facts(
                description,
                profile_text=profile_text,
                tailored_content=(tailored_result or {}).get("content"),
                use_llm=gap_use_llm,
            )
        )
        cl_out = tailor_cover_letter_from_jd(
            description,
            job_title=title,
            company=company,
            location=location,
            summary_card=summary,
            resume_text=resume_text_for_cl,
            template_hint=template_hint,
            extra_facts=cl_extra,
        )
        if not cl_out.get("ok"):
            err = (cl_out.get("content") or {}).get("error", "cover_letter_tailor_failed")
            return {"ok": False, "error": err, "cover_letter": cl_out}
        letter_content = cl_out.get("content") or {}
        # Pass the (already-optimized) resume content so cross-document phrase
        # dedupe can drop narrative phrases the cover letter shares with it
        # ("small-shop environment" / "high-traffic facility" / etc.).
        resume_for_dedupe = (
            (tailored_result or {}).get("content")
            if (tailored_result or {}).get("content") and not (tailored_result or {}).get("content", {}).get("error")
            else None
        )
        letter_content = optimize_cover_letter_content(
            letter_content,
            job_description=description,
            profile_text=profile_text,
            job_title=str(cl_out.get("job_title") or title or ""),
            company=str(cl_out.get("company") or company or ""),
            resume_content=resume_for_dedupe,
        )
        if isinstance(resume_for_dedupe, dict):
            try:
                from job_pipeline.presentation_linter import cross_document_consistency

                cross_findings = cross_document_consistency(
                    resume_for_dedupe,
                    letter_content,
                    role=str(cl_out.get("job_title") or title or ""),
                    company=str(cl_out.get("company") or company or ""),
                )
                for cf in cross_findings:
                    warnings.append(cf.as_note())
            except Exception as exc:
                warnings.append(f"cross-document check skipped: {exc}")
            try:
                from job_pipeline.quality_judge import (
                    clear_judge_cache,
                    judge_quality,
                    pkg_judge_enabled,
                )

                if pkg_judge_enabled():
                    clear_judge_cache()
                    pkg_judge = judge_quality(
                        resume_for_dedupe,
                        job_description=description,
                        job_title=str(cl_out.get("job_title") or title or ""),
                        cover_letter_content=letter_content,
                    )
                    artifacts["quality_judge"] = pkg_judge
                    if pkg_judge.get("ok"):
                        if not pkg_judge.get("passes_gate"):
                            warnings.append(
                                f"Quality judge score {pkg_judge.get('score')} "
                                f"below minimum {pkg_judge.get('judge_min')}"
                            )
                        for line in (pkg_judge.get("critique") or [])[:4]:
                            warnings.append(f"Judge: {line}")
            except Exception as exc:
                warnings.append(f"quality judge skipped: {exc}")
        letter = cover_letter_plain_text_for_storage(letter_content, company=company, profile=profile)
        md_path = export_cover_letter_markdown(
            letter_content,
            company=str(cl_out.get("company") or company or ""),
            job_title=str(cl_out.get("job_title") or title or ""),
            item_id=int(item_id or 0),
            profile=profile,
            outputs_root=out_root,
        )
        artifacts["cover_letter_md"] = md_path
        if render_cover_pdf:
            cover_pdf, cover_diag = render_cover_letter_pdf(
                letter_content,
                company=str(cl_out.get("company") or company or ""),
                job_title=str(cl_out.get("job_title") or title or ""),
                item_id=int(item_id or 0),
                profile=profile,
                theme=theme,
                outputs_root=out_root,
            )
            if cover_pdf:
                artifacts["cover_pdf"] = cover_pdf
            else:
                warnings.append(f"Cover letter PDF render skipped: {cover_diag}")

    return {
        "ok": True,
        "mode": mode_n,
        "letter": letter,
        "letter_content": letter_content,
        "artifacts": artifacts,
        "resume_bullets": resume_bullets,
        "tailored_resume": tailored_result,
    }


def svc_build_package(
    item_id: int,
    resume_id: Optional[str] = None,
    template_id: Optional[str] = None,
    *,
    mode: str = "both",
    tailor_resume: bool = True,
    attached_resume_path: Optional[str] = None,
    is_rebuild: bool = False,
) -> Dict[str, Any]:
    row = get_item(item_id)
    if not row:
        return {"ok": False, "error": "not found"}
    st = row.get("status") or ""
    if st != "approved":
        return {
            "ok": False,
            "error": f"Status must be approved (got {st}). Use action=apply on this item first.",
        }

    dr, dt = get_default_apply_asset_ids()
    rid = resume_id or row.get("recommended_resume_id") or dr
    tid = template_id or row.get("cover_letter_template_id") or dt

    from job_pipeline.company_name import normalize_company_name

    title = row.get("title") or ""
    raw_company = row.get("company_name") or ""
    # Normalize domain-shaped company names ("enterprisesolutioninc.com")
    # into human-readable form ("Enterprise Solution Inc.") so the cover
    # letter address line doesn't read as "Hiring Team, foo.com". No-op
    # when the source name is already human-written (contains spaces, no
    # TLD).
    company = normalize_company_name(raw_company)
    if company != raw_company:
        logger.info(
            "svc_build_package item=%s normalized company_name %r -> %r",
            item_id, raw_company, company,
        )
    desc = row.get("description_text") or ""
    loc = row.get("location") or ""
    summary = _parse_summary_json(row.get("summary_json"))

    try:
        mode_n = _normalize_build_mode(mode)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    t0 = time.perf_counter()
    built = build_application_artifacts(
        mode=mode_n,
        tailor_resume=bool(tailor_resume),
        title=title,
        company=company,
        location=loc,
        description=desc,
        summary_json=summary,
        resume_id=rid,
        template_id=tid,
        item_id=int(item_id),
        attached_resume_path=attached_resume_path,
        gap_use_llm=not is_rebuild,
    )
    build_sec = round(time.perf_counter() - t0, 1)
    logger.info(
        "svc_build_package item=%s mode=%s rebuild=%s artifacts=%.1fs",
        item_id,
        mode_n,
        is_rebuild,
        build_sec,
    )
    if not built.get("ok"):
        return {"ok": False, "error": built.get("error", "build failed"), "detail": built}

    letter = built.get("letter") or ""
    art = built.get("artifacts") or {}
    tr = built.get("tailored_resume") if isinstance(built.get("tailored_resume"), dict) else {}
    validation = tr.get("validation") if isinstance(tr.get("validation"), dict) else {}
    optimization = tr.get("optimization") if isinstance(tr.get("optimization"), dict) else {}
    meta = build_package_metadata(
        rid,
        tid,
        letter,
        title,
        company,
        mode=mode_n,
        artifacts=art,
        resume_bullets=built.get("resume_bullets") or [],
        summary_card=summary,
        skip_llm_check=is_rebuild,
    )
    qj = art.get("quality_judge") if isinstance(art.get("quality_judge"), dict) else {}
    if qj.get("ok"):
        meta["judge_score"] = qj.get("score")
        meta["judge_verdict"] = qj.get("verdict")
        meta["judge_critique"] = qj.get("critique") or []
        meta["judge_passes_gate"] = qj.get("passes_gate")
    meta["gate_passed"] = optimization.get("gate_passed")
    meta["gate_blocked"] = optimization.get("gate_blocked")
    meta["gate_revisions"] = optimization.get("gate_revisions")
    if optimization.get("judge_score") is not None and "judge_score" not in meta:
        meta["judge_score"] = optimization.get("judge_score")
        meta["judge_critique"] = optimization.get("judge_critique") or []

    # Phase 1D: regression check against known bad patterns.
    # This scans the final rendered MD artifacts (so it catches anything
    # that survived the JSON-level scrubbers OR was introduced by the
    # rendercv export). Issues found here are *not* auto-fixed — they're
    # surfaced as quality_block on the package metadata so the dashboard
    # can warn before the user applies.
    #
    # Phase 3A: persist the build's issue log as a JSON sidecar next to
    # the artifacts. The log accumulates automated_issues (overwritten on
    # each rebuild) + manual_issues (preserved across rebuilds), giving
    # the system a durable record of what kept going wrong for this item.
    try:
        from job_pipeline.regression_check import check_artifact_files, write_issue_log
        resume_md = (art.get("resume_md") or "") if isinstance(art, dict) else ""
        cover_md = (art.get("cover_letter_md") or "") if isinstance(art, dict) else ""
        regression_issues = check_artifact_files(resume_md, cover_md)
        if regression_issues:
            meta["quality_block"] = True
            meta["quality_block_reasons"] = regression_issues
            logger.warning(
                "svc_build_package item=%s QUALITY_BLOCK %d issue(s): %s",
                item_id, len(regression_issues), "; ".join(regression_issues[:5]),
            )
        else:
            meta["quality_block"] = False

        log_path = write_issue_log(
            item_id=int(item_id),
            resume_md_path=resume_md,
            cover_letter_md_path=cover_md,
            automated_issues=regression_issues,
            gate_revisions=int(meta.get("gate_revisions") or 0),
            judge_score=meta.get("judge_score"),
            quality_block=bool(meta.get("quality_block")),
            extra={
                "title": title,
                "company": company,
                "build_sec": build_sec,
            },
        )
        if log_path:
            meta["issue_log_path"] = log_path
            logger.info(
                "svc_build_package item=%s issue_log=%s automated=%d",
                item_id, log_path, len(regression_issues),
            )
    except Exception as exc:
        logger.warning("regression_check skipped item=%s: %s", item_id, exc)

    if not set_item_package(item_id, letter, rid, tid, package_meta=meta):
        return {"ok": False, "error": "failed to save package"}

    apply_url = row.get("apply_url") or row.get("job_url") or ""
    trust_issues = [w for w in (meta.get("warnings") or []) if not w.startswith("Review:")]
    opt_score = (optimization.get("score") or {}).get("total") if optimization else None
    return {
        "ok": True,
        "status": "package_ready",
        "mode": mode_n,
        "resume_id": rid,
        "cover_letter_template_id": tid,
        "cover_letter_preview": letter[:1200] + ("…" if len(letter) > 1200 else ""),
        "apply_url": apply_url,
        "resume_pdf": meta.get("resume_pdf"),
        "resume_file": meta.get("resume_file"),
        "cover_pdf": meta.get("cover_pdf"),
        "cover_letter_md": meta.get("cover_letter_md"),
        "resume_md": meta.get("resume_md"),
        "named_requirement_gaps": validation.get("named_requirement_gaps") or [],
        "tailor_validation_issues": validation.get("issues") or [],
        "optimization_score": opt_score,
        "optimization_gate_passed": optimization.get("gate_passed"),
        "optimization_gate_blocked": optimization.get("gate_blocked"),
        "judge_score": meta.get("judge_score"),
        "judge_verdict": meta.get("judge_verdict"),
        "package_trust": {
            "resume_file_ok": meta.get("resume_file_ok"),
            "resume_path": meta.get("resume_path"),
            "resume_bytes": meta.get("resume_bytes"),
            "cover_letter_chars": meta.get("cover_letter_chars"),
            "warnings": meta.get("warnings") or [],
            "blocking_issues": trust_issues,
        },
        "next_step": (
            "Open the apply URL, attach the resume artifact shown above, paste or refine the cover letter, "
            "then submit only after your own final read."
        ),
    }


def svc_tailor_application(
    *,
    job_description: str,
    job_title: str = "",
    company: str = "",
    location: str = "",
    mode: str = "both",
    tailor_resume: bool = True,
    resume_id: Optional[str] = None,
    template_id: Optional[str] = None,
    attached_resume_path: Optional[str] = None,
    render_cover_pdf: bool = True,
    theme: str = "classic",
) -> Dict[str, Any]:
    """
    Ad-hoc application generation — no item_id, no Postgres writes.
    Intended for localhost automation; returns local filesystem paths only (no PDF bytes).
    """
    jd = (job_description or "").strip()
    if len(jd) < 40:
        return {"ok": False, "error": "job_description too short"}

    dr, dt = get_default_apply_asset_ids()
    rid = (resume_id or dr or "").strip()
    tid = (template_id or dt or "").strip()

    try:
        mode_n = _normalize_build_mode(mode)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    built = build_application_artifacts(
        mode=mode_n,
        tailor_resume=bool(tailor_resume),
        title=job_title,
        company=company,
        location=location,
        description=jd,
        summary_json={},
        resume_id=rid,
        template_id=tid,
        item_id=0,
        attached_resume_path=attached_resume_path,
        render_cover_pdf=render_cover_pdf,
        theme=theme,
        render_resume_pdf=True,
        outputs_root=_repo_root(),
    )
    if not built.get("ok"):
        return {"ok": False, "error": built.get("error", "build failed"), "detail": built}

    art = built.get("artifacts") or {}
    return {
        "ok": True,
        "mode": mode_n,
        "tailor_resume": bool(tailor_resume),
        "cover_letter_text": built.get("letter") or "",
        "paths": {
            "resume_pdf": art.get("resume_pdf"),
            "resume_file": art.get("resume_file"),
            "cover_pdf": art.get("cover_pdf"),
            "cover_letter_md": art.get("cover_letter_md"),
            "resume_md": art.get("resume_md"),
        },
        "warnings": art.get("warnings") or [],
    }


def svc_manual_add(
    company_name: str,
    title: str,
    apply_url: str,
    description_text: str,
    location: str = "",
    salary_text: str = "",
) -> Dict[str, Any]:
    try:
        pid, iid = add_manual_posting(
            company_name, title, apply_url, description_text, location, salary_text
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "posting_id": pid, "pipeline_item_id": iid}
