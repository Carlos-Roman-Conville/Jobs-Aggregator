"""Dedicated copy-edit LLM pass — grammar and syntax only, no content rewriting."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Tuple

from job_pipeline.critique_loop import critique_loop_enabled
from job_pipeline.integrity_guards import run_pre_export_guards
from job_pipeline.llm_provider import LLMWritingError, generate_json, writing_providers_available


logger = logging.getLogger(__name__)

_RESUME_KEYS = ("summary", "experience", "skills", "projects")
_COVER_LETTER_KEYS = ("opening", "body_paragraphs", "closing", "proof_targets")


def grammar_pass_enabled() -> bool:
    """Default ON when critique loop is on. Disable via RESUME_OPT_GRAMMAR_PASS=0."""
    if not critique_loop_enabled():
        return False
    return os.getenv("RESUME_OPT_GRAMMAR_PASS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _grammar_system() -> str:
    return (
        "You are a copy editor. Fix grammar, missing verbs, sentence fragments, "
        "tense errors, and broken syntax ONLY. Do NOT rewrite for style, add claims, "
        "change facts, or alter employer names, metrics, or tools. "
        "Return exactly one valid JSON object with no markdown."
    )


def _grammar_user_resume(content: Dict[str, Any], *, job_title: str, profile_text: str) -> str:
    return (
        f"Copy-edit this resume JSON for {job_title or 'the target role'}. "
        "Fix grammar/syntax errors only.\n\n"
        f"DRAFT JSON:\n{json.dumps(content, ensure_ascii=False)[:8000]}\n\n"
        f"PROFILE_TEXT (ground truth — do not add facts beyond this):\n"
        f"{(profile_text or '')[:6000]}\n\n"
        "Return keys: summary, experience, skills, projects — same schema as input."
    )


def _grammar_user_cover_letter(content: Dict[str, Any], *, job_title: str, profile_text: str) -> str:
    return (
        f"Copy-edit this cover letter JSON for {job_title or 'the target role'}. "
        "Fix grammar/syntax errors only.\n\n"
        f"DRAFT JSON:\n{json.dumps(content, ensure_ascii=False)[:6000]}\n\n"
        f"PROFILE_TEXT (ground truth — do not add facts beyond this):\n"
        f"{(profile_text or '')[:6000]}\n\n"
        "Return keys: proof_targets, opening, body_paragraphs, closing — same schema."
    )


def _merge_proofread(
    content: Dict[str, Any],
    revised: Dict[str, Any],
    allowed_keys: Tuple[str, ...],
) -> Tuple[Dict[str, Any], bool]:
    merged = dict(content)
    changed = False
    for key in allowed_keys:
        if key in revised and revised[key] is not None:
            if merged.get(key) != revised[key]:
                merged[key] = revised[key]
                changed = True
    return merged, changed


def proofread_resume_content(
    content: Dict[str, Any],
    *,
    profile_text: str = "",
    job_title: str = "",
) -> Tuple[Dict[str, Any], List[str]]:
    """One cheap LLM grammar pass for resume JSON. Returns (content, notes)."""
    notes: List[str] = []
    if not isinstance(content, dict) or content.get("error"):
        return content, notes
    if not grammar_pass_enabled() or not writing_providers_available():
        notes.append("grammar proofread: skipped (disabled or no provider)")
        return content, notes

    try:
        from job_pipeline.cache_prefix import static_writer_cache_prefix
        revised = generate_json(
            "critique",
            system=_grammar_system(),
            user=_grammar_user_resume(content, job_title=job_title, profile_text=profile_text),
            label="resume_grammar_proofread",
            openai_temperature=0.1,
            system_cacheable_prefix=static_writer_cache_prefix(),
        )
    except (LLMWritingError, ValueError, Exception) as exc:
        logger.warning("resume grammar proofread failed: %s", exc)
        notes.append(f"grammar proofread: skipped ({exc})")
        return content, notes

    if not isinstance(revised, dict):
        notes.append("grammar proofread: skipped (invalid response)")
        return content, notes

    merged, changed = _merge_proofread(content, revised, _RESUME_KEYS)
    if changed:
        notes.append("grammar proofread: applied LLM copy-edit fixes")
        notes.extend(
            run_pre_export_guards(merged, doc_type="resume", job_title=job_title)
        )
        return merged, notes

    notes.append("grammar proofread: no changes needed")
    return content, notes


def proofread_cover_letter_content(
    content: Dict[str, Any],
    *,
    profile_text: str = "",
    job_title: str = "",
    company: str = "",
) -> Tuple[Dict[str, Any], List[str]]:
    """One cheap LLM grammar pass for cover letter JSON. Returns (content, notes)."""
    notes: List[str] = []
    if not isinstance(content, dict) or content.get("error"):
        return content, notes
    if not grammar_pass_enabled() or not writing_providers_available():
        notes.append("grammar proofread: skipped (disabled or no provider)")
        return content, notes

    try:
        from job_pipeline.cache_prefix import static_writer_cache_prefix
        revised = generate_json(
            "critique",
            system=_grammar_system(),
            user=_grammar_user_cover_letter(
                content, job_title=job_title, profile_text=profile_text
            ),
            label="cl_grammar_proofread",
            openai_temperature=0.1,
            system_cacheable_prefix=static_writer_cache_prefix(),
        )
    except (LLMWritingError, ValueError, Exception) as exc:
        logger.warning("cover letter grammar proofread failed: %s", exc)
        notes.append(f"grammar proofread: skipped ({exc})")
        return content, notes

    if not isinstance(revised, dict):
        notes.append("grammar proofread: skipped (invalid response)")
        return content, notes

    merged, changed = _merge_proofread(content, revised, _COVER_LETTER_KEYS)
    if changed:
        notes.append("grammar proofread: applied LLM copy-edit fixes")
        notes.extend(
            run_pre_export_guards(
                merged,
                doc_type="cover_letter",
                job_title=job_title,
                company=company,
            )
        )
        return merged, notes

    notes.append("grammar proofread: no changes needed")
    return content, notes
