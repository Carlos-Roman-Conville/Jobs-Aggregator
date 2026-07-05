"""Shared cache prefix builder for Anthropic prompt caching.

The build pipeline chains 5+ LLM calls per job (resume_tailor →
resume_optimizer LLM passes → cover_letter_tailor → cover letter
optimizer → grammar proofread). Without a shared cacheable prefix,
each call writes its own cache entry that nothing else ever reads
— and the per-build cost stays at ~$0.40-0.50.

This module emits ONE canonical prefix string that every writer call
in the build can use as `system_cacheable_prefix`. The prefix
contains:

  1. Static style + truth-limits rules (identical across all jobs)
  2. The candidate's profile_text (identical across all builds in a
     session — only changes when consolidated_profile.json is
     regenerated)
  3. The serialized evidence.json employer truth-limits block

These are the LARGEST tokens by volume in the writer prompts; moving
them into the cache turns the second-through-fifth calls into ~10%
input price on the prefix portion.

CRITICAL: this string must be byte-for-byte identical across all
calls in a build, or the cache misses. Do NOT interpolate the JD,
job title, current draft JSON, or anything else build-specific
here. Per-job content goes in the dynamic user message.

The caller's role-specific system text (e.g. "You are a skeptical
recruiter") stays in the regular `system` argument — Anthropic
sends it AFTER the cached system prefix, so it can change per role
without invalidating the prefix cache.
"""
from __future__ import annotations

import functools
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Build-stable rules — IDENTICAL across jobs/builds/sessions. Carved
# out of resume_tailor.py's rules_prefix so it can be shared. The
# JD-dependent blocks (light_block, acct_block, _strategy_instructions)
# stay per-call.
_STATIC_WRITER_RULES = """\
WRITER GROUND RULES (applies to every resume / cover-letter call):

Facts come ONLY from PROFILE_TEXT and RESUME_METADATA_JSON below.
Do NOT invent employers, titles, dates, degrees, certifications, or
metrics. If the profile does not support a claim, omit it.

PROFILE_TEXT PRECEDENCE: Sections appear in order — optional
CANDIDATE-CONFIRMED FACTS (gap answers), then CAREER MASTER, then
consolidated or reference resume text. CAREER MASTER governs framing
and honest limits for everything after it. If consolidated text
conflicts with CAREER MASTER, follow CAREER MASTER. CANDIDATE-
CONFIRMED FACTS clarify resume-backed details but do NOT relax HONEST
LIMITS or forbidden claims under CAREER MASTER — those stay absolute.

HONEST LIMITS RULE: If PROFILE_TEXT contains 'Honest limits', 'do
NOT claim', 'No exposure', or 'Touched but cannot claim', treat
those as HARD CONSTRAINTS:
  - Do not include any skill, title, tool, or experience listed
    under 'No exposure' anywhere in output, even if the JD asks for
    it.
  - For 'Touched but cannot claim' / 'small-shop scale', use ONLY
    the explicit acceptable framings in PROFILE_TEXT.
  - When the JD asks for something the candidate honestly lacks,
    omit it. Do not pattern-match adjacent experience into the
    forbidden claim.
  - It is better to ship a shorter, truthful resume than a longer
    one that overreaches.
  - NEVER write meta-audit language ('X is not claimed', 'PST
    coverage is not claimed', 'Y is supported by Z'). Omit gaps
    silently.
  - NEVER append self-deprecating qualifier tails: 'without
    pretending it was X', 'without claiming X', 'without inflating
    to X', 'though not at enterprise scale', 'though not at admin
    level', 'while not claiming X', 'at small-shop scale' (as a
    trailing qualifier), '(at small-shop scale)', '(single-site)',
    '(limited X scope)'. State what you did and stop.
  - Hedged forms of forbidden claims are also forbidden — 'General
    Understanding', 'Familiar with', 'Exposure to', 'Basic knowledge
    of' — these are absolute exclusions, not framings to soften.

SKILLS RULE: technical[] lists 18-22 of the most JD-relevant skills.
Prefer proof-target tools named in requirements_to_surface_by_name.
Any keyword in summary MUST also appear in skills.technical. Omit
(study)/(learning) lab tools unless the JD explicitly asks for them.

MICROSOFT 365 RULE: If 'Microsoft 365' is in skills.technical, DO
NOT also list its child components (Outlook, Teams, OneDrive,
SharePoint, Word, Excel, PowerPoint, Exchange) as separate skills
UNLESS the JD names them explicitly as distinct requirements. The
umbrella covers them; listing both is keyword padding.

SOFT SKILLS RULE: skills.soft[] should contain 4-6 entries — most
JD-relevant. Vague entries ('Independent Work', 'Conflict
Resolution', 'Cross-Functional Coordination') only when JD asks.

M365 ADMIN VERB RULE: When the profile flags Microsoft 365 as
user-level only, NEVER pair admin verbs (Managed, Administered,
Configured, Maintained, Architected, Deployed, Owned, Oversaw,
Provisioned, Implemented) with Microsoft 365 in bullets or summary.
Use 'Used', 'Supported', 'Worked in', 'Handled' instead.

NO-EMPLOYER-INDUSTRY-INFERENCE RULE: Do NOT invent bullets by
inferring duties from employer industry when the duty is not
documented in PROFILE_TEXT. The candidate's ACTUAL documented scope
is what was in PROFILE_TEXT — if a duty isn't there, omit the
bullet entirely rather than fabricating from industry knowledge.

PROJECTS RULE: include at most 1-2 projects most relevant to the
target role; drop tangential personal projects for support/helpdesk.

experience[].title must equal either (a) the candidate's official
job title as it appears in PROFILE_TEXT, or (b) a 'practical / in
practice' alternative phrase explicitly stated in PROFILE_TEXT. Do
NOT invent a title derived from the target JD.
"""


@functools.lru_cache(maxsize=1)
def _load_profile_text_once() -> str:
    """Load the consolidated profile text. Cached at module level
    because it's read on every Claude call and the file rarely changes
    within a session. If it does change, restart the API to repick up.
    """
    try:
        from job_pipeline.bootstrap_resume_profile import load_consolidated_profile_text
        return (load_consolidated_profile_text() or "").strip()
    except Exception as exc:
        logger.warning("cache_prefix: failed to load profile_text (%s); cache prefix will not include it", exc)
        return ""


@functools.lru_cache(maxsize=1)
def _load_evidence_block_once() -> str:
    """Load the evidence.json employer truth-limits block. Sorted to
    keep byte-stable across Python invocations (dict iteration order
    is stable but JSON dump order without sort_keys can vary by
    version). Wrapped in a clearly-labeled block so the model treats
    it as canonical employer truth.
    """
    try:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent / "evidence.json"
        if not path.is_file():
            return ""
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Sort keys to keep byte-stable (silent invalidator avoidance —
        # any change to dict ordering would invalidate cache reads).
        return (
            "EVIDENCE_BLOCK (employer truth-limits — canonical):\n"
            + json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False)
        )
    except Exception as exc:
        logger.warning("cache_prefix: failed to load evidence block (%s); cache prefix will not include it", exc)
        return ""


@functools.lru_cache(maxsize=1)
def static_writer_cache_prefix() -> str:
    """Return the canonical cacheable prefix for every writer Claude call.

    This string MUST be byte-for-byte identical across all calls in a
    build, or the Anthropic cache misses. The output is ~5-15K tokens
    depending on profile + evidence size — well above Sonnet's 2048
    token minimum.

    Cached at module level so all Claude calls in a session see the
    same string (defensive — load_consolidated_profile_text is itself
    deterministic, but caching here costs nothing and guarantees byte
    identity).
    """
    profile = _load_profile_text_once()
    evidence = _load_evidence_block_once()
    parts = [_STATIC_WRITER_RULES]
    if profile:
        parts.append(f"PROFILE_TEXT:\n{profile}")
    if evidence:
        parts.append(evidence)
    return "\n\n".join(parts)


def writer_prefix_token_estimate() -> int:
    """Rough token count of the writer cache prefix.

    Useful for: (a) deciding whether the prefix clears the 2048-token
    Sonnet minimum, (b) sanity-checking the cache hit logs ("cache_r=N"
    should equal this).
    """
    return len(static_writer_cache_prefix()) // 4
