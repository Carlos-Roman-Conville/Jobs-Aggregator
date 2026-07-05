"""
Build application package metadata and consistency checks.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from application_assets import load_application_assets, resolve_resume_path
from job_pipeline.cover_letter_tailor import curate_summary_card_for_cover_letter
from job_pipeline.genai_settings import gemini_model_for
from job_pipeline.llm_provider import LLMWritingError, generate_json, writing_providers_available


def validate_resume_file(resume_id: str) -> Tuple[bool, str, int]:
    try:
        path = resolve_resume_path(resume_id)
    except Exception as e:
        return False, str(e), 0
    try:
        sz = os.path.getsize(path)
    except OSError as e:
        return False, str(e), 0
    # A resume file is valid if it is non-trivially sized AND has a recognized
    # document extension. (The previous version then unconditionally flipped ok
    # back to True for any file > 80 bytes, which silently defeated the extension
    # guard — so an arbitrary/garbage file would pass.)
    allowed_ext = (".pdf", ".doc", ".docx", ".rtf", ".txt", ".md")
    ok = sz > 80 and path.lower().endswith(allowed_ext)
    return ok, path, sz


def _placeholder_warnings(letter: str) -> List[str]:
    w: List[str] = []
    if re.search(r"\{\{|\[YOUR |YOUR NAME|TBD\]", letter, re.I):
        w.append("Cover letter may still contain placeholders — edit before sending.")
    return w


def extract_resume_bullets_from_content(content: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(content, dict):
        return []
    bullets: List[str] = []
    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            for b in exp.get("bullets") or []:
                t = str(b).strip()
                if t:
                    bullets.append(t)
    summary = str(content.get("summary") or "").strip()
    if summary:
        bullets.insert(0, summary)
    return bullets[:40]


def _has_tailored_resume_artifact(artifacts: Dict[str, Any]) -> bool:
    """True when build already produced a usable resume file (skip static resume_id check)."""
    for key in ("resume_pdf", "resume_file", "resume_md"):
        path = artifacts.get(key)
        if path and os.path.isfile(str(path)):
            return True
    return False


def consistency_check_llm(
    letter: str,
    job_title: str,
    company: str,
    resume_skills: List[str],
    *,
    resume_bullets: Optional[List[str]] = None,
    summary_card: Optional[Dict[str, Any]] = None,
) -> List[str]:
    if not writing_providers_available() or os.getenv("JOB_PIPELINE_SKIP_PACKAGE_LLM_CHECK", "").strip().lower() in ("1", "true", "yes"):
        return []
    model = gemini_model_for("package_check")
    card = curate_summary_card_for_cover_letter(summary_card or {})
    bullets = [str(b).strip() for b in (resume_bullets or []) if str(b).strip()][:25]
    system = (
        "You validate a job cover letter against resume bullets and a curated summary card. "
        "Return exactly one valid JSON object."
    )
    user = (
        "Output JSON ONLY: {\"warnings\": [\"short string\", ...]} max 5 warnings. "
        "Flag clear issues: invented credentials, wrong company name, claims in the letter "
        "that contradict RESUME_BULLETS, skills listed under summary gaps that the letter "
        "claims anyway, or serious tone problems. If nothing serious, warnings=[].\n\n"
        f"JOB: {job_title} at {company}\n"
        f"CURATED_SUMMARY_CARD: {json.dumps(card, ensure_ascii=False)}\n"
        f"SKILLS_FROM_RESUME_METADATA: {resume_skills[:40]}\n"
        f"RESUME_BULLETS: {json.dumps(bullets, ensure_ascii=False)}\n\n"
        f"LETTER:\n{letter[:4500]}"
    )
    try:
        data = generate_json(
            "package_check",
            system=system,
            user=user,
            label="package_check",
            gemini_model=model,
            gemini_max_output_tokens=2048,
        )
        arr = data.get("warnings") if isinstance(data, dict) else None
        if not isinstance(arr, list):
            return []
        return [str(x)[:200] for x in arr[:5] if x]
    except (LLMWritingError, Exception):
        return []


def build_package_metadata(
    resume_id: str,
    template_id: str,
    letter: str,
    job_title: str,
    company: str,
    *,
    mode: str = "both",
    artifacts: Optional[Dict[str, Any]] = None,
    resume_bullets: Optional[List[str]] = None,
    summary_card: Optional[Dict[str, Any]] = None,
    skip_llm_check: bool = False,
) -> Dict[str, Any]:
    ok, path_or_err, sz = validate_resume_file(resume_id)
    warnings: List[str] = []
    art = dict(artifacts or {})
    for w in art.get("warnings") or []:
        if w and w not in warnings:
            warnings.append(str(w))

    if not ok and mode != "cover_letter_only" and not _has_tailored_resume_artifact(art):
        warnings.append(f"Resume file issue: {path_or_err}")
    if letter:
        warnings.extend(_placeholder_warnings(letter))
        if len(letter.strip()) < 180:
            warnings.append("Cover letter is very short — consider expanding.")

    skills: List[str] = []
    try:
        assets = json.loads(load_application_assets())
        for r in assets.get("resumes") or []:
            if isinstance(r, dict) and str(r.get("id")) == str(resume_id):
                meta = r.get("metadata") or {}
                skills = list(meta.get("key_skills") or [])
                break
    except Exception:
        pass

    if letter and not skip_llm_check:
        llm_w = consistency_check_llm(
            letter,
            job_title,
            company,
            skills,
            resume_bullets=resume_bullets,
            summary_card=summary_card,
        )
        for x in llm_w:
            if x and x not in warnings:
                warnings.append(f"Review: {x}")

    meta: Dict[str, Any] = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "resume_id": resume_id,
        "template_id": template_id,
        "resume_file_ok": ok,
        "resume_path": path_or_err if ok else None,
        "resume_bytes": sz if ok else 0,
        "cover_letter_chars": len(letter or ""),
        "warnings": warnings,
    }
    for key in ("resume_pdf", "resume_file", "cover_pdf", "cover_letter_md", "resume_md"):
        val = art.get(key)
        if val:
            meta[key] = val
    return meta
