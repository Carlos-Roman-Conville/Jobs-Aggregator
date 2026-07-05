"""Four-way truth-safe classification for JD requirements vs evidence."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from job_pipeline.evidence_db import all_evidence_blobs, load_evidence_db, truth_limits_blob
from job_pipeline.named_requirements import (
    NAMED_REQUIREMENTS,
    NamedRequirement,
    assess_named_requirements,
    detect_named_in_jd,
    parse_light_exposure,
    parse_no_exposure_phrases,
    user_account_management_level,
)

ProofLevel = str  # direct_proven | adjacent_true | learnable | not_true

DIRECT = "direct_proven"
ADJACENT = "adjacent_true"
LEARNABLE = "learnable"
NOT_TRUE = "not_true"

# Requirement id -> evidence category hints
_REQ_EVIDENCE_HINTS: Dict[str, Tuple[str, ...]] = {
    "user_account_management": ("support", "documentation"),
    "active_directory": ("systems",),
    "microsoft_365": ("systems", "support"),
    "macos": ("systems", "support"),
    "mfa_sso": ("support",),
    "itsm_ticketing": ("support",),
    "vpn": ("networking", "support"),
    "video_conferencing": ("support", "systems"),
}

# Approved adjacent phrasing templates (requirement id -> default)
_ADJACENT_PHRASING: Dict[str, str] = {
    "user_account_management": (
        "Supported user onboarding workflows, access-related troubleshooting, "
        "and MFA/SSO issue resolution"
    ),
    "active_directory": "Basic Active Directory exposure through user/account support and access troubleshooting",
    "microsoft_365": "Supported users in Microsoft 365 environments, including productivity and collaboration workflows",
    "macos": "Supported MacOS endpoints and end-user troubleshooting",
    "mfa_sso": "Troubleshot MFA and SSO access issues for end users",
    "itsm_ticketing": "Managed help desk ticket requests with clear communication and timely resolution",
}


def _evidence_text_for_requirement(nr: NamedRequirement) -> str:
    db = load_evidence_db()
    employers = db.get("employers") if isinstance(db.get("employers"), dict) else {}
    cats = _REQ_EVIDENCE_HINTS.get(nr.id, ("systems", "support", "networking", "documentation"))
    parts: List[str] = []
    for rec in employers.values():
        if not isinstance(rec, dict):
            continue
        for cat in cats:
            parts.extend(str(x) for x in (rec.get(cat) or []))
    return " ".join(parts).lower()


def _profile_supports(nr: NamedRequirement, profile_blob: str) -> bool:
    for pat in nr.profile_patterns:
        if re.search(pat, profile_blob, re.I):
            return True
    return False


def _blocked_by_limits(nr: NamedRequirement, limits: List[str], profile_no: Set[str]) -> bool:
    label_l = nr.label.lower()
    for lim in limits:
        ll = lim.lower()
        if label_l in ll or nr.id.replace("_", " ") in ll:
            return True
    for phrase in profile_no:
        if label_l in phrase or any(n.lower() in phrase for n in nr.surface_names):
            return True
    return False


def classify_requirement(
    nr: NamedRequirement,
    *,
    profile_text: str = "",
    light_items: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Classify one named requirement against evidence + profile.

    Returns {id, label, level, approved_phrasing, reason}.
    """
    profile_blob = (profile_text or "").lower()
    evidence_blob = all_evidence_blobs()
    limits = truth_limits_blob()
    no_exposure = parse_no_exposure_phrases(profile_text)
    light_items = light_items if light_items is not None else parse_light_exposure(profile_text)

    if _blocked_by_limits(nr, limits, no_exposure):
        return {
            "id": nr.id,
            "label": nr.label,
            "level": NOT_TRUE,
            "approved_phrasing": "",
            "reason": "blocked by evidence truth_limits or profile No exposure",
        }

    # Direct: profile pattern OR explicit evidence category match on surface names
    if _profile_supports(nr, profile_blob):
        for name in nr.surface_names:
            if name.lower() in evidence_blob or name.lower() in profile_blob:
                return {
                    "id": nr.id,
                    "label": nr.label,
                    "level": DIRECT,
                    "approved_phrasing": name,
                    "reason": "profile and evidence support direct claim",
                }
        return {
            "id": nr.id,
            "label": nr.label,
            "level": DIRECT,
            "approved_phrasing": nr.label,
            "reason": "profile supports direct claim",
        }

    ev_text = _evidence_text_for_requirement(nr)
    for name in nr.surface_names:
        n_l = name.lower()
        if n_l in ev_text:
            return {
                "id": nr.id,
                "label": nr.label,
                "level": DIRECT,
                "approved_phrasing": name,
                "reason": "evidence DB supports direct claim",
            }

    # Light exposure -> learnable or adjacent
    for item in light_items:
        skill = (item.get("skill") or "").lower()
        framing = (item.get("framing") or "").strip()
        if skill and any(skill in n.lower() or n.lower() in skill for n in nr.surface_names + (nr.label,)):
            return {
                "id": nr.id,
                "label": nr.label,
                "level": LEARNABLE if "learning" in framing.lower() or "study" in framing.lower() else ADJACENT,
                "approved_phrasing": framing or _ADJACENT_PHRASING.get(nr.id, ""),
                "reason": "light exposure framing from profile",
            }

    # Adjacent heuristics for common help-desk reqs
    if nr.id == "user_account_management":
        level = user_account_management_level(profile_text)
        if level == "partial":
            return {
                "id": nr.id,
                "label": nr.label,
                "level": ADJACENT,
                "approved_phrasing": _ADJACENT_PHRASING["user_account_management"],
                "reason": "partial account support only — no full lifecycle claim",
            }
        if level == "full":
            return {
                "id": nr.id,
                "label": nr.label,
                "level": DIRECT,
                "approved_phrasing": "User account support including onboarding and access troubleshooting",
                "reason": "profile supports account operations",
            }

    # Adjacent: related evidence tokens without direct tool name
    adjacent_tokens = {
        "active_directory": ("access", "account", "onboarding"),
        "microsoft_365": ("office", "productivity", "collaboration", "google workspace"),
        "mfa_sso": ("access", "authentication", "login"),
        "itsm_ticketing": ("ticket", "help desk", "support request"),
    }
    tokens = adjacent_tokens.get(nr.id, ())
    if tokens and any(t in ev_text for t in tokens):
        return {
            "id": nr.id,
            "label": nr.label,
            "level": ADJACENT,
            "approved_phrasing": _ADJACENT_PHRASING.get(nr.id, ""),
            "reason": "adjacent evidence — phrase carefully, do not upgrade",
        }

    return {
        "id": nr.id,
        "label": nr.label,
        "level": NOT_TRUE,
        "approved_phrasing": "",
        "reason": "no evidence or profile support",
    }


def classify_jd_requirements(
    job_description: str,
    profile_text: str = "",
) -> List[Dict[str, Any]]:
    """Classify each named JD requirement (4-way)."""
    jd_named = detect_named_in_jd(job_description)
    light_items = parse_light_exposure(profile_text)
    return [
        classify_requirement(nr, profile_text=profile_text, light_items=light_items)
        for nr in jd_named
    ]


def classifications_to_gap_records(classifications: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert not_true / learnable gaps into resume_gaps-compatible records."""
    gaps: List[Dict[str, Any]] = []
    for c in classifications:
        level = c.get("level")
        if level in (NOT_TRUE, LEARNABLE):
            gaps.append({
                "requirement": c.get("label"),
                "category": "truth_classifier",
                "severity": "high" if level == NOT_TRUE else "medium",
                "question": (
                    f"JD asks for {c.get('label')}. Classifier: {level} ({c.get('reason')}). "
                    "Confirm or skip."
                ),
                "source": "truth_classifier",
                "proof_level": level,
            })
    return gaps


def merge_assessment_with_classifications(
    job_description: str,
    profile_text: str,
) -> Dict[str, Any]:
    """Combine named_requirements assessment with 4-way classifier."""
    assessment = assess_named_requirements(job_description, profile_text)
    classifications = classify_jd_requirements(job_description, profile_text)
    by_id = {c["id"]: c for c in classifications if c.get("id")}
    to_surface: List[Dict[str, Any]] = []
    for item in assessment.get("to_surface") or []:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        cls = by_id.get(cid) or {}
        level = cls.get("level") or DIRECT
        if level == NOT_TRUE:
            continue
        merged = dict(item)
        merged["proof_level"] = level
        if level in (ADJACENT, LEARNABLE) and cls.get("approved_phrasing"):
            merged["approved_framing"] = cls["approved_phrasing"]
        to_surface.append(merged)
    return {
        "jd_named": assessment.get("jd_named") or [],
        "to_surface": to_surface,
        "gaps": (assessment.get("gaps") or []) + classifications_to_gap_records(classifications),
        "classifications": classifications,
    }
