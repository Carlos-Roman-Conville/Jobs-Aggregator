"""Jobs Aggregator — service layer.

Exposes ingest, summarize, queue browsing, and stats.
No resume tailoring, cover letters, or auto-apply.
"""

import logging
from typing import Any, Dict, List, Optional

from job_pipeline.card_view import card_for_queue_row, digest_line
from job_pipeline.db import (
    clear_all_pipeline_jobs,
    count_completed_jobs,
    count_closed_by_reason,
    count_items_by_status,
    count_pending_review_above_rank,
    count_queue_items,
    category_counts,
    get_item,
    init_job_pipeline_schema,
    list_completed_jobs,
    list_queue,
    list_queue_source_counts,
    list_queue_source_counts_for_statuses,
    update_item_status,
)
from job_pipeline.ingest import add_manual_posting, run_ingest_all
from job_pipeline.summarize import run_summarize_batch, run_summarize_all, summarize_pipeline_item

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
            r["card"] = card_for_queue_row(r.get("summary_json"))
    return {"ok": True, "items": rows}


def svc_digest_pending(limit: int = 7) -> Dict[str, Any]:
    rows = list_queue(status="pending_review", limit=limit, order_by_rank=True)
    lines = [digest_line(r) for r in rows]
    return {"ok": True, "lines": lines, "items": rows}


def svc_get_item(item_id: int) -> Dict[str, Any]:
    row = get_item(item_id)
    if not row:
        return {"ok": False, "error": "not found"}
    return {"ok": True, "item": row, "card": card_for_queue_row(row.get("summary_json"))}


def svc_decide(item_id: int, action: str, notes: str = "") -> Dict[str, Any]:
    a = (action or "").strip().lower()
    mapping = {
        "skip": "closed",
        "later": "closed",
        "defer": "closed",
        "close": "closed",
        "shortlist": "shortlisted",
    }
    if a not in mapping:
        return {
            "ok": False,
            "error": f"unknown action: {action}. Use skip|later|defer|close|shortlist",
        }
    st = mapping[a]
    if not update_item_status(item_id, st, notes):
        return {"ok": False, "error": "update failed (bad item_id?)"}
    return {"ok": True, "status": st}


def svc_pipeline_stats() -> Dict[str, Any]:
    try:
        ingested = count_items_by_status("ingested")
        pending = count_items_by_status("pending_review")
        closed = count_items_by_status("closed")
        completed = count_completed_jobs()
        return {
            "ok": True,
            "ingested": ingested,
            "pending_review": pending,
            "closed": closed,
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


def svc_clear_all_jobs() -> Dict[str, Any]:
    try:
        counts = clear_all_pipeline_jobs()
        return {"ok": True, **counts}
    except Exception as exc:
        logger.exception("svc_clear_all_jobs failed")
        return {"ok": False, "error": str(exc)}


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


def svc_resummarize(
    limit: int = 25,
    *,
    category: Optional[str] = None,
    on_progress=None,
) -> Dict[str, Any]:
    """Re-summarize pending_review items with force=True to refresh scores."""
    rows = list_queue(
        status="pending_review",
        limit=limit,
        order_by_rank=True,
        category=category,
    )
    done, failed, errors = 0, 0, []
    for i, row in enumerate(rows):
        iid = row.get("id") or row.get("item_id")
        if not iid:
            continue
        try:
            ok, msg = summarize_pipeline_item(iid, force=True)
            if ok:
                done += 1
            else:
                failed += 1
                errors.append(f"{iid}: {msg}")
        except Exception as exc:
            failed += 1
            errors.append(f"{iid}: {exc}")
        if on_progress:
            on_progress(i + 1, len(rows), done, failed)
    return {"ok": True, "resummarized": done, "failed": failed, "errors": errors[:10]}


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
