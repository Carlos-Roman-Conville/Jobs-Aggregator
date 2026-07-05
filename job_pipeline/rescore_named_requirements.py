"""Re-apply named-requirement gap penalty to existing pipeline items.

Issue: the base fit_score (LLM + heuristic + domain + seniority + location +
search prefs) does NOT factor in named-requirement gaps from the JD. A role
that asks for AD, MacOS, Ticketing/ITSM, VPN, and PST coverage where the
candidate has none of those should rank below a role with fewer such gaps —
even if title and tech keyword overlap are similar.

This module multiplies fit_score_blended by the named-requirement gap
multiplier computed from career_master honest limits + JD-detected named
requirements. Idempotent: prior multiplier is reversed before re-applying.

CLI:
    python -m job_pipeline.rescore_named_requirements             # all pending_review
    python -m job_pipeline.rescore_named_requirements --item N    # single item
    python -m job_pipeline.rescore_named_requirements --limit 50  # cap batch
    python -m job_pipeline.rescore_named_requirements --dry-run   # print, no DB write
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Tuple

from job_pipeline.db import (
    get_item,
    list_items_by_statuses,
    update_item_fit_domain_rescore,
)
from job_pipeline.named_requirements import compute_named_requirement_gap_multiplier
from job_pipeline.resume_tailor import _load_grounded_profile_text
from job_pipeline.summarize import _compute_list_rank, _quality_bucket


def _parse_summary(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            o = json.loads(raw)
            return o if isinstance(o, dict) else {}
        except Exception:
            return {}
    return {}


def rescore_single_item(
    item_id: int,
    *,
    profile_text: str = "",
    dry_run: bool = False,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Recompute fit_score_blended with named-requirement gap penalty.

    Returns (ok, message, detail).
    """
    row = get_item(item_id)
    if not row:
        return False, "not found", {}
    st = str(row.get("status") or "")
    if st not in ("pending_review", "ranked", "approved", "package_ready"):
        return False, f"status {st} not eligible for named-req rescore", {}

    summary = _parse_summary(row.get("summary_json"))
    title = str(row.get("title") or "")
    desc = str(row.get("description_text") or "")

    if not profile_text:
        profile_text = _load_grounded_profile_text()
    if not profile_text:
        return False, "no profile_text available", {}

    # Reverse any prior named-req multiplier so this is idempotent.
    prior_mult = float(summary.get("named_req_gap_multiplier", 1.0) or 1.0)
    if prior_mult <= 0:
        prior_mult = 1.0

    base_score = float(summary.get("fit_score_blended", 0.5) or 0.5)
    # Unwind prior penalty: pre_penalty = base_score / prior_mult.
    # If prior_mult == 1.0, pre_penalty == base_score (no prior penalty applied).
    pre_penalty = base_score / prior_mult if prior_mult > 0 else base_score
    pre_penalty = max(0.0, min(1.5, pre_penalty))  # safety clamp

    multiplier, detail = compute_named_requirement_gap_multiplier(
        desc, profile_text=profile_text
    )

    new_score = round(min(1.0, max(0.0, pre_penalty * multiplier)), 4)

    # Recompute list_rank from new score + existing verdict / junk flag.
    verdict = str(summary.get("verdict") or "maybe").strip().lower()
    if verdict not in ("strong_match", "maybe", "pass"):
        verdict = "maybe"
    likely_junk = bool(summary.get("likely_junk", False))
    new_list_rank = _compute_list_rank(new_score, verdict, likely_junk)
    new_qbucket = _quality_bucket(verdict, likely_junk)

    detail.update({
        "prior_multiplier": prior_mult,
        "base_score_before_unwind": base_score,
        "pre_penalty_score": round(pre_penalty, 4),
        "new_multiplier": multiplier,
        "new_score": new_score,
        "delta": round(new_score - base_score, 4),
        "new_list_rank": new_list_rank,
    })

    if dry_run:
        return True, "dry-run", detail

    # Persist on the card so the next rescore can unwind it.
    summary["named_req_gap_multiplier"] = multiplier
    summary["named_req_gap_detail"] = detail
    summary["fit_score_blended"] = new_score

    if isinstance(summary.get("score_explanation"), dict):
        se = dict(summary["score_explanation"])
        se["named_req_gap_multiplier"] = multiplier
        se["fit_score_after_named_req"] = new_score
        se["named_req_gap_count"] = detail.get("total_gap_count")
        summary["score_explanation"] = se

    ok = update_item_fit_domain_rescore(item_id, new_score, new_list_rank, summary)
    if not ok:
        return False, "db update failed", detail
    return True, "ok", detail


def rescore_pending_review_batch(
    limit: int = 200,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Iterate pending_review items and re-apply the named-req gap penalty."""
    profile_text = _load_grounded_profile_text()
    if not profile_text:
        return {"ok": False, "error": "no profile_text available"}

    ids = list_items_by_statuses(["pending_review"], limit=max(1, int(limit)))
    ok_l: List[Dict[str, Any]] = []
    err_l: List[Dict[str, Any]] = []
    for iid in ids:
        ok, msg, det = rescore_single_item(
            iid, profile_text=profile_text, dry_run=dry_run
        )
        if ok:
            ok_l.append({"item_id": iid, "detail": det})
        else:
            err_l.append({"item_id": iid, "error": msg})
    return {
        "ok": True,
        "rescored": ok_l,
        "errors": err_l,
        "count": len(ok_l),
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply named-requirement gap penalty to pipeline items."
    )
    parser.add_argument(
        "--item", type=int, default=None, help="Single item id to rescore"
    )
    parser.add_argument(
        "--limit", type=int, default=200, help="Max items in batch mode (default: 200)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print results without writing to DB"
    )
    args = parser.parse_args()

    if args.item is not None:
        ok, msg, det = rescore_single_item(args.item, dry_run=args.dry_run)
        print(json.dumps({"ok": ok, "msg": msg, "item_id": args.item, "detail": det},
                         indent=2, ensure_ascii=False))
        return

    result = rescore_pending_review_batch(limit=args.limit, dry_run=args.dry_run)
    print(f"Rescored {result['count']} item(s) ({'dry-run' if args.dry_run else 'persisted'})")
    if result.get("errors"):
        print(f"Errors: {len(result['errors'])}")
        for e in result["errors"][:10]:
            print(f"  - {e}")
    # Show top deltas
    deltas = [
        (r["item_id"], r["detail"].get("delta", 0), r["detail"].get("new_score"),
         r["detail"].get("total_gap_count", 0))
        for r in result.get("rescored") or []
    ]
    deltas.sort(key=lambda x: x[1])  # biggest negative deltas first
    print()
    print("Top 10 score drops (item_id, delta, new_score, gap_count):")
    for d in deltas[:10]:
        print(f"  item {d[0]:>5}  delta {d[1]:+.4f}  new_score {d[2]:.4f}  gaps {d[3]}")


if __name__ == "__main__":
    main()
