"""Cover letter post-generation optimization (voice mirroring + anti-fluff)."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from job_pipeline.anti_fluff import strip_anti_fluff_in_text
from job_pipeline.cover_letter_tailor import _normalize_cover_letter_content
from job_pipeline.critique_loop import critique_loop_enabled, run_cover_letter_critique_loop
from job_pipeline.integrity_guards import (
    run_cover_letter_guards,
    run_pre_export_guards,
    strip_phrases_shared_with_resume,
)
from job_pipeline.grammar_proofread import proofread_cover_letter_content
from job_pipeline.jd_analysis import parse_job_description, voice_mirroring_block
from job_pipeline.llm_provider import LLMWritingError, generate_json, writing_providers_available
from job_pipeline.presentation_linter import lint_cover_letter as _lint_cl_presentation


def _opt_full() -> bool:
    return os.getenv("RESUME_OPT_FULL", "").strip().lower() in ("1", "true", "yes")


def optimize_cover_letter_content(
    content: Dict[str, Any],
    *,
    job_description: str,
    profile_text: str,
    job_title: str = "",
    company: str = "",
    resume_content: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Deterministic cleanup + optional LLM voice polish when RESUME_OPT_FULL=1.

    `resume_content` (when passed by the caller) enables cross-document phrase
    dedupe — narrative phrases like "small-shop environment" that appear in the
    resume are dropped from the cover letter so the two documents don't echo
    each other word-for-word.
    """
    if not isinstance(content, dict) or content.get("error"):
        return content

    jd = parse_job_description(job_description)
    notes: List[str] = []

    for field in ("opening", "closing"):
        val = str(content.get(field) or "")
        if val:
            fixed, n = strip_anti_fluff_in_text(val)
            content[field] = fixed
            notes.extend(n)

    bodies = content.get("body_paragraphs")
    if isinstance(bodies, list):
        new_bodies = []
        for p in bodies:
            fixed, n = strip_anti_fluff_in_text(str(p))
            new_bodies.append(fixed)
            notes.extend(n)
        content["body_paragraphs"] = new_bodies

    try:
        content = _normalize_cover_letter_content(
            content,
            job_description=job_description,
            profile_text=profile_text,
        )
    except ValueError:
        pass

    notes.extend(
        run_cover_letter_guards(
            content,
            job_title=job_title,
            company=company,
        )
    )

    # Cross-document phrase dedupe: drop narrative phrases (e.g. "small-shop
    # environment", "high-traffic facility") that already appear in the resume.
    # Tool names / proper nouns / JD keywords are left alone.
    if isinstance(resume_content, dict):
        notes.extend(strip_phrases_shared_with_resume(content, resume_content))

    # Critique-and-revise loop (default ON via RESUME_OPT_CRITIQUE_LOOP=1).
    # ALWAYS runs when enabled — deterministic guards can only catch known
    # leak shapes; the LLM critique is the backstop for novel ones.
    # Cost held in check by gpt-4.1-mini + max 2 iterations + early stop on
    # clean critique.
    if critique_loop_enabled() and writing_providers_available():
        def _post_revise_cl(c: Dict[str, Any]) -> None:
            for field in ("opening", "closing"):
                val = str(c.get(field) or "")
                if val:
                    fixed, _ = strip_anti_fluff_in_text(val)
                    c[field] = fixed
            bodies = c.get("body_paragraphs")
            if isinstance(bodies, list):
                new_bodies = []
                for p in bodies:
                    fixed, _ = strip_anti_fluff_in_text(str(p))
                    new_bodies.append(fixed)
                c["body_paragraphs"] = new_bodies
            run_cover_letter_guards(c, job_title=job_title, company=company)
            if isinstance(resume_content, dict):
                strip_phrases_shared_with_resume(c, resume_content)

        content, critique_reports, critique_notes = run_cover_letter_critique_loop(
            content,
            job_description=job_description,
            profile_text=profile_text,
            job_title=job_title,
            post_revise_hook=_post_revise_cl,
        )
        notes.extend(critique_notes)
        notes.extend(
            run_pre_export_guards(
                content,
                doc_type="cover_letter",
                job_title=job_title,
                company=company,
            )
        )
        content, grammar_notes = proofread_cover_letter_content(
            content,
            profile_text=profile_text,
            job_title=job_title,
            company=company,
        )
        notes.extend(grammar_notes)
    elif _opt_full() and writing_providers_available():
        # Legacy single-shot voice polish — kept for back-compat when the
        # critique loop is explicitly disabled.
        try:
            content = _llm_voice_polish(
                content,
                job_description=job_description,
                profile_text=profile_text,
                job_title=job_title,
                company=company,
                jd=jd,
            )
            notes.append("cover letter: LLM voice polish applied (legacy single-shot)")
        except (LLMWritingError, Exception):
            notes.append("cover letter: LLM voice polish skipped (provider error)")

    # Phase 2B: regression-driven one-shot retry for cover letters.
    # If regression_check finds coherence errors (word-drops, broken
    # sentences) we make ONE LLM revise attempt to fix them before
    # falling through to grammar proofread. Bounded to 1 try to cap
    # cost; if the retry doesn't fix it, the post-build regression_check
    # in service.py will set quality_block=true so the user sees the
    # issue before applying.
    if writing_providers_available():
        try:
            from job_pipeline.regression_check import check_cover_letter_content
            issues_before = check_cover_letter_content(content)
        except Exception:
            issues_before = []
        if issues_before:
            try:
                content = _llm_fix_regression_issues(
                    content,
                    issues=issues_before,
                    job_title=job_title,
                    company=company,
                )
                notes.append(
                    f"cover letter: regression retry on {len(issues_before)} issue(s)"
                )
                # Re-scrub after LLM fix to catch any new issues
                # introduced by the rewrite.
                for field in ("opening", "closing"):
                    val = str(content.get(field) or "")
                    if val:
                        fixed, _ = strip_anti_fluff_in_text(val)
                        content[field] = fixed
                bodies = content.get("body_paragraphs")
                if isinstance(bodies, list):
                    new_bodies = []
                    for p in bodies:
                        fixed, _ = strip_anti_fluff_in_text(str(p))
                        new_bodies.append(fixed)
                    content["body_paragraphs"] = new_bodies
            except (LLMWritingError, Exception) as exc:
                notes.append(
                    f"cover letter: regression retry skipped ({exc})"
                )

    if not (critique_loop_enabled() and writing_providers_available()):
        notes.extend(
            run_pre_export_guards(
                content,
                doc_type="cover_letter",
                job_title=job_title,
                company=company,
            )
        )
        content, grammar_notes = proofread_cover_letter_content(
            content,
            profile_text=profile_text,
            job_title=job_title,
            company=company,
        )
        notes.extend(grammar_notes)

    # ---- Final deterministic PRESENTATION pass (LAST WORD before export) ----
    # Same rationale as the resume side: rule-based cleanup of objective defects
    # (generic openers, groveling closers, informal phrasing, capitalization),
    # run after the LLM critique loop. Defensive; never crashes a build.
    presentation = {"penalty": 0.0, "blocking": []}
    try:
        _pres = _lint_cl_presentation(
            content, company=company, role=job_title, jd_text=job_description
        )
        content = _pres.content
        notes.extend(_pres.notes)
        presentation = {
            "penalty": _pres.penalty,
            "blocking": [f.rule_id for f in _pres.blocking],
        }
    except Exception as exc:  # pragma: no cover - defensive, never break a build
        notes.append(f"presentation linter skipped: {exc}")

    content["_optimization"] = {
        "voice_mirroring": jd,
        "presentation": presentation,
        "notes": notes,
    }
    return content


def _llm_fix_regression_issues(
    content: Dict[str, Any],
    *,
    issues: list,
    job_title: str,
    company: str,
) -> Dict[str, Any]:
    """One-shot LLM revise targeted at specific regression_check issues.

    Used by the cover-letter optimizer to auto-recover from word-drop
    coherence errors and similar patterns that the deterministic
    scrubbers can't fix. Bounded to ONE call per build to cap cost.
    """
    import json as _json
    system = (
        "You revise a cover-letter JSON to fix specific issues flagged by an "
        "automated regression checker. Return exactly one JSON object with the "
        "SAME schema as the input. Fix ONLY the flagged issues — do not rewrite "
        "the letter, do not change the tone, do not add new claims. If an issue "
        "indicates a missing word ('word-drop after X'), fill in the most likely "
        "missing word from context. Add no new factual claims."
    )
    user = (
        f"TARGET: {job_title} at {company}\n\n"
        f"REGRESSION ISSUES TO FIX:\n"
        + "\n".join(f"- {i}" for i in issues)
        + "\n\n"
        f"DRAFT JSON (fix the issues above; keep everything else identical):\n"
        f"{_json.dumps(content, ensure_ascii=False)[:8000]}\n\n"
        "Return keys: proof_targets, opening, body_paragraphs, closing."
    )
    from job_pipeline.cache_prefix import static_writer_cache_prefix
    revised = generate_json(
        "cover_letter",
        system=system,
        user=user,
        label="cl_regression_fix",
        system_cacheable_prefix=static_writer_cache_prefix(),
        claude_max_tokens=2048,
    )
    if not isinstance(revised, dict):
        return content
    for k in ("proof_targets", "opening", "body_paragraphs", "closing"):
        if k in revised:
            content[k] = revised[k]
    return content


def _llm_voice_polish(
    content: Dict[str, Any],
    *,
    job_description: str,
    profile_text: str,
    job_title: str,
    company: str,
    jd: Dict[str, Any],
) -> Dict[str, Any]:
    system = (
        "You revise cover letter JSON for credibility and concision. "
        "Return exactly one valid JSON object. Add NO unsupported claims."
    )
    # PROFILE_TEXT is in the system_cacheable_prefix; don't duplicate.
    user = (
        voice_mirroring_block(jd)
        + "\n\nRevise the DRAFT cover letter: skeptical-recruiter pass — remove exaggeration, "
        "vague AI phrasing, and irrelevant tangents. Keep proof_targets accurate.\n\n"
        f"TARGET: {job_title} at {company}\n\n"
        f"DRAFT JSON:\n{content}\n\n"
        "Return keys: proof_targets, opening, body_paragraphs, closing."
    )
    from job_pipeline.cache_prefix import static_writer_cache_prefix
    # NOTE: only fires when RESUME_OPT_FULL=1 (legacy path); default build
    # skips this call.
    revised = generate_json(
        "cover_letter",
        system=system,
        user=user,
        label="cl_voice_polish",
        system_cacheable_prefix=static_writer_cache_prefix(),
    )
    for k in ("proof_targets", "opening", "body_paragraphs", "closing"):
        if k in revised:
            content[k] = revised[k]
    return content
