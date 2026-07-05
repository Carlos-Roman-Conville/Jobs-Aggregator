"""
Re-apply search_preferences.md scoring to existing pipeline items without calling the LLM.
"""
from __future__ import annotations

# Auto-load .env from the repo root so this CLI works in a plain PowerShell
# session without the user having to export POSTGRES_* / API keys by hand.
# Mirrors the pattern used by job_dashboard.py.
from pathlib import Path as _Path
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import argparse
import json
from typing import Any, Dict, List, Tuple

from job_pipeline.db import get_item, list_items_by_statuses, update_item_preferences_rescore
from job_pipeline.domain_fit import calculate_domain_fit, load_career_profile, merge_blended_with_domain
from job_pipeline.ingest import load_pipeline_config, matching_thresholds, salary_hard_gate
from job_pipeline.location_policy import evaluate_location_policy
from job_pipeline.summarize import (
    _compute_list_rank,
    _quality_bucket,
    _should_auto_filter,
    apply_search_preferences_stage,
)


ELIGIBLE_STATUSES = ["pending_review", "ranked", "approved", "package_ready"]


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


def _ensure_after_domain_then_seniority(
    summary: Dict[str, Any],
    *,
    title: str,
    desc: str,
) -> float:
    """Return cached combined-after-seniority score or rebuild domain merge when missing."""
    cached = summary.get("fit_score_after_domain_then_seniority")
    if cached is not None:
        return float(cached)

    llm = float(summary.get("fit_score_model", 0.5))
    h = float(summary.get("fit_score_heuristic", 0.35))
    base_blended = summary.get("fit_score_blended_base")
    if base_blended is None:
        base_blended = round(0.58 * llm + 0.42 * h, 3)
    else:
        base_blended = float(base_blended)

    profile = load_career_profile()
    domain = calculate_domain_fit(title, desc, profile)
    merged_domain = merge_blended_with_domain(base_blended, domain)

    sen_mult = float(summary.get("seniority_multiplier") or 1.0)
    after_seniority = round(min(1.0, max(0.0, merged_domain * sen_mult)), 3)

    summary["fit_score_blended_base"] = round(base_blended, 3)
    summary["fit_score_mid_domain"] = merged_domain
    summary["fit_score_after_domain_then_seniority"] = after_seniority
    summary["domain_fit"] = {
        "domain_score": domain.get("domain_score"),
        "domain_multiplier": domain.get("domain_multiplier"),
        "matched_families": domain.get("matched_families"),
        "penalized_families": domain.get("penalized_families"),
        "detected_families": domain.get("detected_families"),
        "queue_reason": domain.get("queue_reason"),
        "reasons": domain.get("reasons"),
        "title_avoid_hit": domain.get("title_avoid_hit"),
    }
    return after_seniority


def rescore_single_item(item_id: int, *, dry_run: bool = False) -> Tuple[bool, str]:
    row = get_item(item_id)
    if not row:
        return False, "not found"
    st = str(row.get("status") or "")
    if st not in ELIGIBLE_STATUSES:
        return False, f"status {st} not eligible for preferences rescore"

    cfg = load_pipeline_config()
    summary = _parse_summary(row.get("summary_json"))

    title = str(row.get("title") or "")
    desc = str(row.get("description_text") or "")
    location = str(row.get("location") or "")
    salary = str(row.get("salary_text") or "")
    source = str(row.get("source") or "")

    after_seniority = _ensure_after_domain_then_seniority(summary, title=title, desc=desc)

    loc_action, loc_mult, loc_cls, loc_code = evaluate_location_policy(title, location, desc, cfg)
    loc_reject = loc_action == "reject"
    if loc_reject:
        combined_after_location = after_seniority
    else:
        combined_after_location = round(min(1.0, max(0.0, after_seniority * loc_mult)), 3)

    summary["location_policy"] = {
        "action": loc_action,
        "multiplier": loc_mult,
        "classification": loc_cls,
        "reason_code": loc_code,
        "reject": loc_reject,
    }
    summary["fit_score_after_location"] = combined_after_location

    combined_after_preferences, prefs_card, pref_hard_close, pref_code, fit_raw = (
        apply_search_preferences_stage(
            cfg,
            title=title,
            description_text=desc,
            location=location,
            salary_text=salary,
            source=source,
            combined_after_location=combined_after_location,
            loc_reject=loc_reject,
        )
    )
    combined = combined_after_preferences

    verdict = str(summary.get("verdict") or "maybe").strip().lower()
    if verdict not in ("strong_match", "maybe", "pass"):
        verdict = "maybe"

    likely_junk = bool(summary.get("likely_junk", False))
    list_rank = _compute_list_rank(float(fit_raw), verdict, likely_junk)
    qbucket = _quality_bucket(verdict, likely_junk)

    th = matching_thresholds(cfg)
    auto_close = _should_auto_filter(combined, verdict, likely_junk, th)
    sal_close, sal_reason = salary_hard_gate(row, cfg)
    hard_close = auto_close or sal_close or loc_reject or pref_hard_close

    if pref_hard_close:
        filter_reason = f"search_preferences:{pref_code or 'reject'}"
    elif loc_reject:
        filter_reason = f"location_policy:{loc_code or 'rejected'}"
    elif sal_close:
        filter_reason = sal_reason
    elif auto_close:
        if likely_junk:
            filter_reason = "junk_or_noise"
        elif verdict == "pass" and combined < th["auto_close_pass_verdict_combined_below"]:
            filter_reason = "low_fit_or_pass"
        elif combined < th["auto_close_combined_below"]:
            filter_reason = "low_combined_score"
        else:
            filter_reason = "auto_closed"
    else:
        filter_reason = ""

    summary["search_preferences"] = dict(prefs_card)
    summary["fit_score_blended"] = combined
    summary["fit_score_raw"] = float(fit_raw)
    summary["filter_reason"] = filter_reason
    summary["auto_filtered"] = hard_close
    summary["quality_bucket_rescore_preferences"] = qbucket

    if isinstance(summary.get("score_explanation"), dict):
        se = dict(summary["score_explanation"])
        se["domain_merge"] = (
            "preferences_rescore = after_domain_then_seniority * location_mult "
            "(fresh evaluate_location_policy), then apply_search_preferences_stage"
        )
        se["fit_score_after_domain_then_seniority"] = after_seniority
        se["fit_score_after_location"] = combined_after_location
        se["search_preferences_multiplier"] = prefs_card.get("pref_multiplier")
        se["search_preferences_effective_multiplier"] = prefs_card.get("effective_multiplier_applied")
        se["search_preferences_reject_reason"] = prefs_card.get("auto_close_reason")
        se["fit_score_final"] = combined
        se["fit_score_raw"] = float(fit_raw)
        summary["score_explanation"] = se

    close_for_prefs = pref_hard_close

    preview = {
        "item_id": item_id,
        "fit_score_blended": combined,
        "close_for_preferences": close_for_prefs,
        "filter_reason": filter_reason,
        "search_preferences": prefs_card,
    }

    if dry_run:
        return True, json.dumps(preview, indent=2, ensure_ascii=False)

    ok = update_item_preferences_rescore(
        item_id,
        combined,
        list_rank,
        summary,
        close_for_preferences=close_for_prefs,
    )
    if not ok:
        return False, "db update failed"
    return True, json.dumps(preview, indent=2, ensure_ascii=False)


def rescore_preferences_batch(limit: int = 500, *, dry_run: bool = False) -> Dict[str, Any]:
    ids = list_items_by_statuses(ELIGIBLE_STATUSES, limit=max(1, int(limit)))
    ok_l: List[int] = []
    err_l: List[Dict[str, Any]] = []
    previews: List[str] = []
    for iid in ids:
        ok, msg = rescore_single_item(iid, dry_run=dry_run)
        if ok:
            ok_l.append(iid)
            previews.append(msg)
        else:
            err_l.append({"item_id": iid, "error": msg})
    return {
        "ok": True,
        "rescored": ok_l,
        "errors": err_l,
        "count": len(ok_l),
        "dry_run": dry_run,
        "previews": previews if dry_run else [],
    }


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Re-score queued items using search_preferences.md rules.")
    p.add_argument("--id", type=int, default=0, help="Single pipeline item id.")
    p.add_argument("--dry-run", action="store_true", help="Print planned updates without writing.")
    p.add_argument("--limit", type=int, default=500, help="Max items when rescoring all eligible statuses.")
    args = p.parse_args(argv)

    if args.id:
        ok, msg = rescore_single_item(int(args.id), dry_run=args.dry_run)
        print(msg)
        return 0 if ok else 1

    out = rescore_preferences_batch(limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if not out.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
