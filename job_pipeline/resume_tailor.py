"""
Job-specific resume content from reference profile (LinkedIn PDF) + posting text.
Grounded generation only — no fabricated employers, dates, degrees, or certifications.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from job_pipeline.genai_json import preview_model_text
from job_pipeline.genai_settings import DEFAULT_GEMINI_MODEL, gemini_model_for
from job_pipeline.llm_provider import (
    LLMWritingError,
    generate_json,
    writing_providers_available,
    writing_providers_missing_error,
)
from job_pipeline.cache_prefix import static_writer_cache_prefix
from career_understanding import get_profile_text_from_reference
from application_assets import load_application_assets_dict
from job_pipeline.resume_export import export_tailored_resume_markdown
from job_pipeline.db import get_item
from job_pipeline.bootstrap_resume_profile import (
    load_consolidated_profile,
    load_consolidated_profile_text,
)
from job_pipeline.named_requirements import (
    _is_support_target_role,
    account_management_wording_block,
    anti_hype_prompt_block,
    build_tailoring_requirement_strategy,
    check_account_management_wording,
    check_named_requirements_surfaced,
    curate_projects,
    curate_technical_skills,
    enforce_light_exposure_framing_on_skills,
    ensure_surfaced_keywords_in_skills,
    extract_requirements,
    find_hype_violations,
    find_jd_years_echo_violations,
    find_project_jargon_violations,
    fix_jd_years_echo_in_text,
    light_exposure_prompt_block,
    named_requirement_gaps,
    named_requirement_method_block,
    parse_light_exposure,
    project_framing_prompt_block,
    support_summary_framing_prompt_block,
    user_account_management_level,
    years_experience_prompt_block,
)
from job_pipeline.rendercv_export import dedupe_experience_self, dedupe_experience_vs_military
from job_pipeline.resume_optimizer import run_resume_optimization_pipeline


logger = logging.getLogger(__name__)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _parse_json_object(text: str) -> Dict[str, Any]:
    return parse_json_object_from_model(text)


def _resume_metadata_blob() -> str:
    assets = load_application_assets_dict()
    blobs = []
    for r in assets.get("resumes") or []:
        if isinstance(r, dict) and r.get("id"):
            blobs.append({"id": r.get("id"), "metadata": r.get("metadata") or {}})
    return json.dumps(blobs, ensure_ascii=False)[:3500]


def build_tailoring_strategy(
    job_title: str,
    job_description: str,
    profile_text: str,
    strategy_level: str,
) -> Dict[str, Any]:
    reqs = extract_requirements(job_description)
    title_l = (job_title or "").lower()
    meta = _resume_metadata_blob()
    meta_l = meta.lower()
    overlap = [r for r in reqs if r in profile_text.lower() or r in meta_l]

    narrative = "balanced_generalist"
    if any(x in title_l for x in ("senior", "lead", "principal", "director", "head")):
        narrative = "leadership_scope_and_impact"
    elif any(x in title_l for x in ("engineer", "developer", "software", "devops", "sre")):
        narrative = "technical_delivery"
    elif "manager" in title_l or "operations" in title_l:
        narrative = "operations_and_execution"

    req_strategy = build_tailoring_requirement_strategy(job_description, profile_text)
    btb_title, btb_score = _pick_btb_title_for_jd(job_title, job_description, profile_text)

    return {
        "risk_level": strategy_level,
        "matched_keywords_from_posting": reqs[:25],
        "overlap_with_profile_or_metadata": overlap,
        "narrative_angle": narrative,
        "resume_metadata_json": meta,
        "recommended_btb_title": btb_title,
        "recommended_btb_title_score": btb_score,
        **req_strategy,
    }


def _strategy_instructions(level: str) -> str:
    level = (level or "balanced").strip().lower()
    if level == "conservative":
        return (
            "Strategy: CONSERVATIVE — mirror phrasing from PROFILE_TEXT; minimal reinterpretation; "
            "omit uncertain items rather than stretching."
        )
    if level == "aggressive":
        return (
            "Strategy: AGGRESSIVE — strong, confident framing only where PROFILE_TEXT clearly supports it; "
            "still zero new employers, dates, degrees, or metrics not present in sources."
        )
    return (
        "Strategy: BALANCED — emphasize posting overlap; concise achievement bullets; no invented facts."
    )


def generate_tailored_sections(
    row: Dict[str, Any],
    profile_text: str,
    strategy: Dict[str, Any],
    strategy_level: str,
) -> Dict[str, Any]:
    if not writing_providers_available():
        return {"error": writing_providers_missing_error()}

    model = gemini_model_for("tailor")

    title = str(row.get("title") or "")
    company = str(row.get("company_name") or "")
    loc = str(row.get("location") or "")
    desc = str(row.get("description_text") or "")[:16000]
    card = row.get("summary_json")
    if isinstance(card, str):
        try:
            card = json.loads(card)
        except Exception:
            card = {}
    card = card if isinstance(card, dict) else {}

    prof_json = load_consolidated_profile()
    light_block = light_exposure_prompt_block(
        parse_light_exposure(profile_text, prof_json if isinstance(prof_json, dict) else None)
    )
    acct_block = account_management_wording_block(
        user_account_management_level(profile_text)
    )
    years_block = years_experience_prompt_block(desc, profile_text)
    project_block = project_framing_prompt_block()
    support_summary_block = support_summary_framing_prompt_block(title)
    recommended_btb_title = str(strategy.get("recommended_btb_title") or "").strip()
    recommended_btb_score = int(strategy.get("recommended_btb_title_score") or 0)
    btb_title_block = ""
    # Defensibility-filtered title for the summary opener. When the JD title
    # contains a no-exposure role term (e.g. "Security Analyst & Support
    # Technician"), the summary should NOT open with the verbatim JD title —
    # it would imply role-identity claims the candidate cannot defend.
    summary_open_title, summary_title_was_filtered = _defensible_summary_title(title)
    summary_title_block = ""
    if summary_title_was_filtered and summary_open_title != title:
        summary_title_block = (
            "SUMMARY OPENER — DEFENSIBLE TITLE (do not open with the verbatim JD title):\n"
            f"  JD title (verbatim): \"{title}\"\n"
            f"  Defensible opener  : \"{summary_open_title}\"\n"
            "  Reason: the JD title contains a no-exposure role term per PROFILE_TEXT honest limits.\n"
            "  Opening the summary with the verbatim JD title implies a role-identity claim the\n"
            "  candidate cannot defend. Open the summary with the DEFENSIBLE OPENER above\n"
            "  instead — e.g. for a JD titled 'Security Analyst & Support Technician', the\n"
            "  candidate opens as 'Support Technician candidate' or 'IT Support Technician\n"
            "  candidate', NOT 'Security Analyst & Support Technician candidate'.\n"
            "  A post-gen validator will rewrite the opener if you ignore this — using the\n"
            "  defensible opener up front saves tokens and produces cleaner prose.\n"
        )
    if recommended_btb_title:
        match_note = (
            f"matched {recommended_btb_score} trigger(s) in target"
            if recommended_btb_score > 0
            else "no triggers matched; using default"
        )
        btb_title_block = (
            "BEAT THE BOMB TITLE — JD-AWARE PICKER:\n"
            f"  Selected phrasing: \"{recommended_btb_title}\"\n"
            f"  Reason: {match_note}.\n"
            "  Use this exact string for the BEAT THE BOMB entry's experience[].title.\n"
            "  All approved alts are listed in PROFILE_TEXT Section 2.6 — selecting any other\n"
            "  entry from that block is acceptable only if it more accurately reflects the role\n"
            "  for this specific JD; never invent a title outside Section 2.6.\n"
            "  Frame bullets to align with the selected phrasing: when the picked title\n"
            "  emphasizes IT/sysadmin scope, lead BEAT THE BOMB bullets with technical work\n"
            "  (DNS, VLAN, Local GPO, backup/restore, hardware repair, kiosk imaging); when\n"
            "  the picked title emphasizes operations management, lead with cross-functional\n"
            "  ops scope (inventory, workflow improvements, cross-role fill-in). Both are\n"
            "  documented in Section 1.\n"
        )

    system = (
        "You are an expert resume writer. Return exactly one valid JSON object with no markdown "
        "fences or commentary. Follow every rule in the user message exactly."
    )
    # PROMPT CACHING: split the prompt into a STATIC rules prefix (cacheable)
    # and a DYNAMIC per-job suffix. The Claude prefix is identical across all
    # tailor calls in a session, so prompt caching charges it at 10% of normal
    # input cost on hits within the 5-min TTL window. Saves ~30-40% per build
    # when running multiple jobs. See claude_client.user_cacheable_prefix docs.
    rules_prefix = (
        "You are an expert resume writer. Output ONE JSON object ONLY (no markdown).\n"
        "Facts must come ONLY from PROFILE_TEXT and RESUME_METADATA_JSON. "
        "Do NOT invent employers, titles, dates, degrees, certifications, or metrics.\n"
        "If the profile does not support a section, use an empty array or a short honest summary line.\n\n"
        "PROFILE_TEXT PRECEDENCE: Sections appear in order: optional CANDIDATE-CONFIRMED FACTS (gap answers), "
        "then CAREER MASTER, then consolidated or reference resume text. "
        "CAREER MASTER governs framing and honest limits for everything after it. "
        "If consolidated text conflicts with CAREER MASTER, follow CAREER MASTER.\n"
        "CANDIDATE-CONFIRMED FACTS (when present) add or clarify resume-backed details for the tailor pass; "
        "they do NOT relax HONEST LIMITS or forbidden claims under CAREER MASTER — those stay absolute.\n\n"
        "HONEST LIMITS RULE: If PROFILE_TEXT contains a section labeled 'Honest limits', "
        "'do NOT claim', 'No exposure', or 'Touched but cannot claim', treat those as HARD CONSTRAINTS.\n"
        "  - Do not include any skill, title, tool, or experience listed under 'No exposure' anywhere "
        "in the output, even if the JOB_DESCRIPTION asks for it.\n"
        "  - For items under 'Touched but cannot claim' / 'small-shop scale', use ONLY the explicit "
        "acceptable framings provided in PROFILE_TEXT — prefer the candidate's phrasing over the JD's keywords.\n"
        "  - When the JD asks for something the candidate honestly lacks, omit it from skills and bullets. "
        "Do not pattern-match adjacent experience into the forbidden claim.\n"
        "  - It is better to ship a shorter, truthful resume than a longer one that overreaches.\n"
        "  - NEVER write meta-audit language in summary or bullets (e.g. 'X is not claimed', "
        "'PST coverage is not claimed', 'Y is supported by Z'). Omit gaps silently.\n"
        "  - NEVER append self-deprecating qualifier tails to bullets — forbidden tail phrases "
        "include: 'without pretending it was X', 'without claiming X', 'without inflating to X', "
        "'though not at enterprise scale', 'though not at admin level', 'while not claiming X', "
        "'at small-shop scale' (as a trailing qualifier), 'in a small-site environment', "
        "'with limited network/admin/operational scope', '(at small-shop scale)', "
        "'(single-site)', '(limited X scope)'. State what you did and stop. The honest scope "
        "is documented in PROFILE_TEXT — the reader does not need you to re-narrate the limit. "
        "These tails will be deterministically stripped at validation, wasting tokens.\n"
        "If PROFILE_TEXT contains a section labeled 'Honest limits', 'No exposure', "
        "'do NOT claim', or 'Never touched', any skill/tool/title listed there MUST "
        "NOT appear in the output, including hedged forms like 'General "
        "Understanding', 'Familiar with', 'Exposure to', or 'Basic knowledge of'. "
        "These are absolute exclusions, not framings to soften.\n"
        "Exception: LIGHT EXPOSURE approved phrasing (below) may be used ONLY for skills listed there — "
        "never for No exposure items.\n\n"
        + (light_block + "\n\n" if light_block else "")
        + (acct_block + "\n\n" if acct_block else "")
        + (years_block + "\n\n" if years_block else "")
        + (project_block + "\n\n" if project_block else "")
        + (support_summary_block + "\n\n" if support_summary_block else "")
        + f"{named_requirement_method_block()}\n"
        + f"{anti_hype_prompt_block()}\n"
        "SKILLS RULE: technical[] should list 18-22 of the most JD-relevant skills only. "
        "Prefer proof-target tools named in requirements_to_surface_by_name. "
        "Any keyword in summary MUST also appear in skills.technical. "
        "Omit (study)/(learning)-tagged lab tools unless the JD explicitly asks for them.\n"
        "MICROSOFT 365 RULE: If 'Microsoft 365' is in skills.technical, DO NOT also list its "
        "child components (Outlook, Teams, OneDrive, SharePoint, Word, Excel, PowerPoint, "
        "Exchange) as separate skills UNLESS the JD names them explicitly as distinct "
        "requirements. The umbrella covers them; listing both is keyword padding the validator "
        "will deterministically strip.\n"
        "SOFT SKILLS RULE: skills.soft[] should contain 4-6 entries — the most JD-relevant. "
        "Vague entries like 'Independent Work', 'Conflict Resolution', or 'Cross-Functional "
        "Coordination' should only appear when the JD explicitly asks for them. The validator "
        "caps soft skills at 6 by JD relevance — exceeding that wastes tokens.\n"
        "M365 ADMIN VERB RULE: When the profile flags Microsoft 365 as user-level only, "
        "NEVER pair admin verbs (Managed, Administered, Configured, Maintained, Architected, "
        "Deployed, Owned, Oversaw, Provisioned, Implemented) with Microsoft 365 in bullets or "
        "summary. Use 'Used', 'Supported', 'Worked in', 'Handled' instead. Example: NOT "
        "'Managed user accounts using Microsoft 365'; YES 'Supported user accounts in "
        "Microsoft 365' or 'Handled user-level Microsoft 365 tasks'. The validator will "
        "deterministically rewrite admin-verb pairings — getting it right up front saves tokens.\n"
        "NO-EMPLOYER-INDUSTRY-INFERENCE RULE: Do NOT invent bullets by inferring duties from "
        "the employer's INDUSTRY when the duty is not documented in PROFILE_TEXT. Example "
        "(forbidden): the employer name '1-800-GOT-JUNK' tells you it's junk removal — do "
        "NOT therefore claim fleet maintenance, waste-stream compliance, e-waste handling, "
        "hazardous-material handling, safety-protocol training, environmental compliance, "
        "or DOT/OSHA scope. The candidate's ACTUAL documented scope is what was in PROFILE_TEXT — "
        "if a duty isn't there, omit the bullet entirely rather than fabricating from industry "
        "knowledge. PROFILE_TEXT Section 8 includes explicit 'forbidden inferences' subsections "
        "for known-target employers; follow them. The validator strips bullets that violate this.\n"
        "PROJECTS RULE: include at most 1-2 projects most relevant to TARGET_JOB.title; "
        "drop tangential personal projects for support/helpdesk roles. **PROJECTS ALLOWLIST: "
        "the projects currently approved for the resume Projects block are (a) 'Home Cleanliness "
        "Assistant' and (b) 'AI Job-Application Pipeline'. For (b), describe ONLY the working pieces: "
        "multi-source job aggregation, LLM-driven fit scoring, and automated per-posting tailored "
        "resume + cover letter generation. Do NOT claim end-to-end auto-apply / auto-submit — that "
        "component is not currently working as intended and is off-limits. Other projects in "
        "PROFILE_TEXT ('The Organizer', Art pipeline, Etsy 3D-printed sets) are NOT approved yet — "
        "do NOT add them. Emit an empty projects array if neither approved project fits TARGET_JOB. "
        "A deterministic post-gen guard enforces this allowlist.**\n\n"
        "experience[].title must equal either (a) the candidate's official job "
        "title as it appears in PROFILE_TEXT, or (b) a 'practical / in practice' "
        "alternative phrase explicitly stated in PROFILE_TEXT. Do NOT invent a "
        "title derived from the target JD.\n\n"
        + (btb_title_block + "\n" if btb_title_block else "")
        + (summary_title_block + "\n" if summary_title_block else "")
        + f"{_strategy_instructions(strategy_level)}\n"
    )
    # Per-job DYNAMIC suffix — this changes every build. The system_cacheable_prefix
    # (in static_writer_cache_prefix()) already contains the candidate's PROFILE_TEXT,
    # evidence block, and static writer rules — DO NOT duplicate them here or you'll
    # double the input tokens AND break the cache. Anything per-JD goes here.
    dynamic_suffix = (
        "TARGET_JOB:\n"
        f"title: {title}\ncompany: {company}\nlocation: {loc}\n\n"
        f"JOB_DESCRIPTION:\n{desc}\n\n"
        f"PIPELINE_CARD_SUMMARY (may help focus; still subordinate to PROFILE_TEXT in the cached prefix):\n{json.dumps(card, ensure_ascii=False)[:2000]}\n\n"
        f"TAILORING_STRATEGY:\n{json.dumps({k: v for k, v in strategy.items() if k != 'resume_metadata_json'}, ensure_ascii=False)}\n\n"
        f"RESUME_METADATA_JSON:\n{strategy.get('resume_metadata_json', '[]')}\n\n"
        "JSON keys:\n"
        "summary (string, 3-5 lines; open with the exact target job title string from TARGET_JOB "
        "(verbatim wording), then cover fit in 3-5 lines total; MUST cite supported named JD "
        "requirements BY NAME when listed in requirements_to_surface_by_name. "
        "SUMMARY COHERENCE: write grammatically complete sentences with proper punctuation. "
        "End every sentence with a period before starting the next. NEVER run two independent clauses "
        "together without a conjunction or period — e.g. NOT 'Handled X and onboarding used Microsoft 365 for Y' "
        "(missing period or conjunction between 'onboarding' and 'used Microsoft 365'); YES 'Handled X and "
        "onboarding. Used Microsoft 365 for Y.' Each sentence must be parseable on its own; if you find "
        "yourself joining noun phrases with past-tense verbs mid-clause, break into a new sentence),\n"
        "experience (array of {title, company, duration, bullets[]} — only roles supported by PROFILE_TEXT),\n"
        "skills {technical: string[], soft: string[]},\n"
        "projects (array of {name, description, impact}, optional — factual tone only, no hype).\n"
        "Bullets: max 7 per role, each under 220 characters, lead with strong verbs when supported by text.\n"
    )
    # Combined prompt for providers that don't support per-block caching
    # (Gemini, OpenAI). Claude path uses rules_prefix as the cacheable prefix
    # and dynamic_suffix as the user content — see the generate_json call below.
    prompt = rules_prefix + dynamic_suffix

    repair_suffix = (
        "\n\nCRITICAL: Your previous answer was not valid JSON. "
        "Return ONLY one JSON object matching the schema above. "
        "No markdown fences, no commentary, no text before or after the object."
    )

    attempts: List[Tuple[str, str]] = [
        ("", ""),
        ("", repair_suffix),
    ]
    if model != DEFAULT_GEMINI_MODEL:
        attempts.append(("", repair_suffix))

    last_err: Optional[Exception] = None
    for _, suffix in attempts:
        try:
            return generate_json(
                "tailor",
                system=system,
                # CACHING STRATEGY (Claude only):
                #   - system_cacheable_prefix = static_writer_cache_prefix(): the
                #     5K-token static block (rules + profile + evidence) that's
                #     IDENTICAL across every writer Claude call in a build. This
                #     gets cached with 1-hour TTL on the first call and read at
                #     ~10% input cost on every subsequent call.
                #   - user_cacheable_prefix = rules_prefix: the per-JD strategy
                #     blocks (light/acct/years/project/strategy). These vary per
                #     job but stay constant within a build, so if the same job
                #     hits multiple writer calls (resume + cover letter) they
                #     get cached too.
                #   - user = dynamic_suffix: per-call dynamic content (JD,
                #     target, schema reminder).
                # Providers that don't support caching (Gemini, OpenAI) get
                # everything concatenated into the user message.
                user=dynamic_suffix + suffix,
                user_cacheable_prefix=rules_prefix,
                system_cacheable_prefix=static_writer_cache_prefix(),
                label="tailor_sections",
                gemini_model=model,
                gemini_max_output_tokens=8192,
            )
        except ValueError as exc:
            last_err = exc
            logger.warning(
                "tailor_sections JSON parse failed (%s); trying next attempt",
                exc,
            )
        except LLMWritingError as exc:
            logger.error("generate_tailored_sections failed: %s", exc)
            return {"error": f"llm_unavailable: {exc}"}
        except Exception as exc:
            logger.error("generate_tailored_sections failed: %s", exc)
            return {"error": f"llm_unavailable: {exc}"}

    logger.error(
        "generate_tailored_sections exhausted JSON retries preview=%s",
        preview_model_text(str(last_err or "")),
    )
    return {
        "error": "json_parse_failed",
        "detail": str(last_err or "no JSON object in model response"),
        "raw": "",
    }


# --- Post-generation grounding guardrails (honest limits, skills/projects/titles) ---

_STATIC_FORBIDDEN_SKILL_SUBSTRINGS: List[str] = [
    "active directory",
    "azure active directory",
    "azure ad",
    "microsoft entra",
    "entra id",
    "gpmc",
    "group policy management console",
    "wsus",
    "system center configuration manager",
    "sccm",
    "mecm",
    "microsoft intune",
    "intune",
    "esxi",
    "vsphere",
    "hyper-v",
    "microsoft hyper-v",
    "proxmox",
    "okta",
    "sailpoint",
    "amazon web services",
    "google cloud platform",
    "splunk",
    "microsoft sentinel",
    "azure sentinel",
    "datadog",
    "servicenow",
    "jira service management",
    # Enterprise IAM / identity tooling — career_master Section 3 "No exposure"
    # under "Enterprise IAM" lists Okta, Microsoft Entra/Azure AD, SailPoint,
    # SSO configuration, conditional access. The acronym-style skill claims
    # ("MFA/SSO", "Single Sign-On", "Conditional Access") were leaking through
    # because only the platform names (Okta, etc.) were in this list.
    "single sign-on",
    "single sign on",
    "multi-factor authentication",
    "multi factor authentication",
    "multifactor authentication",
    "two-factor authentication",
    "two factor authentication",
    "conditional access",
    "identity provider",
    "federated identity",
    "identity federation",
    "privileged access management",
    "privileged identity management",
]


# Short acronyms requiring word-boundary matching to avoid false positives on
# substrings (e.g. "elk" would match inside other words without boundaries).
# Each acronym corresponds to a No-Exposure tool/concept in career_master
# Section 3 — see comments on _STATIC_FORBIDDEN_SKILL_SUBSTRINGS for sources.
_FORBIDDEN_SKILL_ACRONYMS: Tuple[str, ...] = (
    "aws",   # AWS at engineer level — no exposure
    "gcp",   # GCP at engineer level — no exposure
    "elk",   # ELK stack at admin/query-author level — no exposure
    "mfa",   # multi-factor auth — no enterprise IAM exposure
    "sso",   # single sign-on — no enterprise IAM exposure
    "iam",   # cloud/enterprise IAM at engineer level — no exposure
    "2fa",   # two-factor — no enterprise IAM exposure
    "scim",  # System for Cross-domain Identity Management — no exposure
    "idp",   # Identity Provider — no exposure (rare term, low FP risk)
)


def norm_title_canon(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _honest_limits_body(profile_text: str) -> str:
    m = re.search(
        r"(?ims)^#{1,6}\s*[^\n]*\b3[\.\)]\s*[^\n]*Honest\s+limits[^\n]*\n(.*?)(?=^#{1,6}\s*4[\.\)])",
        profile_text,
    )
    return m.group(1) if m else ""


def _bullet_phrases_from_limits_body(body: str) -> List[str]:
    out: List[str] = []
    for line in (body or "").splitlines():
        line = line.strip()
        if not line.startswith("-") and not line.startswith("*"):
            continue
        rest = re.sub(r"^[\-\*\+]\s+", "", line)
        bm = re.match(r"\*\*([^*]{2,240})\*\*", rest)
        chunk = bm.group(1).strip().split("—")[0].split("-")[0].strip() if bm else ""
        if not chunk:
            chunk = rest.split("—")[0].split(".")[0].strip()
        if len(chunk) < 4:
            continue
        out.append(chunk.lower())
        for part in re.split(r"[\u2014\-\|,]+", chunk):
            p = part.strip().lower()
            if len(p) >= 6:
                out.append(p)
    return sorted(set(out), key=len, reverse=True)


def _compile_forbidden_skill_matchers(profile_text: str) -> List[re.Pattern[str]]:
    lim = _honest_limits_body(profile_text)
    phrases = _bullet_phrases_from_limits_body(lim)
    uniq_phrases = sorted(
        {p.lower() for p in phrases}.union({s.lower() for s in _STATIC_FORBIDDEN_SKILL_SUBSTRINGS}),
        key=len,
        reverse=True,
    )
    matchers: List[re.Pattern[str]] = []
    seen: set[str] = set()
    for phrase in uniq_phrases:
        if len(phrase) < 3:
            continue
        rx = re.compile(re.escape(phrase), re.IGNORECASE)
        if rx.pattern in seen:
            continue
        seen.add(rx.pattern)
        matchers.append(rx)
    for w in _FORBIDDEN_SKILL_ACRONYMS:
        rx = re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.IGNORECASE)
        if rx.pattern not in seen:
            seen.add(rx.pattern)
            matchers.append(rx)
    return matchers


def _skill_contains_forbidden_impl(skill: str, matchers: List[re.Pattern[str]]) -> Tuple[bool, str]:
    s = skill or ""
    for rx in matchers:
        m = rx.search(s)
        if m:
            return True, m.group(0)
    return False, ""


def _skill_blocked_by_honest_limits(
    skill: str,
    profile_text: str,
    light_exposure_labels_lower: Set[str],
) -> Tuple[bool, str]:
    """Reverse-direction check: is the bare SKILL a substring of any No-Exposure
    phrase in career_master?

    Catches the leak where the LLM produces "Active Directory" alone in skills
    even though career_master forbids "Active Directory administration via GPMC".
    The forward `_skill_contains_forbidden_impl` only fires when the long
    phrase is inside the skill — useless for bare-term overclaims.

    Skips skills explicitly authorized via Light Exposure (so "Windows Server
    basics" survives even though "Windows Server administration" is forbidden).
    """
    s = (skill or "").strip().lower()
    if not s or len(s) < 3:
        return False, ""
    if s in light_exposure_labels_lower:
        return False, ""
    # Also skip when the skill HAS a qualifier (e.g. "Active Directory basics",
    # "Microsoft Entra ID" — these are honest framings).
    if re.search(r"\b(basics?|fundamentals?|exposure|home[\- ]lab|familiar)\b", s):
        return False, ""
    # Pull no-exposure phrases from the profile.
    from job_pipeline.named_requirements import parse_no_exposure_phrases  # local import to avoid cycle

    for phrase in parse_no_exposure_phrases(profile_text):
        p = phrase.strip().lower()
        if not p or p == s:
            continue
        # If the bare skill appears as a word-bounded substring of a forbidden
        # phrase, treat it as an overclaim. Whole-word match on the skill side
        # so "AD" doesn't match "AD-integrated server roles" through "ad" in
        # "advanced" (it won't here anyway, but the boundary is cheap insurance).
        if re.search(rf"\b{re.escape(s)}\b", p):
            return True, phrase
    return False, ""


def _office365_in_profile(profile_lower: str) -> bool:
    return bool(
        re.search(
            r"microsoft\s*365|office\s*365|\bm365\b",
            profile_lower,
        )
    )


def _office365_skill(skill: str) -> bool:
    return bool(re.search(r"microsoft\s*365|office\s*365|\bm365\b", skill, re.I))


def _career_master_plaintext_lower() -> str:
    """Raw career_master.md (not the stacked PROFILE_TEXT). Used for M365 skill allowlisting."""
    master_path = os.path.join(_repo_root(), "job_pipeline", "career_master.md")
    if not os.path.isfile(master_path):
        return ""
    try:
        with open(master_path, encoding="utf-8") as f:
            return f.read().lower()
    except OSError:
        return ""


def _strip_office365_phrasing(skill: str) -> str:
    """Drop Microsoft / Office / M365 wording; normalize slashes and commas left behind."""
    t = str(skill or "")
    t = re.sub(r"(?i)\bmicrosoft\s*365\b", "", t)
    t = re.sub(r"(?i)\boffice\s*365\b", "", t)
    t = re.sub(r"(?i)\bm365\b", "", t)
    t = re.sub(r"[,\s]*\/[,\s]*", "/", t)
    t = re.sub(r"\s+", " ", t).strip(" ,/\t\r\n;-")
    t = re.sub(r"\s+\/\s+\/\s+", " / ", t)
    t = re.sub(r"(?i)^(/?\s*)+/", "", t)
    t = re.sub(r"\s+/\s+$", "", t)
    return t.strip(" ,/\t\r\n;-").strip()


# ---------------------------------------------------------------------------
# Incident-response overclaim stripper (issue 4 from latest review)
# ---------------------------------------------------------------------------
# Carlos has real medical incident-response training (68W combat medic with
# mass-casualty / CBRN exposure), but BTB live troubleshooting is NOT formal
# "incident response procedures" / "incident response training." The LLM
# tends to co-mingle the two — "Applied military and venue incident response
# procedures" — which implies formal IR training at BTB that doesn't exist.

_IR_OVERCLAIM_PATTERNS: Tuple[Tuple[str, str, str], ...] = (
    # "military and venue incident response procedures/training"
    (
        r"\bmilitary\s+and\s+(?:venue|btb|beat\s+the\s+bomb)\s+incident\s+response\s+(?:procedures?|training)\b",
        "military medic incident response training and live-venue troubleshooting",
        "co-mingled military + venue IR procedures",
    ),
    # Reversed order: "venue and military incident response procedures"
    (
        r"\b(?:venue|btb|beat\s+the\s+bomb)\s+and\s+military\s+incident\s+response\s+(?:procedures?|training)\b",
        "live-venue troubleshooting and military medic incident response training",
        "venue + military IR procedures (reversed order)",
    ),
    # Bare "venue incident response procedures/training" without military pairing
    (
        r"\b(?:venue|btb|beat\s+the\s+bomb)\s+incident\s+response\s+(?:procedures?|training)\b",
        "live-venue troubleshooting under time pressure",
        "bare venue IR procedures claim",
    ),
    # "Incident response procedures at BTB / at BEAT THE BOMB / at the venue"
    (
        r"\bincident\s+response\s+(?:procedures?|training)\s+at\s+(?:the\s+venue|btb|beat\s+the\s+bomb)\b",
        "live troubleshooting at BEAT THE BOMB",
        "IR procedures-at-venue claim",
    ),
)


def _downgrade_ir_overclaim_in_text(text: str) -> Tuple[str, List[str]]:
    """Rewrite incident-response overclaim patterns. Returns (new_text, notes)."""
    notes: List[str] = []
    out = text or ""
    for pat, replacement, friendly in _IR_OVERCLAIM_PATTERNS:
        rx = re.compile(pat, re.IGNORECASE)
        new_out, n = rx.subn(replacement, out)
        if n:
            notes.append(f"downgraded IR overclaim ({friendly}, {n}x)")
            out = new_out
    return out, notes


def _downgrade_ir_overclaim_phrasing(
    content: Dict[str, Any],
    issues: List[str],
) -> None:
    """Sweep summary, bullets, projects, and sidecar _role_thesis for the
    'venue incident response procedures' family of overclaims.
    """
    if not isinstance(content, dict) or content.get("error"):
        return

    summary = str(content.get("summary") or "")
    if summary:
        new_summary, notes = _downgrade_ir_overclaim_in_text(summary)
        if new_summary != summary:
            content["summary"] = new_summary
            for n in notes:
                issues.append(f"summary: {n}")

    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            bullets = exp.get("bullets")
            if not isinstance(bullets, list):
                continue
            new_bullets: List[str] = []
            for b in bullets:
                bs = str(b)
                new_b, notes = _downgrade_ir_overclaim_in_text(bs)
                if new_b != bs:
                    for n in notes:
                        issues.append(f"bullet: {n}")
                new_bullets.append(new_b)
            exp["bullets"] = new_bullets

    projs = content.get("projects")
    if isinstance(projs, list):
        for proj in projs:
            if not isinstance(proj, dict):
                continue
            for field in ("description", "impact"):
                val = str(proj.get(field) or "")
                if not val:
                    continue
                new_val, notes = _downgrade_ir_overclaim_in_text(val)
                if new_val != val:
                    proj[field] = new_val
                    for n in notes:
                        issues.append(f"project.{field}: {n}")

    for sidecar in ("_role_thesis",):
        val = str(content.get(sidecar) or "")
        if not val:
            continue
        new_val, notes = _downgrade_ir_overclaim_in_text(val)
        if new_val != val:
            content[sidecar] = new_val
            for n in notes:
                issues.append(f"{sidecar}: {n}")


# ---------------------------------------------------------------------------
# Domain-irrelevant skill stripper (issue D)
# ---------------------------------------------------------------------------
# Carlos has real Light Exposure to several entertainment/AV/live-production
# domain skills (Cisco audio troubleshooting, DMX, Dante audio, RFID, OBS,
# Unity file management). When the JD is a vanilla IT support role with no
# AV/entertainment context, those skills are irrelevant noise — they signal
# wrong-target-role and dilute the JD-relevant signal in skills.technical.
#
# Pattern: each entry is a tag word that marks a skill as domain-specific.
# If a skill contains one of these tags AND the JD does NOT mention that tag,
# the skill is dropped. The JD-mention check uses word-boundary matching so
# "production environment" in a JD doesn't pull in skills tagged with bare
# "production" via substring matching.
_DOMAIN_SPECIFIC_TAG_WORDS: Tuple[str, ...] = (
    "audio",
    "video",
    "av",          # audio-visual
    "lighting",
    "dmx",
    "dante",
    "broadcasting",
    "streaming",
    "rfid",
    "obs",
    "unity",
    "kiosk",
    "kiosks",
    "venue",
    "game",        # game-room / game backend
    "gaming",
    "music",
    "stage",
    "podcast",
    "podcasts",
    "stream",
    "twitch",
    "elgato",
)


def _drop_domain_irrelevant_skills(
    content: Dict[str, Any],
    job_description: str,
    issues: List[str],
) -> None:
    """Drop skills containing domain-specific tag words when the JD doesn't
    mention those domains. Catches entertainment/AV/live-production skills
    surfacing on unrelated IT support / business JDs.

    A skill with tokens {a, b, c} is dropped iff at least one token is in
    the domain-tag list AND none of those tag tokens appear word-bounded
    in the JD. Skills without domain tags are untouched.
    """
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return
    tech = sk.get("technical")
    if not isinstance(tech, list):
        return
    jd_lower = (job_description or "").lower()
    tag_set = set(_DOMAIN_SPECIFIC_TAG_WORDS)
    new_tech: List[str] = []
    for s in tech:
        ss = str(s)
        sl = ss.lower()
        tokens = [t for t in re.split(r"[^\w]+", sl) if t]
        skill_tags = [t for t in tokens if t in tag_set]
        if not skill_tags:
            new_tech.append(ss)
            continue
        # Check if any of this skill's tag words appear word-bounded in JD.
        jd_has_any = any(
            re.search(rf"\b{re.escape(tag)}\b", jd_lower)
            for tag in skill_tags
        )
        if jd_has_any:
            new_tech.append(ss)
        else:
            issues.append(
                f"dropped domain-irrelevant skill '{ss}' — contains domain "
                f"tag(s) {skill_tags} not present in JD"
            )
    sk["technical"] = new_tech


# ---------------------------------------------------------------------------
# JD-named-requirement gap skills stripper (issue C)
# ---------------------------------------------------------------------------
# The LLM tends to put JD-required skills into skills.technical even when the
# profile doesn't document them ("VPN" appears in JD -> LLM puts VPN in skills
# despite Carlos having zero VPN exposure). The named-requirement assessor
# already flags these as "gaps" — this stripper consumes that classification
# and removes the corresponding skill claims.


def _strip_ungrounded_skills_from_gaps(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str,
    issues: List[str],
) -> None:
    """Strip skills.technical items whose label matches a JD named-requirement
    GAP (a requirement the JD asks for that profile_text does NOT support).

    Dynamic — uses assess_named_requirements() to compute the gap set. As
    career_master grows to document new skills, the gap shrinks and previously
    stripped skills are automatically allowed again. No code change needed.

    Word-boundary matching on lowercase forms prevents collateral damage on
    skills that merely contain a gap term as a substring of a different word.
    """
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return
    tech = sk.get("technical")
    if not isinstance(tech, list):
        return

    from job_pipeline.named_requirements import (
        assess_named_requirements,
        NAMED_REQUIREMENTS,
    )

    # CRITICAL: do NOT pass tailored_content here. Including the LLM's own
    # generated content creates a circular reference where the LLM's claim
    # of "VPN" in skills counts as profile evidence for VPN, classifying
    # VPN as to_surface instead of gap. Profile_text is the authoritative
    # ground truth for what the candidate actually has.
    assessment = assess_named_requirements(job_description, profile_text)
    gaps = assessment.get("gaps") or []
    if not gaps:
        return

    # Index NamedRequirement entries by lowercase label so we can pull
    # surface_names for richer match coverage.
    nr_by_label = {nr.label.lower(): nr for nr in NAMED_REQUIREMENTS}

    forbidden_terms: List[str] = []
    seen: set = set()
    for g in gaps:
        req = str(g.get("requirement") or g.get("label") or "").strip().lower()
        if not req or req in seen:
            continue
        seen.add(req)
        forbidden_terms.append(req)
        nr = nr_by_label.get(req)
        if nr:
            for sn in nr.surface_names or ():
                snl = str(sn or "").strip().lower()
                if snl and snl not in seen:
                    seen.add(snl)
                    forbidden_terms.append(snl)

    if not forbidden_terms:
        return

    # Sort longest-first so multi-word terms ("active directory") match before
    # their substrings ("directory").
    forbidden_terms.sort(key=len, reverse=True)
    matchers = [
        (term, re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE))
        for term in forbidden_terms
    ]

    new_tech: List[str] = []
    for s in tech:
        ss = str(s)
        matched_term = None
        for term, rx in matchers:
            if rx.search(ss):
                matched_term = term
                break
        if matched_term:
            issues.append(
                f"stripped ungrounded skill '{ss}' — matches JD named-requirement "
                f"GAP '{matched_term}' (profile_text does not support this claim)"
            )
        else:
            new_tech.append(ss)
    sk["technical"] = new_tech


# ---------------------------------------------------------------------------
# Fabricated-employer-duty stripper (issue E)
# ---------------------------------------------------------------------------
# The LLM tends to infer industry-specific duties from employer names (e.g.,
# "1-800-GOT-JUNK" -> hauling/waste/fleet/mechanic bullets). When those
# duties aren't documented in PROFILE_TEXT, the bullet is a fabrication.
# Each entry is (regex, friendly_name).
_GOT_JUNK_FABRICATION_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"mechanical\s+servicing", "mechanical servicing"),
    (r"\bvehicle\s+(?:maintenance|servicing|repair|inspection|inspections|compliance)", "vehicle maintenance/servicing/compliance"),
    (r"\bfleet\s+(?:maintenance|servicing|repair|readiness|inspection|inspections|compliance)", "fleet maintenance/readiness/compliance"),
    (r"waste[- ]stream\s+(?:compliance|management|handling|disposal)", "waste-stream compliance/management"),
    (r"\be[- ]?waste\b", "e-waste handling"),
    (r"\brecyclables?\b", "recyclables handling"),
    (r"\bhazardous\s+(?:items?|materials?|waste|chemicals?)\b", "hazardous-material handling"),
    (r"safety\s+protocols?\s+(?:training|standards?)", "safety-protocol training"),
    (r"trained?\s+frontline\s+teams?", "trained frontline teams"),
    (r"trained?\s+(?:new\s+)?(?:drivers?|helpers?|crews?)\b", "trained drivers/helpers"),
    (r"logistical\s+best\s+practices", "logistical best practices coordination"),
    (r"environmental\s+(?:compliance|regulations?)", "environmental compliance"),
    (r"disposal\s+(?:regulations?|standards?|protocols?)", "disposal regulations"),
    (r"\bDOT\s+(?:compliance|regulations?|standards?)", "DOT compliance"),
    (r"\bOSHA\b", "OSHA compliance"),
    (r"emissions?\s+(?:compliance|standards?|reporting)", "emissions compliance"),
)


def _strip_fabricated_gotjunk_bullets(
    content: Dict[str, Any],
    profile_text: str,
    issues: List[str],
) -> None:
    """For each 1-800-GOT-JUNK experience entry, drop any bullet whose text
    matches a known fabrication pattern that is NOT also documented in the
    profile_text.

    Whole-bullet strip — these patterns describe duties Carlos did not
    perform, so a bullet containing them is fundamentally a fabrication
    regardless of any documented content it may co-mingle.
    """
    exps = content.get("experience")
    if not isinstance(exps, list):
        return
    profile_lower = (profile_text or "").lower()
    for exp in exps:
        if not isinstance(exp, dict):
            continue
        company = str(exp.get("company") or "")
        cl = company.lower()
        is_gotjunk = (
            "gotjunk" in cl.replace(" ", "").replace("-", "")
            or "got-junk" in cl
            or "got junk" in cl
            or "1-800" in cl and "junk" in cl
            or "1800" in cl and "junk" in cl
        )
        if not is_gotjunk:
            continue
        bullets = exp.get("bullets")
        if not isinstance(bullets, list):
            continue
        new_bullets: List[str] = []
        for b in bullets:
            bs = str(b)
            bs_lower = bs.lower()
            matched_fabrications: List[str] = []
            for pat, friendly in _GOT_JUNK_FABRICATION_PATTERNS:
                rx = re.compile(pat, re.IGNORECASE)
                if rx.search(bs):
                    # Only treat as fabrication if profile_text doesn't
                    # support it — keeps the door open for the rare case
                    # where Carlos updates career_master to add the duty.
                    if not rx.search(profile_lower):
                        matched_fabrications.append(friendly)
            if matched_fabrications:
                issues.append(
                    f"stripped fabricated 1-800-GOT-JUNK bullet "
                    f"(claims undocumented: {', '.join(matched_fabrications[:3])}): "
                    f"'{bs[:80]}{'...' if len(bs) > 80 else ''}'"
                )
                continue
            new_bullets.append(bs)
        exp["bullets"] = new_bullets


# ---------------------------------------------------------------------------
# Microsoft 365 phrasing guards (issue #5 follow-up)
# ---------------------------------------------------------------------------

# When the profile flags M365 as user-level only (no tenant admin), these
# admin-verb patterns indicate the LLM over-claimed M365 administration in
# a bullet/summary. They get deterministically rewritten to user-level verbs.
_M365_KEYWORD_RE = re.compile(r"Microsoft\s*365|Office\s*365|\bM365\b", re.IGNORECASE)

# "Managed X using/in/via/across Microsoft 365" -> "Supported X in Microsoft 365"
# Verb must be strong enough to imply admin; preserved verbs like "Used",
# "Worked", "Supported", "Handled" don't trigger.
_M365_ADMIN_VERB_PHRASE_RE = re.compile(
    r"\b(Managed|Administered|Configured|Maintained|Architected|Deployed|"
    r"Owned|Oversaw|Provisioned|Implemented|Built)\s+"
    r"([^.;,]{3,80}?)\s+(?:using|via|in|across|within|through)\s+"
    r"(Microsoft\s*365|M365|Office\s*365)\b",
    re.IGNORECASE,
)

# "Managed/Configured Microsoft 365 [object]" (direct object form)
_M365_ADMIN_VERB_DIRECT_RE = re.compile(
    r"\b(Managed|Administered|Configured|Maintained|Architected|Deployed|"
    r"Provisioned|Implemented)\s+"
    r"(Microsoft\s*365|M365|Office\s*365)\b",
    re.IGNORECASE,
)

# Components of M365 that are redundant once the M365 umbrella is listed.
# Listing them separately is keyword padding unless the JD names them
# explicitly as separate requirements.
_M365_COMPONENT_NAMES: Tuple[str, ...] = (
    "Outlook",
    "Teams",
    "Microsoft Teams",
    "OneDrive",
    "SharePoint",
    "Exchange",
    "Word",
    "Microsoft Word",
    "Excel",
    "Microsoft Excel",
    "PowerPoint",
    "Microsoft PowerPoint",
    "OneNote",
)


def _m365_user_only_in_profile(profile_text: str) -> bool:
    """True if profile flags Microsoft 365 as user-level only (no tenant admin).
    Looks for the explicit honest-limit phrasing in career_master Section 1.
    """
    if not profile_text:
        return False
    blob = profile_text.lower()
    # Explicit user-level disclaimer in Section 1
    if "power end-user" in blob and "not m365 admin" in blob:
        return True
    if "not microsoft 365 admin" in blob or "no tenant administration" in blob:
        return True
    # Section 3 enterprise IAM no-exposure also implies M365 admin is no-exposure
    if "enterprise iam" in blob and "no exposure" in blob:
        return True
    return False


def _downgrade_m365_admin_in_text(text: str) -> Tuple[str, int]:
    """Rewrite admin-verb + M365 phrasing in a single string.
    Returns (cleaned_text, replacement_count).
    """
    if not text:
        return text, 0
    out = text
    count = 0

    def _rewrite_phrase(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        action_text = m.group(2).strip()
        m365 = m.group(3)
        return f"Supported {action_text} in {m365}"

    out = _M365_ADMIN_VERB_PHRASE_RE.sub(_rewrite_phrase, out)

    def _rewrite_direct(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        m365 = m.group(2)
        return f"Worked in {m365}"

    out = _M365_ADMIN_VERB_DIRECT_RE.sub(_rewrite_direct, out)
    return out, count


def _downgrade_m365_admin_phrasing(
    content: Dict[str, Any],
    profile_text: str,
    issues: List[str],
) -> None:
    """Resume-content M365 admin-verb downgrade.

    Activates only when the profile flags M365 as user-level only. Rewrites
    bullets and summary so admin verbs (Managed/Administered/Configured/...)
    paired with Microsoft 365 get downgraded to user-level verbs (Supported,
    Worked in).
    """
    if not _m365_user_only_in_profile(profile_text):
        return
    summary = str(content.get("summary") or "")
    if summary:
        new_summary, n = _downgrade_m365_admin_in_text(summary)
        if n:
            content["summary"] = new_summary
            issues.append(
                f"downgraded {n} M365 admin-verb phrase(s) in summary to user-level wording"
            )
    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            bullets = exp.get("bullets")
            if not isinstance(bullets, list):
                continue
            new_bullets: List[str] = []
            total = 0
            for b in bullets:
                bs = str(b)
                new_b, n = _downgrade_m365_admin_in_text(bs)
                if n:
                    total += n
                new_bullets.append(new_b)
            if total:
                exp["bullets"] = new_bullets
                issues.append(
                    f"downgraded {total} M365 admin-verb phrase(s) in bullets to user-level wording"
                )


def _dedup_m365_components_from_skills(
    content: Dict[str, Any],
    job_description: str,
    issues: List[str],
) -> None:
    """If 'Microsoft 365' is in skills.technical AND a child component
    (Outlook/Teams/Word/Excel/etc.) is also listed, drop the components
    unless the JD names them as a distinct requirement.

    Keeps the umbrella as the canonical recruiter-search term and removes
    redundant padding. Components that the JD explicitly mentions (e.g.
    "Microsoft 365 administration: Exchange, Teams, OneDrive") are kept
    because they match the JD's vocabulary directly.
    """
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return
    tech = sk.get("technical")
    if not isinstance(tech, list):
        return
    # Check if M365 umbrella is present
    has_m365 = any(
        _M365_KEYWORD_RE.search(str(s)) for s in tech
    )
    if not has_m365:
        return
    jd_lower = (job_description or "").lower()
    component_set_lower = {c.lower() for c in _M365_COMPONENT_NAMES}
    new_tech: List[str] = []
    dropped: List[str] = []
    for s in tech:
        s_str = str(s)
        s_lower = s_str.strip().lower()
        # Strip leading "Microsoft " prefix for matching ("Microsoft Word" -> "word")
        match_form = s_lower
        if match_form.startswith("microsoft "):
            match_form = match_form[len("microsoft "):]
        if match_form in component_set_lower:
            # Component — keep only if JD names it explicitly
            if match_form in jd_lower:
                new_tech.append(s_str)
            else:
                dropped.append(s_str)
        else:
            new_tech.append(s_str)
    if dropped:
        sk["technical"] = new_tech
        issues.append(
            "deduplicated M365 components from skills (M365 umbrella covers them; "
            f"JD did not name separately): {', '.join(dropped)}"
        )


# Soft-skills cap — recruiter best practice is 4-6. Bloated lists with
# vague entries ("Independent Work", "Conflict Resolution") dilute signal.
_SOFT_SKILLS_CAP = 6


def _score_soft_skill_jd_relevance(soft: str, jd_lower: str) -> float:
    """Score a soft-skill string by direct JD relevance."""
    s = (soft or "").strip().lower()
    if not s:
        return 0.0
    score = 0.0
    if s in jd_lower:
        score += 3.0  # exact-phrase match
    for tok in re.split(r"[^\w]+", s):
        if len(tok) >= 4 and tok in jd_lower:
            score += 1.0
    return score


def _cap_soft_skills(
    content: Dict[str, Any],
    job_description: str,
    issues: List[str],
    *,
    cap: int = _SOFT_SKILLS_CAP,
) -> None:
    """Cap skills.soft at `cap` entries, keeping the most JD-relevant ones.

    Preserves the original order for ties (stable sort) so the LLM's
    intentional ordering survives when relevance is the same.
    """
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return
    soft = sk.get("soft")
    if not isinstance(soft, list):
        return
    if len(soft) <= cap:
        return
    jd_lower = (job_description or "").lower()
    scored = [
        (i, _score_soft_skill_jd_relevance(str(s), jd_lower), str(s))
        for i, s in enumerate(soft)
    ]
    # Sort by score desc, then original index asc (stable for equal scores)
    scored.sort(key=lambda x: (-x[1], x[0]))
    kept = [s for _, _, s in scored[:cap]]
    dropped = [s for _, _, s in scored[cap:]]
    sk["soft"] = kept
    issues.append(
        f"capped soft skills to {cap} (was {len(soft)}); dropped: {', '.join(dropped)}"
    )


# Title-level no-exposure indicators — when a JD title contains any of these
# as a contiguous substring, opening the resume summary with the verbatim JD
# title would imply a role-identity claim the candidate cannot defend per
# career_master Section 3 honest limits. The picker splits hybrid titles on
# common separators and keeps only the defensible halves.
_TITLE_NO_EXPOSURE_INDICATORS: Tuple[str, ...] = (
    # SOC / security analyst family
    "security analyst",
    "security engineer",
    "soc analyst",
    "soc engineer",
    "threat analyst",
    "threat hunter",
    "incident response analyst",
    "incident response engineer",
    "cybersecurity engineer",
    "cybersecurity analyst",
    "vulnerability analyst",
    "penetration tester",
    "pen tester",
    "infosec analyst",
    "infosec engineer",
    # Network engineer / admin family (full-scope)
    "network engineer",
    "senior network admin",
    "senior network administrator",
    "network architect",
    "voice engineer",
    # DevOps / SRE / Platform / Infra engineers
    "devops engineer",
    "site reliability engineer",
    "platform engineer",
    "infrastructure engineer",
    "build engineer",
    "release engineer",
    # Software engineer family
    "software engineer",
    "software developer",
    "backend engineer",
    "frontend engineer",
    "full stack engineer",
    "full-stack engineer",
    "ml engineer",
    "machine learning engineer",
    "data engineer",
    "data scientist",
    # Cloud engineer (vs cloud support — support is OK)
    "cloud engineer",
    "aws engineer",
    "azure engineer",
    "gcp engineer",
    "cloud architect",
    # Identity / IAM / AD engineering
    "identity engineer",
    "iam engineer",
    "iam analyst",
    "ad administrator",
    "active directory administrator",
    "active directory engineer",
    # Senior / Principal / Architect tier (beyond candidate's level)
    "senior systems administrator",
    "senior sysadmin",
    "principal engineer",
    "principal sysadmin",
    "systems architect",
    "solutions architect",
    "enterprise architect",
    # Specialized engineering titles
    "database engineer",
    "database administrator",
    "sql developer",
)


# Common separators in hybrid JD titles ("A & B", "A / B", "A and B", "A, B").
_TITLE_SPLIT_RE = re.compile(r"\s*(?:&|/|\sand\s|,|—|–|\|)\s*", re.IGNORECASE)

# Fallback safe-title when every part of the JD title is no-exposure.
_DEFENSIBLE_TITLE_FALLBACK = "IT Support Specialist"


def _split_jd_title(title: str) -> List[str]:
    """Split a hybrid JD title into parts on common separators (&, /, and, ',', —)."""
    if not title:
        return []
    parts = _TITLE_SPLIT_RE.split(title)
    return [p.strip() for p in parts if p.strip()]


def _part_contains_no_exposure_title(part: str) -> bool:
    """True if `part` contains any no-exposure title indicator as a substring."""
    if not part:
        return False
    pl = part.lower()
    return any(ind in pl for ind in _TITLE_NO_EXPOSURE_INDICATORS)


def _defensible_summary_title(jd_title: str) -> Tuple[str, bool]:
    """Return (summary_open_title, was_filtered).

    - If every part of the JD title is defensible, returns the verbatim title.
    - If a hybrid title has both no-exposure and defensible parts, returns the
      defensible parts joined with ' & '.
    - If the entire title is no-exposure (e.g. "DevOps Engineer"), returns the
      generic safe fallback ('IT Support Specialist').
    """
    title = (jd_title or "").strip()
    if not title:
        return ("the role", False)
    parts = _split_jd_title(title)
    if len(parts) <= 1:
        if _part_contains_no_exposure_title(title):
            return (_DEFENSIBLE_TITLE_FALLBACK, True)
        return (title, False)
    defensible_parts = [p for p in parts if not _part_contains_no_exposure_title(p)]
    if len(defensible_parts) == len(parts):
        return (title, False)  # all parts defensible
    if defensible_parts:
        return (" & ".join(defensible_parts), True)
    return (_DEFENSIBLE_TITLE_FALLBACK, True)


def _fix_summary_title_overclaim(
    content: Dict[str, Any],
    target_title: str,
    issues: List[str],
) -> None:
    """Post-gen guard — if the summary opens with a JD title that contains a
    no-exposure role indicator, rewrite the opener to the defensible title.

    Handles a few common opener shapes:
      "<title> candidate with ..."
      "<title> role candidate ..."
      "<title>, ..."
      "<title> position ..."
    """
    summary = str(content.get("summary") or "").strip()
    if not summary or not target_title:
        return
    defensible, was_filtered = _defensible_summary_title(target_title)
    if not was_filtered:
        return
    if defensible.lower() == target_title.strip().lower():
        return
    # Replace the verbatim JD title at the start of the summary with the
    # defensible version, preserving any trailing "candidate" / "role" wording.
    title_escaped = re.escape(target_title.strip())
    pattern = re.compile(
        rf"^\s*{title_escaped}(\b|\s|$)",
        re.IGNORECASE,
    )
    new_summary, n = pattern.subn(f"{defensible}\\1", summary, count=1)
    if n and new_summary != summary:
        content["summary"] = new_summary
        issues.append(
            f"rewrote summary opening: '{target_title}' -> '{defensible}' "
            f"(filtered no-exposure title indicator from summary)"
        )


def _strip_forbidden_terms_from_text(
    text: str,
    matchers: List[re.Pattern[str]],
) -> Tuple[str, List[str]]:
    """Strip forbidden-skill terms from prose text (summary, bullets, project
    descriptions). Performs grammar cleanup so removing list items doesn't
    leave double-commas or "and" orphans.

    Returns (cleaned_text, list_of_removed_terms).
    """
    removed: List[str] = []
    out = text or ""
    for rx in matchers:
        # Find all matches; track unique terms for the log.
        for m in rx.finditer(out):
            removed.append(m.group(0))
        out = rx.sub("", out)
    if not removed:
        return out, []
    # Grammar cleanup — common artifacts when a list item is removed:
    #   "X, Y, and Z" -> strip Y -> "X, , and Z" -> "X and Z"
    #   "MFA/SSO" -> strip both -> "/" (orphan separator)
    #   "Worked on AD and ." -> "Worked on AD."
    # Run a few targeted patterns; iterate until stable.
    cleanups: Tuple[Tuple[str, str], ...] = (
        # Orphan separators from compound terms (e.g. "MFA/SSO" -> "/", "MFA, SSO" -> ",")
        # Match a slash/pipe with optional whitespace, BUT only when surrounded
        # by whitespace or punctuation, not embedded in words like TCP/IP.
        (r"(?:^|(?<=[\s,;:.]))[/|]+(?=[\s,;:.]|$)", ""),
        # Collapse repeated commas with optional whitespace.
        (r",\s*,+", ","),
        # ", and X" left over when X-1 was stripped (and " ,and" variant)
        (r",\s*and\b", " and"),
        # ", or X" same family
        (r",\s*or\b", " or"),
        # Leading orphan "and " or "or " at start of sentence after a leading
        # strip (e.g. ", MFA/SSO, and X" -> ", , and X" -> " and X" -> "X")
        (r"(?<=[\.\;]\s)(?:and|or)\s+", ""),
        # "X and ." -> "X." (trailing "and" before period)
        (r"\s+and\s*\.", "."),
        # "X or ." -> "X."
        (r"\s+or\s*\.", "."),
        # "X,  and" double space cleanup
        (r"\s{2,}", " "),
        # "X ,Y" -> "X, Y" (orphan space-before-comma)
        (r"\s+,", ","),
        # Trailing comma before period: "X, ." -> "X."
        (r",\s*\.", "."),
        # Trailing comma at end of string
        (r",\s*$", ""),
        # "supporting , and ..." -> "supporting and ..." -> "supporting ..."
        (r"(\w)\s+,\s*", r"\1, "),
        # Bare leading punctuation/conjunction at start of sentence after strip:
        # ". and X" -> ". X" (after sentence boundary, and is fragment)
        (r"^\s*(?:and|or)\s+", ""),
    )
    prev = None
    iterations = 0
    while out != prev and iterations < 5:
        prev = out
        for pattern, repl in cleanups:
            out = re.sub(pattern, repl, out)
        iterations += 1
    return out.strip(), removed


def _sanitize_forbidden_terms_in_content(
    content: Dict[str, Any],
    matchers: List[re.Pattern[str]],
    issues: List[str],
) -> None:
    """Run _strip_forbidden_terms_from_text against summary, bullets, and
    project descriptions. Keeps the resume body free of forbidden claims so
    the JD-requirement surfacer can't re-add them to skills.
    """
    summary = str(content.get("summary") or "")
    if summary:
        cleaned, removed = _strip_forbidden_terms_from_text(summary, matchers)
        if cleaned != summary:
            content["summary"] = cleaned
            for term in set(removed):
                issues.append(f"stripped forbidden term from summary: '{term}'")
    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            bullets = exp.get("bullets")
            if not isinstance(bullets, list):
                continue
            new_bullets: List[str] = []
            for b in bullets:
                bs = str(b)
                cleaned, removed = _strip_forbidden_terms_from_text(bs, matchers)
                if cleaned != bs:
                    for term in set(removed):
                        issues.append(f"stripped forbidden term from bullet: '{term}'")
                new_bullets.append(cleaned)
            exp["bullets"] = new_bullets
    projs = content.get("projects")
    if isinstance(projs, list):
        for proj in projs:
            if not isinstance(proj, dict):
                continue
            for field in ("description", "impact"):
                val = str(proj.get(field) or "")
                if not val:
                    continue
                cleaned, removed = _strip_forbidden_terms_from_text(val, matchers)
                if cleaned != val:
                    proj[field] = cleaned
                    for term in set(removed):
                        issues.append(f"stripped forbidden term from project.{field}: '{term}'")


def _sanitize_skill_lists(
    content: Dict[str, Any],
    profile_text: str,
    matchers: List[re.Pattern[str]],
    issues: List[str],
) -> None:
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return
    # M365 on consolidated PDFs is often noise; only allow it if career_master.md explicitly mentions it.
    m365_ok = _office365_in_profile(_career_master_plaintext_lower())
    # Pre-compute the Light Exposure approved-label set so the reverse-direction
    # honest-limits check doesn't false-positive on legitimate basics framings.
    from job_pipeline.named_requirements import parse_light_exposure  # local import
    light_labels_lower: Set[str] = {
        str(it.get("skill") or "").strip().lower()
        for it in parse_light_exposure(profile_text=profile_text)
    }
    for key in ("technical", "soft"):
        arr = sk.get(key) if isinstance(sk.get(key), list) else []
        if not isinstance(arr, list):
            continue
        keep: List[Any] = []
        for skill in arr:
            sskill = str(skill).strip()
            if not sskill:
                continue
            if _office365_skill(sskill) and not m365_ok:
                trimmed = _strip_office365_phrasing(sskill)
                if trimmed and trimmed.lower() != sskill.lower():
                    issues.append(
                        f"trimmed Microsoft/Office 365 phrasing from skill: '{sskill}' -> '{trimmed}' "
                        "(not authorized in career_master.md)"
                    )
                    sskill = trimmed
                else:
                    issues.append(
                        f"stripped forbidden skill: {sskill} (Microsoft/Office 365 not mentioned in career_master.md)"
                    )
                    continue
            bad, term = _skill_contains_forbidden_impl(sskill, matchers)
            if bad:
                issues.append(
                    f"stripped forbidden skill: {sskill} (matches '{term}' from honest limits)"
                )
                continue
            # Reverse-direction check: is the bare skill name a substring of a
            # No-Exposure phrase (e.g. "Active Directory" inside "Active
            # Directory administration via GPMC")? Catches bare-term overclaims
            # that survive the forward forbidden-substring check.
            blocked, phrase = _skill_blocked_by_honest_limits(
                sskill, profile_text, light_labels_lower
            )
            if blocked:
                issues.append(
                    f"stripped forbidden skill: '{sskill}' "
                    f"(bare term implicitly claims '{phrase}', which is in honest limits)"
                )
                continue
            keep.append(sskill)
        sk[key] = keep


def _apply_skills_curation(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str,
    issues: List[str],
) -> None:
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return
    curated, notes = curate_technical_skills(content, job_description, profile_text)
    for note in notes:
        issues.append(note)
    sk["technical"] = curated


def _ensure_m365_for_support_role(
    content: Dict[str, Any],
    job_title: str,
    issues: List[str],
) -> None:
    """For help-desk / service-desk targets where career_master.md authorizes M365,
    inject "Microsoft 365" into skills.technical if the LLM omitted it. M365 is a
    near-universal screening keyword for these roles and Carlos's profile supports it.
    """
    if not _is_support_target_role(job_title):
        return
    if not _office365_in_profile(_career_master_plaintext_lower()):
        return
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return
    tech = sk.get("technical")
    if not isinstance(tech, list):
        tech = []
        sk["technical"] = tech
    if any(_office365_skill(str(s)) for s in tech):
        return
    tech.append("Microsoft 365")
    issues.append("injected Microsoft 365 into skills.technical (support-role + career_master authorized)")


def _apply_projects_curation(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str,
    job_title: str,
    issues: List[str],
) -> None:
    curated, notes = curate_projects(
        content,
        job_description,
        profile_text,
        job_title=job_title,
    )
    for note in notes:
        issues.append(note)
    content["projects"] = curated


def _sync_surfaced_skills(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str,
    issues: List[str],
) -> None:
    for note in ensure_surfaced_keywords_in_skills(content, job_description, profile_text):
        issues.append(note)


def _fix_jd_years_echo_summary(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str,
    issues: List[str],
) -> None:
    summary = str(content.get("summary") or "")
    if not summary:
        return
    fixed, changed = fix_jd_years_echo_in_text(summary, job_description, profile_text)
    if changed:
        content["summary"] = fixed
        issues.append("replaced JD experience-band echo in summary with profile-backed phrasing")


def _downgrade_strong_account_wording(
    content: Dict[str, Any],
    profile_text: str,
    issues: List[str],
) -> None:
    """Rewrite inflated account-management phrases when profile is partial/none."""
    level = user_account_management_level(profile_text)
    if level == "full" or content.get("error"):
        return
    replacement = (
        "user account support, onboarding workflows, and access-related troubleshooting"
    )
    strong_re = re.compile(
        r"\b(?:proven ability to )?manage user accounts?\b|"
        r"\bmanaging user accounts?\b|"
        r"\bmanaged user accounts?\b|"
        r"\buser account management\b",
        re.IGNORECASE,
    )

    def _fix_text(text: str) -> str:
        return strong_re.sub(replacement, text)

    summary = str(content.get("summary") or "")
    if summary and strong_re.search(summary):
        content["summary"] = _fix_text(summary)
        issues.append("downgraded strong account-management wording in summary")

    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            bullets = exp.get("bullets")
            if not isinstance(bullets, list):
                continue
            new_bullets = []
            for b in bullets:
                bs = str(b)
                if strong_re.search(bs):
                    issues.append("downgraded strong account-management wording in bullet")
                    new_bullets.append(_fix_text(bs))
                else:
                    new_bullets.append(bs)
            exp["bullets"] = new_bullets

    sk = content.get("skills")
    if isinstance(sk, dict):
        for key in ("technical", "soft"):
            arr = sk.get(key)
            if not isinstance(arr, list):
                continue
            sk[key] = [
                _fix_text(str(s)) if strong_re.search(str(s)) else str(s) for s in arr
            ]

    # Projects — same downgrade pass. Without this, a project impact field
    # like "bringing user account management experience" survives the
    # summary/bullet pass and leaks into the rendered resume.
    projs = content.get("projects")
    if isinstance(projs, list):
        for proj in projs:
            if not isinstance(proj, dict):
                continue
            for field in ("description", "impact"):
                val = str(proj.get(field) or "")
                if val and strong_re.search(val):
                    proj[field] = _fix_text(val)
                    issues.append(
                        f"downgraded strong account-management wording in project.{field}"
                    )

    # Sidecar metadata fields — _role_thesis is generated by
    # jd_analysis.build_role_thesis from raw JD tech requirements without
    # honest-limits filtering, so it can contain "user account management"
    # claims that survive the downgrade pass. The thesis doesn't render in
    # the PDF but does count for ATS-overlap and trips validation checks.
    for sidecar_key in ("_role_thesis",):
        val = str(content.get(sidecar_key) or "")
        if val and strong_re.search(val):
            content[sidecar_key] = _fix_text(val)
            issues.append(
                f"downgraded strong account-management wording in {sidecar_key}"
            )


_PROJECT_TITLE_TOKEN_STOPWORDS = frozenset(
    {"project", "system", "tool", "app", "service", "platform", "framework"}
)


_EDU_HEADING_RE = re.compile(
    r"(?ms)^#{1,6}\s*Education[^\n]*\n(.*?)(?=^#{1,6}\s|\Z)",
)

# Consolidated profiles use a plain `## Education` block separate from broader
# "Education timeline" sections in career_master; scan both independently.
_EDU_LEGACY_H2_RE = re.compile(r"(?ms)^##\s*Education\b[^\n]*\n(.*?)(?=^##\s|\Z)")


def _all_education_sections_lower(profile_text: str) -> str:
    parts: List[str] = []
    for pat in (_EDU_HEADING_RE, _EDU_LEGACY_H2_RE):
        for m in pat.finditer(profile_text or ""):
            blk = (m.group(1) or "").strip()
            if blk:
                parts.append(blk.lower())
    return "\n".join(parts)


def _project_core_education_dedupe(name: str) -> str:
    """Lowercase title with parentheticals removed — compare to Education body only."""
    s = re.sub(r"\([^)]*\)", "", name or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def _project_is_duplicate_school_narrative(name: str, profile_text: str) -> bool:
    """
    If the same title already lives in the Education section, do not treat it as a resume
    'project' entry (consolidated_profile often duplicates these under ## Projects).
    """
    educ = _all_education_sections_lower(profile_text)
    if not educ or len(educ) < 12:
        return False
    core = _project_core_education_dedupe(name)
    if len(core) < 10:
        return False
    return core in educ


def _project_grounded(name: str, profile_text: str) -> bool:
    if not (name or "").strip():
        return True
    pl = profile_text.lower()
    nlow = name.strip().lower()

    if _project_is_duplicate_school_narrative(name, profile_text):
        return False

    tokens = [t for t in re.split(r"[^\w]+", nlow) if len(t) > 2]
    if not tokens:
        return False

    distinctive = [t for t in tokens if t not in _PROJECT_TITLE_TOKEN_STOPWORDS]
    if not distinctive:
        return nlow in pl

    def _word_in_profile(tok: str) -> bool:
        return bool(re.search(rf"(?<!\w){re.escape(tok)}(?!\w)", pl))

    longest = max(distinctive, key=len)
    if not _word_in_profile(longest):
        return False
    # At least one distinctive token grounded (implies longest grounded above).
    return any(_word_in_profile(t) for t in distinctive)


def _strip_ungrounded_projects(content: Dict[str, Any], profile_text: str, issues: List[str]) -> None:
    projs = content.get("projects")
    if not isinstance(projs, list):
        return
    keep: List[Any] = []
    for p in projs:
        if not isinstance(p, dict):
            continue
        nm = (p.get("name") or "").strip()
        if _project_grounded(nm, profile_text):
            keep.append(p)
        else:
            issues.append(f"stripped fabricated project: {nm} (not present in grounding files)")
    content["projects"] = keep


# Resume-projects allowlist — Carlos is honest-framing-sensitive about claiming
# personal projects he hasn't shipped to "feature-complete enough" yet. Even when
# career_master.md mentions a project (so the ungrounded-projects guard passes
# it), only projects on this allowlist may render in the resume Projects block.
# Sync with memory/feedback_resume_projects_allowlist.md when Carlos approves new
# projects. Match is normalized (lowercase, alphanumeric only) — handle inflected
# / parenthesized LLM outputs like "Home Cleanliness Assistant (Python)".
_RESUME_PROJECTS_ALLOWLIST_NORMALIZED: frozenset = frozenset(
    {
        "homecleanlinessassistant",
        # AI Job-Application Pipeline — APPROVED but SCOPED to ingestion +
        # tailoring (resume + cover letter); the auto-apply piece is NOT
        # claimed. The shorter token "jobapplicationpipeline" matches both
        # "AI Job-Application Pipeline" and "Job Application Pipeline" variants.
        "jobapplicationpipeline",
    }
)


def _normalize_project_name_for_allowlist(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _strip_disallowed_projects(content: Dict[str, Any], issues: List[str]) -> None:
    """
    Enforce Carlos's resume-projects allowlist. Even if a project name is
    grounded in career_master.md, drop it from the rendered resume unless it
    is explicitly approved. Education-section bullets are unaffected — this
    only touches the resume Projects block (content["projects"]).
    """
    projs = content.get("projects")
    if not isinstance(projs, list):
        return
    keep: List[Any] = []
    for p in projs:
        if not isinstance(p, dict):
            continue
        nm = (p.get("name") or "").strip()
        norm = _normalize_project_name_for_allowlist(nm)
        # Substring match in either direction: the allowlist entry may be the
        # core name and the LLM may add a parenthetical, or the LLM may use a
        # shortened alias. Conservative: require the allowlist token to appear.
        allowed = any(
            allowed_norm and allowed_norm in norm
            for allowed_norm in _RESUME_PROJECTS_ALLOWLIST_NORMALIZED
        )
        if allowed:
            keep.append(p)
        else:
            issues.append(
                f"stripped disallowed project from resume: '{nm}' "
                "(not on resume-projects allowlist; see memory/feedback_resume_projects_allowlist.md)"
            )
    content["projects"] = keep


def _parse_gkj_documented_titles(profile_text: str) -> List[str]:
    m = re.search(
        r"(?is)###\s*1[- ]800[- ]GOT[- ]JUNK[^\n]*\n(.*?)(?=\n###|\n##\s)",
        profile_text,
    )
    if not m:
        return ["Helper", "Driver", "Operations Manager"]
    body = m.group(1)
    titles: List[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        inner0 = line[1:].strip()
        if inner0.startswith("He did ") or inner0.startswith("On resume bullets"):
            continue
        if "title belongs" in inner0.lower() and "beat" in inner0.lower():
            continue
        inner = line[1:].strip()
        nm = inner.split("(")[0].split(",")[0].strip()
        nm = re.sub(r"[\*]+", "", nm).strip()
        if nm and len(nm) < 90 and nm[0].isalpha():
            titles.append(nm)
    extra = ["Operations Manager"]
    merged = titles + extra
    out = []
    seen = set()
    for t in merged:
        k = norm_title_canon(t)
        if k and k not in seen:
            seen.add(k)
            out.append(t)
    return out or ["Operations Manager"]


def _title_compatible_with_documents(title: str, candidates: List[str]) -> bool:
    ta = norm_title_canon(title)
    if not ta:
        return False
    for c in candidates:
        tc = norm_title_canon(c)
        if not tc:
            continue
        if tc in ta or ta in tc:
            return True
        overlap = sum(1 for w in tc.split() if len(w) > 2 and w in ta.split())
        if overlap >= max(1, len(tc.split()) - 1):
            return True
    return False


_BTB_ALT_TITLES_SECTION_RE = re.compile(
    r"(?ims)^#{1,4}\s*2\.6\s+Approved\s+alt[- ]titles[^\n]*\n(.*?)(?=^#{1,4}\s|\Z)"
)
_BTB_ALT_TITLE_BULLET_RE = re.compile(
    r"(?m)^\s*[-*]\s*\*\*([^*]+?)\*\*\s*(.+)$"
)
_BTB_TRIGGERS_INLINE_RE = re.compile(
    r"(?i)triggers?\s*:\s*([^—]+)"
)


def _parse_btb_alt_titles(profile_text: str) -> List[Dict[str, Any]]:
    """Parse the '## 2.6 Approved alt-titles — BEAT THE BOMB' block from
    career_master.md. Returns a list of {title, triggers}. First entry is the
    default fallback. Empty list if the block is absent.
    """
    section_m = _BTB_ALT_TITLES_SECTION_RE.search(profile_text or "")
    body = section_m.group(1) if section_m else ""
    out: List[Dict[str, Any]] = []
    if not body:
        return out
    for m in _BTB_ALT_TITLE_BULLET_RE.finditer(body):
        title = m.group(1).strip()
        rest = m.group(2).strip()
        triggers: List[str] = []
        tm = _BTB_TRIGGERS_INLINE_RE.search(rest)
        if tm:
            triggers = [
                t.strip().lower() for t in tm.group(1).split(",") if t.strip()
            ]
        if title:
            out.append({"title": title, "triggers": triggers})
    return out


def _beat_the_bomb_titles(profile_text: str) -> List[str]:
    """List of candidate phrasings for the BEAT THE BOMB role. Reads
    career_master.md Section 2.6 if present, falls back to the legacy
    two-title list. First entry is the default fallback.
    """
    parsed = _parse_btb_alt_titles(profile_text)
    if parsed:
        return [p["title"] for p in parsed]
    tset = {"Operations Manager", "Technical Operations Manager"}
    pt = profile_text
    extra: List[str] = []
    for t in sorted(tset, key=len, reverse=True):
        if re.search(re.escape(t), pt, re.I):
            extra.append(t)
    return extra or ["Operations Manager"]


def _pick_btb_title_for_jd(
    target_title: str,
    target_jd: str,
    profile_text: str,
) -> Tuple[str, int]:
    """Pick the BEAT THE BOMB alt-title best matching the target job. Substring
    scores each candidate's triggers against (target_title + JD head).
    Returns (title, score). Score 0 means no triggers matched and the default
    (first entry) is being used.
    """
    parsed = _parse_btb_alt_titles(profile_text)
    if not parsed:
        fallback = _beat_the_bomb_titles(profile_text)
        return (fallback[0] if fallback else "Operations Manager", 0)
    haystack = (str(target_title or "") + " \n " + str(target_jd or "")[:1500]).lower()
    best_idx = 0
    best_score = 0
    for i, entry in enumerate(parsed):
        score = sum(
            1 for trig in (entry.get("triggers") or []) if trig and trig in haystack
        )
        if score > best_score:
            best_score = score
            best_idx = i
    return (parsed[best_idx]["title"], best_score)


def _force_experience_dates_from_profile(
    content: Dict[str, Any],
    issues: List[str],
) -> None:
    """Overwrite LLM-generated experience dates with canonical values from
    consolidated_profile.json.

    CRITICAL: dates must NEVER come from the LLM. They are factual,
    employer-attested values. The LLM has been observed truncating end_date
    "2026-03" to "2024", which silently breaks rendercv PDF rendering. This
    function matches experience entries by canonical company key and forcibly
    replaces start_date / end_date with the profile values.
    """
    try:
        from job_pipeline.evidence_db import match_employer_key, employer_record
        prof = load_consolidated_profile()
    except Exception:
        return
    exps = content.get("experience")
    if not isinstance(exps, list):
        return

    # Build canonical {company_key: (start_date, end_date)} from consolidated
    # profile experience entries. Also pull from evidence.json date_range as a
    # secondary source if profile dates are missing.
    canon: Dict[str, Tuple[str, str]] = {}
    for prof_exp in (prof.get("experience") or []):
        if not isinstance(prof_exp, dict):
            continue
        co = str(prof_exp.get("company") or "").strip()
        if not co:
            continue
        ck = re.sub(r"[^a-z0-9]+", "", co.lower().split(",")[0])
        sd = str(prof_exp.get("start_date") or "").strip()
        ed = str(prof_exp.get("end_date") or "").strip()
        if ck and (sd or ed):
            canon[ck] = (sd, ed)

    for exp in exps:
        if not isinstance(exp, dict):
            continue
        co = str(exp.get("company") or "").strip()
        if not co:
            continue
        ck = re.sub(r"[^a-z0-9]+", "", co.lower().split(",")[0])
        if ck not in canon:
            continue
        canon_start, canon_end = canon[ck]
        cur_start = str(exp.get("start_date") or "").strip()
        cur_end = str(exp.get("end_date") or "").strip()
        if canon_start and cur_start != canon_start:
            exp["start_date"] = canon_start
            issues.append(
                f"date-override: {co} start_date {cur_start!r} -> {canon_start!r} (LLM mis-wrote)"
            )
        if canon_end and cur_end != canon_end:
            exp["end_date"] = canon_end
            issues.append(
                f"date-override: {co} end_date {cur_end!r} -> {canon_end!r} (LLM mis-wrote)"
            )


def _fix_experience_titles(
    content: Dict[str, Any],
    profile_text: str,
    issues: List[str],
    *,
    target_title: str = "",
    target_jd: str = "",
) -> None:
    exps = content.get("experience")
    if not isinstance(exps, list):
        return
    plc = profile_text.lower()
    gkj_candidates = _parse_gkj_documented_titles(profile_text)
    bt_candidates = _beat_the_bomb_titles(profile_text)
    picked_btb, picked_score = _pick_btb_title_for_jd(
        target_title, target_jd, profile_text
    )
    for exp in exps:
        if not isinstance(exp, dict):
            continue
        title = str(exp.get("title") or "").strip()
        company = str(exp.get("company") or "").strip()
        cl = company.lower().replace(" ", "")
        ct = norm_title_canon(title)
        if "gotjunk" in cl or "got-junk" in company.lower() or "1800" in company.lower() or "1-800" in company.lower():
            # GOT-JUNK canonical title is "Operations Manager" (per Carlos, 2026-06-18).
            # Only repair titles that are NOT among the documented candidates.
            if not _title_compatible_with_documents(title, gkj_candidates):
                new_title = "Operations Manager"
                issues.append(
                    "retitled 1-800-GOT-JUNK: "
                    f"'{title}' -> '{new_title}' (closer to documented title)"
                )
                exp["title"] = new_title
            continue
        if ("beat" in plc and "bomb" in plc) and (
            "beat" in company.lower() and "bomb" in company.lower()
        ):
            desired = picked_btb or (bt_candidates[0] if bt_candidates else "")
            if not desired:
                continue
            in_pool = bool(title) and _title_compatible_with_documents(title, bt_candidates)
            already_picked = norm_title_canon(title) == norm_title_canon(desired)
            if already_picked:
                continue
            if not in_pool:
                issues.append(
                    f"retitled BEAT THE BOMB: '{title}' -> '{desired}' "
                    "(out-of-pool title; JD-aware picker fallback)"
                )
                exp["title"] = desired
                continue
            if picked_score > 0:
                issues.append(
                    f"retitled BEAT THE BOMB: '{title}' -> '{desired}' "
                    f"(JD-aware picker matched {picked_score} trigger(s))"
                )
                exp["title"] = desired
                continue
            continue


def validate_tailored_content(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str,
    military_service: Optional[List[Dict[str, Any]]] = None,
    *,
    job_title: str = "",
) -> Dict[str, Any]:
    issues: List[str] = []
    strengths: List[str] = []

    if content.get("error"):
        issues.append(str(content.get("error")))
        return {"truthfulness_note": "not_scored", "ats_overlap_hits": 0, "issues": issues, "strengths": strengths}

    dedupe_experience_self(content, issues)
    dedupe_experience_vs_military(content, military_service, issues)
    # CRITICAL: overwrite LLM-generated dates with canonical profile values.
    # The LLM hallucinated "2024" for BEAT THE BOMB end_date (real: 2026-03),
    # which broke rendercv PDF render silently. Dates are factual, not creative.
    _force_experience_dates_from_profile(content, issues)

    matchers_list = _compile_forbidden_skill_matchers(profile_text)
    _sanitize_skill_lists(content, profile_text, matchers_list, issues)
    # Strip forbidden terms from prose fields (summary, bullets, projects)
    # BEFORE the surfacer runs — otherwise the surfacer sees forbidden terms
    # in the resume body and re-adds them to skills.technical.
    _sanitize_forbidden_terms_in_content(content, matchers_list, issues)
    _apply_skills_curation(content, job_description, profile_text, issues)
    _sync_surfaced_skills(content, job_description, profile_text, issues)
    # SAFETY NET: re-sanitize skills after the surfacer in case it pulled a
    # forbidden term from somewhere we missed (e.g. project metadata).
    _sanitize_skill_lists(content, profile_text, matchers_list, issues)
    _ensure_m365_for_support_role(content, job_title, issues)
    # Restore "basics" / "(home lab)" qualifiers on light-exposure skills if the
    # LLM dropped them. Bare "Active Directory" overclaims compared to the
    # career_master Light exposure section.
    for note in enforce_light_exposure_framing_on_skills(content, profile_text):
        issues.append(note)
    _downgrade_strong_account_wording(content, profile_text, issues)
    _strip_ungrounded_projects(content, profile_text, issues)
    _strip_disallowed_projects(content, issues)
    _apply_projects_curation(content, job_description, profile_text, job_title, issues)
    _fix_jd_years_echo_summary(content, job_description, profile_text, issues)
    _fix_experience_titles(
        content,
        profile_text,
        issues,
        target_title=job_title,
        target_jd=job_description,
    )
    # Rewrite summary opener if it uses a JD title containing a no-exposure
    # role term (e.g. "Security Analyst & Support Technician candidate" ->
    # "Support Technician candidate"). Idempotent.
    _fix_summary_title_overclaim(content, job_title, issues)
    # M365 admin-verb downgrade: rewrite "Managed X using Microsoft 365" ->
    # "Supported X in Microsoft 365" when profile flags M365 as user-only.
    _downgrade_m365_admin_phrasing(content, profile_text, issues)
    # M365 component dedup: if "Microsoft 365" is in skills, drop child
    # components (Outlook/Teams/Word/Excel/...) unless the JD names them
    # as distinct requirements. Reduces keyword padding.
    _dedup_m365_components_from_skills(content, job_description, issues)
    # Soft-skills cap: enforce recruiter best practice of 4-6 soft skills,
    # keeping the most JD-relevant entries.
    _cap_soft_skills(content, job_description, issues)
    # Title-case skill labels leaking into prose (issue F): normalize skill
    # label casing AND lowercase verbatim skill-label mentions mid-sentence
    # so "Backup And Restore Basics" -> "backup and restore basics" in prose.
    from job_pipeline.integrity_guards import fix_titlecase_skill_labels_in_resume
    for note in fix_titlecase_skill_labels_in_resume(content):
        issues.append(note)
    # Issue E: strip 1-800-GOT-JUNK bullets that claim industry-inferred duties
    # (fleet maintenance, waste-stream compliance, training programs, etc.)
    # not documented in career_master Section 8 scope rules.
    _strip_fabricated_gotjunk_bullets(content, profile_text, issues)
    # Issue C: strip skills.technical items that match JD named-requirement GAPS
    # (skills the JD asks for that profile_text does NOT support — e.g., VPN,
    # MacOS, Active Directory). Dynamic — auto-relaxes as career_master grows.
    _strip_ungrounded_skills_from_gaps(content, job_description, profile_text, issues)
    # Issue D: drop domain-irrelevant skills (Cisco Audio, DMX, Dante, OBS,
    # Unity, RFID, etc.) when the JD has no entertainment/AV/live-production
    # context. Carlos's real Light Exposure shouldn't dilute the JD-relevant
    # signal on unrelated IT/business JDs.
    _drop_domain_irrelevant_skills(content, job_description, issues)
    # Issue 4 (latest review): downgrade "military and venue incident response
    # procedures" co-mingled overclaim. Carlos has real military medical IR
    # training; BTB is live troubleshooting under pressure, NOT formal IR.
    _downgrade_ir_overclaim_phrasing(content, issues)
    # Strip meta-audit / hedge language from summary, bullets, and projects.
    # Phrase patterns (e.g. "without pretending X was Y", "at small-shop scale"
    # tail, "in a small-site environment", "with limited X scope") plus the
    # existing sentence-level "I do not claim" / "is not claimed" strip.
    # Idempotent — safe even though rendercv_export also calls it at PDF time.
    from job_pipeline.integrity_guards import strip_claim_audit_from_resume
    audit_notes = strip_claim_audit_from_resume(content)
    for note in audit_notes:
        issues.append(note)

    blob = json.dumps(content, ensure_ascii=False).lower()
    if len(blob) < 120:
        issues.append("Generated content is very short — check profile extract.")

    reqs = set(extract_requirements(job_description))
    hits = sum(1 for r in reqs if r in blob)
    if content.get("summary"):
        strengths.append("Has summary")
    exps = content.get("experience")
    if isinstance(exps, list) and len(exps) > 0:
        strengths.append("Has experience entries")
    sk = content.get("skills") if isinstance(content.get("skills"), dict) else {}
    tech = sk.get("technical") if isinstance(sk.get("technical"), list) else []
    if len(tech) >= 4:
        strengths.append("Skills section present")

    profile_words = set(re.findall(r"[a-z]{4,}", profile_text.lower()))
    content_words = set(re.findall(r"[a-z]{4,}", blob))
    overlap_ratio = len(profile_words & content_words) / max(1, len(content_words))
    if overlap_ratio < 0.08 and len(blob) > 400:
        issues.append("Low lexical overlap with profile — verify bullets against your source PDF.")

    for issue in check_named_requirements_surfaced(job_description, content, profile_text):
        issues.append(issue)

    for issue in check_account_management_wording(content, profile_text):
        issues.append(issue)

    summary_blob = str(content.get("summary") or "")
    for band in find_jd_years_echo_violations(summary_blob, job_description, profile_text):
        issues.append(f"JD experience band echoed in summary (use profile fact, not JD): {band}")

    jargon = find_project_jargon_violations(blob)
    if jargon:
        issues.append(f"Project jargon to simplify: {', '.join(jargon[:4])}")

    hype_hits = find_hype_violations(blob)
    if hype_hits:
        issues.append(f"Anti-hype: remove or rewrite inflated phrasing ({', '.join(hype_hits[:5])})")

    return {
        "truthfulness_note": "heuristic_only_not_legal_guarantee",
        "ats_overlap_hits": hits,
        "issues": issues,
        "strengths": strengths,
        "named_requirement_gaps": [
            g.get("requirement")
            for g in named_requirement_gaps(job_description, profile_text, content)
            if g.get("requirement")
        ],
    }


def _career_master_block() -> str:
    """Human-edited grounding file; optional. Empty string if absent or blank.

    Returned text carries an explicit precedence header so the tailor LLM reads
    this section as the primary framing authority — earlier sections in
    PROFILE_TEXT override later ones on framing conflicts.
    """
    master_path = os.path.join(_repo_root(), "job_pipeline", "career_master.md")
    if not os.path.isfile(master_path):
        return ""
    try:
        with open(master_path, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    try:
        extra = raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(
            "career_master.md is not valid UTF-8; decoding with replacement characters."
        )
        extra = raw.decode("utf-8", errors="replace")
    extra_stripped = extra.strip()
    if not extra_stripped:
        return ""
    return (
        "# === CAREER MASTER (off-resume grounding, primary framing) ===\n"
        "# Hand-edited authority. Honest-limits sections are hard constraints.\n\n"
        + extra_stripped
    )


def _profile_source_label() -> str:
    """Comma-joined list of grounding sources actually present on disk."""
    sources: List[str] = []
    master_path = os.path.join(_repo_root(), "job_pipeline", "career_master.md")
    if os.path.isfile(master_path):
        sources.append("career_master")
    text = load_consolidated_profile_text()
    if text and len(text.strip()) >= 200:
        sources.append("consolidated_profile")
    if not sources:
        sources.append("reference_pdf")
    return "+".join(sources)


def _load_grounded_profile_text() -> str:
    """
    Stack grounding sources with explicit precedence, top to bottom:
      1. career_master.md         — hand-edited, primary framing + honest limits.
      2. consolidated_profile.md  — LLM-merged from every resume PDF.
      3. reference PDF (fallback) — single LinkedIn-export PDF, back-compat only.
    Returns the markdown the LLM uses as PROFILE_TEXT.
    """
    parts: List[str] = []

    master = _career_master_block()
    if master:
        parts.append(master)

    consolidated = load_consolidated_profile_text()
    if consolidated and len(consolidated.strip()) >= 200:
        parts.append(
            "# === CONSOLIDATED PROFILE (resume-derived) ===\n\n"
            + consolidated.strip()
        )
    else:
        try:
            _, fallback = get_profile_text_from_reference("")
            if fallback:
                parts.append(
                    "# === REFERENCE PROFILE (single-PDF fallback) ===\n\n"
                    + fallback.strip()
                )
        except Exception:
            pass

    return "\n\n---\n\n".join(parts)


def _augment_profile_with_user_facts(profile_text: str, extra_facts: List[str]) -> str:
    """
    Prepend user-provided answers from the gap-fill flow so they override
    resume-extracted bullets on framing conflicts. HONEST LIMITS sections in
    career_master remain hard constraints regardless of position — see the
    HONEST LIMITS RULE in generate_tailored_sections.
    """
    cleaned = [str(f).strip() for f in (extra_facts or []) if str(f).strip()]
    if not cleaned:
        return profile_text
    block_lines = [
        "# === CANDIDATE-CONFIRMED FACTS (gap-fill answers) ===",
        "# Authoritative for resume-extracted data; does NOT override HONEST LIMITS sections in CAREER MASTER.",
        "",
    ]
    for f in cleaned:
        block_lines.append(f"- {f}")
    block = "\n".join(block_lines) + "\n"
    if not profile_text:
        return block
    return block + "\n---\n\n" + profile_text


def tailor_resume_from_jd(
    job_description: str,
    *,
    job_title: str = "",
    company: str = "",
    location: str = "",
    strategy_level: str = "balanced",
    extra_facts: Optional[List[str]] = None,
    summary_json: Optional[Dict[str, Any]] = None,
    export_markdown: bool = True,
    item_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Manual-JD entrypoint: feed in a raw job description and get a tailored resume
    draft back. Does NOT require a job to be in the DB.

    Returns the same shape as tailor_resume_for_item.
    """
    if not (job_description or "").strip():
        raise ValueError("job_description is empty.")

    profile_text = _load_grounded_profile_text()
    if not profile_text:
        raise ValueError(
            "No profile available. Run: python -m job_pipeline.bootstrap_resume_profile"
        )
    profile_text = _augment_profile_with_user_facts(profile_text, extra_facts or [])

    # Build a synthetic "row" matching the shape generate_tailored_sections expects.
    card = summary_json if isinstance(summary_json, dict) else {}
    synthetic_row: Dict[str, Any] = {
        "title": (job_title or "").strip() or "the role",
        "company_name": (company or "").strip() or "the company",
        "location": (location or "").strip(),
        "description_text": str(job_description),
        "summary_json": card,
    }

    strategy = build_tailoring_strategy(
        synthetic_row["title"],
        synthetic_row["description_text"],
        profile_text,
        strategy_level,
    )
    content = generate_tailored_sections(synthetic_row, profile_text, strategy, strategy_level)
    prof_ms = load_consolidated_profile()
    ms_rows = (
        prof_ms.get("military_service") if isinstance(prof_ms.get("military_service"), list) else []
    )
    validation = validate_tailored_content(
        content,
        synthetic_row["description_text"],
        profile_text,
        military_service=ms_rows,
        job_title=str(synthetic_row.get("title") or job_title or ""),
    )

    optimization: Dict[str, Any] = {}
    if not content.get("error"):
        opt = run_resume_optimization_pipeline(
            content,
            synthetic_row["description_text"],
            profile_text,
            validation=validation,
            job_title=str(synthetic_row.get("title") or job_title or ""),
            company=str(synthetic_row.get("company_name") or company or ""),
            revalidate_fn=lambda c: validate_tailored_content(
                c,
                synthetic_row["description_text"],
                profile_text,
                military_service=ms_rows,
                job_title=str(synthetic_row.get("title") or job_title or ""),
            ),
        )
        content = opt.get("content") or content
        validation = opt.get("validation") or validation
        optimization = opt.get("optimization") or {}

    md_path = ""
    if export_markdown and not content.get("error"):
        md_path = export_tailored_resume_markdown(
            content,
            company=synthetic_row["company_name"],
            job_title=synthetic_row["title"],
            item_id=int(item_id) if item_id else 0,
            outputs_root=_repo_root(),
            education=prof_ms.get("education") if isinstance(prof_ms.get("education"), list) else None,
            military_service=ms_rows,
            certifications=prof_ms.get("certifications") if isinstance(prof_ms.get("certifications"), list) else None,
        )

    return {
        "ok": not bool(content.get("error")),
        "item_id": int(item_id) if item_id else None,
        "job_title": synthetic_row["title"],
        "company": synthetic_row["company_name"],
        "location": synthetic_row["location"],
        "status": "manual_jd",
        "strategy_level": strategy_level,
        "strategy": strategy,
        "content": content,
        "validation": validation,
        "optimization": optimization,
        "markdown_path": md_path,
        "profile_source": _profile_source_label(),
    }


def tailor_resume_for_item(
    item_id: int,
    strategy_level: str = "balanced",
    *,
    export_markdown: bool = True,
) -> Dict[str, Any]:
    row = get_item(int(item_id))
    if not row:
        raise ValueError(f"Job pipeline item not found: {item_id}")
    if not (row.get("title") or row.get("description_text")):
        raise ValueError(f"Item {item_id} has no title/description to tailor against.")

    profile_text = _load_grounded_profile_text()
    if not profile_text:
        raise ValueError(
            "No profile available. Run: python -m job_pipeline.bootstrap_resume_profile"
        )
    strategy = build_tailoring_strategy(
        str(row.get("title") or ""),
        str(row.get("description_text") or ""),
        profile_text,
        strategy_level,
    )
    content = generate_tailored_sections(row, profile_text, strategy, strategy_level)
    prof_ms = load_consolidated_profile()
    ms_rows = (
        prof_ms.get("military_service") if isinstance(prof_ms.get("military_service"), list) else []
    )
    validation = validate_tailored_content(
        content,
        str(row.get("description_text") or ""),
        profile_text,
        military_service=ms_rows,
        job_title=str(row.get("title") or ""),
    )

    optimization: Dict[str, Any] = {}
    if not content.get("error"):
        opt = run_resume_optimization_pipeline(
            content,
            str(row.get("description_text") or ""),
            profile_text,
            validation=validation,
            job_title=str(row.get("title") or ""),
            company=str(row.get("company_name") or ""),
            revalidate_fn=lambda c: validate_tailored_content(
                c,
                str(row.get("description_text") or ""),
                profile_text,
                military_service=ms_rows,
                job_title=str(row.get("title") or ""),
            ),
        )
        content = opt.get("content") or content
        validation = opt.get("validation") or validation
        optimization = opt.get("optimization") or {}

    md_path = ""
    if export_markdown and not content.get("error"):
        md_path = export_tailored_resume_markdown(
            content,
            company=str(row.get("company_name") or "company"),
            job_title=str(row.get("title") or "role"),
            item_id=int(item_id),
            outputs_root=_repo_root(),
            education=prof_ms.get("education") if isinstance(prof_ms.get("education"), list) else None,
            military_service=ms_rows,
            certifications=prof_ms.get("certifications") if isinstance(prof_ms.get("certifications"), list) else None,
        )

    return {
        "ok": not bool(content.get("error")),
        "item_id": int(item_id),
        "job_title": row.get("title"),
        "company": row.get("company_name"),
        "status": row.get("status"),
        "strategy_level": strategy_level,
        "strategy": strategy,
        "content": content,
        "validation": validation,
        "optimization": optimization,
        "markdown_path": md_path,
        "profile_source": _profile_source_label(),
    }


def format_tailored_resume_chat(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        c = result.get("content") or {}
        err = c.get("error", "unknown")
        raw = c.get("raw")
        tail = f"\n\n_Model snippet:_\n```\n{(raw or '')[:900]}\n```" if raw else ""
        return f"Resume tailoring failed: **{err}**{tail}"

    content = result.get("content") or {}
    lines = [
        f"**Tailored resume draft** — **{result.get('job_title')}** at **{result.get('company')}** (item `#{result.get('item_id')}`)",
        "",
        f"_Strategy: {result.get('strategy_level')}_ · ATS keyword overlaps (rough): **{result.get('validation', {}).get('ats_overlap_hits', 0)}**",
        "",
    ]
    if content.get("summary"):
        lines.extend(["**Summary**", content["summary"], ""])

    exps = content.get("experience") if isinstance(content.get("experience"), list) else []
    if exps:
        lines.append("**Experience (sample)**")
        for exp in exps[:3]:
            if not isinstance(exp, dict):
                continue
            lines.append(
                f"- **{exp.get('title')}** @ {exp.get('company')} _{exp.get('duration', '')}_"
            )
            for b in (exp.get("bullets") or [])[:3]:
                lines.append(f"  - {b}")
        lines.append("")

    sk = content.get("skills") if isinstance(content.get("skills"), dict) else {}
    if sk:
        tech = sk.get("technical") or []
        soft = sk.get("soft") or []
        if tech:
            lines.append("**Technical:** " + ", ".join(str(x) for x in tech[:12]))
        if soft:
            lines.append("**Soft:** " + ", ".join(str(x) for x in soft[:8]))
        lines.append("")

    v = result.get("validation") or {}
    if v.get("strengths"):
        lines.append("**Checks:** " + "; ".join(v["strengths"]))
    if v.get("issues"):
        lines.append("**Warnings:** " + "; ".join(v["issues"]))
    lines.append("")

    if result.get("markdown_path"):
        lines.append(f"**Saved Markdown:** `{result['markdown_path']}`")
    lines.append("")
    lines.append(
        "**PDF:** Export via RenderCV from `make_resume.py` / Streamlit **Manual resume** when you need an ATS-friendly PDF."
    )
    return "\n".join(lines)
