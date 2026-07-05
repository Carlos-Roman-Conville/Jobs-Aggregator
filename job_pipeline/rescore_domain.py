"""
Re-apply domain fit to existing pipeline items without calling Gemini again.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from job_pipeline.db import get_item, list_items_by_statuses, update_item_fit_domain_rescore
from job_pipeline.domain_fit import calculate_domain_fit, load_career_profile, merge_blended_with_domain
from job_pipeline.summarize import _compute_list_rank, _quality_bucket, _reconcile_verdict_with_scores


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


def rescore_single_item(item_id: int) -> Tuple[bool, str]:
    row = get_item(item_id)
    if not row:
        return False, "not found"
    st = str(row.get("status") or "")
    if st not in ("pending_review", "ranked", "approved", "package_ready"):
        return False, f"status {st} not eligible for domain rescore"

    summary = _parse_summary(row.get("summary_json"))
    title = str(row.get("title") or "")
    desc = str(row.get("description_text") or "")

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
    lp = summary.get("location_policy") if isinstance(summary.get("location_policy"), dict) else {}
    loc_mult = float(lp.get("multiplier") or 1.0) if not lp.get("reject") else 1.0
    after_location = round(min(1.0, max(0.0, after_seniority * loc_mult)), 3)

    # Search-preferences multiplier — was MISSING from this rescore, so primary-target
    # jobs silently lost their boost vs. a fresh summarize. Re-apply to match the live chain.
    sp = summary.get("search_preferences") if isinstance(summary.get("search_preferences"), dict) else {}
    pref_mult = 1.0 if sp.get("reject") else float(
        sp.get("effective_multiplier_applied") or sp.get("pref_multiplier") or 1.0
    )
    fit_raw = after_location * pref_mult  # unclamped (can exceed 1.0 with a boost), matches summarize
    merged = round(min(1.0, max(0.0, fit_raw)), 3)

    # Re-reconcile the verdict from the RAW LLM verdict against the CORRECTED score.
    # Existing rows carried a verdict stamped when the (now-removed) domain guard had
    # crushed the score, which falsely downgraded good fits to "pass". Recompute it.
    raw_verdict = str(summary.get("verdict_llm") or summary.get("verdict") or "maybe").strip().lower()
    if raw_verdict not in ("strong_match", "maybe", "pass"):
        raw_verdict = "maybe"
    ao = summary.get("ats_overlap") if isinstance(summary.get("ats_overlap"), dict) else {}
    ats_score = float(ao.get("ats_score") or 0.0)
    verdict, _verdict_reason = _reconcile_verdict_with_scores(raw_verdict, merged, ats_score)
    likely_junk = bool(summary.get("likely_junk", False))

    list_rank = _compute_list_rank(fit_raw, verdict, likely_junk)
    qbucket = _quality_bucket(verdict, likely_junk)

    summary["verdict"] = verdict
    summary["fit_score_blended_base"] = round(base_blended, 3)
    summary["fit_score_mid_domain"] = merged_domain
    summary["fit_score_after_domain_then_seniority"] = after_seniority
    summary["fit_score_blended"] = merged
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
    summary["quality_bucket_rescore"] = qbucket

    if isinstance(summary.get("score_explanation"), dict):
        se = dict(summary["score_explanation"])
        se["domain_merge"] = "combined = blended_base * domain_mult * seniority_mult * location_mult (cached knobs)"
        se["fit_score_blended_base"] = round(base_blended, 3)
        se["fit_score_after_domain_only"] = merged_domain
        se["fit_score_after_domain_then_seniority"] = after_seniority
        se["fit_score_final"] = merged
        se["domain_score"] = domain.get("domain_score")
        se["domain_multiplier"] = domain.get("domain_multiplier")
        summary["score_explanation"] = se

    ok = update_item_fit_domain_rescore(item_id, merged, list_rank, summary)
    if not ok:
        return False, "db update failed"
    return True, "ok"


def rescore_pending_review_batch(limit: int = 200) -> Dict[str, Any]:
    ids = list_items_by_statuses(["pending_review"], limit=max(1, int(limit)))
    ok_l: List[int] = []
    err_l: List[Dict[str, Any]] = []
    for iid in ids:
        ok, msg = rescore_single_item(iid)
        if ok:
            ok_l.append(iid)
        else:
            err_l.append({"item_id": iid, "error": msg})
    return {"ok": True, "rescored": ok_l, "errors": err_l, "count": len(ok_l)}
