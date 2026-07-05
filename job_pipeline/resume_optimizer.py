"""
Controlled resume optimization pipeline — evidence-grounded gates before PDF export.

Toggle via env:
  RESUME_OPT_ENABLED=1 (default) — deterministic optimization passes
  RESUME_OPT_FULL=1 — legacy LLM recruiter + ATS single-shot passes (only used
    when RESUME_OPT_CRITIQUE_LOOP=0 disables the new critique loop).
  RESUME_OPT_CRITIQUE_LOOP=1 (default) — adaptive up-to-2-iteration critique-and-revise
    loop driven by an explicit rubric and skeptical-recruiter persona. Subsumes
    the legacy single-shot LLM passes.
  RESUME_OPT_MIN_SCORE=90 — rubric gate threshold (warn if below; never blocks export by default)
  RESUME_OPT_MAX_REVISIONS=2 — extra targeted revise attempts when gate fails
  RESUME_OPT_GATE_BLOCK=0 — set 1 to mark gate_blocked when score/block findings fail after max revisions
  RESUME_OPT_PKG_JUDGE=0 — optional holistic resume+CL judge after both docs build (slow; resume gate judge stays on)
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from job_pipeline.anti_fluff import red_flag_report, strip_anti_fluff_content
from job_pipeline.critique_loop import critique_loop_enabled, run_resume_critique_loop
from job_pipeline.evidence_db import (
    apply_parser_safe_experience,
    evidence_prompt_block,
    match_employer_key,
    metric_display_for_employer,
    metrics_for_employer,
)
from job_pipeline.integrity_guards import run_integrity_guards, run_pre_export_guards
from job_pipeline.grammar_proofread import proofread_resume_content
from job_pipeline.quality_judge import judge_enabled, judge_quality, opt_judge_min
from job_pipeline.jd_analysis import build_role_thesis, parse_job_description
from job_pipeline.llm_provider import LLMWritingError, generate_json, writing_providers_available
from job_pipeline.named_requirements import curate_technical_skills
from job_pipeline.rubric_scorer import score_resume_rubric
from job_pipeline.presentation_linter import lint_resume as _lint_resume_presentation
from job_pipeline.truth_classifier import (
    ADJACENT,
    DIRECT,
    LEARNABLE,
    classify_jd_requirements,
    merge_assessment_with_classifications,
)

logger = logging.getLogger(__name__)

_SOFT_SKILLS_CAP_MIN = 8
_SOFT_SKILLS_CAP_MAX = 12
_SCRIPTING_JD_HINTS = ("python", "script", "automation", "api", "powershell", "bash")
_DEV_TOOL_DROP = ("docker", "git", "kubernetes", "terraform")


def opt_enabled() -> bool:
    return os.getenv("RESUME_OPT_ENABLED", "1").strip().lower() not in ("0", "false", "no")


def opt_full() -> bool:
    return os.getenv("RESUME_OPT_FULL", "").strip().lower() in ("1", "true", "yes")


def opt_min_score() -> int:
    raw = (os.getenv("RESUME_OPT_MIN_SCORE") or "90").strip()
    try:
        return max(0, min(100, int(raw)))
    except ValueError:
        return 90


def opt_max_revisions() -> int:
    raw = (os.getenv("RESUME_OPT_MAX_REVISIONS") or "2").strip()
    try:
        return max(0, min(5, int(raw)))
    except ValueError:
        return 2


def gate_block_enabled() -> bool:
    return os.getenv("RESUME_OPT_GATE_BLOCK", "0").strip().lower() in ("1", "true", "yes")


def _apply_presentation_pass(
    working: Dict[str, Any],
    score: Dict[str, Any],
    *,
    job_title: str,
    job_description: str,
    opt_notes: List[str],
    revalidate_fn,
    validation: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Any], Optional[Dict[str, Any]]]:
    presentation_findings: List[Any] = []
    try:
        _pres = _lint_resume_presentation(working, job_title=job_title, jd_text=job_description)
        working = _pres.content
        presentation_findings = _pres.findings
        opt_notes.extend(_pres.notes)
        if _pres.penalty:
            score = dict(score)
            score["total"] = max(0.0, round(score["total"] - _pres.penalty, 1))
            score["presentation_penalty"] = _pres.penalty
        if revalidate_fn and _pres.notes:
            validation = revalidate_fn(working)
    except Exception as exc:  # pragma: no cover
        opt_notes.append(f"presentation linter skipped: {exc}")
    return working, score, presentation_findings, validation


def _targeted_gate_revise(
    content: Dict[str, Any],
    *,
    job_description: str,
    profile_text: str,
    job_title: str,
    presentation_findings: List[Any],
    score: Dict[str, Any],
    judge_critique: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """One targeted LLM revise when rubric/presentation/judge gate fails."""
    if not writing_providers_available():
        return None
    issues = [
        getattr(f, "as_note", lambda: str(f))()
        for f in (presentation_findings or [])[:12]
    ]
    for line in (judge_critique or [])[:8]:
        issues.append(f"judge: {line}")
    breakdown = score.get("breakdown") or {}
    weak = [k for k, v in breakdown.items() if isinstance(v, (int, float)) and v < 12]
    system = (
        "You revise resume JSON to fix presentation and quality gate failures. "
        "Apply ONLY the listed fixes. Do NOT invent employers, metrics, or tools. "
        "Return exactly one valid JSON object."
    )
    user = (
        f"TARGET ROLE: {job_title}\n\n"
        f"PRESENTATION / QUALITY ISSUES TO FIX:\n"
        + "\n".join(f"- {i}" for i in issues)
        + "\n\n"
        f"LOW RUBRIC DIMENSIONS: {', '.join(weak) or 'none'}\n\n"
        f"DRAFT JSON:\n{json.dumps(content, ensure_ascii=False)[:8000]}\n\n"
        f"PROFILE_TEXT:\n{profile_text[:6000]}\n\n"
        f"JD (context):\n{job_description[:3000]}\n\n"
        "Return keys: summary, experience, skills, projects."
    )
    try:
        from job_pipeline.cache_prefix import static_writer_cache_prefix
        revised = generate_json(
            "critique",
            system=system,
            user=user,
            label="gate_targeted_revise",
            system_cacheable_prefix=static_writer_cache_prefix(),
        )
    except (LLMWritingError, Exception):
        return None
    if not isinstance(revised, dict):
        return None
    merged = dict(content)
    for k in ("summary", "experience", "skills", "projects"):
        if k in revised:
            merged[k] = revised[k]
    return merged


def _jd_wants_scripting(job_description: str) -> bool:
    jd = (job_description or "").lower()
    return any(h in jd for h in _SCRIPTING_JD_HINTS)


def compress_skills_extended(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str,
) -> List[str]:
    """Extend R2.1: technical 18-24, soft 8-12; drop dev tools unless JD-relevant."""
    notes: List[str] = []
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return notes

    tech, tnotes = curate_technical_skills(content, job_description, profile_text, cap=22)
    notes.extend(tnotes)
    sk["technical"] = tech

    soft = [str(s).strip() for s in (sk.get("soft") or []) if str(s).strip()]
    if len(soft) > _SOFT_SKILLS_CAP_MAX:
        soft = soft[:_SOFT_SKILLS_CAP_MAX]
        notes.append(f"compressed soft skills to {_SOFT_SKILLS_CAP_MAX}")

    if not _jd_wants_scripting(job_description):
        tech_filtered = []
        for item in sk.get("technical") or []:
            il = item.lower()
            if any(d in il for d in _DEV_TOOL_DROP) and "python" not in il:
                notes.append(f"dropped dev tool (JD not scripting-focused): {item}")
                continue
            tech_filtered.append(item)
        sk["technical"] = tech_filtered

    sk["soft"] = soft
    return notes


def _bullet_tags(text: str) -> set:
    t = text.lower()
    tags: set = set()
    if any(w in t for w in ("troubleshoot", "resolve", "support", "ticket", "incident")):
        tags.add("support")
    if any(w in t for w in ("windows", "linux", "m365", "microsoft", "rustdesk", "hardware", "network")):
        tags.add("tools")
    if any(w in t for w in ("sop", "runbook", "document", "onboarding", "procedure")):
        tags.add("documentation")
    if re.search(r"\d+\s*%|\d+\s*\+|reduced|increased", t):
        tags.add("impact")
    if any(w in t for w in ("led", "supervis", "coordinat", "train", "communicat")):
        tags.add("leadership")
    return tags


def enforce_bullet_balance(content: Dict[str, Any], max_per_role: int = 6) -> List[str]:
    """Prefer a balanced mix of bullet types for the primary experience entry."""
    notes: List[str] = []
    exps = content.get("experience")
    if not isinstance(exps, list) or not exps:
        return notes

    for idx, exp in enumerate(exps[:2]):
        if not isinstance(exp, dict):
            continue
        bullets = [str(b).strip() for b in (exp.get("bullets") or []) if str(b).strip()]
        if len(bullets) <= max_per_role:
            continue

        scored: List[Tuple[float, str, set]] = []
        for b in bullets:
            tags = _bullet_tags(b)
            priority = 0.0
            if "support" in tags:
                priority += 2
            if "tools" in tags:
                priority += 1.5
            if "documentation" in tags:
                priority += 1.5
            if "impact" in tags:
                priority += 2
            if "leadership" in tags:
                priority += 1
            if idx == 0:
                priority += 0.1
            scored.append((priority, b, tags))

        scored.sort(key=lambda x: -x[0])
        kept: List[str] = []
        type_counts = {"support": 0, "tools": 0, "documentation": 0, "impact": 0, "leadership": 0}
        for _, b, tags in scored:
            if len(kept) >= max_per_role:
                break
            if "impact" in tags and type_counts["impact"] >= 2:
                continue
            kept.append(b)
            for tg in tags:
                if tg in type_counts:
                    type_counts[tg] += 1

        if len(kept) < len(bullets):
            exp["bullets"] = kept
            notes.append(f"bullet balance: role {idx + 1} curated {len(bullets)} -> {len(kept)}")
    return notes


def inject_metric_bank(content: Dict[str, Any], max_per_role: int = 3) -> List[str]:
    """Ensure best evidence-backed metrics appear in experience bullets."""
    notes: List[str] = []
    exps = content.get("experience")
    if not isinstance(exps, list):
        return notes

    for exp in exps:
        if not isinstance(exp, dict):
            continue
        company = str(exp.get("company") or "")
        key = match_employer_key(company)
        if not key:
            # Truthfulness gate: never inject a metric into an employer we don't have
            # evidence for — that's how the 75% wait-time win ended up under a hotel job.
            notes.append(f"skipped metric injection — no evidence for employer: {company}")
            continue
        metrics = metric_display_for_employer(key)
        if not metrics:
            continue
        bullets = [str(b) for b in (exp.get("bullets") or [])]
        blob = " ".join(bullets).lower()
        added = 0
        for metric in metrics:
            if added >= max_per_role:
                break
            # skip if numeric core already present
            nums = re.findall(r"\d+", metric)
            if nums and nums[0] in blob:
                continue
            if metric.lower() in blob:
                continue
            # craft bullet from metric
            bullets.append(metric[0].upper() + metric[1:] if metric else metric)
            added += 1
            notes.append(f"injected metric for {company}: {metric[:50]}")
        if added:
            exp["bullets"] = bullets
    return notes


def apply_role_thesis(content: Dict[str, Any], thesis: str) -> List[str]:
    """Record the controlling thesis as sidecar metadata for downstream scoring.

    We intentionally do NOT prepend it to the existing summary — the heuristic
    template can produce ungrammatical comma-salad when JD culture/tech lists
    are abstract noun-fragments, and the LLM-generated summary already carries
    JD alignment via the named-requirement gates. If the summary is empty,
    seed it with the thesis as a safe minimum.
    """
    notes: List[str] = []
    if not thesis or content.get("error"):
        return notes
    summary = str(content.get("summary") or "").strip()
    if not summary:
        content["summary"] = thesis
        notes.append("set summary from role thesis (was empty)")
    content["_role_thesis"] = thesis
    return notes


def _llm_recruiter_pass(
    content: Dict[str, Any],
    *,
    job_description: str,
    profile_text: str,
    job_title: str,
    thesis: str,
) -> Dict[str, Any]:
    system = (
        "Skeptical recruiter review. Revise resume JSON for credibility and concision. "
        "Remove exaggeration, vague AI phrasing, and irrelevant content. "
        "Add NO unsupported claims. Return exactly one JSON object."
    )
    # PROFILE_TEXT + evidence are now in the system_cacheable_prefix; do not
    # duplicate them here or you double the input tokens.
    user = (
        f"ROLE THESIS (every section must support this):\n{thesis}\n\n"
        "Find anything exaggerated, vague, AI-generated, irrelevant, risky, or mismatched. "
        "Revise for credibility, concision, and JD alignment.\n\n"
        f"TARGET JOB: {job_title}\n\n"
        f"DRAFT JSON:\n{json.dumps(content, ensure_ascii=False)[:14000]}\n\n"
        "Return keys: summary, experience, skills, projects (same schema)."
    )
    from job_pipeline.cache_prefix import static_writer_cache_prefix
    # NOTE: this pass only fires when RESUME_OPT_FULL=1 (legacy path); the
    # default build doesn't reach this call. Kept on Sonnet because when
    # it DOES fire, the rewrite quality matters.
    revised = generate_json(
        "tailor",
        system=system,
        user=user,
        label="recruiter_review",
        system_cacheable_prefix=static_writer_cache_prefix(),
    )
    for k in ("summary", "experience", "skills", "projects"):
        if k in revised:
            content[k] = revised[k]
    return content


def _llm_ats_pass(
    content: Dict[str, Any],
    *,
    job_description: str,
    profile_text: str,
    classifications: List[Dict[str, Any]],
) -> Dict[str, Any]:
    system = (
        "ATS optimizer pass. Add missing must-have keywords ONLY when truthfully supported. "
        "Keep human-readable and under two pages. Return exactly one JSON object."
    )
    supported = [
        c.get("approved_phrasing") or c.get("label")
        for c in classifications
        if c.get("level") in (DIRECT, ADJACENT, LEARNABLE)
        and (c.get("approved_phrasing") or c.get("label"))
    ]
    # PROFILE_TEXT is in the system_cacheable_prefix; don't duplicate.
    user = (
        f"JD:\n{job_description[:8000]}\n\n"
        f"TRUTH-SAFE KEYWORDS YOU MAY ADD (only these):\n{supported[:15]}\n\n"
        f"DRAFT JSON:\n{json.dumps(content, ensure_ascii=False)[:12000]}\n\n"
        "Return keys: summary, experience, skills, projects."
    )
    from job_pipeline.cache_prefix import static_writer_cache_prefix
    # NOTE: legacy fallback path only; default build skips this call.
    revised = generate_json(
        "tailor",
        system=system,
        user=user,
        label="ats_optimizer",
        system_cacheable_prefix=static_writer_cache_prefix(),
    )
    for k in ("summary", "experience", "skills", "projects"):
        if k in revised:
            content[k] = revised[k]
    return content


def run_resume_optimization_pipeline(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str,
    *,
    validation: Optional[Dict[str, Any]] = None,
    job_title: str = "",
    company: str = "",
    revalidate_fn=None,
) -> Dict[str, Any]:
    """
    Run optimization gates on tailored resume content (mutates copy).

    revalidate_fn: callable(content) -> validation dict, run after mutations.

    Phase 0 integrity guards always run — even when RESUME_OPT_ENABLED=0 — so
    cross-job metric leaks, malformed summaries, dupe skills, and dupe bullets
    are never exported.
    """
    if content.get("error"):
        return {
            "content": content,
            "validation": validation or {},
            "optimization": {"skipped": True, "reason": "content error"},
        }

    working = copy.deepcopy(content)

    # ---- Phase 0: ALWAYS-ON integrity guards ----
    integrity_notes = run_integrity_guards(working, job_title=job_title)

    if not opt_enabled():
        try:
            _pres = _lint_resume_presentation(working, job_title=job_title, jd_text=job_description)
            working = _pres.content
            integrity_notes.extend(_pres.notes)
        except Exception:
            pass
        if revalidate_fn and integrity_notes:
            validation = revalidate_fn(working)
        return {
            "content": working,
            "validation": validation or {},
            "optimization": {
                "skipped_optimization": True,
                "integrity_notes": integrity_notes,
            },
        }

    opt_notes: List[str] = list(integrity_notes)

    # Cache JD parse + classifications for this build
    jd_analysis = parse_job_description(job_description)
    classifications = classify_jd_requirements(job_description, profile_text)
    assessment = merge_assessment_with_classifications(job_description, profile_text)
    thesis = build_role_thesis(job_title, jd_analysis)

    working, parser_notes = apply_parser_safe_experience(working)
    opt_notes.extend(parser_notes)

    opt_notes.extend(apply_role_thesis(working, thesis))
    opt_notes.extend(enforce_bullet_balance(working))
    opt_notes.extend(inject_metric_bank(working))
    opt_notes.extend(compress_skills_extended(working, job_description, profile_text))

    working, fluff_notes = strip_anti_fluff_content(working)
    opt_notes.extend(fluff_notes)

    # Re-run guards after optimization mutations (metric injection / bullet balance
    # can resurface dupes; anti-fluff edits can leave fragments).
    opt_notes.extend(run_integrity_guards(working, job_title=job_title))

    if revalidate_fn:
        validation = revalidate_fn(working)

    score = score_resume_rubric(
        working,
        job_description,
        profile_text,
        classifications=classifications,
        jd_analysis=jd_analysis,
        thesis=thesis,
        job_title=job_title,
        parser_notes=parser_notes,
        validation=validation,
    )

    # ---- Phase 2: critique-and-revise loop (default ON) ----
    # The critique loop subsumes the old single-shot recruiter + ATS passes.
    # It ALWAYS runs (when the loop is enabled and a provider is available) —
    # the deterministic guards can only catch known leak shapes, and the whole
    # point of the LLM critique is to find the novel ones we haven't seen yet
    # ("Microsoft 365 suite" duplicate, awkward phrasing, packed summaries...).
    # Skipping the critique when deterministic guards were quiet was a cost
    # optimization that defeated the loop's purpose.
    #
    # Cost is held in check by: (a) cheaper gpt-4.1-mini on the critique pass,
    # (b) max 2 iterations, (c) early stop when critique returns no high-severity
    # issues — most builds finish after one cheap critique call.
    if critique_loop_enabled() and writing_providers_available():
        def _post_revise(c: Dict[str, Any]) -> None:
            strip_anti_fluff_content(c)
            run_integrity_guards(c, job_title=job_title)

        working, critique_reports, critique_notes = run_resume_critique_loop(
            working,
            job_description=job_description,
            profile_text=profile_text,
            job_title=job_title,
            post_revise_hook=_post_revise,
        )
        opt_notes.extend(critique_notes)
        opt_notes.extend(
            run_pre_export_guards(working, doc_type="resume", job_title=job_title)
        )
        working, grammar_notes = proofread_resume_content(
            working,
            profile_text=profile_text,
            job_title=job_title,
        )
        opt_notes.extend(grammar_notes)
        if revalidate_fn and critique_reports:
            validation = revalidate_fn(working)
        score = score_resume_rubric(
            working,
            job_description,
            profile_text,
            classifications=classifications,
            jd_analysis=jd_analysis,
            thesis=thesis,
            job_title=job_title,
            parser_notes=parser_notes,
            validation=validation,
        )
    elif opt_full() and writing_providers_available() and score["total"] < opt_min_score():
        # Legacy path: when the critique loop is explicitly disabled but full opt
        # is on AND the deterministic score is still low, fall back to the old
        # single-shot recruiter+ATS passes. Kept for back-compat / testing.
        try:
            working = _llm_recruiter_pass(
                working,
                job_description=job_description,
                profile_text=profile_text,
                job_title=job_title,
                thesis=thesis,
            )
            opt_notes.append("LLM: recruiter review pass (legacy single-shot)")
            working = _llm_ats_pass(
                working,
                job_description=job_description,
                profile_text=profile_text,
                classifications=classifications,
            )
            opt_notes.append("LLM: ATS optimizer pass (legacy single-shot)")
            working, fluff_notes = strip_anti_fluff_content(working)
            opt_notes.extend(fluff_notes)
            opt_notes.extend(run_integrity_guards(working, job_title=job_title))
            if revalidate_fn:
                validation = revalidate_fn(working)
            score = score_resume_rubric(
                working,
                job_description,
                profile_text,
                classifications=classifications,
                jd_analysis=jd_analysis,
                thesis=thesis,
                job_title=job_title,
                parser_notes=parser_notes,
                validation=validation,
            )
        except LLMWritingError as exc:
            opt_notes.append(f"LLM optimization skipped: {exc}")
            logger.warning("resume optimization LLM passes failed: %s", exc)

    if not (critique_loop_enabled() and writing_providers_available()):
        opt_notes.extend(
            run_pre_export_guards(working, doc_type="resume", job_title=job_title)
        )
        working, grammar_notes = proofread_resume_content(
            working,
            profile_text=profile_text,
            job_title=job_title,
        )
        opt_notes.extend(grammar_notes)

    # ---- Final deterministic PRESENTATION pass + gate revise loop + calibrated judge ----
    # Judge is expensive (large anchor prompt). Run it only after rubric + presentation
    # pass; gate revisions reuse deterministic guards without re-proofreading grammar.
    presentation_findings: List[Any] = []
    judge_result: Dict[str, Any] = {"ok": False}
    gate_revision = 0
    while True:
        working, score, presentation_findings, validation = _apply_presentation_pass(
            working,
            score,
            job_title=job_title,
            job_description=job_description,
            opt_notes=opt_notes,
            revalidate_fn=revalidate_fn,
            validation=validation,
        )

        # Phase 2B: regression_check issues are gate blockers. The
        # deterministic scrubbers auto-fix what they can; what remains is
        # by definition not auto-fixable (e.g. LLM word-drops where we
        # don't know the missing word). Force a gate revise so the LLM
        # gets another shot — feed the issues into presentation_findings
        # as block-severity so _targeted_gate_revise sees them as fix
        # targets.
        try:
            from job_pipeline.regression_check import check_resume_content
            regression_issues = check_resume_content(working)
        except Exception as exc:
            logger.warning("regression_check failed during gate loop: %s", exc)
            regression_issues = []
        if regression_issues:
            from types import SimpleNamespace
            for issue in regression_issues:
                presentation_findings.append(
                    SimpleNamespace(
                        severity="block",
                        message=f"regression_check: {issue}",
                        rule="regression_check",
                    )
                )
            opt_notes.append(
                f"regression_check: {len(regression_issues)} issue(s) blocking gate: "
                + "; ".join(regression_issues[:3])
            )

        blocking = [
            f for f in presentation_findings if getattr(f, "severity", "") == "block"
        ]
        rubric_ok = score["total"] >= opt_min_score()

        if rubric_ok and not blocking and judge_enabled():
            judge_result = judge_quality(
                working,
                job_description=job_description,
                job_title=job_title,
                cover_letter_content=None,
                use_cache=(gate_revision == 0),
            )
            if judge_result.get("ok"):
                opt_notes.append(
                    f"judge: score={judge_result.get('score')} "
                    f"verdict={judge_result.get('verdict')} "
                    f"min={opt_judge_min()}"
                )
            elif gate_revision == 0:
                opt_notes.append(
                    f"judge: skipped ({judge_result.get('reason', 'unavailable')})"
                )

        judge_ok = (not judge_result.get("ok")) or judge_result.get("passes_gate", False)
        gate_passed = rubric_ok and not blocking and judge_ok

        if gate_passed or gate_revision >= opt_max_revisions():
            break

        revised = _targeted_gate_revise(
            working,
            job_description=job_description,
            profile_text=profile_text,
            job_title=job_title,
            presentation_findings=presentation_findings,
            score=score,
            judge_critique=(
                judge_result.get("critique")
                if judge_result.get("ok") and rubric_ok and not blocking
                else None
            ),
        )
        if not revised:
            opt_notes.append(
                f"gate revise attempt {gate_revision + 1} skipped (no LLM revise)"
            )
            break

        working = revised
        gate_revision += 1
        opt_notes.append(f"gate revise attempt {gate_revision}: targeted quality pass")
        from job_pipeline.quality_judge import clear_judge_cache

        clear_judge_cache()
        judge_result = {"ok": False}
        working, fluff_notes = strip_anti_fluff_content(working)
        opt_notes.extend(fluff_notes)
        opt_notes.extend(run_integrity_guards(working, job_title=job_title))
        opt_notes.extend(run_pre_export_guards(working, doc_type="resume", job_title=job_title))
        if revalidate_fn:
            validation = revalidate_fn(working)
        score = score_resume_rubric(
            working,
            job_description,
            profile_text,
            classifications=classifications,
            jd_analysis=jd_analysis,
            thesis=thesis,
            job_title=job_title,
            parser_notes=parser_notes,
            validation=validation,
        )

    judge_ok = (not judge_result.get("ok")) or judge_result.get("passes_gate", False)
    gate_passed = (
        score["total"] >= opt_min_score()
        and not [
            f for f in presentation_findings if getattr(f, "severity", "") == "block"
        ]
        and judge_ok
    )
    red_flags = red_flag_report(working, job_description, profile_text)

    return {
        "content": working,
        "validation": validation or {},
        "optimization": {
            "thesis": thesis,
            "jd_analysis": jd_analysis,
            "classifications": classifications,
            "assessment": assessment,
            "score": score,
            "gate_passed": gate_passed,
            "gate_blocked": gate_block_enabled() and not gate_passed,
            "gate_revisions": gate_revision,
            "min_score": opt_min_score(),
            "judge_score": judge_result.get("score") if judge_result.get("ok") else None,
            "judge_subscores": judge_result.get("subscores") if judge_result.get("ok") else {},
            "judge_critique": judge_result.get("critique") if judge_result.get("ok") else [],
            "judge_verdict": judge_result.get("verdict") if judge_result.get("ok") else "",
            "judge_min": opt_judge_min(),
            "red_flags": red_flags[:12],
            "presentation_penalty": score.get("presentation_penalty", 0.0),
            "presentation_blocking": [
                getattr(f, "rule_id", "")
                for f in presentation_findings
                if getattr(f, "severity", "") == "block"
            ],
            "notes": opt_notes,
            "full_mode": opt_full(),
        },
    }
