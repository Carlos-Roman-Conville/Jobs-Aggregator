"""
Named JD requirement detection, profile support assessment, and light-exposure parsing.

Used by resume_tailor, resume_gaps, and cover_letter_tailor to surface truthful
matches BY NAME or record structured gaps — never fabricate.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Pattern, Set, Tuple

# Shared keyword list (superset used by gap heuristics).
COMMON_REQ_TERMS: List[str] = [
    "python", "javascript", "typescript", "java", "sql",
    "aws", "azure", "gcp", "docker", "kubernetes",
    "react", "node", "node.js", "mongodb", "postgres",
    "git", "agile", "scrum",
    "leadership", "communication", "stakeholder", "operations",
    "project management", "program management",
    "excel", "tableau", "power bi", "salesforce",
    "security", "compliance", "budget", "p&l",
    "linux", "windows server", "active directory", "powershell",
    "bash", "vmware", "hyper-v", "okta", "intune", "sccm",
    "nagios", "datadog", "splunk", "servicenow", "jira",
    "networking", "tcp/ip", "dns", "dhcp", "firewall",
    "ansible", "terraform", "ci/cd",
    "microsoft 365", "office 365", "macos", "mac os",
    "freshdesk", "zendesk", "vpn", "mfa", "sso",
    "onboarding", "offboarding", "password reset",
    "ticketing", "itsm", "video conferencing", "zoom", "teams",
]

HYPE_BANNED_WORDS: Tuple[str, ...] = (
    "revolutionized",
    "revolutionised",
    "transformed",
    "cutting-edge",
    "cutting edge",
    "world-class",
    "world class",
    "synergy",
    "single-handedly",
    "single handedly",
    "groundbreaking",
    "game-changing",
    "game changing",
    "paradigm",
    "best-in-class",
    "best in class",
    "industry-leading",
    "industry leading",
    "disruptive",
    "unparalleled",
    "visionary",
)

AI_PIPELINE_FACTUAL_FRAMING = (
    "Built a modular Python-based job-application pipeline for job discovery, scoring, "
    "resume tailoring, and application tracking — demonstrating practical automation, "
    "API usage, and workflow design."
)

# Account-management claim strength (R2.3).
_USER_ACCOUNT_STRONG_PATTERNS: Tuple[str, ...] = (
    r"account\s+creation",
    r"access\s+provision",
    r"password\s+reset",
    r"account\s+deactiv",
    r"added/removed\s+users",
    r"added\s+and\s+removed\s+users",
    r"removed\s+users",
    r"user\s+provisioning",
    r"group\s+management",
    r"permission\s+management",
    r"deactivat\w+\s+accounts",
)
_USER_ACCOUNT_PARTIAL_PATTERNS: Tuple[str, ...] = (
    r"onboarding\s+doc",
    r"onboarding\s+workflow",
    r"user\s+support",
    r"end[-\s]?user\s+support",
    r"access[-\s]?related\s+troubleshoot",
    r"account\s+support",
    r"help\s+desk",
)
STRONG_ACCOUNT_MANAGEMENT_PHRASES: Tuple[str, ...] = (
    "manage user accounts",
    "managing user accounts",
    "managed user accounts",
    "proven ability to manage user accounts",
    "user account management",
    "account provisioning",
    "access provisioning",
)

VAGUE_VERB_BANNED: Tuple[str, ...] = (
    "leveraged",
    "utilized",
    "spearheaded",
)

_SKILLS_CAP_DEFAULT = 20
_SKILLS_CAP_MIN = 18
_SKILLS_CAP_MAX = 22
_PROJECTS_CAP_DEFAULT = 2
_PROJECTS_CAP_MIN = 1

_JD_YEARS_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*-\s*(\d{1,2})\s*years(?:\s+of)?(?:\s+(?:experience|exp))?\b",
    re.IGNORECASE,
)
_JD_YEARS_PLUS_RE = re.compile(
    r"\b(\d{1,2})\+\s*years(?:\s+of)?(?:\s+(?:experience|exp))?\b",
    re.IGNORECASE,
)
_OUTPUT_YEARS_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*-\s*(\d{1,2})\s*years\b",
    re.IGNORECASE,
)
_OUTPUT_YEARS_PLUS_RE = re.compile(
    r"\b(\d{1,2})\+\s*years\b",
    re.IGNORECASE,
)
_PROFILE_YEARS_RE = re.compile(
    r"\b(?:over|more than|at least|approximately|about|~)?\s*(\d{1,2})\+?\s*years\b",
    re.IGNORECASE,
)

PROJECT_JARGON_PHRASES: Tuple[str, ...] = (
    "architectural pivot",
    "paradigm shift",
    "tech stack overhaul",
    "scalable architecture",
    "microservices architecture",
    "cloud-native architecture",
)

SUPPORT_ROLE_TITLE_HINTS: Tuple[str, ...] = (
    "help desk",
    "helpdesk",
    "it support",
    "desktop support",
    "service desk",
    "technical support",
    "support specialist",
    "support technician",
)

AI_PIPELINE_SUPPORT_FRAMING = (
    "I build personal automation tools, including a Python job-application pipeline — "
    "the same habit I bring to support work: spotting repeated friction, documenting it, "
    "and improving the workflow."
)


@dataclass(frozen=True)
class NamedRequirement:
    id: str
    label: str
    jd_patterns: Tuple[str, ...]
    profile_patterns: Tuple[str, ...]
    surface_names: Tuple[str, ...]
    severity: str = "high"
    category: str = "named_req"


NAMED_REQUIREMENTS: Tuple[NamedRequirement, ...] = (
    NamedRequirement(
        id="user_account_management",
        label="User account management",
        jd_patterns=(
            r"user\s+account",
            r"account\s+management",
            r"onboarding",
            r"offboarding",
            r"account\s+creation",
            r"access\s+provision",
            r"password\s+reset",
            r"permission\s+management",
            r"group\s+management",
            r"account\s+deactiv",
            r"user\s+provisioning",
        ),
        profile_patterns=(
            r"user\s+account",
            r"onboarding",
            r"offboarding",
            r"password\s+reset",
            r"access\s+provision",
            r"added/removed\s+users",
            r"account\s+creation",
            r"permission",
            r"group\s+management",
        ),
        surface_names=(
            "user account management",
            "onboarding",
            "offboarding",
            "password reset",
            "account creation",
            "access provisioning",
        ),
    ),
    NamedRequirement(
        id="active_directory",
        label="Active Directory",
        jd_patterns=(
            r"active\s+directory",
            r"\bad\b",
            r"azure\s+ad",
            r"microsoft\s+entra",
            r"entra\s+id",
            r"group\s+policy",
            r"\bgpmc\b",
        ),
        profile_patterns=(
            r"active\s+directory",
            r"\bad\b",
            r"azure\s+ad",
            r"entra",
            r"group\s+policy",
        ),
        surface_names=("Active Directory", "AD", "Azure AD", "Entra"),
    ),
    NamedRequirement(
        id="microsoft_365",
        label="Microsoft 365",
        jd_patterns=(
            r"microsoft\s*365",
            r"office\s*365",
            r"\bm365\b",
            r"exchange\s+online",
            r"sharepoint",
            r"teams\s+admin",
        ),
        profile_patterns=(
            r"microsoft\s*365",
            r"office\s*365",
            r"\bm365\b",
            r"exchange\s+online",
            r"sharepoint",
        ),
        surface_names=("Microsoft 365", "Office 365", "M365"),
    ),
    NamedRequirement(
        id="macos",
        label="MacOS support",
        jd_patterns=(
            r"\bmacos\b",
            r"\bmac\s+os\b",
            r"\bapple\b.*\b(endpoint|desktop|support)\b",
        ),
        profile_patterns=(
            r"\bmacos\b",
            r"\bmac\s+os\b",
            r"\bmac\b.*\b(support|troubleshoot|desktop)\b",
        ),
        surface_names=("MacOS", "Mac OS", "Mac desktop"),
    ),
    NamedRequirement(
        id="mfa_sso",
        label="MFA / SSO",
        jd_patterns=(
            r"\bmfa\b",
            r"multi[-\s]?factor",
            r"\bsso\b",
            r"single\s+sign[-\s]?on",
            r"two[-\s]?factor",
            r"\b2fa\b",
        ),
        profile_patterns=(
            r"\bmfa\b",
            r"multi[-\s]?factor",
            r"\bsso\b",
            r"single\s+sign[-\s]?on",
            r"two[-\s]?factor",
        ),
        surface_names=("MFA", "multi-factor authentication", "SSO", "single sign-on"),
    ),
    NamedRequirement(
        id="itsm_ticketing",
        label="Ticketing / ITSM",
        jd_patterns=(
            r"\bitsm\b",
            r"ticketing",
            r"service\s+desk\s+tool",
            r"help\s+desk\s+tool",
            r"freshdesk",
            r"zendesk",
            r"jira\s+service",
            r"servicenow",
        ),
        profile_patterns=(
            r"\bitsm\b",
            r"ticketing",
            r"service\s+desk",
            r"help\s+desk\s+ticket",
            r"freshdesk",
            r"zendesk",
            r"jira",
            r"servicenow",
        ),
        surface_names=(
            "ticketing",
            "ITSM",
            "Freshdesk",
            "Zendesk",
            "Jira Service Management",
            "ServiceNow",
        ),
    ),
    NamedRequirement(
        id="vpn",
        label="VPN",
        jd_patterns=(r"\bvpn\b", r"virtual\s+private\s+network"),
        profile_patterns=(r"\bvpn\b", r"virtual\s+private\s+network"),
        surface_names=("VPN",),
        severity="medium",
    ),
    NamedRequirement(
        id="video_conferencing",
        label="Video conferencing",
        jd_patterns=(
            r"video\s+conferenc",
            r"\bzoom\b",
            r"microsoft\s+teams",
            r"\bwebex\b",
            r"google\s+meet",
        ),
        profile_patterns=(
            r"video\s+conferenc",
            r"\bzoom\b",
            r"microsoft\s+teams",
            r"\bwebex\b",
            r"google\s+meet",
        ),
        surface_names=("video conferencing", "Zoom", "Microsoft Teams", "Webex"),
        severity="medium",
    ),
    NamedRequirement(
        id="pst_hours",
        label="PST / time-zone coverage",
        jd_patterns=(
            r"\bpst\b",
            r"pacific\s+time",
            r"time\s*zone",
            r"business\s+hours",
            r"after[-\s]?hours",
        ),
        profile_patterns=(
            r"\bpst\b",
            r"pacific\s+time",
            r"time\s*zone",
            r"after[-\s]?hours",
            r"on[-\s]?call",
        ),
        surface_names=("PST", "Pacific time", "time zone", "business hours"),
        severity="medium",
    ),
    NamedRequirement(
        id="document_management",
        label="Document management",
        jd_patterns=(
            r"document\s+management",
            r"sharepoint",
            r"file\s+sharing",
            r"records\s+management",
        ),
        profile_patterns=(
            r"document\s+management",
            r"sharepoint",
            r"file\s+sharing",
        ),
        surface_names=("document management", "SharePoint"),
        severity="medium",
    ),
)


def _compile_patterns(patterns: Tuple[str, ...]) -> List[Pattern[str]]:
    out: List[Pattern[str]] = []
    for p in patterns:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue
    return out


_NAMED_JD_COMPILED: Dict[str, List[Pattern[str]]] = {
    nr.id: _compile_patterns(nr.jd_patterns) for nr in NAMED_REQUIREMENTS
}
_NAMED_PROFILE_COMPILED: Dict[str, List[Pattern[str]]] = {
    nr.id: _compile_patterns(nr.profile_patterns) for nr in NAMED_REQUIREMENTS
}


def extract_requirements(job_description: str) -> List[str]:
    desc = (job_description or "").lower()
    return [t for t in COMMON_REQ_TERMS if t in desc]


def _no_exposure_section(profile_text: str) -> str:
    # Find ALL matching sections and concatenate their bodies. The previous
    # implementation took only the first match, which fired on a parent
    # heading like "## 3. Honest limits — do NOT claim primary expertise"
    # and captured the empty space before the real "### No exposure" subsection.
    bodies = []
    for m in re.finditer(
        r"(?ims)(?:^#{1,6}\s*[^\n]*\b(?:no\s+exposure|never\s+touched|do\s+not\s+claim)\b[^\n]*\n)(.*?)(?=^#{1,6}\s|\Z)",
        profile_text or "",
    ):
        body = m.group(1).strip()
        if body:
            bodies.append(body)
    if bodies:
        return "\n\n".join(bodies)
    m2 = re.search(
        r"(?ims)^#{1,6}\s*[^\n]*Honest\s+limits[^\n]*\n(.*?)(?=^#{1,6}\s|\Z)",
        profile_text or "",
    )
    return m2.group(1) if m2 else ""


def parse_no_exposure_phrases(profile_text: str) -> Set[str]:
    body = _no_exposure_section(profile_text)
    phrases: Set[str] = set()
    for line in (body or "").splitlines():
        line = line.strip()
        if not line.startswith(("-", "*")):
            continue
        rest = re.sub(r"^[\-\*\+]\s+", "", line)
        bm = re.match(r"\*\*([^*]{2,240})\*\*", rest)
        chunk = bm.group(1).strip() if bm else rest.split("—")[0].split("-")[0].strip()
        chunk = re.sub(r"^\([^)]*\)\s*", "", chunk).strip()
        if len(chunk) >= 4:
            phrases.add(chunk.lower())
    return phrases


def _light_exposure_section(profile_text: str) -> str:
    m = re.search(
        r"(?ims)^#{1,6}\s*[^\n]*light\s+exposure[^\n]*\n(.*?)(?=^#{1,6}\s|\Z)",
        profile_text or "",
    )
    return m.group(1).strip() if m else ""


def parse_light_exposure(
    profile_text: str = "",
    profile_json: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """
    Parse approved light-exposure framings from career_master markdown and/or JSON.
    Items blocked by No exposure / do NOT claim are excluded.
    """
    blocked = parse_no_exposure_phrases(profile_text or "")
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    def _blocked(skill: str) -> bool:
        sk = (skill or "").lower()
        for b in blocked:
            if b in sk or sk in b:
                return True
        return False

    body = _light_exposure_section(profile_text or "")
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith(("-", "*")):
            continue
        rest = re.sub(r"^[\-\*\+]\s+", "", line)
        m = re.match(r"\*\*([^*]+)\*\*\s*[:\-—]\s*(.+)", rest)
        if m:
            skill, framing = m.group(1).strip(), m.group(2).strip()
        else:
            parts = re.split(r"[:\-—]", rest, maxsplit=1)
            if len(parts) != 2:
                continue
            skill, framing = parts[0].strip(), parts[1].strip()
        if not skill or not framing or _blocked(skill):
            continue
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append({"skill": skill, "framing": framing})

    raw = (profile_json or {}).get("light_exposure")
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            skill = str(row.get("skill") or "").strip()
            framing = str(row.get("framing") or row.get("approved_framing") or "").strip()
            if not skill or not framing or _blocked(skill):
                continue
            key = skill.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append({"skill": skill, "framing": framing})

    return items


def light_exposure_prompt_block(items: List[Dict[str, str]]) -> str:
    if not items:
        return ""
    lines = [
        "LIGHT EXPOSURE (approved phrasing only — use verbatim or near-verbatim when surfacing these skills):",
    ]
    for it in items:
        lines.append(f"- {it['skill']}: {it['framing']}")
    lines.append(
        "Light exposure does NOT override Honest limits / No exposure / do NOT claim. "
        "Never upgrade light exposure to full expertise."
    )
    return "\n".join(lines)


_LIGHT_EXPOSURE_BARE_SUFFIX_RE = re.compile(
    r"\s*(?:basics|fundamentals?|\(.*\))\s*$", re.IGNORECASE
)


def light_exposure_bare_to_approved(
    items: List[Dict[str, str]]
) -> Dict[str, str]:
    """Return {bare-canonical-lower: approved-framing-label} for each light-exposure skill.

    A "bare canonical" is the approved label with its qualifier stripped: e.g.
    "Active Directory basics" -> bare "active directory" -> mapping to the full
    approved label. Used by the post-LLM skill enforcer to rewrite overclaims
    (LLM emits "Active Directory" alone -> we restore "Active Directory basics").
    """
    out: Dict[str, str] = {}
    for it in items:
        approved = str(it.get("skill") or "").strip()
        if not approved:
            continue
        bare = _LIGHT_EXPOSURE_BARE_SUFFIX_RE.sub("", approved).strip()
        if bare and bare.lower() != approved.lower():
            out[bare.lower()] = approved
    return out


def enforce_light_exposure_framing_on_skills(
    content: Dict[str, Any], profile_text: str
) -> List[str]:
    """Rewrite bare-canonical skills (e.g. 'Active Directory') to their approved
    light-exposure framing ('Active Directory basics') when the profile only
    authorizes light exposure. Deterministic post-LLM safety net so the skills
    section can't overclaim past the candidate's actual depth.
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    items = parse_light_exposure(profile_text=profile_text)
    mapping = light_exposure_bare_to_approved(items)
    if not mapping:
        return notes
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return notes
    tech = sk.get("technical")
    if not isinstance(tech, list):
        return notes
    new_tech: List[Any] = []
    for s in tech:
        key = str(s).strip().lower()
        if key in mapping:
            approved = mapping[key]
            if str(s).strip() != approved:
                notes.append(
                    f"rewrote skill to light-exposure framing: '{s}' -> '{approved}'"
                )
                new_tech.append(approved)
                continue
        new_tech.append(s)
    sk["technical"] = new_tech
    return notes


def _blob_lower(profile_text: str, content: Optional[Dict[str, Any]] = None) -> str:
    parts = [profile_text or ""]
    if isinstance(content, dict):
        try:
            parts.append(json.dumps(content, ensure_ascii=False))
        except Exception:
            pass
    return "\n".join(parts).lower()


def _matches_any(patterns: List[Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _light_exposure_covers(req: NamedRequirement, light_items: List[Dict[str, str]]) -> Optional[str]:
    req_tokens = {req.label.lower(), req.id.replace("_", " ")}
    req_tokens.update(n.lower() for n in req.surface_names)
    for it in light_items:
        sk = (it.get("skill") or "").lower()
        for tok in req_tokens:
            if tok and (tok in sk or sk in tok):
                return it.get("framing") or ""
        for pat in _NAMED_PROFILE_COMPILED.get(req.id, []):
            if pat.search(sk):
                return it.get("framing") or ""
    return None


def _blocked_by_no_exposure(req: NamedRequirement, no_exposure: Set[str]) -> bool:
    label_l = req.label.lower()
    if label_l in no_exposure:
        return True
    for phrase in no_exposure:
        for pat in _NAMED_PROFILE_COMPILED.get(req.id, []):
            if pat.search(phrase):
                return True
        if req.id.replace("_", " ") in phrase or label_l in phrase:
            return True
    return False


def detect_named_in_jd(job_description: str) -> List[NamedRequirement]:
    jd = job_description or ""
    found: List[NamedRequirement] = []
    seen: Set[str] = set()
    for nr in NAMED_REQUIREMENTS:
        if nr.id in seen:
            continue
        if _matches_any(_NAMED_JD_COMPILED.get(nr.id, []), jd):
            seen.add(nr.id)
            found.append(nr)
    return found


def assess_named_requirements(
    job_description: str,
    profile_text: str = "",
    tailored_content: Optional[Dict[str, Any]] = None,
    light_items: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    For each named requirement in the JD, classify as to_surface, gap, or blocked.
    """
    jd_named = detect_named_in_jd(job_description)
    blob = _blob_lower(profile_text, tailored_content)
    no_exposure = parse_no_exposure_phrases(profile_text)
    light_items = light_items if light_items is not None else parse_light_exposure(profile_text)

    to_surface: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []

    for nr in jd_named:
        if _blocked_by_no_exposure(nr, no_exposure):
            blocked.append({
                "id": nr.id,
                "label": nr.label,
                "reason": "listed under Honest limits / No exposure",
            })
            gaps.append({
                "requirement": nr.label,
                "category": "named_req",
                "severity": nr.severity,
                "question": (
                    f"The JD asks for {nr.label}, but your profile marks this under "
                    "No exposure / do NOT claim. Confirm you cannot claim it."
                ),
                "source": "named_req",
                "blocked": True,
            })
            continue

        light_framing = _light_exposure_covers(nr, light_items)
        full_support = _matches_any(_NAMED_PROFILE_COMPILED.get(nr.id, []), blob)

        if full_support or light_framing:
            entry: Dict[str, Any] = {
                "id": nr.id,
                "label": nr.label,
                "surface_names": list(nr.surface_names),
                "support": "light_exposure" if light_framing and not full_support else "full",
            }
            if light_framing:
                entry["approved_framing"] = light_framing
            to_surface.append(entry)
        else:
            gaps.append({
                "requirement": nr.label,
                "category": "named_req",
                "severity": nr.severity,
                "question": (
                    f"The JD emphasizes {nr.label}. Do you have truthful experience? "
                    "If yes, give one concrete example; if no, type skip."
                ),
                "source": "named_req",
            })

    return {
        "jd_named": [nr.label for nr in jd_named],
        "to_surface": to_surface,
        "gaps": gaps,
        "blocked": blocked,
    }


def named_requirement_gaps(
    job_description: str,
    profile_text: str = "",
    tailored_content: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    return assess_named_requirements(
        job_description, profile_text, tailored_content
    ).get("gaps") or []


# Per-gap penalty weights for compute_named_requirement_gap_multiplier.
# Tuned so that ~5 high-severity gaps drops a 0.92 score to ~0.67 (well below
# a cleanly-aligned 0.85+ role) while a single gap is a modest tap.
# Edit these if rescore results overshoot or undershoot in practice.
_NAMED_REQ_PENALTY_HIGH = 0.06     # 6% per high-severity unmet requirement
_NAMED_REQ_PENALTY_MEDIUM = 0.04   # 4% per medium-severity gap
_NAMED_REQ_PENALTY_LOW = 0.02      # 2% per low-severity gap
_NAMED_REQ_PENALTY_BLOCKED = 0.10  # 10% per "no exposure" blocker (hard constraint)
_NAMED_REQ_MULTIPLIER_FLOOR = 0.45  # never drop below this — preserves some signal


def compute_named_requirement_gap_multiplier(
    job_description: str,
    profile_text: str = "",
    tailored_content: Optional[Dict[str, Any]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """Compute a 0..1 multiplier that downweights fit_score by the count and
    severity of unmet JD-named requirements.

    Returns (multiplier, detail). The detail dict carries per-severity counts
    and a list of reason strings — useful for storing on summary_json so the
    UI can explain WHY a score dropped.

    A score with N high-severity gaps gets a multiplier ~= 1 - N*0.06, floored
    at 0.45. So 5 high-severity gaps -> 0.70 multiplier; 8+ gaps -> floor.
    Blocked items (no-exposure honest-limits) count as 10% each — they are
    hard constraints, not soft gaps.
    """
    assessment = assess_named_requirements(
        job_description, profile_text, tailored_content
    )
    gaps = assessment.get("gaps") or []
    high = sum(
        1 for g in gaps
        if (g.get("severity") or "high") == "high" and not g.get("blocked")
    )
    medium = sum(
        1 for g in gaps
        if (g.get("severity") or "high") == "medium" and not g.get("blocked")
    )
    low = sum(
        1 for g in gaps
        if (g.get("severity") or "high") == "low" and not g.get("blocked")
    )
    blocked = sum(1 for g in gaps if g.get("blocked"))

    penalty = (
        _NAMED_REQ_PENALTY_HIGH * high
        + _NAMED_REQ_PENALTY_MEDIUM * medium
        + _NAMED_REQ_PENALTY_LOW * low
        + _NAMED_REQ_PENALTY_BLOCKED * blocked
    )
    multiplier = max(_NAMED_REQ_MULTIPLIER_FLOOR, 1.0 - penalty)

    reasons: List[str] = []
    if high:
        reasons.append(f"{high} high-severity gap(s)")
    if medium:
        reasons.append(f"{medium} medium-severity gap(s)")
    if low:
        reasons.append(f"{low} low-severity gap(s)")
    if blocked:
        reasons.append(f"{blocked} no-exposure blocker(s)")
    if multiplier == _NAMED_REQ_MULTIPLIER_FLOOR:
        reasons.append(f"clamped to floor {_NAMED_REQ_MULTIPLIER_FLOOR}")

    return round(multiplier, 4), {
        "multiplier": round(multiplier, 4),
        "high_gap_count": high,
        "medium_gap_count": medium,
        "low_gap_count": low,
        "blocked_count": blocked,
        "total_gap_count": high + medium + low + blocked,
        "gap_labels": [g.get("requirement") for g in gaps if g.get("requirement")],
        "reasons": reasons,
        "penalty_weights": {
            "high": _NAMED_REQ_PENALTY_HIGH,
            "medium": _NAMED_REQ_PENALTY_MEDIUM,
            "low": _NAMED_REQ_PENALTY_LOW,
            "blocked": _NAMED_REQ_PENALTY_BLOCKED,
            "floor": _NAMED_REQ_MULTIPLIER_FLOOR,
        },
    }


def check_named_requirements_surfaced(
    job_description: str,
    content: Dict[str, Any],
    profile_text: str = "",
) -> List[str]:
    """Post-generation: flag supported JD requirements missing from tailored output."""
    assessment = assess_named_requirements(job_description, profile_text, content)
    if content.get("error"):
        return []
    try:
        blob = json.dumps(content, ensure_ascii=False).lower()
    except Exception:
        blob = str(content).lower()

    issues: List[str] = []
    for item in assessment.get("to_surface") or []:
        if not isinstance(item, dict):
            continue
        names = [item.get("label") or ""] + list(item.get("surface_names") or [])
        framing = (item.get("approved_framing") or "").lower()
        if framing and framing[:24] in blob:
            continue
        if any((n or "").lower() in blob for n in names if n):
            continue
        issues.append(
            f"JD named requirement not surfaced by name in output: {item.get('label')}"
        )
    return issues


def find_hype_violations(text: str) -> List[str]:
    t = (text or "").lower()
    found: List[str] = []
    for w in HYPE_BANNED_WORDS:
        if w in t:
            found.append(w)
    return found


def find_vague_verb_violations(text: str) -> List[str]:
    t = (text or "").lower()
    return [v for v in VAGUE_VERB_BANNED if re.search(rf"\b{re.escape(v)}\b", t)]


_PARTIAL_CONTEXT_SECTION_RE = re.compile(
    r"(?ms)^#{1,4}\s*(?:2\.5|2\.6|3)[\.\)\s].*?(?=^#{1,4}\s*(?:[14-9]|2\.7|2\.8|2\.9)|^#{1,4}\s+(?!2\.5|2\.6|3)|\Z)"
)


def _strip_partial_context_sections(blob: str) -> str:
    """Remove career_master Sections 2.5 (Light Exposure), 2.6 (Alt-titles),
    and 3 (Honest Limits) from profile_text. Phrases in these sections
    describe SCOPE LIMITS (partial/no exposure), not full hands-on work, so
    they MUST NOT count as strong evidence for any skill level.

    Example: Section 2.5 contains 'account creation/disable workflows' to
    describe the partial scope of Carlos's account work. Without stripping,
    the substring 'account creation' matches a STRONG pattern and the
    assessor returns 'full' when it should return 'partial'.
    """
    return _PARTIAL_CONTEXT_SECTION_RE.sub("", blob or "")


def user_account_management_level(profile_text: str) -> str:
    """
    Return claim strength for user-account wording: 'full', 'partial', or 'none'.
    'full' requires profile evidence of creation/provisioning/resets/deactivation
    OUTSIDE the partial-context sections (2.5 Light Exposure, 3 Honest Limits).
    """
    blob = (profile_text or "").lower()
    # STRONG evidence only counts when found OUTSIDE the partial-context
    # sections — Sections 2.5 (Light Exposure) and 3 (Honest Limits) describe
    # scope limits, not full claims.
    strong_eligible_blob = _strip_partial_context_sections(blob)
    strong = [re.compile(p, re.IGNORECASE) for p in _USER_ACCOUNT_STRONG_PATTERNS]
    if _matches_any(strong, strong_eligible_blob):
        return "full"
    # PARTIAL evidence: any mention anywhere in profile counts.
    partial = [re.compile(p, re.IGNORECASE) for p in _USER_ACCOUNT_PARTIAL_PATTERNS]
    if _matches_any(partial, blob):
        return "partial"
    if re.search(r"\bonboarding\b", blob) or re.search(r"user\s+account", blob):
        return "partial"
    return "none"


def account_management_wording_block(level: str) -> str:
    if level == "full":
        return (
            "USER ACCOUNT WORDING: Profile supports substantive account-management work "
            "(creation, provisioning, password resets, or deactivation). Cite only what "
            "PROFILE_TEXT explicitly documents — do not inflate beyond it."
        )
    if level == "partial":
        return (
            "USER ACCOUNT WORDING: Profile supports PARTIAL user-account work only "
            "(onboarding docs, user support, access troubleshooting). Do NOT write "
            "'manage user accounts', 'proven ability to manage user accounts', "
            "'user account management', or imply account creation/provisioning/deactivation. "
            "Use softer accurate phrasing such as 'user account support, onboarding "
            "workflows, and access-related troubleshooting'."
        )
    return (
        "USER ACCOUNT WORDING: Profile does not document user account management. "
        "Do not claim it even if the JD asks."
    )


def check_account_management_wording(
    content: Dict[str, Any],
    profile_text: str,
) -> List[str]:
    """Flag strong account-management claims when profile only supports partial/none."""
    level = user_account_management_level(profile_text)
    if level == "full" or content.get("error"):
        return []
    try:
        blob = json.dumps(content, ensure_ascii=False).lower()
    except Exception:
        blob = str(content).lower()
    issues: List[str] = []
    for phrase in STRONG_ACCOUNT_MANAGEMENT_PHRASES:
        if phrase in blob:
            issues.append(
                f"Account-management wording too strong for profile ({level}): '{phrase}'"
            )
    return issues


_STUDY_TAG_RE = re.compile(
    r"\((study|learning|lab|homelab|practice|coursework|training)\)",
    re.IGNORECASE,
)


def _skill_jd_relevance_score(skill: str, jd_lower: str, surface_names: Set[str]) -> float:
    s = (skill or "").lower()
    score = 0.0
    for tok in re.split(r"[^\w+#./]+", s):
        if len(tok) >= 3 and tok in jd_lower:
            score += 2.0
    for ns in surface_names:
        nsl = (ns or "").lower()
        if nsl and (nsl in s or s in nsl):
            score += 3.0
    m = _STUDY_TAG_RE.search(skill or "")
    if m:
        bare = _STUDY_TAG_RE.sub("", skill).strip().lower()
        if bare and bare in jd_lower:
            score += 0.5
        else:
            score -= 4.0
    if s in jd_lower:
        score += 1.5
    return score


def curate_technical_skills(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str = "",
    *,
    cap: int = _SKILLS_CAP_DEFAULT,
) -> Tuple[List[str], List[str]]:
    """
    Rank and cap technical skills to the most JD-relevant items.
    Returns (curated_list, action_notes).
    """
    cap = max(_SKILLS_CAP_MIN, min(_SKILLS_CAP_MAX, int(cap)))
    sk = content.get("skills") if isinstance(content.get("skills"), dict) else {}
    tech_raw = sk.get("technical") if isinstance(sk.get("technical"), list) else []
    notes: List[str] = []

    try:
        from job_pipeline.rendercv_export import clean_skill_items
    except ImportError:
        clean_skill_items = lambda x: [str(i).strip() for i in (x or []) if str(i).strip()]  # type: ignore

    tech = clean_skill_items(tech_raw)
    if len(tech) <= cap:
        return tech, notes

    jd_lower = (job_description or "").lower()
    assessment = assess_named_requirements(job_description, profile_text, content)
    surface_names: Set[str] = set()
    for item in assessment.get("to_surface") or []:
        if isinstance(item, dict):
            surface_names.add(str(item.get("label") or ""))
            for n in item.get("surface_names") or []:
                surface_names.add(str(n))

    kept: List[Tuple[str, float]] = []
    for skill in tech:
        if _STUDY_TAG_RE.search(skill):
            bare = _STUDY_TAG_RE.sub("", skill).strip().lower()
            if bare and bare not in jd_lower:
                notes.append(f"dropped study-only skill: {skill}")
                continue
        kept.append((skill, _skill_jd_relevance_score(skill, jd_lower, surface_names)))

    kept.sort(key=lambda pair: (-pair[1], pair[0].lower()))
    curated = [s for s, _ in kept[:cap]]
    if len(tech) > len(curated):
        notes.append(f"curated technical skills {len(tech)} -> {len(curated)}")
    return curated, notes


def extract_jd_experience_bands(job_description: str) -> List[str]:
    """JD-stated experience requirements — must not be echoed as candidate facts."""
    jd = job_description or ""
    bands: List[str] = []
    seen: Set[str] = set()
    for pat in (_JD_YEARS_RANGE_RE, _JD_YEARS_PLUS_RE):
        for m in pat.finditer(jd):
            phrase = re.sub(r"\s+", " ", m.group(0).strip().lower())
            if phrase and phrase not in seen:
                seen.add(phrase)
                bands.append(phrase)
    return bands


def _jd_required_year_ranges(job_description: str) -> List[Tuple[int, int]]:
    """Numeric year ranges stated as requirements in the JD."""
    jd = job_description or ""
    ranges: List[Tuple[int, int]] = []
    seen: Set[Tuple[int, int]] = set()
    for m in _JD_YEARS_RANGE_RE.finditer(jd):
        pair = (int(m.group(1)), int(m.group(2)))
        if pair not in seen:
            seen.add(pair)
            ranges.append(pair)
    return ranges


def _jd_required_year_plus(job_description: str) -> List[int]:
    jd = job_description or ""
    mins: List[int] = []
    seen: Set[int] = set()
    for m in _JD_YEARS_PLUS_RE.finditer(jd):
        val = int(m.group(1))
        if val not in seen:
            seen.add(val)
            mins.append(val)
    return mins


def _profile_documents_year_range(profile_text: str, low: int, high: int) -> bool:
    blob = profile_text or ""
    return bool(re.search(rf"\b{low}\s*-\s*{high}\s*years\b", blob, re.IGNORECASE))


def _profile_documents_year_plus(profile_text: str, minimum: int) -> bool:
    blob = profile_text or ""
    return bool(re.search(rf"\b{minimum}\+\s*years\b", blob, re.IGNORECASE))


def extract_profile_years_phrases(profile_text: str) -> List[str]:
    """Candidate-documented tenure phrases from profile text."""
    blob = (profile_text or "").lower()
    phrases: List[str] = []
    seen: Set[str] = set()
    for m in _PROFILE_YEARS_RE.finditer(blob):
        phrase = re.sub(r"\s+", " ", m.group(0).strip().lower())
        if phrase and phrase not in seen:
            seen.add(phrase)
            phrases.append(phrase)
    return phrases


def years_experience_prompt_block(job_description: str, profile_text: str) -> str:
    jd_bands = extract_jd_experience_bands(job_description)
    profile_phrases = extract_profile_years_phrases(profile_text)
    lines = [
        "YEARS OF EXPERIENCE (strict):",
        "- State ONLY tenure documented in PROFILE_TEXT "
        '(e.g. "3+ years", "over three years").',
        "- NEVER echo the JD's required experience band as if it were your own fact "
        '(e.g. if JD says "3-5 years required", do NOT write "I have 3-5 years").',
    ]
    if profile_phrases:
        lines.append(f"- Profile-backed phrases you MAY use: {', '.join(profile_phrases[:5])}")
    if jd_bands:
        lines.append(f"- JD bands to NOT echo verbatim: {', '.join(jd_bands[:5])}")
    return "\n".join(lines)


def find_jd_years_echo_violations(
    text: str,
    job_description: str,
    profile_text: str = "",
) -> List[str]:
    """Detect JD requirement bands echoed in output without profile backing."""
    jd_ranges = _jd_required_year_ranges(job_description)
    jd_plus = _jd_required_year_plus(job_description)
    if not jd_ranges and not jd_plus:
        return []

    profile_blob = (profile_text or "").lower()
    out_blob = text or ""
    violations: List[str] = []
    seen: Set[str] = set()

    # Exact JD band phrases still echoed verbatim (legacy path).
    for band in extract_jd_experience_bands(job_description):
        if band not in out_blob.lower():
            continue
        if band in profile_blob:
            continue
        rm = re.match(r"^(\d{1,2})\s*-\s*(\d{1,2})\s*years", band.strip())
        if rm and _profile_documents_year_range(profile_text, int(rm.group(1)), int(rm.group(2))):
            continue
        pm = re.match(r"^(\d{1,2})\+\s*years", band.strip())
        if pm and _profile_documents_year_plus(profile_text, int(pm.group(1))):
            continue
        if band not in seen:
            seen.add(band)
            violations.append(band)

    # Broader: any JD-required numeric range echoed in output (e.g. "3-5 years in ...").
    for m in _OUTPUT_YEARS_RANGE_RE.finditer(out_blob):
        low, high = int(m.group(1)), int(m.group(2))
        phrase = re.sub(r"\s+", " ", m.group(0).strip().lower())
        if (low, high) not in jd_ranges:
            continue
        if _profile_documents_year_range(profile_text, low, high):
            continue
        if phrase not in seen:
            seen.add(phrase)
            violations.append(phrase)

    for m in _OUTPUT_YEARS_PLUS_RE.finditer(out_blob):
        minimum = int(m.group(1))
        phrase = re.sub(r"\s+", " ", m.group(0).strip().lower())
        if minimum not in jd_plus:
            continue
        if _profile_documents_year_plus(profile_text, minimum):
            continue
        if phrase not in seen:
            seen.add(phrase)
            violations.append(phrase)

    return violations


def find_project_jargon_violations(text: str) -> List[str]:
    t = (text or "").lower()
    return [p for p in PROJECT_JARGON_PHRASES if p in t]


def project_framing_prompt_block() -> str:
    return (
        "PERSONAL PROJECT FRAMING (support/helpdesk audiences):\n"
        "- Frame personal projects as transferable support habits, not engineering insider talk.\n"
        f"- Prefer phrasing like: \"{AI_PIPELINE_SUPPORT_FRAMING}\"\n"
        "- Avoid jargon such as: architectural pivot, paradigm shift, scalable architecture, "
        "microservices, cloud-native refactor.\n"
    )


def _is_support_target_role(job_title: str) -> bool:
    t = (job_title or "").lower()
    return any(h in t for h in SUPPORT_ROLE_TITLE_HINTS)


def support_summary_framing_prompt_block(job_title: str) -> str:
    """
    For help-desk / service-desk / IT-support targets, instruct the LLM to keep the
    summary anchored on operational support — NOT on Python, scripting, or personal
    automation projects. Empty string for non-support roles, so callers can include
    it unconditionally.
    """
    if not _is_support_target_role(job_title):
        return ""
    return (
        "SUPPORT-ROLE SUMMARY FRAMING (this target is a help-desk / service-desk / IT-support role):\n"
        "- The 'summary' field MUST open with operational support framing — ticketing, end-user "
        "support, hardware/Windows troubleshooting, documentation, escalation, communication.\n"
        "- Do NOT open the summary with Python, scripting, automation, or 'self-directed coding "
        "practice'. Do NOT lead with personal-project framing. Those topics may appear inside the "
        "projects[] array only, never as the summary's opening framing.\n"
        "- Do NOT use phrasing like 'Supported by N years of self-directed Python practice', "
        "'years of personal coding', or similar. The recruiter for this role is a service-desk "
        "manager, not an engineering hiring manager.\n"
    )


_SUPPORT_ROLE_PROJECT_HARD_BLOCKS: Tuple[str, ...] = (
    "the organizer",
    "art pipeline",
    "organizer app",
)


def _project_relevance_score(
    project: Dict[str, Any],
    jd_lower: str,
    job_title: str,
    surface_names: Set[str],
) -> float:
    name = str(project.get("name") or "")
    desc = str(project.get("description") or "")
    impact = str(project.get("impact") or "")
    blob = f"{name} {desc} {impact}".lower()
    # Hard block: for support roles, certain projects are off-brand regardless
    # of JD-token overlap. A soft -6.0 penalty got overcome by JD-token matches
    # (e.g. "workflow", "support") on actual support-role JDs, so we use a
    # sentinel score that no positive sum can overcome.
    if _is_support_target_role(job_title):
        if any(x in blob for x in _SUPPORT_ROLE_PROJECT_HARD_BLOCKS):
            return -1000.0
    score = 0.0
    for tok in re.split(r"[^\w+#./-]+", blob):
        if len(tok) >= 3 and tok in jd_lower:
            score += 2.0
    for ns in surface_names:
        nsl = (ns or "").lower()
        if nsl and nsl in blob:
            score += 2.5
    if "job-application" in blob or "job application" in blob or "pipeline" in blob:
        score += 1.0
    if _is_support_target_role(job_title):
        if any(x in blob for x in ("support", "help desk", "ticket", "automation", "workflow")):
            score += 1.5
    return score


def curate_projects(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str = "",
    *,
    job_title: str = "",
    cap: int = _PROJECTS_CAP_DEFAULT,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Keep the 1-2 projects most relevant to the target role.

    Scoring runs even when the LLM already returned <= cap projects, so role-specific
    penalties (e.g. drop "The Organizer" for support roles) fire regardless of count.
    """
    cap = max(_PROJECTS_CAP_MIN, min(_PROJECTS_CAP_DEFAULT, int(cap)))
    projs = content.get("projects")
    if not isinstance(projs, list):
        return [], []
    valid = [p for p in projs if isinstance(p, dict) and (p.get("name") or "").strip()]
    notes: List[str] = []
    if not valid:
        return valid, notes

    jd_lower = (job_description or "").lower()
    assessment = assess_named_requirements(job_description, profile_text, content)
    surface_names: Set[str] = set()
    for item in assessment.get("to_surface") or []:
        if isinstance(item, dict):
            surface_names.add(str(item.get("label") or ""))
            for n in item.get("surface_names") or []:
                surface_names.add(str(n))

    scored = [
        (p, _project_relevance_score(p, jd_lower, job_title, surface_names)) for p in valid
    ]
    scored.sort(key=lambda pair: (-pair[1], str(pair[0].get("name") or "").lower()))
    curated = [p for p, sc in scored[:cap] if sc > -3.0]
    if len(valid) > len(curated):
        dropped = [str(p.get("name") or "") for p, _ in scored[len(curated) :]]
        notes.append(f"curated projects {len(valid)} -> {len(curated)} (dropped: {', '.join(dropped[:3])})")
    return curated, notes


def _best_skill_label_for_surface(name: str, approved_framing: str = "") -> str:
    if approved_framing:
        # Use short skill token from label when light exposure has long framing
        return (name or "").strip() or approved_framing[:60]
    return (name or "").strip()


def _resume_content_blob(content: Dict[str, Any]) -> str:
    """Searchable text from summary, experience bullets, and projects."""
    parts: List[str] = [str(content.get("summary") or "")]
    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            parts.append(str(exp.get("company") or ""))
            for b in exp.get("bullets") or []:
                parts.append(str(b))
    projs = content.get("projects")
    if isinstance(projs, list):
        for proj in projs:
            if not isinstance(proj, dict):
                continue
            parts.append(str(proj.get("name") or ""))
            parts.append(str(proj.get("description") or ""))
            parts.append(str(proj.get("impact") or ""))
    return " ".join(p for p in parts if p).lower()


def ensure_surfaced_keywords_in_skills(
    content: Dict[str, Any],
    job_description: str,
    profile_text: str = "",
) -> List[str]:
    """
    R3.4: If a supported requirement appears anywhere in the resume body, ensure it
    is also in skills.technical (not summary-only).
    """
    if content.get("error"):
        return []
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return []
    resume_blob = _resume_content_blob(content)
    if not resume_blob.strip():
        return []

    try:
        from job_pipeline.rendercv_export import clean_skill_items
    except ImportError:
        clean_skill_items = lambda x: [str(i).strip() for i in (x or []) if str(i).strip()]  # type: ignore

    tech = clean_skill_items(sk.get("technical") if isinstance(sk.get("technical"), list) else [])
    tech_lower = {t.lower() for t in tech}
    notes: List[str] = []

    assessment = assess_named_requirements(job_description, profile_text, content)
    for item in assessment.get("to_surface") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        names = [label] + [str(n).strip() for n in (item.get("surface_names") or []) if str(n).strip()]
        in_resume = any(n.lower() in resume_blob for n in names if n)
        if not in_resume:
            continue
        added = False
        for n in names:
            if not n:
                continue
            if n.lower() in resume_blob and n.lower() not in tech_lower:
                tech.append(_best_skill_label_for_surface(n, str(item.get("approved_framing") or "")))
                tech_lower.add(n.lower())
                notes.append(f"added surfaced keyword to skills: {n}")
                added = True
                break
        if not added and label and label.lower() in resume_blob and label.lower() not in tech_lower:
            tech.append(label)
            tech_lower.add(label.lower())
            notes.append(f"added surfaced keyword to skills: {label}")

    sk["technical"] = clean_skill_items(tech)
    return notes


def fix_jd_years_echo_in_text(text: str, job_description: str, profile_text: str) -> Tuple[str, bool]:
    """Replace JD experience-band echo with profile-backed phrasing when possible."""
    violations = find_jd_years_echo_violations(text, job_description, profile_text)
    if not violations:
        return text, False
    profile_phrases = extract_profile_years_phrases(profile_text)
    replacement = profile_phrases[0] if profile_phrases else "documented experience from my profile"
    out = text
    changed = False
    for band in violations:
        out_new = re.sub(re.escape(band), replacement, out, flags=re.IGNORECASE)
        if out_new != out:
            out = out_new
            changed = True
            continue
        # Broader fallback for range echoes like "3-5 years in help desk roles".
        m = re.match(r"^(\d{1,2})\s*-\s*(\d{1,2})\s*years$", band.strip(), re.IGNORECASE)
        if m:
            low, high = m.group(1), m.group(2)
            pattern = rf"\b{low}\s*-\s*{high}\s*years\b"
            out_new = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
            if out_new != out:
                out = out_new
                changed = True
    return out, changed


def build_tailoring_requirement_strategy(
    job_description: str,
    profile_text: str,
) -> Dict[str, Any]:
    """Compact strategy block injected into the resume tailor prompt."""
    light_items = parse_light_exposure(profile_text)
    assessment = assess_named_requirements(job_description, profile_text, light_items=light_items)
    acct_level = user_account_management_level(profile_text)
    return {
        "named_jd_requirements": assessment.get("jd_named") or [],
        "requirements_to_surface_by_name": assessment.get("to_surface") or [],
        "requirement_gaps_do_not_fabricate": [
            g.get("requirement") for g in (assessment.get("gaps") or []) if g.get("requirement")
        ],
        "light_exposure_count": len(light_items),
        "user_account_management_level": acct_level,
        "candidate_experience_phrases": extract_profile_years_phrases(profile_text),
        "jd_experience_bands_do_not_echo": extract_jd_experience_bands(job_description),
    }


def anti_hype_prompt_block() -> str:
    banned = ", ".join(f'"{w}"' for w in HYPE_BANNED_WORDS[:12])
    return (
        "CREDIBILITY / ANTI-HYPE (strict):\n"
        f"- Do NOT use hype words/phrases including: {banned}, and similar superlatives.\n"
        f"- For the AI job-application pipeline project, use factual phrasing like: "
        f"\"{AI_PIPELINE_FACTUAL_FRAMING}\"\n"
        "- Quantify ONLY with numbers explicitly present in PROFILE_TEXT. Never invent metrics.\n"
        "- Help-desk and IT-support roles: plain, operational language — not startup/marketing tone.\n"
        "- Avoid engineering insider jargon (architectural pivot, paradigm shift, scalable architecture) "
        "when the target role is support/helpdesk.\n"
    )


def named_requirement_method_block() -> str:
    return (
        "NAMED REQUIREMENT METHOD (do internally before writing JSON):\n"
        "1. Read TAILORING_STRATEGY.named_jd_requirements and requirements_to_surface_by_name.\n"
        "2. For each supported requirement, surface it BY NAME in summary, a bullet, AND skills.technical.\n"
        "   Use approved_framing verbatim when support is light_exposure.\n"
        "   Any keyword surfaced in summary MUST also appear in skills.technical for ATS consistency.\n"
        "3. For requirement_gaps_do_not_fabricate items, omit entirely — do not hedge or pattern-match.\n"
        "4. NEVER write meta-audit language in the exported resume (e.g. 'X is not claimed', "
        "'Y is supported by Z', 'PST coverage is not claimed'). Gap tracking is internal only; "
        "omit unsupported requirements silently.\n"
        "5. Prioritize: user account management, Active Directory, Microsoft 365, MacOS, MFA/SSO, "
        "ticketing/ITSM, VPN, video conferencing when JD mentions them and profile supports them.\n"
    )
