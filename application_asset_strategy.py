"""
Resume / cover-letter selection rules from application_assets.json (optional).

Top-level optional key: "asset_strategy" with "job_families".
Each resume may have "suggest_when" (job_families, title_keywords_any, description_keywords_any).
Each template may have "maps_to_job_families", "maps_to_title_keywords_any".

Backward compatible: missing strategy → empty detection, no overrides.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

import json

from application_assets import load_application_assets_dict


def _norm_blob(title: str, desc: str) -> str:
    return f"{title or ''} {desc or ''}".lower()


def detect_job_families(title: str, description: str) -> List[str]:
    assets = load_application_assets_dict()
    strat = assets.get("asset_strategy") or {}
    families = strat.get("job_families") or {}
    if not isinstance(families, dict):
        return []
    blob = _norm_blob(title, description)
    hit: List[str] = []
    for fam_id, spec in families.items():
        if not isinstance(spec, dict):
            continue
        pats = spec.get("title_patterns") or spec.get("match_title_substrings") or []
        if not isinstance(pats, list):
            continue
        for p in pats:
            if p and str(p).lower() in blob:
                hit.append(str(fam_id))
                break
        rx = spec.get("title_regex") or spec.get("match_title_regex")
        if rx and isinstance(rx, str):
            try:
                if re.search(rx, f"{title} {description}", re.I):
                    if str(fam_id) not in hit:
                        hit.append(str(fam_id))
            except re.error:
                continue
    return list(dict.fromkeys(hit))


def _keywords_score(blob: str, kws: Any) -> int:
    if not kws or not isinstance(kws, list):
        return 0
    return sum(1 for k in kws if k and str(k).lower() in blob)


def score_resume_for_posting(
    resume: Dict[str, Any],
    title: str,
    description: str,
    families: List[str],
) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    blob = _norm_blob(title, description)
    sw = resume.get("suggest_when") or {}
    if not isinstance(sw, dict):
        sw = {}
    score = 0.0

    fams = sw.get("job_families") or []
    if isinstance(fams, list) and families:
        overlap = set(str(x) for x in fams) & set(families)
        if overlap:
            score += 3.0 * len(overlap)
            reasons.append(f"job_family:{','.join(overlap)}")

    t_kw = sw.get("title_keywords_any") or sw.get("title_keywords") or []
    d_kw = sw.get("description_keywords_any") or sw.get("description_keywords") or []
    ts = _keywords_score(blob, t_kw)
    ds = _keywords_score(blob, d_kw)
    if ts:
        score += min(2.0, 0.6 * ts)
        reasons.append(f"title_kw:{ts}")
    if ds:
        score += min(2.5, 0.45 * ds)
        reasons.append(f"desc_kw:{ds}")

    meta = resume.get("metadata") or {}
    if isinstance(meta, dict):
        skill_hits = [sk for sk in (meta.get("key_skills") or []) if sk and str(sk).lower() in blob]
        if skill_hits:
            score += 0.35 * min(len(skill_hits), 4)
            reasons.append(f"skills:{len(skill_hits)}")

    return score, reasons


def score_template_for_posting(
    template: Dict[str, Any],
    title: str,
    description: str,
    families: List[str],
) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    blob = _norm_blob(title, description)
    score = 0.0

    mf = template.get("maps_to_job_families") or []
    if isinstance(mf, list) and families:
        overlap = set(str(x) for x in mf) & set(families)
        if overlap:
            score += 3.0 * len(overlap)
            reasons.append(f"family:{','.join(overlap)}")

    tk = template.get("maps_to_title_keywords_any") or template.get("maps_to_keywords") or []
    if isinstance(tk, list):
        n = _keywords_score(blob, tk)
        if n:
            score += min(2.0, 0.55 * n)
            reasons.append(f"tpl_kw:{n}")

    return score, reasons


def suggest_assets_for_posting(title: str, description: str) -> Dict[str, Any]:
    """
    Rule-based primary resume/template. Used to steer the LLM and optional post-override.
    """
    assets = load_application_assets_dict()
    families = detect_job_families(title, description)
    resumes = [r for r in (assets.get("resumes") or []) if isinstance(r, dict) and r.get("id")]
    templates = [t for t in (assets.get("cover_letter_templates") or []) if isinstance(t, dict) and t.get("id")]

    best_r: Optional[str] = None
    best_r_score = -1.0
    r_debug: List[Dict[str, Any]] = []
    for r in resumes:
        sc, rs = score_resume_for_posting(r, title, description, families)
        r_debug.append({"id": r.get("id"), "score": round(sc, 3), "reasons": rs})
        if sc > best_r_score:
            best_r_score = sc
            best_r = str(r.get("id"))

    best_t: Optional[str] = None
    best_t_score = -1.0
    t_debug: List[Dict[str, Any]] = []
    for t in templates:
        sc, rs = score_template_for_posting(t, title, description, families)
        t_debug.append({"id": t.get("id"), "score": round(sc, 3), "reasons": rs})
        if sc > best_t_score:
            best_t_score = sc
            best_t = str(t.get("id"))

    if not best_r and resumes:
        best_r = str(resumes[0].get("id"))
        best_r_score = 0.0
    if not best_t and templates:
        best_t = str(templates[0].get("id"))
        best_t_score = 0.0

    return {
        "detected_job_families": families,
        "primary_resume_id": best_r,
        "primary_resume_score": round(best_r_score, 3),
        "primary_template_id": best_t,
        "primary_template_score": round(best_t_score, 3),
        "resume_rank_debug": sorted(r_debug, key=lambda x: -float(x.get("score") or 0))[:6],
        "template_rank_debug": sorted(t_debug, key=lambda x: -float(x.get("score") or 0))[:6],
    }


def strategy_prompt_block(title: str, description: str) -> str:
    s = suggest_assets_for_posting(title, description)
    lines = [
        "RULE_BASED_ASSET_HINTS (prefer these ids unless the posting clearly fits another resume/template):",
        json.dumps(s, ensure_ascii=False),
    ]
    return "\n".join(lines)


def maybe_override_llm_assets(
    llm_resume_id: str,
    llm_template_id: str,
    title: str,
    description: str,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    If rules strongly favor different assets than the model, swap and record why.
    """
    sug = suggest_assets_for_posting(title, description)
    meta: Dict[str, Any] = {"strategy": sug, "overrode_resume": False, "overrode_template": False}

    pr = sug.get("primary_resume_id") or llm_resume_id
    pt = sug.get("primary_template_id") or llm_template_id
    rs = float(sug.get("primary_resume_score") or 0)
    rdbg = sug.get("resume_rank_debug") or []
    llm_r_score = 0.0
    for row in rdbg:
        if str(row.get("id")) == str(llm_resume_id):
            llm_r_score = float(row.get("score") or 0)
            break

    if rs >= 2.5 and llm_r_score <= 0.5 and pr and pr != llm_resume_id:
        meta["overrode_resume"] = True
        meta["override_resume_reason"] = f"rules_score={rs} vs llm_pick_score={llm_r_score}"
        llm_resume_id = pr

    ts = float(sug.get("primary_template_score") or 0)
    tdbg = sug.get("template_rank_debug") or []
    llm_t_score = 0.0
    for row in tdbg:
        if str(row.get("id")) == str(llm_template_id):
            llm_t_score = float(row.get("score") or 0)
            break

    if ts >= 2.0 and llm_t_score <= 0.25 and pt and pt != llm_template_id:
        meta["overrode_template"] = True
        meta["override_template_reason"] = f"rules_score={ts} vs llm_pick_score={llm_t_score}"
        llm_template_id = pt

    return llm_resume_id, llm_template_id, meta
