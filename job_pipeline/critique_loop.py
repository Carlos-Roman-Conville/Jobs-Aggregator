"""Generate -> critique -> revise loop for resume and cover-letter content.

The first-pass generator (resume_tailor or cover_letter_tailor) is good at
producing JD-shaped content but commits to phrasing left-to-right under many
simultaneous constraints, so it often ships repeated phrases, generic
buzzwords, and adjacency overclaims. A separate critique pass — driven by an
explicit rubric and a "skeptical hiring manager" persona — catches those
much better than the writer can during generation. Revise loops fold the
critique back in. 1-3 iterations is the sweet spot; we stop early when the
critique returns no high-severity issues.

Loop integration:
- resume_optimizer.run_resume_optimization_pipeline() calls
  run_resume_critique_loop() after deterministic Phase-0 guards.
- cover_letter_optimizer.optimize_cover_letter_content() calls
  run_cover_letter_critique_loop().

Each call is gated by `RESUME_OPT_CRITIQUE_LOOP` (default "1" / on). When the
LLM provider is unavailable or any iteration's JSON is malformed, the loop
returns the existing content with a diagnostic note — never raises into the
caller's build path.

Phase-0 deterministic guards still run between iterations so the LLM can't
quietly resurface known leak shapes (audit language, AD-vs-gpedit, etc.).

LLM call budget (per document, RESUME_OPT_CRITIQUE_LOOP=1, typical / worst case):
- Resume: 1 tailor generate (+1 JSON retry) + up to 2 critique + up to 2 revise
  => typical ~2 calls (gen + 1 clean critique); worst case ~5 calls.
- Cover letter: 1 tailor generate (+1 JSON retry) + up to 2 critique + up to 2 revise
  (DEFAULT_MAX_ITERATIONS for CL matches resume since grammar-check layer landed).
  => typical ~2 calls; worst case ~5 calls.
- Full package typical ~4 LLM calls; worst case ~10 before the grammar proofread pass.
- Grammar proofread (grammar_proofread.py): +1 cheap call per document when enabled.

The critique loop is a skeptical hiring-manager content review — NOT a copy editor.
Basic syntax/grammar is handled by deterministic grammar guards + grammar_proofread.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from job_pipeline.llm_provider import LLMWritingError, generate_json, writing_providers_available


logger = logging.getLogger(__name__)


_HIGH_SEVERITY = "high"
_MEDIUM_SEVERITY = "medium"
_LOW_SEVERITY = "low"

# 2 iterations captures most of the improvement; 3 was burning cost for
# diminishing returns. Override per-call via max_iterations kwarg.
DEFAULT_MAX_ITERATIONS = 2


def critique_loop_enabled() -> bool:
    """Master switch — default OFF (cost optimization, see arXiv 2604.01029).

    The critique-revise loop was high-value when the underlying generator
    hallucinated frequently (older Gemini, GPT-4). With Claude Sonnet 4.5+ as
    the writer, the loop mostly catches sampling variance — the "improvement"
    on revise is largely the model re-solving rather than fixing real bugs.

    Cost impact: each iteration adds ~2 Claude calls (critique + revise).
    Disabling cuts a typical build from ~4 LLM calls to ~2, dropping cost
    from $0.30-0.50/job to ~$0.10/job.

    Quality impact: the deterministic scrubbers (anti_fluff, integrity_guards,
    evidence.json truth_limits) catch ~80% of what the critique caught.
    Subtle tone/AI-tell issues survive at low rate; tradeoff is acceptable.

    Re-enable with RESUME_OPT_CRITIQUE_LOOP=1 if quality regresses.
    """
    return os.getenv("RESUME_OPT_CRITIQUE_LOOP", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _critique_model_role() -> str:
    """Critique uses a cheaper model role than tailor (gpt-4.1-mini by default).
    Override via OPENAI_CRITIQUE_MODEL env var.
    """
    return "critique"


# ---------------------------------------------------------------------------
# Rubrics
# ---------------------------------------------------------------------------

RESUME_CRITIQUE_RUBRIC: List[str] = [
    "Repeated multi-word phrases anywhere in summary, a single bullet, or one paragraph.",
    "Verb+noun-stem double-ups like 'Supported user account support' or 'Provided account support work'.",
    "Generic buzzwords or AI-tell phrasing: 'results-driven', 'passionate about', 'proven track record', 'leveraged', 'utilized', 'spearheaded', 'team player', 'self-starter', 'wealth of experience', 'in order to', 'going forward', 'at the end of the day', 'wide range of', 'deep understanding of', 'successfully managed/implemented' (drop the 'successfully'), 'detail-oriented'.",
    "Vague claims with no concrete proof — bullets that don't name a tool, a metric, a scale, or a specific outcome.",
    "Adjacency overclaims — small-shop or end-user tools described as if they were enterprise tools (e.g. 'Active Directory' when the only evidence is Local Group Policy Editor / gpedit.msc).",
    "Defensive disclaimer phrasing on a resume: 'Personal project only', 'I do not claim X', 'I have partial Y experience', 'side project only', 'at the user level only', 'as documented in prior X work'.",
    "Audit-mode meta-language that leaked from the generator: 'X is supported by', 'time-zone coverage is not claimed', 'I do not claim ...'.",
    "Skills section semantic duplicates — variants of the same concept appearing as separate chips. CHECK CAREFULLY: 'Microsoft 365' + 'Microsoft 365 suite' is a duplicate. 'Remote administration' + 'Remote systems administration' is a duplicate. If skill A's words are a subset of skill B's words, they're the same skill — flag the longer one.",
    "Skills section ordering: highest-relevance JD keywords should appear first; least-relevant last.",
    "Project description and impact must NOT be verbatim copies of each other — the impact should add new information about WHY the project mattered.",
    "Tense and voice inconsistency across bullets in the same role.",
    "Summary opener: must directly match or echo the target job title for skim-readability.",
    "Summary keyword density: a single summary sentence should not list more than ~5-6 distinct skill keywords. If a sentence reads as a comma-salad of keywords, flag it.",
    "ATS keyword presence: any must-have keyword from the JD that the candidate is genuinely qualified for should be in skills.technical or experience bullets.",
    "Broken or incomplete sentences — missing verbs (e.g. 'I also across hardware...'), sentence fragments, or clauses with no main verb in summary or any bullet.",
]

COVER_LETTER_CRITIQUE_RUBRIC: List[str] = [
    "Back-to-back closings — a closing-intent sentence ('I'd welcome a conversation' / 'I'd like to discuss') appearing in BOTH the final body paragraph AND the closing field.",
    "Paragraph density — each body paragraph should be 3-5 sentences. No wall-of-text paragraphs over ~7 sentences. No paragraph stuffed with multiple metrics back-to-back.",
    "Repeated multi-word phrases across paragraphs.",
    "Generic openings: 'I am writing to apply for', 'I am excited to apply', 'It is with great enthusiasm', 'I am the perfect fit'.",
    "Tone mismatch with the role — formal corporate tone for a startup, or casual tone for a regulated/medical employer. Avoid colloquial phrases like 'without slowing down the room' / 'keep things moving' for formal employers (court, healthcare, government).",
    "Length: should fit on one page. Three to four body paragraphs is the target. Cut anything that doesn't add evidence.",
    "Specific proof points — at least one body paragraph should name a tool, a metric, a scale, or a concrete outcome from the candidate's actual experience.",
    "Closing has a clear call-to-action and is the only closing-intent sentence in the document.",
    "Defensive or hedging phrasing — 'while my background is not from', 'I do not have formal', 'I have limited', 'at the user level only', 'as documented in prior X work'.",
    "Awkward LLM phrasing — 'What I bring from <Company>'s side of the work' (use 'to <Company>' instead), 'in technical operations work' as a hedge tail, defensive justifications for a keyword.",
    "Broken or incomplete sentences — missing verbs (e.g. 'I also across hardware...' instead of 'I also worked/resolved incidents across...'), sentence fragments, or clauses with no main verb.",
    "Forbidden tangents — for help-desk targets: no 'I build personal automation tools' / Python pipeline mentions in the body; those belong on the resume.",
]


# ---------------------------------------------------------------------------
# Critique pass
# ---------------------------------------------------------------------------

def _critique_system_for(target: str) -> str:
    if target == "cover_letter":
        return (
            "You are a skeptical hiring manager who reads 50 cover letters every day. "
            "You hate AI-generated fluff, wall-of-text paragraphs, repeated phrasing, "
            "duplicate closings, generic openings, and defensive hedging. "
            "Grammar/syntax errors (missing verbs, fragments, broken sentences) are "
            "ALWAYS high severity — never set ready_to_ship if any exist. "
            "Be blunt. Return exactly one JSON object with no markdown."
        )
    return (
        "You are a skeptical hiring manager with 15 years of IT recruiting experience. "
        "You hate AI-generated buzzword fluff, repeated phrases, vague claims with no "
        "proof, defensive disclaimer language, and adjacency overclaims (e.g. claiming "
        "Active Directory when the only evidence is Local Group Policy). "
        "Grammar/syntax errors (missing verbs, fragments, broken sentences) are "
        "ALWAYS high severity — never set ready_to_ship if any exist. "
        "Be blunt. Return exactly one JSON object with no markdown."
    )


def _critique_user_for_resume(
    content: Dict[str, Any],
    *,
    rubric: List[str],
    job_description: str,
    job_title: str,
) -> str:
    rubric_block = "\n".join(f"- {item}" for item in rubric)
    return (
        f"Critique this resume for the target role: {job_title or '(unknown)'}\n\n"
        f"RUBRIC — check each:\n{rubric_block}\n\n"
        f"TARGET JD (first 3000 chars — context only):\n{(job_description or '')[:3000]}\n\n"
        f"RESUME JSON (current draft):\n{json.dumps(content, ensure_ascii=False)[:8000]}\n\n"
        "Return EXACTLY this JSON shape:\n"
        "{\n"
        '  "issues": [\n'
        '    {\n'
        '      "severity": "high" | "medium" | "low",\n'
        '      "location": "summary" | "skills.technical" | "skills.soft" | '
        '"experience[<index>].bullets[<i>]" | "projects[<index>].description" | '
        '"projects[<index>].impact",\n'
        '      "category": "repetition" | "buzzword" | "vague_claim" | "overclaim" | '
        '"defensive" | "audit_language" | "skill_dupe" | "tense" | "ats_gap" | "tone" | "other",\n'
        '      "snippet": "<the actual text exhibiting the issue, <=140 chars>",\n'
        '      "fix_hint": "<short, specific instruction to fix it>"\n'
        "    }\n"
        "  ],\n"
        '  "ready_to_ship": true | false\n'
        "}\n"
        "Mark severity HIGH for credibility-damaging issues (overclaim, audit language, "
        "defensive disclaimers, verb+noun double-ups, repeated multi-word phrases, "
        "AND any grammar/syntax error: missing verbs, fragments, broken sentences). "
        "Mark MEDIUM for buzzwords, tense slips, skill duplicates, ordering. "
        "Mark LOW for stylistic preferences. "
        "Set ready_to_ship to true ONLY if there are no HIGH-severity issues."
    )


def _critique_user_for_cover_letter(
    content: Dict[str, Any],
    *,
    rubric: List[str],
    job_description: str,
    job_title: str,
) -> str:
    rubric_block = "\n".join(f"- {item}" for item in rubric)
    return (
        f"Critique this cover letter for the target role: {job_title or '(unknown)'}\n\n"
        f"RUBRIC — check each:\n{rubric_block}\n\n"
        f"TARGET JD (first 2500 chars — context only):\n{(job_description or '')[:2500]}\n\n"
        f"COVER LETTER JSON (current draft):\n{json.dumps(content, ensure_ascii=False)[:6000]}\n\n"
        "Return EXACTLY this JSON shape:\n"
        "{\n"
        '  "issues": [\n'
        '    {\n'
        '      "severity": "high" | "medium" | "low",\n'
        '      "location": "opening" | "body_paragraphs[<index>]" | "closing",\n'
        '      "category": "duplicate_closing" | "density" | "repetition" | '
        '"generic_open" | "tone" | "length" | "vague" | "defensive" | "tangent" | "other",\n'
        '      "snippet": "<actual text, <=140 chars>",\n'
        '      "fix_hint": "<short, specific instruction>"\n'
        "    }\n"
        "  ],\n"
        '  "ready_to_ship": true | false\n'
        "}\n"
        "Mark severity HIGH for credibility issues: back-to-back closings, generic "
        "openings, wall-of-text paragraphs, tone mismatch, defensive hedging, forbidden "
        "tangents, AND any grammar/syntax error (missing verbs, fragments, broken sentences). "
        "Mark MEDIUM for repetition, vague proof, length issues. "
        "Mark LOW for stylistic preferences. "
        "Set ready_to_ship to true ONLY if there are no HIGH-severity issues."
    )


def _llm_critique(
    content: Dict[str, Any],
    *,
    target: str,
    rubric: List[str],
    job_description: str,
    job_title: str,
    label: str,
) -> Optional[Dict[str, Any]]:
    """Run one critique pass. Returns the parsed critique dict, or None on failure."""
    system = _critique_system_for(target)
    if target == "cover_letter":
        user = _critique_user_for_cover_letter(
            content, rubric=rubric, job_description=job_description, job_title=job_title
        )
    else:
        user = _critique_user_for_resume(
            content, rubric=rubric, job_description=job_description, job_title=job_title
        )
    try:
        result = generate_json(
            _critique_model_role(),  # cheaper model than the tailor / revise pass
            system=system,
            user=user,
            label=label,
            openai_temperature=0.2,  # low temp for consistent critique
        )
    except LLMWritingError as exc:
        logger.warning("%s critique pass failed: %s", label, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s critique pass raised: %s", label, exc)
        return None
    if not isinstance(result, dict):
        return None
    if not isinstance(result.get("issues"), list):
        # Malformed — treat as no issues but log.
        logger.warning("%s critique returned no issues array; got %s", label, type(result.get("issues")))
        result["issues"] = []
    return result


# ---------------------------------------------------------------------------
# Revise pass
# ---------------------------------------------------------------------------

def _revise_system_for(target: str) -> str:
    if target == "cover_letter":
        return (
            "You revise cover letter JSON. Apply ALL high-severity fixes from the "
            "critique. Apply medium-severity fixes when they don't conflict with "
            "high-severity fixes. Do not introduce new claims, employers, tools, or "
            "metrics not already in the draft. Return exactly one JSON object."
        )
    return (
        "You revise resume JSON. Apply ALL high-severity fixes from the critique. "
        "Apply medium-severity fixes when they don't conflict with high-severity "
        "fixes. Do not invent new employers, titles, metrics, dates, or skills. "
        "Keep all factual content intact. Return exactly one JSON object."
    )


def _revise_user(
    content: Dict[str, Any],
    *,
    critique: Dict[str, Any],
    target: str,
    job_description: str,
    profile_text: str,
) -> str:
    issues = critique.get("issues") or []
    # Sort: high, medium, low so the LLM addresses critical issues first.
    severity_order = {_HIGH_SEVERITY: 0, _MEDIUM_SEVERITY: 1, _LOW_SEVERITY: 2}
    issues_sorted = sorted(
        issues, key=lambda i: severity_order.get(str(i.get("severity") or "low").lower(), 3)
    )
    issues_block = json.dumps(issues_sorted, ensure_ascii=False)
    keys_hint = (
        "Return keys: opening, body_paragraphs, closing."
        if target == "cover_letter"
        else "Return keys: summary, experience, skills, projects."
    )
    return (
        f"CRITIQUE ISSUES TO FIX (severity-ordered):\n{issues_block[:4000]}\n\n"
        f"CURRENT DRAFT JSON:\n{json.dumps(content, ensure_ascii=False)[:8000]}\n\n"
        f"PROFILE_TEXT (ground truth — use for context, do NOT add new facts):\n"
        f"{(profile_text or '')[:4000]}\n\n"
        f"JD (for keyword fit context):\n{(job_description or '')[:2000]}\n\n"
        f"{keys_hint}\n"
        "Apply every high-severity fix. Then apply medium-severity fixes. "
        "Do NOT add facts that are not in the current draft or PROFILE_TEXT."
    )


def _llm_revise(
    content: Dict[str, Any],
    *,
    critique: Dict[str, Any],
    target: str,
    job_description: str,
    profile_text: str,
    label: str,
) -> Optional[Dict[str, Any]]:
    """Apply critique fixes via LLM. Returns updated content dict, or None on failure."""
    system = _revise_system_for(target)
    user = _revise_user(
        content,
        critique=critique,
        target=target,
        job_description=job_description,
        profile_text=profile_text,
    )
    try:
        revised = generate_json(
            "tailor",
            system=system,
            user=user,
            label=label,
            openai_temperature=0.3,
        )
    except LLMWritingError as exc:
        logger.warning("%s revise pass failed: %s", label, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s revise pass raised: %s", label, exc)
        return None
    if not isinstance(revised, dict):
        return None
    # Merge revised keys back in. Only known keys are copied.
    allowed_keys = (
        ("opening", "body_paragraphs", "closing")
        if target == "cover_letter"
        else ("summary", "experience", "skills", "projects")
    )
    merged = dict(content)
    for k in allowed_keys:
        if k in revised:
            merged[k] = revised[k]
    return merged


# ---------------------------------------------------------------------------
# Loop driver
# ---------------------------------------------------------------------------

def _has_high_severity(critique: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(critique, dict):
        return False
    if critique.get("ready_to_ship") is True:
        # Explicit signal from the critic — short-circuit.
        return False
    for issue in critique.get("issues") or []:
        if str(issue.get("severity") or "").lower() == _HIGH_SEVERITY:
            return True
    return False


def _run_critique_loop(
    content: Dict[str, Any],
    *,
    target: str,
    rubric: List[str],
    job_description: str,
    profile_text: str,
    job_title: str,
    max_iterations: int,
    post_revise_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
    label_prefix: str = "critique",
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """Run the adaptive critique-and-revise loop.

    Returns (updated_content, iteration_reports, notes). Always returns a usable
    content dict — on LLM failure it returns the input content unchanged and
    appends a diagnostic note.
    """
    notes: List[str] = []
    reports: List[Dict[str, Any]] = []

    if not isinstance(content, dict) or content.get("error"):
        return content, reports, notes
    if not critique_loop_enabled():
        return content, reports, notes
    if not writing_providers_available():
        notes.append(f"{label_prefix}: skipped (no writing provider available)")
        return content, reports, notes

    working = dict(content)
    for iteration in range(1, max_iterations + 1):
        critique = _llm_critique(
            working,
            target=target,
            rubric=rubric,
            job_description=job_description,
            job_title=job_title,
            label=f"{label_prefix}_critique_{iteration}",
        )
        if critique is None:
            notes.append(f"{label_prefix}: iteration {iteration} critique pass failed; stopping")
            break
        reports.append({"iteration": iteration, "critique": critique})

        if not _has_high_severity(critique):
            notes.append(
                f"{label_prefix}: stopped after {iteration} iteration(s) — no high-severity issues"
            )
            break

        revised = _llm_revise(
            working,
            critique=critique,
            target=target,
            job_description=job_description,
            profile_text=profile_text,
            label=f"{label_prefix}_revise_{iteration}",
        )
        if revised is None:
            notes.append(
                f"{label_prefix}: iteration {iteration} revise pass failed; keeping previous draft"
            )
            break
        working = revised
        if post_revise_hook is not None:
            try:
                post_revise_hook(working)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s post-revise hook raised: %s", label_prefix, exc)

        notes.append(
            f"{label_prefix}: iteration {iteration} applied {len([i for i in critique.get('issues') or [] if str(i.get('severity') or '').lower() == _HIGH_SEVERITY])} high-severity fix(es)"
        )
    else:
        notes.append(
            f"{label_prefix}: hit max iterations ({max_iterations}); review final output for residual issues"
        )

    return working, reports, notes


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def run_resume_critique_loop(
    content: Dict[str, Any],
    *,
    job_description: str,
    profile_text: str,
    job_title: str = "",
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    post_revise_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    return _run_critique_loop(
        content,
        target="resume",
        rubric=RESUME_CRITIQUE_RUBRIC,
        job_description=job_description,
        profile_text=profile_text,
        job_title=job_title,
        max_iterations=max_iterations,
        post_revise_hook=post_revise_hook,
        label_prefix="resume_critique",
    )


def run_cover_letter_critique_loop(
    content: Dict[str, Any],
    *,
    job_description: str,
    profile_text: str,
    job_title: str = "",
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    post_revise_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    return _run_critique_loop(
        content,
        target="cover_letter",
        rubric=COVER_LETTER_CRITIQUE_RUBRIC,
        job_description=job_description,
        profile_text=profile_text,
        job_title=job_title,
        max_iterations=max_iterations,
        post_revise_hook=post_revise_hook,
        label_prefix="cl_critique",
    )
