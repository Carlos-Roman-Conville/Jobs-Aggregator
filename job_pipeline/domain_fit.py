"""
Domain-aware fit: job title/description vs career_profile.json.
See calculate_domain_fit() and career_identity_prompt_block().
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Set

_BASE = Path(__file__).resolve().parent

# family_id -> lowercase phrases to find in title/description
FAMILY_KEYWORDS: Dict[str, List[str]] = {
    "it_support": [
        "it support",
        "service desk",
        "support specialist",
        "technical support rep",
        "information technology support",
    ],
    "helpdesk": [
        "help desk",
        "helpdesk",
        "desktop support",
        "user support",
        "end user support",
    ],
    "noc_technician": [
        "noc technician",
        "noc engineer",
        "network operations center",
        "noc analyst",
    ],
    "jr_sysadmin": [
        "junior systems admin",
        "jr systems admin",
        "jr. systems admin",
        "junior sysadmin",
        "jr sysadmin",
        "associate systems admin",
        "systems admin i",
        "system administrator i",
    ],
    "systems_support_ops": [
        "systems support",
        "systems administrator",
        "system administrator",
        "linux admin",
        "windows server admin",
        "server admin",
        "infrastructure technician",
    ],
    "operations": [
        "operations",
        " ops ",
        "ops)",
        "(ops",
        "operational",
        "service delivery",
        "field operations",
    ],
    "logistics": [
        "logistics",
        "supply chain",
        "inventory",
        "warehouse",
        "distribution",
        "fulfillment",
        "routing",
        "dispatch",
    ],
    "business_operations": [
        "business operations",
        "bizops",
        "cross-functional",
        "operational planning",
        "operations analyst",
    ],
    "technical_operations": [
        "technical operations",
        "systems operations",
        "production operations",
        "it operations",
        "platform operations",
        "business systems",
        "implementation",
        "integrations",
        "systems support",
    ],
    "process_improvement": [
        "process improvement",
        "continuous improvement",
        "workflow",
        "efficiency",
        " sop",
        "sop ",
        "operational excellence",
    ],
    "procurement": [
        "procurement",
        "purchasing",
        "sourcing",
        "vendor management",
        "supplier management",
    ],
    "procurement_it_adjacent": [
        "it procurement",
        "technology procurement",
        "software asset management",
        "hardware asset management",
        "it asset management",
    ],
    "coordination": [
        "coordinator",
        "scheduling",
        "dispatch",
        "routing",
        "calendar management",
        "resource planning",
    ],
    "coordination_pure": [
        "administrative coordinator",
        "office coordinator",
        "executive assistant",
        "facilities coordinator",
        "event coordinator",
    ],
    "implementation": [
        "implementation",
        "onboarding",
        "rollout",
        "deployment coordination",
        "customer implementation",
    ],
    "software_engineering": [
        "software engineer",
        "software developer",
        "application developer",
    ],
    "backend_engineering": [
        "backend developer",
        "backend engineer",
        "django",
        "flask",
        "microservices",
        "distributed systems",
    ],
    "frontend_engineering": [
        "frontend",
        "front-end",
        "react",
        "angular",
        "ui engineer",
        "javascript developer",
    ],
    "devops_engineering": [
        "devops",
        "ci/cd",
        "kubernetes",
        "terraform",
        "infrastructure as code",
        "platform engineer",
    ],
    "site_reliability_engineering": [
        "site reliability",
        "reliability engineer",
        " sre",
        "sre ",
    ],
    "qa_engineering": [
        "qa engineer",
        "test engineer",
        "software engineer in test",
        "sdet",
    ],
}

TECH_ALIGNED_FAMILIES: Set[str] = {
    "it_support",
    "helpdesk",
    "noc_technician",
    "jr_sysadmin",
    "systems_support_ops",
    "technical_operations",
    "implementation",
    "software_engineering",
    "backend_engineering",
    "frontend_engineering",
    "devops_engineering",
    "site_reliability_engineering",
    "qa_engineering",
}

DEFAULT_PROFILE_PATH = _BASE / "career_profile.json"


def load_career_profile() -> Dict[str, Any]:
    path = (os.getenv("JOB_PIPELINE_CAREER_PROFILE") or "").strip()
    p = Path(path) if path else DEFAULT_PROFILE_PATH
    if not p.is_file():
        return _minimal_fallback_profile()
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return _minimal_fallback_profile()
    return data


def _minimal_fallback_profile() -> Dict[str, Any]:
    return {
        "identity": {
            "primary_domain": "information_technology",
            "tech_as_support": False,
            "tech_as_primary": True,
            "management_track": False,
            "experience_band": "junior",
        },
        "identity_prompt": [
            "Prefers hands-on IT / systems support style roles over unrelated logistics-only jobs.",
            "Pure software engineering IC roles are usually a stretch unless clearly entry-level.",
        ],
        "target_role_families": sorted(
            [
                "it_support",
                "helpdesk",
                "technical_operations",
                "jr_sysadmin",
                "implementation",
            ]
        ),
        "target_titles": ["it support specialist", "helpdesk analyst", "junior systems administrator"],
        "avoid_role_families": [
            "logistics",
            "software_engineering",
            "backend_engineering",
            "frontend_engineering",
            "devops_engineering",
            "site_reliability_engineering",
            "qa_engineering",
        ],
        "avoid_titles": ["senior software engineer", "principal software engineer", "staff software engineer"],
        "constraints": {"claim_years_technical_experience": 2, "max_apply_min_years_experience_gap": 4},
    }


def classify_role_family(job_title: str, job_desc: str) -> List[str]:
    """
    Return sorted list of family ids with at least one keyword hit in title or description.
    Title is weighted by scanning a doubled title prefix in the search blob.
    """
    title = (job_title or "").lower()
    desc = (job_desc or "").lower()
    blob = f"{title} {title} {desc}"

    found: Set[str] = set()
    for fam, kws in FAMILY_KEYWORDS.items():
        for kw in kws:
            k = kw.lower().strip()
            if not k:
                continue
            if k in title or k in blob:
                found.add(fam)
                break
    return sorted(found)


def posting_has_tech_role_signal(job_title: str, job_desc: str) -> bool:
    """True if posting matches at least one technology-aligned family."""
    m = set(classify_role_family(job_title, job_desc))
    return bool(m & TECH_ALIGNED_FAMILIES)


def _domain_multiplier(domain_score: float) -> float:
    """Maps 0..1 domain score to multiplier applied to base blended fit."""
    ds = max(0.0, min(1.0, float(domain_score)))
    if ds < 0.22:
        return 0.18 + 0.35 * ds
    if ds > 0.75:
        return 0.78 + 0.22 * ds
    return 0.45 + 0.55 * ds


def _queue_reason_one_liner(
    domain_score: float,
    penalized: List[str],
    good: List[str],
    title_avoid: bool,
    *,
    tech_primary: bool,
) -> str:
    if title_avoid:
        return "Penalized: title matches explicit avoid pattern"
    if tech_primary:
        if penalized and not good:
            return "Penalized: strong non-target / engineering-only signals for tech-primary profile"
        if good and domain_score >= 0.72:
            return "Strong technical / IT operations alignment"
        if good and domain_score >= 0.45:
            return "Moderate IT / technical operations overlap"
        if penalized and good:
            return "Mixed signals: IT-friendly keywords with some avoid-family noise"
        if domain_score < 0.28:
            return "Low domain fit vs tech-primary profile"
        return "Neutral domain fit"
    # legacy operations-first flavor
    if penalized and not good:
        return "Penalized: strong engineering-role signals, weak operations fit"
    if good and domain_score >= 0.72:
        if "logistics" in good or "coordination" in good:
            return "Strong operations/logistics overlap"
        if "technical_operations" in good or "implementation" in good:
            return "Technical-operations hybrid, good fit"
        return "Strong operations / business-ops alignment"
    if good and domain_score >= 0.45:
        return "Moderate operations-related overlap"
    if penalized and good:
        return "Mixed signals: ops keywords with some engineering-family matches"
    if domain_score < 0.28:
        return "Low domain fit vs operations-first profile"
    return "Neutral domain fit"


def _title_suggests_entry_level(title: str) -> bool:
    t = (title or "").lower()
    return bool(
        re.search(
            r"\b(junior|jr\.?|entry|associate|graduate|intern|apprentice|level\s*1|i\b)\b",
            t,
        )
    )


def calculate_domain_fit(job_title: str, job_desc: str, profile: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Returns domain_score 0..1, families, reasons, queue_reason, and domain_multiplier.
    """
    profile = profile or load_career_profile()
    identity = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    tech_primary = bool(identity.get("tech_as_primary"))
    target_fams: Set[str] = set(profile.get("target_role_families") or [])
    avoid_fams: Set[str] = set(profile.get("avoid_role_families") or [])
    target_titles: List[str] = list(profile.get("target_titles") or [])
    avoid_titles: List[str] = list(profile.get("avoid_titles") or [])

    title = job_title or ""
    desc = job_desc or ""
    title_l = title.lower()
    blob = f"{title_l} {desc.lower()}"

    matched = classify_role_family(title, desc)
    matched_set = set(matched)
    penalized_families = sorted(matched_set & avoid_fams)
    good_families = sorted(matched_set & target_fams)

    reasons: List[str] = []
    score = 0.5

    title_avoid_hit = False
    for phrase in avoid_titles:
        pl = phrase.lower().strip()
        if pl and pl in title_l:
            title_avoid_hit = True
            score = 0.07
            reasons.append(f"Hard penalty: title matches avoid pattern ({phrase})")
            break

    if not title_avoid_hit:
        for phrase in target_titles:
            pl = phrase.lower().strip()
            if pl and pl in title_l:
                score = max(score, 0.92)
                reasons.append(f"Title aligns with target role ({phrase})")

    tech_hybrid = bool(matched_set & TECH_ALIGNED_FAMILIES)
    ops_hybrid = bool(good_families) or bool(
        matched_set
        & {
            "operations",
            "logistics",
            "technical_operations",
            "business_operations",
            "coordination",
            "implementation",
        }
    )

    if not title_avoid_hit:
        if tech_primary and matched_set & {"logistics", "coordination_pure"} and not tech_hybrid:
            score -= 0.18
            reasons.append("Non-technical logistics/coordination signals without IT context")

        for pf in penalized_families:
            if pf not in matched_set:
                continue
            entry = _title_suggests_entry_level(title)
            if tech_primary and pf in ("software_engineering", "backend_engineering", "frontend_engineering"):
                delta = 0.06 if entry else 0.14
                score -= delta
                reasons.append(
                    f"{'Soft' if entry else 'Moderate'} penalty: {pf} (tech-primary profile; entry-level title eases)"
                )
            elif tech_primary and pf in ("devops_engineering", "site_reliability_engineering", "qa_engineering"):
                delta = 0.08 if entry else 0.18
                score -= delta
                reasons.append(
                    f"{'Soft' if entry else 'Strong'} penalty: {pf} without clear junior framing"
                )
            elif (not tech_primary) and ops_hybrid and pf in (
                "software_engineering",
                "backend_engineering",
                "frontend_engineering",
            ):
                score -= 0.11
                reasons.append(f"Soft penalty: {pf} keywords with operations context")
            elif (not tech_primary) and ops_hybrid and pf in (
                "devops_engineering",
                "site_reliability_engineering",
                "qa_engineering",
            ):
                score -= 0.14
                reasons.append(f"Moderate penalty: {pf} with ops-related signals")
            else:
                score -= 0.24
                reasons.append(f"Penalized role family: {pf}")

        for gf in good_families:
            score += 0.10
            reasons.append(f"Target role family: {gf}")

        if tech_primary:
            tech_terms = [
                "powershell",
                "bash",
                "linux",
                "windows server",
                "active directory",
                "vpn",
                "ticketing",
                "jira servicedesk",
                "servicenow",
                "network troubleshooting",
                "tcp/ip",
                "dns",
            ]
            hits_t = [t for t in tech_terms if t in blob]
            if hits_t and score > 0.12:
                bump = min(0.14, 0.028 * len(hits_t))
                score += bump
                reasons.append("Technical skill signals (" + ", ".join(hits_t[:5]) + ")")
            if identity.get("management_track") and tech_hybrid:
                if re.search(r"\b(manager|lead|supervisor)\b", title, re.I):
                    score += 0.04
                    reasons.append("People leadership title with technical/IR signals")
        else:
            if identity.get("tech_as_support"):
                support_terms = [
                    "salesforce",
                    "integrations",
                    "business systems",
                    "it operations",
                    "platform operations",
                    "systems support",
                    "implementation",
                ]
                hits = [t for t in support_terms if t in blob]
                if hits and score > 0.12:
                    bump = min(0.12, 0.035 * len(hits))
                    score += bump
                    reasons.append("Tech-as-support signals (" + ", ".join(hits[:4]) + ")")

            if identity.get("management_track") and good_families:
                if re.search(r"\b(manager|lead|supervisor|director|head of)\b", title, re.I):
                    score += 0.055
                    reasons.append("Management title aligned with operations families")

    score = max(0.0, min(1.0, score))
    mult = _domain_multiplier(score)
    qreason = _queue_reason_one_liner(
        score,
        penalized_families,
        good_families,
        title_avoid_hit,
        tech_primary=tech_primary,
    )

    if not reasons:
        reasons.append("No strong domain signals; default neutral weighting")

    return {
        "domain_score": round(score, 4),
        "domain_multiplier": round(mult, 4),
        "matched_families": good_families,
        "penalized_families": penalized_families,
        "detected_families": matched,
        "reasons": reasons[:14],
        "queue_reason": qreason,
        "title_avoid_hit": title_avoid_hit,
    }


def merge_blended_with_domain(base_blended: float, domain_result: Dict[str, Any]) -> float:
    """Return the model+heuristic blended score UNCHANGED.

    The domain multiplier was REMOVED 2026-06-20. It was a speculative keyword/title
    guard meant to catch the LLM overrating off-domain jobs — but it was applied as a
    MULTIPLIER on a hand-listed keyword allowlist, so when it misclassified a real IT
    job (e.g. "Network Monitoring & Support Tech" -> domain_score 0.08 -> x0.21) it
    vetoed a strong LLM fit and buried good jobs with the junk. Measured against the
    live DB: removing it un-buried all 24 wrongly-buried good-fit jobs (6/14/24 below
    0.10/0.20/0.30 -> 0) while the LLM still sinks genuine junk on its own (Software
    Engineer/Nurse stayed < 0.11). domain_score/domain_multiplier are still COMPUTED and
    stored in summary_json for transparency; they no longer scale the score.
    """
    return round(max(0.0, min(1.0, float(base_blended))), 3)


def career_identity_prompt_block(profile: Dict[str, Any] | None = None) -> str:
    """Short text appended to LLM summarize prompt."""
    profile = profile or load_career_profile()
    ident = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    primary = ident.get("primary_domain", "information_technology")
    secondary = ident.get("secondary_domain", "technical_operations")
    band = ident.get("experience_band", "")
    bullets = profile.get("identity_prompt")
    lines_body: List[str] = []
    if isinstance(bullets, list):
        lines_body = [str(b).strip() for b in bullets if str(b).strip()]
    if not lines_body:
        tp = ident.get("tech_as_primary")
        ts = ident.get("tech_as_support")
        if tp:
            lines_body.append(
                "Tech-primary profile: prioritize hands-on IT, support, jr systems, NOC, "
                "and technical operations—not unrelated logistics/coordinator-only postings."
            )
        elif ts:
            lines_body.append(
                "Technology is an enabler: prefer technical operations, implementation, "
                "and hybrid operations + systems—not pure IC software ladders."
            )
        else:
            lines_body.append("Operations-centric profile with selective technical overlap.")
        targets = profile.get("target_role_families") or []
        avoid = profile.get("avoid_role_families") or []
        lines_body.append(f"Prefer role families resembling: {', '.join(str(x) for x in targets[:10])}.")
        lines_body.append(
            f"Tighten verdict for families: {', '.join(str(x) for x in avoid[:8])}, "
            "unless JD clearly aligns with transferable hands-on tooling experience."
        )
    band_line = f"- Experience calibration: {band}.\n" if band else ""
    body = "".join(f"- {line}\n" for line in lines_body)
    return (
        "APPLICANT_CAREER_IDENTITY (mandatory calibration — follow exactly):\n"
        f"- Declared domains: primary={primary}; secondary={secondary}.\n"
        f"{band_line}"
        "- Use these bullets verbatim as the truth source for verdict + fit calibration:\n"
        f"{body}"
    )


def operations_identity_prompt_block() -> str:
    """Operations-lens calibration, used ONLY when scoring operations-category jobs.

    The default (IT-primary) identity deliberately discounts the applicant's
    operations background ("transferable only when JD revolves around tooling"),
    which is correct for the IT search but wrongly buries genuine ops fits. For
    ops-lane jobs we score against the applicant's REAL operations record instead,
    so a coordinator/specialist role he's qualified for isn't rated as a 0.30.
    """
    return (
        "APPLICANT_CAREER_IDENTITY (operations lens — mandatory calibration, follow exactly):\n"
        "- Score THIS posting against the applicant's OPERATIONS record, not an IT lens.\n"
        "- ~4.5 years of hands-on operations experience: IT Operations Manager at a flagship "
        "entertainment venue (team coordination, scheduling and shift coverage, vendor management, "
        "SOP/runbook/KB authoring, new-hire training, live-event incident and escalation ownership) "
        "PLUS Junior Operations Coordinator at 1-800-GOT-JUNK (dispatch, route/logistics coordination, "
        "on-site estimates, in-person negotiation, Salesforce, promoted twice).\n"
        "- Treat these as STRONG, on-target fits (do not under-rate): operations coordinator, "
        "operations specialist / associate / analyst, implementation / onboarding specialist, "
        "customer success, service operations, project/program coordinator, dispatch/logistics "
        "coordinator, office manager, and small-shop / generalist operations manager.\n"
        "- Credit coordination, vendor, scheduling, process/SOP, documentation, training, and "
        "customer-facing experience as PRIMARY here — not 'only transferable with IT tooling'.\n"
        "- Stretch (genuinely lower fit, rate honestly): specialized senior ops requiring domain "
        "expertise he lacks (revenue ops, clinical/healthcare ops, financial/treasury ops, product ops "
        "at scale), and director/VP roles leading large teams.\n"
        "- Additional strengths: bilingual English/Spanish; eight years U.S. Army Reserve (discipline, "
        "instruction, high-pressure coordination); B.A. Political Science (3.80, Cum Laude).\n"
    )
