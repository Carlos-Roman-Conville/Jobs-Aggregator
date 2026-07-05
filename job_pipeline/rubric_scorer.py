"""Resume quality rubric (0-100) for pre-export gating."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from job_pipeline.anti_fluff import red_flag_report
from job_pipeline.named_requirements import extract_requirements
from job_pipeline.truth_classifier import ADJACENT, DIRECT, LEARNABLE, NOT_TRUE

# Weights from brief
_W_COVERAGE = 25
_W_PROOF = 25
_W_READABILITY = 15
_W_IMPACT = 10
_W_NO_FLUFF = 10
_W_ALIGNMENT = 10
_W_PARSER = 5


def _score_coverage(content: Dict[str, Any], must_haves: List[str]) -> float:
    if not must_haves:
        return float(_W_COVERAGE)
    blob = json.dumps(content, ensure_ascii=False).lower()
    hits = sum(1 for m in must_haves if m.lower() in blob)
    ratio = hits / max(1, len(must_haves))
    return round(_W_COVERAGE * min(1.0, ratio + 0.15), 1)


def _score_proof(classifications: List[Dict[str, Any]]) -> float:
    if not classifications:
        return float(_W_PROOF) * 0.7
    direct = sum(1 for c in classifications if c.get("level") == DIRECT)
    adjacent = sum(1 for c in classifications if c.get("level") == ADJACENT)
    learnable = sum(1 for c in classifications if c.get("level") == LEARNABLE)
    not_true = sum(1 for c in classifications if c.get("level") == NOT_TRUE)
    n = len(classifications)
    raw = (direct * 1.0 + adjacent * 0.75 + learnable * 0.4) / max(1, n)
    penalty = min(0.3, not_true * 0.08)
    return round(_W_PROOF * max(0, raw - penalty), 1)


def _score_readability(content: Dict[str, Any]) -> float:
    score = _W_READABILITY
    summary = str(content.get("summary") or "")
    if len(summary) > 520:
        score -= 3
    exps = content.get("experience") if isinstance(content.get("experience"), list) else []
    for exp in exps[:2]:
        bullets = exp.get("bullets") if isinstance(exp.get("bullets"), list) else []
        if len(bullets) > 7:
            score -= 2
        for b in bullets:
            if len(str(b)) > 240:
                score -= 1
    return max(0, round(score, 1))


def _score_impact(content: Dict[str, Any]) -> float:
    blob = json.dumps(content, ensure_ascii=False).lower()
    metric_patterns = (
        r"\d+\s*%",
        r"\d+\s*\+",
        r"reduced",
        r"increased",
        r"from \d",
        r"\d+\s*min",
    )
    hits = sum(1 for p in metric_patterns if re.search(p, blob))
    if hits >= 3:
        return float(_W_IMPACT)
    if hits >= 1:
        return round(_W_IMPACT * 0.7, 1)
    return round(_W_IMPACT * 0.3, 1)


def _score_no_fluff(content: Dict[str, Any], job_description: str, profile_text: str) -> float:
    flags = red_flag_report(content, job_description, profile_text)
    penalty = min(_W_NO_FLUFF, len(flags) * 2.5)
    return round(max(0, _W_NO_FLUFF - penalty), 1)


def _score_alignment(content: Dict[str, Any], thesis: str, job_title: str) -> float:
    score = _W_ALIGNMENT * 0.5
    summary = str(content.get("summary") or "").lower()
    title_l = (job_title or "").lower()
    if title_l and title_l.split()[0] in summary:
        score += _W_ALIGNMENT * 0.25
    if thesis and any(w in summary for w in thesis.lower().split()[:6] if len(w) > 4):
        score += _W_ALIGNMENT * 0.25
    return round(min(_W_ALIGNMENT, score), 1)


def _score_parser(content: Dict[str, Any], parser_notes: List[str]) -> float:
    score = float(_W_PARSER)
    exps = content.get("experience") if isinstance(content.get("experience"), list) else []
    for exp in exps:
        if not str(exp.get("company") or "").strip():
            score -= 1
        if not str(exp.get("title") or "").strip():
            score -= 1
    score -= min(2, len(parser_notes))
    return max(0, round(score, 1))


def score_resume_rubric(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str,
    *,
    classifications: Optional[List[Dict[str, Any]]] = None,
    jd_analysis: Optional[Dict[str, Any]] = None,
    thesis: str = "",
    job_title: str = "",
    parser_notes: Optional[List[str]] = None,
    validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return {total, breakdown, pass} with component scores."""
    jd_analysis = jd_analysis or {}
    must_haves = jd_analysis.get("must_haves") or list(extract_requirements(job_description))[:10]
    classifications = classifications or []

    breakdown = {
        "coverage": _score_coverage(content, must_haves),
        "proof": _score_proof(classifications),
        "readability": _score_readability(content),
        "quantified_impact": _score_impact(content),
        "no_fluff_no_overclaim": _score_no_fluff(content, job_description, profile_text),
        "company_alignment": _score_alignment(content, thesis, job_title),
        "formatting_parser": _score_parser(content, parser_notes or []),
    }
    total = round(sum(breakdown.values()), 1)
    issues = (validation or {}).get("issues") or []
    if issues:
        total = max(0, total - min(8, len(issues) * 0.5))

    return {
        "total": total,
        "breakdown": breakdown,
        "max": 100,
    }
