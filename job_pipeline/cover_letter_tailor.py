"""
Grounded cover-letter tailoring — structured JSON for the exporter.

Output wire format: {opening, body_paragraphs[], closing}. Salutation and signoff
are added by cover_letter_export from load_consolidated_profile().
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from job_pipeline.anti_fluff import strip_anti_fluff_in_text
from job_pipeline.genai_json import preview_model_text
from job_pipeline.genai_settings import gemini_model_for
from job_pipeline.jd_analysis import parse_job_description, voice_mirroring_block
from job_pipeline.llm_provider import (
    LLMWritingError,
    generate_json,
    writing_providers_available,
    writing_providers_missing_error,
)
from job_pipeline.resume_tailor import (
    _augment_profile_with_user_facts,
    _load_grounded_profile_text,
)
from job_pipeline.bootstrap_resume_profile import load_consolidated_profile
from job_pipeline.named_requirements import (
    find_jd_years_echo_violations,
    find_project_jargon_violations,
    find_vague_verb_violations,
    fix_jd_years_echo_in_text,
    light_exposure_prompt_block,
    parse_light_exposure,
    project_framing_prompt_block,
    years_experience_prompt_block,
)


def curate_summary_card_for_cover_letter(summary: Any) -> Dict[str, Any]:
    """Subset of pipeline summary card fed to the cover-letter prompt."""
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except Exception:
            summary = {}
    if not isinstance(summary, dict):
        summary = {}

    boost = summary.get("boost_signals") or []
    if not isinstance(boost, list):
        boost = []
    boost = [str(x).strip() for x in boost if str(x).strip()][:8]

    gaps = summary.get("gaps") or []
    if not isinstance(gaps, list):
        gaps = []
    gaps = [str(x).strip() for x in gaps if str(x).strip()][:12]

    return {
        "verdict": str(summary.get("verdict") or "").strip(),
        "why_match": str(summary.get("why_match") or "").strip(),
        "gaps": gaps,
        "application_friction": str(summary.get("application_friction") or "").strip(),
        "boost_signals": boost,
    }


_CL_PLACEHOLDER_SLOT_NAMES: Tuple[str, ...] = (
    "opening",
    "closing",
    "body",
    "body_paragraphs",
    "body paragraphs",
    "body paragraph",
    "salutation",
    "signoff",
    "sign-off",
    "paragraph",
    "tbd",
    "todo",
    "placeholder",
    "lorem ipsum",
    "n/a",
    "none",
)


_CL_PLACEHOLDER_STRIP_RE = re.compile(r"[\s\.\:\;\,\[\]\<\>\(\)\{\}\-\_]+")


_CL_OUTER_PUNCT_RE = re.compile(
    r"^[\s\.\:\;\,\!\?\[\<\(\{]+|[\s\.\:\;\,\!\?\]\>\)\}]+$"
)


# Closing-intent phrases — the "next-step" / "let's talk" / "thank you" patterns
# that belong EXCLUSIVELY in the closing field. When one of these appears in a
# body_paragraph AND the closing field also has one, the body is acting as a
# second closing — strip the closing-intent sentence(s) from the body.
_CLOSING_INTENT_PATTERNS: Tuple["re.Pattern[str]", ...] = tuple(
    re.compile(p, re.I)
    for p in (
        # "I would welcome a conversation/chance/opportunity"
        r"\b(?:i(?:'d|\s+would)|i\s+am\s+(?:keen|happy|eager))\s+"
        r"(?:welcome|love|appreciate|like)\s+(?:a\s+|the\s+)?"
        r"(?:conversation|chance|opportunity|chat)\b",
        # Bare "welcome a conversation"
        r"\bwelcome\s+(?:a\s+|the\s+)?(?:conversation|chance|opportunity)\b",
        # "look(ing) forward to discussing/hearing/connecting"
        r"\blook(?:ing)?\s+forward\s+to\s+(?:discussing|hearing|connecting|speaking|chatting)\b",
        # "thank you for your time/consideration"
        r"\bthank\s+you\s+for\s+(?:your\s+)?(?:consideration|time|reviewing)\b",
        # "happy/glad to discuss"
        r"\b(?:happy|glad|excited)\s+to\s+discuss\b",
        # "would love to (discuss|chat|connect)"
        r"\bwould\s+love\s+to\s+(?:discuss|chat|connect|speak|talk)\b",
        # "would appreciate the chance/opportunity"
        r"\bwould\s+appreciate\s+the\s+(?:chance|opportunity)\b",
        # "I'd love to (chat|speak|talk|discuss)"
        r"\b(?:i'?d|i\s+would)\s+love\s+to\s+(?:chat|speak|talk|discuss|connect)\b",
        # "available at your earliest convenience" / "at your convenience"
        r"\bat\s+your\s+(?:earliest\s+)?convenience\b",
    )
)


def _looks_like_closing_intent(text: str) -> bool:
    """True if the text contains any closing-style next-step phrase."""
    if not text:
        return False
    return any(p.search(text) for p in _CLOSING_INTENT_PATTERNS)


def _strip_closing_intent_sentences(text: str) -> Tuple[str, bool]:
    """Remove sentences that contain closing-intent phrases. Returns
    (cleaned_text, changed). Splits on sentence-final punctuation; preserves
    sentences that don't contain closing intent."""
    if not text:
        return text, False
    # Split on sentence-final punctuation while keeping the punctuation with the sentence.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept: List[str] = []
    changed = False
    for s in sentences:
        if _looks_like_closing_intent(s):
            changed = True
            continue
        kept.append(s)
    return (" ".join(kept).strip(), changed)


def _looks_like_placeholder_value(value: str, *, slot_name: str = "") -> bool:
    """Detect when an LLM emitted a slot-name (or other placeholder token)
    as the field value instead of generated prose.

    Triggers when:
    - The cleaned value (surrounding punctuation/brackets stripped, lowercased)
      equals any token in _CL_PLACEHOLDER_SLOT_NAMES — catches multi-word
      placeholders ("lorem ipsum", "body paragraphs") directly.
    - For single-token values, the punctuation-collapsed form matches the slot
      name or any normalized placeholder (catches "[closing]", "<opening>",
      "body_paragraphs", "TBD." etc.).
    Returns False for legitimate short prose like "Best regards" or "Thank you."
    """
    v = (value or "").strip()
    if not v:
        return False  # empty handled separately by caller
    v_lower = v.lower()
    # Strip ONLY outer punctuation; keep internal whitespace so multi-word
    # placeholder names like "lorem ipsum" survive intact for comparison.
    v_clean = _CL_OUTER_PUNCT_RE.sub("", v_lower)
    if not v_clean:
        return False
    placeholder_set_lower = {p.lower() for p in _CL_PLACEHOLDER_SLOT_NAMES}
    if v_clean in placeholder_set_lower:
        return True
    # Single-token strict check — collapse internal punctuation so
    # "body_paragraphs" / "[opening]." reduce to bare slot names.
    if len(v_clean.split()) == 1:
        bare = _CL_PLACEHOLDER_STRIP_RE.sub("", v_clean)
        if not bare:
            return False
        if slot_name and bare == slot_name.lower().replace("_", "").replace("-", ""):
            return True
        normalized_placeholders = {
            _CL_PLACEHOLDER_STRIP_RE.sub("", p.lower()) for p in _CL_PLACEHOLDER_SLOT_NAMES
        }
        return bare in normalized_placeholders
    return False


def _clean_cover_letter_field(
    text: str,
    *,
    job_description: str,
    profile_text: str,
) -> Tuple[str, List[str]]:
    """Deterministic in-place cleanup for one cover-letter prose field."""
    notes: List[str] = []
    out = text or ""
    fixed, fluff_notes = strip_anti_fluff_in_text(out)
    if fluff_notes:
        notes.extend(fluff_notes)
    out = fixed
    fixed, changed = fix_jd_years_echo_in_text(out, job_description, profile_text)
    if changed:
        notes.append("jd_years_echo: fixed in place")
    return fixed, notes


def _normalize_cover_letter_content(
    raw: Dict[str, Any],
    *,
    job_description: str = "",
    profile_text: str = "",
) -> Dict[str, Any]:
    opening = str(raw.get("opening") or "").strip()
    closing = str(raw.get("closing") or "").strip()
    bodies_raw = raw.get("body_paragraphs")
    if not isinstance(bodies_raw, list):
        bodies_raw = []
    body_paragraphs = [str(p).strip() for p in bodies_raw if str(p).strip()]
    if not opening and not body_paragraphs and not closing:
        raise ValueError("empty cover letter content")

    # Reject slot-name placeholder leaks ("closing" as the value of `closing`,
    # "opening" as the value of `opening`, single-word "TBD"/"placeholder", etc.).
    # Raising ValueError here triggers the retry loop in
    # generate_cover_letter_content with the repair suffix.
    if opening and _looks_like_placeholder_value(opening, slot_name="opening"):
        raise ValueError(
            f"cover letter opening is a placeholder/slot-name leak: {opening!r}"
        )
    if closing and _looks_like_placeholder_value(closing, slot_name="closing"):
        raise ValueError(
            f"cover letter closing is a placeholder/slot-name leak: {closing!r}"
        )
    # body_paragraphs is an array — drop placeholder entries individually rather
    # than failing the whole letter, so a partially-bad emission can still produce
    # a usable letter (provided the remaining body content is non-trivial).
    cleaned_for_placeholders: List[str] = []
    for p in body_paragraphs:
        if _looks_like_placeholder_value(p, slot_name="body"):
            continue
        cleaned_for_placeholders.append(p)
    if len(cleaned_for_placeholders) != len(body_paragraphs):
        # If filtering left fewer than 2 body paragraphs and there was at least
        # one placeholder, force a retry — a healthy letter has 2-3 bodies.
        if len(cleaned_for_placeholders) < 2:
            raise ValueError(
                f"cover letter body_paragraphs contained placeholder entries "
                f"({len(body_paragraphs) - len(cleaned_for_placeholders)} placeholder(s); "
                f"only {len(cleaned_for_placeholders)} usable bodies remain)"
            )
    body_paragraphs = cleaned_for_placeholders

    notes: List[str] = []
    if opening:
        opening, field_notes = _clean_cover_letter_field(
            opening,
            job_description=job_description,
            profile_text=profile_text,
        )
        notes.extend(field_notes)

    cleaned_bodies: List[str] = []
    for paragraph in body_paragraphs:
        cleaned, field_notes = _clean_cover_letter_field(
            paragraph,
            job_description=job_description,
            profile_text=profile_text,
        )
        cleaned_bodies.append(cleaned)
        notes.extend(field_notes)
    body_paragraphs = cleaned_bodies

    if closing:
        closing, field_notes = _clean_cover_letter_field(
            closing,
            job_description=job_description,
            profile_text=profile_text,
        )
        notes.extend(field_notes)

    # Duplicate-closing strip: if the closing field has closing intent (a real
    # closing) AND the last body_paragraph ALSO has closing intent, the last
    # body is acting as a second closing. Strip closing-intent sentence(s)
    # from the last body, keeping any evidence content. If that leaves the
    # last body empty, drop it entirely. Body paragraphs are evidence-only;
    # next-step language belongs exclusively in the closing field.
    if body_paragraphs and closing and _looks_like_closing_intent(closing):
        last_idx = len(body_paragraphs) - 1
        last_body = body_paragraphs[last_idx]
        if _looks_like_closing_intent(last_body):
            cleaned_last, changed = _strip_closing_intent_sentences(last_body)
            if changed:
                if cleaned_last:
                    body_paragraphs[last_idx] = cleaned_last
                    notes.append(
                        "stripped closing-intent sentence(s) from last body_paragraph "
                        "(closing field already provides next-step)"
                    )
                else:
                    body_paragraphs = body_paragraphs[:-1]
                    notes.append(
                        "dropped last body_paragraph (was entirely closing-intent; "
                        "closing field already provides next-step)"
                    )

    blob = " ".join([opening, *body_paragraphs, closing])
    warnings: List[str] = []
    vague = find_vague_verb_violations(blob)
    if vague:
        warnings.append(f"vague_verbs: {', '.join(vague)}")
    for band in find_jd_years_echo_violations(blob, job_description, profile_text):
        warnings.append(f"jd_years_echo: {band}")
    jargon = find_project_jargon_violations(blob)
    if jargon:
        warnings.append(f"project_jargon: {', '.join(jargon[:3])}")

    return {
        "opening": opening,
        "body_paragraphs": body_paragraphs,
        "closing": closing,
        "_cl_warnings": warnings,
        "_cl_normalize_notes": notes,
    }


def generate_cover_letter_content(
    *,
    job_title: str,
    company: str,
    location: str,
    job_description: str,
    profile_text: str,
    summary_card: Optional[Dict[str, Any]] = None,
    resume_text: str = "",
    template_hint: str = "",
    salutation_override: str = "",
) -> Dict[str, Any]:
    """
    Call the writing LLM for structured cover-letter JSON.
    salutation_override is accepted for API symmetry; exporter applies salutation.
    """
    _ = salutation_override  # exporter-owned; kept for call-site compatibility

    if not writing_providers_available():
        return {"error": writing_providers_missing_error()}

    card = curate_summary_card_for_cover_letter(summary_card or {})
    desc = str(job_description or "")[:12000]
    resume_block = (resume_text or "").strip()
    if resume_block:
        resume_block = resume_block[:8000]
    else:
        resume_block = "(none — ground the letter in PROFILE_TEXT only.)"

    template_block = (template_hint or "").strip()
    if template_block:
        template_block = template_block[:2500]
    else:
        template_block = "(none)"

    prof_json = load_consolidated_profile()
    light_block = light_exposure_prompt_block(
        parse_light_exposure(profile_text, prof_json if isinstance(prof_json, dict) else None)
    )
    years_block = years_experience_prompt_block(desc, profile_text)
    project_block = project_framing_prompt_block()
    voice_block = voice_mirroring_block(parse_job_description(desc))

    system = (
        "You write truthful, sharp job-application cover letters that read like a real person wrote them. "
        "Return exactly one valid JSON object with no markdown fences or commentary. "
        "Follow every rule in the user message exactly."
    )
    model = gemini_model_for("cover_letter")
    prompt = (
        "You write truthful, sharp job-application cover letters that read like a real person wrote them. "
        "Output ONE JSON object ONLY (no markdown fences).\n"
        "Facts must come ONLY from PROFILE_TEXT, optional RESUME_TEXT, and CURATED_SUMMARY_CARD. "
        "Do NOT invent employers, titles, dates, degrees, certifications, or metrics.\n\n"
        "HONEST LIMITS RULE: If PROFILE_TEXT contains 'Honest limits', 'No exposure', 'do NOT claim', "
        "'Never touched', or 'Touched but cannot claim', treat those as HARD CONSTRAINTS. "
        "Do not claim skills or experience listed there, even if the JOB_DESCRIPTION asks for them.\n"
        "CANDIDATE-CONFIRMED FACTS (when present) may clarify resume-backed details but do NOT relax HONEST LIMITS.\n"
        + (light_block + "\n\n" if light_block else "")
        + (years_block + "\n\n" if years_block else "")
        + (project_block + "\n\n" if project_block else "")
        + (voice_block + "\n\n" if voice_block else "")
        + "LIGHT EXPOSURE: when present above, you may use approved phrasing for partial skills — "
        "never for No exposure items, and never upgrade to full expertise.\n\n"
        "METHOD (do this internally before writing):\n"
        "1. Read JOB_DESCRIPTION and extract 6-10 concrete PROOF TARGETS — the specific skills, tools, "
        "responsibilities, and NAMED requirements the employer most cares about (e.g. Active Directory, "
        "user onboarding/offboarding, Microsoft 365, ticketing/ITSM tools, hardware/software/network "
        "troubleshooting, documentation, escalation, client communication).\n"
        "2. For each proof target, decide whether PROFILE_TEXT/RESUME_TEXT TRUTHFULLY supports it. "
        "Address the supported ones explicitly BY NAME in the letter. Silently skip any the candidate "
        "cannot honestly claim — never fabricate to cover a target.\n"
        "3. Read the COMPANY VOICE: the values, culture, and tone the JOB_DESCRIPTION signals "
        "(e.g. ownership, speed, growth, accountability, clear communication, building scalable systems). "
        "Reflect genuine alignment with ONE or two of those values in the candidate's own words — never "
        "parrot the posting's phrases, never invent enthusiasm or claim a cultural fit the evidence does "
        "not support.\n\n"
        "TARGET SHAPE (adapt, do not label sections):\n"
        "- Opening: why THIS role/company specifically + the single strongest, specific fit hook.\n"
        "- Body 1: 3-5 exact JD requirements mapped to concrete experience (the proof targets).\n"
        "- Body 2: authentic culture/values match + ONE quantified achievement from PROFILE_TEXT/RESUME_TEXT "
        "(only if a real metric exists; do not invent numbers).\n"
        "- Closing: confident and short, no fluff.\n\n"
        "CRITICAL — BODY PARAGRAPHS ARE EVIDENCE-ONLY. body_paragraphs[] MUST NOT contain any "
        "closing-style or next-step phrase such as: 'I would welcome a conversation', "
        "'I look forward to discussing', 'thank you for your time/consideration', "
        "'happy to discuss', 'would love to chat/discuss', 'would appreciate the chance', "
        "'at your convenience', or any variant. Those phrases belong EXCLUSIVELY in the "
        "closing field. If a body paragraph ends with a 'let's talk' / 'I'd welcome a conversation' "
        "sentence, the letter has two consecutive closings, which reads as duplication. The "
        "validator will deterministically strip closing-intent sentences from body paragraphs — "
        "leaving them in wastes tokens.\n\n"
        "STYLE RULES (strict):\n"
        "- Short paragraphs, ONE idea each: opening + 2-3 body paragraphs + closing. Never one dense block.\n"
        "- Each paragraph at most ~4 sentences. Lead every body paragraph with concrete evidence, not adjectives.\n"
        "- Plain, direct, human voice. Do NOT use these phrases or close variants: "
        "'I am writing to', 'I am excited to apply', 'enthusiastic interest', 'confident in my ability', "
        "'aligns perfectly', 'aligns well', 'a perfect fit', 'at your earliest convenience', "
        "'fast-paced environment', 'proven track record', 'team player', 'wealth of experience'.\n"
        "- Do NOT use vague filler verbs: leveraged, utilized, spearheaded. State what you did concretely "
        "(e.g. 'supported users in Google Workspace and Microsoft 365 environments, including productivity, "
        "access, and collaboration workflows' — not 'leveraged Microsoft 365').\n"
        "- NEVER echo the JD's required experience range as your own (e.g. do NOT write '3-5 years' because "
        "the posting asked for it — use only years/tenure documented in PROFILE_TEXT).\n"
        "- NEVER write meta-audit or gap-tracking language (e.g. 'X is not claimed', 'Y is supported by Z'). "
        "Omit unsupported requirements silently.\n"
        "- NEVER append self-deprecating qualifier tails to sentences. Forbidden tail phrases include: "
        "'without pretending it was X', 'without claiming X', 'without inflating to X', "
        "'without overstating X', 'though not at enterprise scale', 'though not at admin level', "
        "'while not claiming X', 'at small-shop scale' (as a trailing qualifier), "
        "'in a small-site environment', 'with limited network/admin/operational scope', "
        "'(at small-shop scale)', '(single-site)', '(limited X scope)'. Make a positive claim of what "
        "you did and stop. The honest scope is in PROFILE_TEXT — the reader does not need a narrated "
        "self-correction. These tails will be deterministically stripped before export, wasting tokens.\n"
        "- For help desk / service desk / desktop support roles: lead with service desk work, users, "
        "documentation, and support outcomes. Personal AI/automation projects belong only when the JD has "
        "a clear AI / data-tooling / Python / generative-AI hook AND honest-framing constraints are honored.\n"
        "- PERSONAL-PROJECTS ALLOWLIST (cover letter): approved projects are (a) 'Home Cleanliness Assistant' "
        "(reference only when the JD has a personal-AI or vision-model hook) and (b) 'AI Job-Application "
        "Pipeline' (reference only when the JD has an AI / data-engineering / Python / generative-AI hook, "
        "AND scoped to multi-source aggregation + LLM-driven scoring + tailored resume/cover letter generation "
        "— do NOT claim end-to-end auto-apply / auto-submit). The Organizer, Art pipeline, and Etsy 3D-printing "
        "are NOT approved — do NOT reference them anywhere in the cover letter.\n"
        "- Closing must be professional: express interest in discussing how your experience supports the team. "
        "Do NOT use casual lines like 'If useful, I can walk through...'.\n"
        "- State any quantity (e.g. years of experience) AT MOST ONCE, then prove it with examples; do not repeat it.\n"
        "- Do NOT assert or describe the work arrangement (remote/hybrid/onsite/relocation/location) in any way "
        "that could contradict JOB_DESCRIPTION. If the arrangement is mixed or unclear, do not mention it at all — "
        "focus on capability and fit.\n"
        "- Do not copy whole sentences from JOB_DESCRIPTION; demonstrate each requirement in the candidate's own words.\n\n"
        f"TARGET_JOB:\ntitle: {job_title}\ncompany: {company}\nlocation: {location}\n\n"
        f"JOB_DESCRIPTION:\n{desc}\n\n"
        f"CURATED_SUMMARY_CARD:\n{json.dumps(card, ensure_ascii=False)}\n\n"
        f"BASE_TEMPLATE_HINT (tone/structure only; do not copy placeholders literally):\n{template_block}\n\n"
        f"RESUME_TEXT:\n{resume_block}\n\n"
        f"PROFILE_TEXT:\n{profile_text}\n\n"
        "JSON keys (all required):\n"
        "proof_targets (array of 4-10 short strings — the supported JD requirements you chose to address),\n"
        "opening (string, 2-3 sentences — role interest plus the single strongest, specific fit hook; no boilerplate openers),\n"
        "body_paragraphs (array of 2-3 strings, each 2-4 sentences, ONE theme each, evidence-led),\n"
        "closing (string, 1-2 sentences — direct interest and a clear next step; no 'earliest convenience').\n"
        "Do NOT include salutation ('Dear ...') or sign-off ('Sincerely') in any field.\n"
        "Plain text only inside JSON string values.\n"
        "CRITICAL — NEVER emit a slot name as the value of that slot. The value of 'closing' "
        "must be the actual closing prose, NOT the literal word 'closing'. Same for 'opening' "
        "and 'body_paragraphs'. Never use placeholder tokens like 'TBD', 'placeholder', "
        "'lorem ipsum', or '[opening]' / '[closing]' in any field. If you cannot generate real "
        "prose for a field, omit it entirely rather than emitting a placeholder — the validator "
        "will retry the generation, which is cheaper than shipping a broken letter.\n"
    )

    repair_suffix = (
        "\n\nCRITICAL: Return ONLY one valid JSON object. "
        "No markdown fences or commentary."
    )
    attempts = ["", repair_suffix]

    last_err: Optional[Exception] = None
    for suffix in attempts:
        try:
            from job_pipeline.cache_prefix import static_writer_cache_prefix
            parsed = generate_json(
                "cover_letter",
                system=system,
                user=prompt + suffix,
                label="cover_letter",
                gemini_model=model,
                gemini_max_output_tokens=4096,
                system_cacheable_prefix=static_writer_cache_prefix(),
            )
            return _normalize_cover_letter_content(
                parsed,
                job_description=desc,
                profile_text=profile_text,
            )
        except ValueError as exc:
            # JSON parse failure from generate_json, or empty content from normalize.
            last_err = exc
            continue
        except LLMWritingError as exc:
            return {"error": f"llm_unavailable: {exc}"}
        except Exception as exc:
            return {"error": f"llm_unavailable: {exc}"}

    return {
        "error": "json_parse_failed",
        "detail": preview_model_text(str(last_err or "")),
        "raw": "",
    }


def tailor_cover_letter_from_jd(
    job_description: str,
    *,
    job_title: str = "",
    company: str = "",
    location: str = "",
    summary_card: Optional[Dict[str, Any]] = None,
    resume_text: str = "",
    template_hint: str = "",
    salutation_override: str = "",
    extra_facts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Manual-JD entrypoint — no DB required."""
    if not (job_description or "").strip():
        raise ValueError("job_description is empty.")

    profile_text = _load_grounded_profile_text()
    if not profile_text:
        raise ValueError(
            "No profile available. Run: python -m job_pipeline.bootstrap_resume_profile"
        )
    profile_text = _augment_profile_with_user_facts(profile_text, extra_facts or [])

    content = generate_cover_letter_content(
        job_title=(job_title or "").strip() or "the role",
        company=(company or "").strip() or "the company",
        location=(location or "").strip(),
        job_description=job_description,
        profile_text=profile_text,
        summary_card=summary_card,
        resume_text=resume_text,
        template_hint=template_hint,
        salutation_override=salutation_override,
    )
    if content.get("error"):
        return {"ok": False, "content": content, "job_title": job_title, "company": company}
    return {
        "ok": True,
        "content": content,
        "job_title": (job_title or "").strip() or "the role",
        "company": (company or "").strip() or "the company",
    }


def cover_letter_prose_blocks(content: Dict[str, Any]) -> List[str]:
    """Ordered paragraph list from structured tailor output."""
    if not isinstance(content, dict):
        return []
    # Final-export safety net: scrub claim-audit / first-person hedge language
    # ("I do not claim...", "I have partial...", "is supported through...") even
    # if upstream cover_letter_optimizer was skipped. Idempotent.
    from job_pipeline.integrity_guards import strip_claim_audit_in_text

    def _scrub(s: str) -> str:
        cleaned, _ = strip_claim_audit_in_text(s)
        return cleaned

    blocks: List[str] = []
    opening = _scrub(str(content.get("opening") or "").strip())
    if opening:
        blocks.append(opening)
    bodies = content.get("body_paragraphs")
    if isinstance(bodies, list):
        for p in bodies:
            para = _scrub(str(p).strip())
            if para:
                blocks.append(para)
    closing = _scrub(str(content.get("closing") or "").strip())
    if closing:
        blocks.append(closing)
    return blocks
