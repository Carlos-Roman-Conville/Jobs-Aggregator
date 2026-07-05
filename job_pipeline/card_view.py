"""
Canonical fields for fast pending_review cards (UI + API).
summary_json may contain extra keys from the model; we normalize here.
"""
from typing import Any, Dict, Optional


def card_for_queue_row(summary_json: Any) -> Dict[str, Any]:
    s: Dict[str, Any] = summary_json if isinstance(summary_json, dict) else {}
    return {
        "headline": (s.get("headline_one_line") or s.get("one_line") or "")[:160],
        "verdict": s.get("verdict") or "maybe",
        "company": s.get("company") or "",
        "role": s.get("role") or "",
        "location": s.get("location") or "",
        "salary": s.get("salary") or "not listed",
        "friction": s.get("application_friction") or "",
        "why_match": s.get("why_match") or "",
        "gaps": s.get("gaps") or "",
        "seniority_fit": s.get("seniority_fit") or "",
        "time_to_apply_minutes": s.get("time_to_apply_minutes_estimate"),
        "custom_cover_worth_it": bool(s.get("custom_cover_worth_it", True)),
        "recommended_resume_id": s.get("recommended_resume_id") or "",
        "likely_junk": bool(s.get("likely_junk", False)),
        "junk_reason": s.get("junk_reason") or "",
        "filter_reason": s.get("filter_reason") or "",
    }


def digest_line(item: Dict[str, Any]) -> str:
    c = card_for_queue_row(item.get("summary_json"))
    fit = item.get("fit_score")
    fit_s = f"{float(fit):.0%}" if fit is not None else "—"
    head = c.get("headline") or f"{c.get('role')} @ {c.get('company')}"
    return f"[{fit_s}] {c.get('verdict', '?').upper()} | {head}"
