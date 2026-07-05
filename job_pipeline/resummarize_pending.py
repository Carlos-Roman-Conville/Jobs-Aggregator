"""
Re-run OpenAI summarize + full scoring chain for queue rows with stale prompt framing.

Eligible statuses: ``pending_review`` and ``ranked`` only (never approved/package_ready).

Staleness is determined solely by ``summary_json.prompt_framing_version`` vs
``summarize.PROMPT_FRAMING_VERSION`` — see ``summary_prompt_framing_is_stale``.

Usage::

    python -m job_pipeline.resummarize_pending --dry-run --all-stale --limit 20
    python -m job_pipeline.resummarize_pending --ids 101,102 --dry-run
    python -m job_pipeline.resummarize_pending --all-stale --limit 50

Requires OPENAI_API_KEY and Postgres env vars (same as normal summarize).
"""
from __future__ import annotations

# Auto-load .env from the repo root so this CLI works in a plain PowerShell
# session without the user having to export POSTGRES_* / OPENAI_API_KEY by hand.
# Mirrors the pattern used by job_dashboard.py.
from pathlib import Path as _Path
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from job_pipeline.db import get_item, list_items_by_statuses
from job_pipeline.summarize import (
    PROMPT_FRAMING_VERSION,
    summarize_pipeline_item,
    summary_prompt_framing_is_stale,
)

ELIGIBLE_STATUSES = frozenset({"pending_review", "ranked"})


def _parse_ids_csv(raw: str) -> List[int]:
    out: List[int] = []
    for part in (raw or "").split(","):
        p = part.strip()
        if not p:
            continue
        out.append(int(p))
    return out


def _row_preview(row: Dict[str, Any]) -> Dict[str, Any]:
    sj = row.get("summary_json")
    parsed = sj if isinstance(sj, dict) else {}
    return {
        "item_id": row.get("id"),
        "status": row.get("status"),
        "title": row.get("title"),
        "prompt_framing_version": parsed.get("prompt_framing_version"),
        "stale": summary_prompt_framing_is_stale(sj),
    }


def collect_targets(
    *,
    explicit_ids: Sequence[int],
    all_stale: bool,
    scan_limit: int,
    max_process: int,
) -> Tuple[List[int], List[Dict[str, Any]]]:
    """
    Returns (ids_to_process, skipped_or_rejected_rows_for_dry_run_preview).
    """
    meta: List[Dict[str, Any]] = []
    candidates: List[int] = []

    if explicit_ids:
        for iid in explicit_ids:
            row = get_item(iid)
            if not row:
                meta.append({"item_id": iid, "error": "not found"})
                continue
            st = str(row.get("status") or "")
            if st not in ELIGIBLE_STATUSES:
                meta.append(
                    {
                        "item_id": iid,
                        "error": f"status {st!r} not in {sorted(ELIGIBLE_STATUSES)}",
                    }
                )
                continue
            if not summary_prompt_framing_is_stale(row.get("summary_json")):
                meta.append(_row_preview(row) | {"skipped": "framing_current"})
                continue
            candidates.append(iid)
    elif all_stale:
        scanned = list_items_by_statuses(
            list(ELIGIBLE_STATUSES), limit=max(1, int(scan_limit))
        )
        for iid in scanned:
            row = get_item(iid)
            if not row:
                continue
            if str(row.get("status") or "") not in ELIGIBLE_STATUSES:
                continue
            if summary_prompt_framing_is_stale(row.get("summary_json")):
                candidates.append(iid)
    else:
        raise ValueError("internal: neither explicit_ids nor all_stale")

    return candidates[: max(1, int(max_process))], meta


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Re-summarize pending_review/ranked items whose "
            "prompt_framing_version is missing or older than the current constant."
        )
    )
    p.add_argument(
        "--all-stale",
        action="store_true",
        help=f"Scan up to --scan-limit ids in {sorted(ELIGIBLE_STATUSES)}; process stale rows.",
    )
    p.add_argument(
        "--ids",
        type=str,
        default="",
        help="Comma-separated pipeline item ids (must be pending_review or ranked).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print targets only; no OpenAI calls and no DB writes.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Max items to process after filtering (default 25).",
    )
    p.add_argument(
        "--scan-limit",
        type=int,
        default=1000,
        help="Max rows to scan from DB when using --all-stale (ordered by id ASC).",
    )
    args = p.parse_args(argv)

    explicit = _parse_ids_csv(args.ids)
    if not args.all_stale and not explicit:
        p.error("Provide --all-stale or non-empty --ids (not both)")
    if args.all_stale and explicit:
        p.error("Use either --all-stale or --ids, not both")

    targets, preview_meta = collect_targets(
        explicit_ids=explicit if explicit else [],
        all_stale=bool(args.all_stale),
        scan_limit=int(args.scan_limit),
        max_process=int(args.limit),
    )

    payload: Dict[str, Any] = {
        "current_prompt_framing_version": PROMPT_FRAMING_VERSION,
        "dry_run": bool(args.dry_run),
        "targets": targets,
        "preview_notes": preview_meta,
    }

    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    errors: List[Dict[str, Any]] = []
    ok_ids: List[int] = []
    for iid in targets:
        ok, msg = summarize_pipeline_item(iid, force=True)
        if ok:
            ok_ids.append(iid)
        else:
            errors.append({"item_id": iid, "error": msg})

    print(
        json.dumps(
            {
                "ok": not errors,
                "current_prompt_framing_version": PROMPT_FRAMING_VERSION,
                "summarized": ok_ids,
                "errors": errors,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
