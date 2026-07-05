"""Phase 0 integrity guards — always-on, deterministic, no LLM required.

These run on every export regardless of optimization mode. They catch the
credibility-critical bugs observed in real builds:

- 0.1 Cross-job duplicate-metric / misplaced-bullet detection.
- 0.2 Garbage / malformed-line detection (AI comma-salad summaries).
- 0.3 Semantic skills dedupe (concept variants like
       "Ticketing / ITSM" / "ticketing" / "ITSM" collapse to one).
- 0.4 Intra-job near-duplicate bullet collapse.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from job_pipeline.evidence_db import load_evidence_db, match_employer_key


# ---------------------------------------------------------------------------
# 0.3 Skills semantic dedupe
# ---------------------------------------------------------------------------

# Canonical skill name lookup. Keys are stored as "normalized" forms (see
# `_normalize_key_variants`) so case/whitespace/slash/pluralization variants all
# hit the same entry.
SKILL_CANONICAL_MAP: Dict[str, str] = {
    # Ticketing / ITSM
    "ticketing": "Ticketing/ITSM",
    "ticketing/itsm": "Ticketing/ITSM",
    "itsm": "Ticketing/ITSM",
    "help desk ticketing": "Ticketing/ITSM",
    "ticketing & itsm": "Ticketing/ITSM",
    "service desk ticketing": "Ticketing/ITSM",
    "freshdesk": "Freshdesk",
    "zendesk": "Zendesk",
    "jira service management": "Jira Service Management",
    # Active Directory
    "active directory": "Active Directory",
    "ad": "Active Directory",
    "azure ad": "Azure AD / Entra ID",
    "entra": "Azure AD / Entra ID",
    "entra id": "Azure AD / Entra ID",
    "microsoft entra": "Azure AD / Entra ID",
    # Microsoft 365 / Office — all variants collapse to Microsoft 365
    "office 365": "Microsoft 365",
    "m365": "Microsoft 365",
    "microsoft 365": "Microsoft 365",
    "microsoft 365 suite": "Microsoft 365",
    "o365": "Microsoft 365",
    "microsoft office": "Microsoft 365",
    "ms office": "Microsoft 365",
    "office suite": "Microsoft 365",
    "office": "Microsoft 365",
    "microsoft office suite": "Microsoft 365",
    # MFA / SSO
    "mfa": "MFA/SSO",
    "sso": "MFA/SSO",
    "mfa/sso": "MFA/SSO",
    "multi-factor authentication": "MFA/SSO",
    "multifactor authentication": "MFA/SSO",
    "single sign-on": "MFA/SSO",
    "single sign on": "MFA/SSO",
    "two-factor authentication": "MFA/SSO",
    "2fa": "MFA/SSO",
    # Onboarding — bare "onboarding" collapses with "user onboarding"
    "onboarding": "User onboarding",
    "user onboarding": "User onboarding",
    "onboarding workflow": "User onboarding",
    "onboarding workflows": "User onboarding",
    "user onboarding workflow": "User onboarding",
    "user onboarding workflows": "User onboarding",
    "onboarding documentation": "User onboarding documentation",
    "user onboarding documentation": "User onboarding documentation",
    # Account workflows — the "creation/disable" cluster
    "user account support": "User account support",
    "user account management": "User account support",
    "user-account support": "User account support",
    "account support": "User account support",
    "account management": "User account support",
    "account creation": "Account creation/disable workflows",
    "account disable": "Account creation/disable workflows",
    "account creation/disable": "Account creation/disable workflows",
    "account creation/disable workflow": "Account creation/disable workflows",
    "account creation/disable workflows": "Account creation/disable workflows",
    "access-related troubleshooting": "Access-related troubleshooting",
    "access related troubleshooting": "Access-related troubleshooting",
    # A/V — recruiter-standard form uses the slash; bare "AV" reads like a typo.
    "av": "A/V",
    "a/v": "A/V",
    "a v": "A/V",
    "audio/video": "A/V",
    "audio video": "A/V",
    "av conference equipment": "A/V Conference Equipment Support",
    "a/v conference equipment": "A/V Conference Equipment Support",
    "av conference equipment support": "A/V Conference Equipment Support",
    "a/v conference equipment support": "A/V Conference Equipment Support",
    "av equipment support": "A/V Equipment Support",
    "a/v equipment support": "A/V Equipment Support",
    "av support": "A/V Support",
    "a/v support": "A/V Support",
    # Common skill name variants the LLM cycles through.
    "end user support": "End-User Support",
    "end-user support": "End-User Support",
    "enduser support": "End-User Support",
    "endpoint support": "End-User Support",
    "customer service": "Customer Service",
    "customer support": "Customer Service",
    "verbal communication": "Verbal Communication",
    "written communication": "Written Communication",
    "problem solving": "Problem-Solving",
    "problem-solving": "Problem-Solving",
    "troubleshooting": "Technical troubleshooting",
    "technical troubleshooting": "Technical troubleshooting",
    "incident response": "Incident response",
    "incident management": "Incident response",
    "root cause analysis": "Root-cause analysis",
    "root-cause analysis": "Root-cause analysis",
    "rca": "Root-cause analysis",
    "ticket triage": "Ticketing/ITSM",
    "ticketing system": "Ticketing/ITSM",
    "service desk": "Ticketing/ITSM",
    "service-desk": "Ticketing/ITSM",
    "tier 1 support": "Help Desk Support",
    "tier-1 support": "Help Desk Support",
    "tier 2 support": "Help Desk Support",
    "tier-2 support": "Help Desk Support",
    "first-level support": "Help Desk Support",
    "front-line support": "Help Desk Support",
    "phone support": "Help Desk Support",
    "email support": "Help Desk Support",
    "chat support": "Help Desk Support",
    "pc hardware": "PC Hardware Troubleshooting",
    "hardware swaps": "PC Hardware Troubleshooting",
    "hardware triage": "PC Hardware Troubleshooting",
    # Microsoft 365 component tools — keep these SEPARATE from "Microsoft 365"
    # itself. Recruiters want to see them as distinct keywords for ATS matching.
    # Just normalize spelling.
    "ms outlook": "Outlook",
    "outlook": "Outlook",
    "ms teams": "Teams",
    "microsoft teams": "Teams",
    "ms word": "Word",
    "ms excel": "Excel",
    "ms powerpoint": "PowerPoint",
    # OS support
    "windows troubleshooting": "Windows OS Troubleshooting",
    "windows os troubleshooting": "Windows OS Troubleshooting",
    "windows endpoint support": "Windows OS Troubleshooting",
    "macos": "MacOS support",
    "mac os": "MacOS support",
    "mac os support": "MacOS support",
    # Networking
    "tcp/ip": "TCP/IP",
    "dns troubleshooting": "DNS troubleshooting",
    "ethernet troubleshooting": "Ethernet troubleshooting",
    "remote administration": "Remote Administration",
    "remote support": "Remote Administration",
    "remote systems administration": "Remote Administration",
    "remote system administration": "Remote Administration",
    "rustdesk": "Remote administration (RustDesk)",
    # SOPs / Runbooks / KB docs all collapse to one chip — recruiter-equivalent.
    "sop": "SOPs/Runbooks",
    "sops": "SOPs/Runbooks",
    "runbook": "SOPs/Runbooks",
    "runbooks": "SOPs/Runbooks",
    "sop authoring": "SOPs/Runbooks",
    "runbook authoring": "SOPs/Runbooks",
    "sop and runbook authoring": "SOPs/Runbooks",
    "sops and runbooks": "SOPs/Runbooks",
    "sop/runbook authoring": "SOPs/Runbooks",
    "sops/runbooks": "SOPs/Runbooks",
    "sop writing": "SOPs/Runbooks",
    "runbook creation": "SOPs/Runbooks",
    "sop creation": "SOPs/Runbooks",
    "standard operating procedure": "SOPs/Runbooks",
    "standard operating procedures": "SOPs/Runbooks",
    "knowledgebase documentation": "SOPs/Runbooks",
    "knowledge base documentation": "SOPs/Runbooks",
    "knowledge-base documentation": "SOPs/Runbooks",
    "knowledge base article": "SOPs/Runbooks",
    "knowledge-base article": "SOPs/Runbooks",
    "kb article": "SOPs/Runbooks",
    "kb articles": "SOPs/Runbooks",
    # Documentation (generic, not the SOP/KB cluster)
    "technical documentation": "Technical documentation",
    # Process authoring siblings (audit: each "X / X authoring" pair collapses)
    "policy authoring": "Policy/Procedure authoring",
    "procedure authoring": "Policy/Procedure authoring",
    "policy and procedure authoring": "Policy/Procedure authoring",
    "policies and procedures": "Policy/Procedure authoring",
    "process documentation": "Process documentation",
    "process authoring": "Process documentation",
    # Hardware
    "hardware troubleshooting": "PC Hardware Troubleshooting",
    "pc hardware troubleshooting": "PC Hardware Troubleshooting",
    "hardware repair": "PC Hardware Troubleshooting",
    # Tiered support
    "tier 1": "Tier 1-2 support",
    "tier 1-2 support": "Tier 1-2 support",
    "tier 1/2 support": "Tier 1-2 support",
    "tier 1 - 2 support": "Tier 1-2 support",
    "help desk support": "Help Desk Support",
    "service desk support": "Help Desk Support",
    "help desk": "Help Desk Support",
    "desktop support": "Desktop Support",
    "software support": "Software Support",
    "application support": "Application Support",
    "account and access support": "Account & Access Support",
    "user account and access support": "Account & Access Support",
    "cross-functional coordination": "Cross-Functional Coordination",
    "documentation": "Documentation",
    "teamwork": "Teamwork",
    "collaboration": "Collaboration",
    "analytical thinking": "Analytical Thinking",
    "attention to detail": "Attention to Detail",
    "task prioritization": "Task Prioritization",
    "time management": "Time Management",
}


# Items that are NOT skills and must be removed from technical/soft skills
# entirely (not merged). These are availability / logistics / role-context terms
# that belong on the summary or availability line, never in a skills section.
# Compared against the same normalized key variants as SKILL_CANONICAL_MAP.
NON_SKILL_BLOCKLIST: Set[str] = {
    "pst",
    "est",
    "cst",
    "mst",
    "utc",
    "pst/time-zone coverage",
    "pst time-zone coverage",
    "pst timezone coverage",
    "time-zone coverage",
    "timezone coverage",
    "time zone coverage",
    "remote work expectation",
    "remote work expectations",
    "on-call availability",
    "on call availability",
    "weekend availability",
    "evening availability",
    "shift availability",
    "us work authorization",
    "work authorization",
    "willing to relocate",
    "ability to commute",
}


def _singularize_token(tok: str) -> str:
    """Strip simple trailing 's' from a single alphabetic token (>3 chars, not -ss)."""
    if len(tok) > 3 and tok.isalpha() and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _normalize_key_variants(s: str) -> List[str]:
    """Return progressively-normalized lookup keys for the canonical map / blocklist.

    Tolerates case, whitespace around slashes, parenthetical qualifiers, and
    simple plural endings — so "SOP and runbook authoring", "runbooks", "SOPs",
    "PST / time-zone coverage", and "pst/time-zone coverage" all hit the same
    entry.
    """
    s0 = (s or "").strip().lower()
    if not s0:
        return []
    variants: List[str] = []

    v1 = re.sub(r"\s+", " ", s0)
    variants.append(v1)

    v2 = re.sub(r"\s*/\s*", "/", v1)
    if v2 != v1:
        variants.append(v2)

    v3 = re.sub(r"\s*\([^)]*\)\s*", "", v2).strip()
    if v3 and v3 not in variants:
        variants.append(v3)

    # word-level singularization on each existing variant
    for v in list(variants):
        tokens = re.split(r"(\W+)", v)
        sing = "".join(_singularize_token(t) for t in tokens)
        if sing and sing not in variants:
            variants.append(sing)

    # collapsed slashes on singularized forms too
    for v in list(variants):
        collapsed = re.sub(r"\s*/\s*", "/", v)
        if collapsed and collapsed not in variants:
            variants.append(collapsed)

    return variants


def _norm_skill_key(s: str) -> str:
    """Canonical lookup key for the dedupe `seen` set (single normalized form)."""
    key = re.sub(r"\s+", " ", (s or "").strip().lower())
    key = re.sub(r"\s*/\s*", "/", key)
    return key


def is_non_skill_item(s: str) -> bool:
    """True if `s` matches any blocklisted availability/logistics term."""
    for v in _normalize_key_variants(s):
        if v in NON_SKILL_BLOCKLIST:
            return True
    return False


def canonicalize_skill(skill: str) -> Optional[str]:
    """Resolve a skill string.

    Returns:
        - the canonical label string when a synonym match is found,
        - the trimmed original when no match is found but the item IS a skill,
        - None when the item is blocklisted (availability/logistics) and must
          be removed entirely.
    """
    if not skill:
        return None
    s = str(skill).strip()
    if not s:
        return None
    variants = _normalize_key_variants(s)
    for v in variants:
        if v in NON_SKILL_BLOCKLIST:
            return None
    for v in variants:
        if v in SKILL_CANONICAL_MAP:
            return SKILL_CANONICAL_MAP[v]
    return s


_SKILL_TOKEN_STOPWORDS: Set[str] = {
    "and", "or", "of", "the", "a", "an", "to", "in", "for", "with",
    "&", "/", "-",
}


def _skill_token_set(label: str) -> Set[str]:
    """Tokens of a skill label, lowercased, stopwords removed.

    Used by the fuzzy subset dedupe: if skill A's tokens are a subset of skill
    B's tokens, they refer to the same concept and the longer form ("Microsoft
    365 suite") collapses to the shorter ("Microsoft 365"). This is what
    finally kills the "Microsoft 365" + "Microsoft 365 suite" leak without
    needing every variant pre-listed in SKILL_CANONICAL_MAP.
    """
    raw = str(label or "").lower()
    # Split on whitespace, slashes, and punctuation. Keep alphanumerics + hyphens.
    parts = re.split(r"[^\w-]+", raw)
    return {p for p in parts if p and p not in _SKILL_TOKEN_STOPWORDS and len(p) > 1}


def dedupe_skills_semantic(items: List[Any]) -> Tuple[List[str], List[str]]:
    """Apply blocklist + canonicalization + case-insensitive dedupe + fuzzy subset dedupe.

    Returns (deduped_items, notes). Blocklisted non-skill terms (e.g. "PST",
    "PST / time-zone coverage") are removed entirely, not merged.

    Fuzzy subset pass: after canonical dedupe, if one item's tokens are a
    subset of another's, the longer/more-padded form is dropped in favor of
    the shorter canonical form (recruiter-conventional). Example:
      "Microsoft 365" + "Microsoft 365 suite" -> "Microsoft 365"
      "Remote administration" + "Remote systems administration" -> "Remote administration"
      "User onboarding" + "User onboarding documentation" -> kept separate
        ONLY because "documentation" is a distinguishing tail token.
    """
    seen: Set[str] = set()
    out: List[str] = []
    notes: List[str] = []
    for raw in items if isinstance(items, list) else []:
        s = str(raw or "").strip().strip(",").strip()
        if not s:
            continue
        canon = canonicalize_skill(s)
        if canon is None:
            notes.append(f"removed non-skill item from skills: {s}")
            continue
        key = _norm_skill_key(canon)
        if not key:
            continue
        if key in seen:
            if canon.lower() != s.lower():
                notes.append(f"merged skill variant: {s} -> {canon}")
            else:
                notes.append(f"dropped duplicate skill: {s}")
            continue
        seen.add(key)
        if canon != s and _norm_skill_key(canon) != _norm_skill_key(s):
            notes.append(f"canonicalized skill: {s} -> {canon}")
        out.append(canon)

    # Fuzzy subset pass — handles the "M365" + "M365 suite" shape that the
    # canonical map missed. Build token sets for each remaining skill; if A's
    # tokens are a proper subset of B's, drop B (the padded form). Two-pass to
    # be order-independent.
    token_sets = [_skill_token_set(item) for item in out]
    drop_indexes: Set[int] = set()
    for i, ti in enumerate(token_sets):
        if i in drop_indexes or not ti:
            continue
        for j, tj in enumerate(token_sets):
            if i == j or j in drop_indexes or not tj:
                continue
            # tj is a strict superset of ti and ti has at least one token —
            # the longer form ("Microsoft 365 suite") refers to the same concept
            # as the shorter ("Microsoft 365"). Drop the longer.
            if ti < tj:
                drop_indexes.add(j)
                notes.append(
                    f"fuzzy-dropped skill (token subset of '{out[i]}'): {out[j]}"
                )
    if drop_indexes:
        out = [item for idx, item in enumerate(out) if idx not in drop_indexes]
    return out, notes


# ---------------------------------------------------------------------------
# 0.1 Cross-job metric leak detection
# ---------------------------------------------------------------------------

_METRIC_STOP = {
    "a", "an", "the", "of", "to", "and", "or", "in", "for", "by", "with", "from",
    "on", "at", "as", "into", "per", "while", "approximately", "about", "around",
    "over", "than", "more",
}


def _metric_signature(text: str) -> Set[str]:
    t = re.sub(r"[^\w\s%./-]+", " ", str(text or "").lower())
    tokens = [tok for tok in t.split() if tok not in _METRIC_STOP and len(tok) > 1]
    # include numbers verbatim — they're the strongest signal
    return set(tokens)


def _signature_overlap(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _employer_metric_sigs() -> Dict[str, List[Tuple[str, Set[str]]]]:
    """Map employer_key -> list of (raw_metric, signature)."""
    db = load_evidence_db()
    employers = db.get("employers") if isinstance(db.get("employers"), dict) else {}
    out: Dict[str, List[Tuple[str, Set[str]]]] = {}
    for key, rec in employers.items():
        if not isinstance(rec, dict):
            continue
        metrics = rec.get("metrics") or []
        out[key] = [(str(m), _metric_signature(m)) for m in metrics if str(m).strip()]
    return out


def strip_cross_job_metric_leaks(content: Dict[str, Any]) -> List[str]:
    """
    Remove bullets that reproduce another employer's signature metric.

    A metric belongs to its owning employer in evidence.json; if a bullet under
    a DIFFERENT employer matches an owning employer's metric signature, drop it.
    """
    notes: List[str] = []
    exps = content.get("experience")
    if not isinstance(exps, list):
        return notes

    owner_metrics = _employer_metric_sigs()
    for exp in exps:
        if not isinstance(exp, dict):
            continue
        company = str(exp.get("company") or "")
        own_key = match_employer_key(company)
        own_sigs = [sig for _, sig in (owner_metrics.get(own_key) or [])] if own_key else []
        bullets = exp.get("bullets")
        if not isinstance(bullets, list):
            continue
        kept: List[str] = []
        for b in bullets:
            sig = _metric_signature(b)
            leak_owner: Optional[str] = None
            for owner_key, items in owner_metrics.items():
                if owner_key == own_key:
                    continue
                for raw, owner_sig in items:
                    if not owner_sig:
                        continue
                    if _signature_overlap(sig, owner_sig) < 0.6:
                        continue
                    # If the bullet also matches own employer's metric, keep it.
                    if any(_signature_overlap(sig, os_) >= 0.6 for os_ in own_sigs):
                        break
                    leak_owner = owner_key
                    break
                if leak_owner:
                    break
            if leak_owner:
                notes.append(
                    f"stripped misplaced metric from {company or '(unknown)'} "
                    f"(belongs to {leak_owner}): {str(b)[:80]}"
                )
                continue
            kept.append(b)
        exp["bullets"] = kept
    return notes


# ---------------------------------------------------------------------------
# 0.2 Malformed / AI comma-salad detection
# ---------------------------------------------------------------------------

# Our own broken thesis pattern from earlier builds.
_THESIS_GHOST_RE = re.compile(
    r"(?:\s*[—\-]\s*)?aligned with\s+[A-Za-z0-9 ,]+?\s+while delivering\s+supported experience in\s+[^.]+\.?",
    re.IGNORECASE,
)
_THESIS_GHOST_SHORT_RE = re.compile(
    r"\bwith hands-on\s+[A-Za-z0-9 ,/]+\s+experience,\s+strong\s+[A-Za-z0-9 ,]+,\s+and a track record",
    re.IGNORECASE,
)

# Generic "3+ lowercase abstract nouns in a comma list inside one clause".
_COMMA_SALAD_RE = re.compile(
    r"\b(?:in|with|across|aligned with|delivering|focused on)\s+"
    r"[A-Za-z][a-z]+(?:\s*/\s*[A-Za-z][a-z]+)?\s*,\s*"
    r"[A-Za-z][a-z]+(?:\s*/\s*[A-Za-z][a-z]+)?\s*,\s*"
    r"[A-Za-z][a-z]+",
    re.IGNORECASE,
)

# A run of slashed tech labels in prose (e.g. "Ticketing / ITSM, PST / time-zone coverage").
_SLASHED_LIST_IN_PROSE_RE = re.compile(
    r"[A-Za-z]+\s*/\s*[A-Za-z\-]+\s*,\s*[A-Za-z]+\s*/\s*[A-Za-z\-]+",
)


def is_malformed_line(text: str) -> bool:
    if not text or not isinstance(text, str):
        return False
    if _THESIS_GHOST_RE.search(text):
        return True
    if _THESIS_GHOST_SHORT_RE.search(text):
        return True
    if _SLASHED_LIST_IN_PROSE_RE.search(text):
        return True
    if _COMMA_SALAD_RE.search(text):
        return True
    return False


def _strip_thesis_ghost(text: str) -> Tuple[str, bool]:
    """Remove just the broken thesis sentence/fragment if present, leave the rest."""
    out = text
    changed = False
    new = _THESIS_GHOST_RE.sub(".", out)
    if new != out:
        out = new
        changed = True
    new = _THESIS_GHOST_SHORT_RE.sub("", out)
    if new != out:
        out = new
        changed = True
    out = re.sub(r"\s{2,}", " ", out).strip()
    # Collapse orphan periods ("foo ." -> "foo.") but preserve dot-prefixed tokens
    # like ".env", ".json", ".py" — the dot starts a real token if followed by a word char.
    out = re.sub(r"\s+\.(?!\w)", ".", out)
    out = re.sub(r"\.{2,}", ".", out)
    return out, changed


def clean_summary(content: Dict[str, Any]) -> List[str]:
    """Strip malformed sentences/ghost-thesis from summary; never export garbage."""
    notes: List[str] = []
    summary = str(content.get("summary") or "").strip()
    if not summary:
        return notes

    stripped, changed = _strip_thesis_ghost(summary)
    if changed:
        notes.append("removed broken thesis fragment from summary")
        summary = stripped

    sentences = re.split(r"(?<=[.!?])\s+", summary)
    kept = [s for s in sentences if s.strip() and not is_malformed_line(s)]
    if len(kept) < len(sentences):
        notes.append(
            f"dropped {len(sentences) - len(kept)} malformed sentence(s) from summary"
        )

    if kept:
        new_summary = " ".join(kept).strip()
    else:
        # last-resort: keep first clean clause to avoid empty export
        new_summary = summary.split(".")[0].strip() + "."
        notes.append("trimmed summary to first clause")

    if new_summary != str(content.get("summary") or "").strip():
        content["summary"] = new_summary
    return notes


# ---------------------------------------------------------------------------
# 0.4 Intra-job near-duplicate bullets
# ---------------------------------------------------------------------------

def _bullet_signature(text: str) -> Set[str]:
    t = re.sub(r"[^\w\s%/.-]+", " ", str(text or "").lower())
    return {
        tok for tok in t.split()
        if len(tok) > 2 and tok not in _METRIC_STOP
    }


def _numeric_tokens(sig: Set[str]) -> Set[str]:
    return {tok for tok in sig if any(c.isdigit() for c in tok)}


def _are_intra_job_dupes(a: Set[str], b: Set[str], overlap_threshold: float) -> bool:
    if not a or not b:
        return False
    # Strong signal: same number reference (20-30, 75%, etc.) + any other shared
    # content token = same underlying fact restated.
    nums = _numeric_tokens(a) & _numeric_tokens(b)
    if nums:
        non_num_shared = (a - _numeric_tokens(a)) & (b - _numeric_tokens(b))
        if non_num_shared:
            return True
    return _signature_overlap(a, b) >= overlap_threshold


def dedupe_intra_job_bullets(
    content: Dict[str, Any],
    overlap_threshold: float = 0.78,
) -> List[str]:
    """Collapse near-duplicate bullets within a single experience entry."""
    notes: List[str] = []
    exps = content.get("experience")
    if not isinstance(exps, list):
        return notes

    for exp in exps:
        if not isinstance(exp, dict):
            continue
        bullets = [str(b).strip() for b in (exp.get("bullets") or []) if str(b).strip()]
        if not bullets:
            continue
        kept: List[str] = []
        kept_sigs: List[Set[str]] = []
        for b in bullets:
            sig = _bullet_signature(b)
            is_dup = any(
                _are_intra_job_dupes(sig, ks, overlap_threshold) for ks in kept_sigs
            )
            if is_dup:
                notes.append(
                    f"dropped near-duplicate bullet under {exp.get('company', '(unknown)')}: "
                    f"{b[:80]}"
                )
                continue
            kept.append(b)
            kept_sigs.append(sig)
        exp["bullets"] = kept
    return notes


_VAGUE_FILLER_VERB_RE = re.compile(
    r"^(?:Managed|Handled|Performed|Did|Worked on|Maintained)\b",
    re.IGNORECASE,
)
_VAGUE_FILLER_NOUN_RE = re.compile(
    r"\b(?:systems?|tasks?|duties|operations|things|work)\s*\.?\s*$",
    re.IGNORECASE,
)
_VAGUE_FILLER_TOOL_HINTS: Tuple[str, ...] = (
    "salesforce", "mysql", "windows", "linux", "rustdesk", "microsoft",
    "outlook", "teams", "dns", "tcp", "vpn", "office", "365",
)


def _is_vague_filler_bullet(text: str) -> bool:
    """True when a bullet is generic verb + generic noun with no proof."""
    t = str(text or "").strip()
    if not t:
        return False
    tokens = re.findall(r"[\w-]+", t)
    if len(tokens) > 6:
        return False
    if re.search(r"\d|%", t):
        return False
    if not _VAGUE_FILLER_VERB_RE.search(t):
        return False
    if not _VAGUE_FILLER_NOUN_RE.search(t):
        return False
    lower = t.lower()
    if any(hint in lower for hint in _VAGUE_FILLER_TOOL_HINTS):
        return False
    words = t.split()
    for word in words[1:]:
        if word[:1].isupper() and word.lower() not in ("i",):
            return False
    return True


def strip_vague_filler_bullets(content: Dict[str, Any]) -> List[str]:
    """Drop bullets like 'Managed site technical systems.' that say almost nothing."""
    notes: List[str] = []
    exps = content.get("experience")
    if not isinstance(exps, list):
        return notes
    for exp in exps:
        if not isinstance(exp, dict):
            continue
        bullets = [str(b).strip() for b in (exp.get("bullets") or []) if str(b).strip()]
        if not bullets:
            continue
        kept: List[str] = []
        for b in bullets:
            if _is_vague_filler_bullet(b):
                notes.append(
                    f"dropped vague-filler bullet under {exp.get('company', '(unknown)')}: "
                    f"{b[:80]}"
                )
                continue
            kept.append(b)
        exp["bullets"] = kept
    return notes


_THIN_PHOTON_BULLET_RE = re.compile(
    r"^(?:Administered|Supported|Managed)\s+Linux Photon servers,\s*NUC kiosks,.+$",
    re.IGNORECASE,
)
_PHOTON_RICH_DETAIL_RE = re.compile(r"\b(CCTV|RFID|DMX|Dante|OBS)\b", re.IGNORECASE)
_PHOTON_BULLET_HELP_DESK = (
    "Supported Linux Photon servers, NUC kiosks, and networked facility "
    "systems in a live technical environment."
)


def expand_thin_photon_infrastructure_bullet(
    content: Dict[str, Any], job_title: str = ""
) -> List[str]:
    """Replace vague Linux Photon / NUC bullets with a clearer help-desk phrasing."""
    notes: List[str] = []
    if not isinstance(content, dict) or not _is_help_desk_role(job_title):
        return notes
    exps = content.get("experience")
    if not isinstance(exps, list):
        return notes
    for exp in exps:
        if not isinstance(exp, dict):
            continue
        bullets = exp.get("bullets")
        if not isinstance(bullets, list):
            continue
        new_bullets: List[str] = []
        changed = False
        for b in bullets:
            s = str(b).strip()
            if (
                _THIN_PHOTON_BULLET_RE.match(s)
                and not _PHOTON_RICH_DETAIL_RE.search(s)
            ):
                new_bullets.append(_PHOTON_BULLET_HELP_DESK)
                changed = True
                notes.append(
                    f"expanded thin Photon/NUC bullet under {exp.get('company', '(unknown)')}"
                )
            else:
                new_bullets.append(s)
        if changed:
            exp["bullets"] = new_bullets
    return notes


# ---------------------------------------------------------------------------
# 0.5 Claim-audit language strip (internal truth-checking must not export)
# ---------------------------------------------------------------------------

_CLAIM_AUDIT_SENTENCE_RE = re.compile(
    r"[^.!?]*(?:"
    # Audit-mode disclaimers ("X is not claimed", "do not claim ...").
    r"is not claimed|are not claimed|not claimed|"
    r"do not claim|don't claim|cannot claim|"
    # First-person hedges that should never leak into a resume.
    r"I (?:do not|don't) have|"
    r"I (?:am not|'m not) (?:yet|currently)|"
    r"I have (?:partial|limited|some)\b|"
    # Audit-style "supported by/through" framings the LLM was told to omit.
    r"is supported (?:by managed|through (?:managed )?)|"
    r"are supported (?:by managed|through (?:managed )?)|"
    # Time-zone / coverage hedge.
    r"time[- ]?zone coverage is not|"
    r"PST\s*/\s*time[- ]?zone coverage is not"
    r")[^.!?]*[.!?]\s*",
    re.IGNORECASE,
)

# "Personal project only" / "Side project only" — defensive disclaimer that
# reads like an apology on a resume. The reader already knows it's a personal
# project from where it lives in the document. Strip the leading qualifier.
_DEFENSIVE_PROJECT_DISCLAIMER_RE = re.compile(
    r"\b(?:Personal|Side)\s+project\s+only\s*[;,.]?\s*",
    re.IGNORECASE,
)

_CLAIM_AUDIT_CLAUSE_RE = re.compile(
    r";\s*[^.;]*(?:"
    r"is not claimed|are not claimed|not claimed|"
    r"do not claim|don't claim|cannot claim|"
    r"I (?:do not|don't) have|"
    r"I have (?:partial|limited|some)|"
    r"is supported (?:by|through)|are supported (?:by|through)|"
    r"time[- ]?zone coverage"
    r")[^.;]*",
    re.IGNORECASE,
)


# Phrase-level hedges — IN-sentence clauses the LLM appends to "soften" a
# claim toward honesty. They violate the career_master "omit gaps silently"
# rule. Patterns strip the hedge CLAUSE but leave the legitimate lead claim
# intact when possible. Applied BEFORE the sentence-level claim-audit strip
# so a sentence with a good lead + hedge tail keeps the lead.
#
# Each entry: (regex, replacement). Replacement is "" for most; the cleanup
# pass collapses whitespace/orphan periods afterward.
_HEDGE_PHRASE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    # 1. Relative clause containing a meta-audit hedge:
    #    ", which taught/showed/gave/let me X without pretending Y"
    (
        r",\s+which\s+(?:taught|showed|gave|let|helped)\s+(?:me|us)\s+"
        r"[^,.;!?]+?\s+without\s+"
        r"(?:pretending|claiming|inflating|overstating|overclaiming|misrepresenting)\b"
        r"[^.!?]*",
        "",
    ),
    # 2. "without pretending/claiming/inflating/..." — direct hedge phrase,
    #    optionally preceded by comma. Strip to end of sentence.
    (
        r",?\s+without\s+"
        r"(?:pretending|claiming|inflating|overstating|overclaiming|misrepresenting)\b"
        r"[^.!?]*",
        "",
    ),
    # 3. "while not pretending/claiming/..." — same family.
    (
        r",?\s+while\s+not\s+"
        r"(?:pretending|claiming|inflating|overclaiming|overstating)\b"
        r"[^.!?]*",
        "",
    ),
    # 4. "though not at enterprise/production/multi-site/admin/IOS CLI/kernel
    #    scale|level|depth|tier" — narrow technical hedges only, so common
    #    phrases like "though not active duty" are NOT touched.
    (
        r",\s+though\s+not\s+(?:at|in)\s+"
        r"(?:enterprise|production|multi[- ]site|admin|administrator|"
        r"IOS\s+CLI|kernel|distro|tenant|enterprise[- ]grade|fleet)\s+"
        r"(?:scale|level|depth|tier|administration|admin)\b[^.!?]*",
        "",
    ),
    # 5. Parens-wrapped scale qualifiers — "(at small-shop scale)",
    #    "(small-site)", "(single-site)", "(limited X scope)".
    (r"\s*\(\s*(?:at\s+)?small[- ]shop\s+scale\s*\)", ""),
    (r"\s*\(\s*small[- ]site(?:\s+only)?\s*\)", ""),
    (r"\s*\(\s*single[- ]site(?:\s+only)?\s*\)", ""),
    (r"\s*\(\s*limited\s+[\w\s-]{1,40}\s+scope\s*\)", ""),
    # 6. Trailing scale qualifiers without parens. Comma is optional —
    #    catches both "..., at small-shop scale" and "...troubleshooting at
    #    small-shop scale" (no comma).
    (r",?\s+at\s+small[- ]shop\s+scale\b", ""),
    (r",?\s+at\s+single[- ]site\s+scale\b", ""),
    (r",?\s+at\s+small[- ]site\s+scale\b", ""),
    (r",?\s+at\s+small[- ]team\s+scale\b", ""),
    # 6a. Comma-compound and "and"-compound scale qualifiers:
    #     "at small-shop, single-site scale" / "at single-site and small-shop scale"
    #     The LLM combines two scale framings into one apologetic tail.
    (
        r",?\s+at\s+(?:small|single)[- ](?:shop|site|team)"
        r"\s*(?:,\s*|\s+and\s+|\s+or\s+|\s*/\s*)"
        r"(?:small|single)[- ](?:shop|site|team)\s+scale\b",
        "",
    ),
    # 7. Trailing "with limited X scope" — strip the trailing clause from
    #    " with limited" through to end of sentence (period).
    (
        r"\s+with\s+limited\s+"
        r"(?:network|administrative|admin|operational|backup|infrastructure|management|policy)\s+"
        r"scope\b",
        "",
    ),
    # 8. Trailing "<verb> a (small|single)-(site|shop|team) <tail>".
    #    Generalizes the original "in a small-site environment" pattern:
    #      verbs: for, in, within, across, at
    #      adjectives: small, single
    #      nouns: site, shop, team
    #      tails: environment, setting, context, setup, deployment,
    #             installation, build, configuration, stack, infrastructure
    #    Catches "for a small-site setup", "in a single-site environment",
    #    "within a small-shop deployment", etc.
    (
        r"\s+(?:for|in|within|across|at)\s+a\s+"
        r"(?:small|single)[- ](?:site|shop|team)\s+"
        r"(?:environment|setting|context|setup|deployment|installation|"
        r"build|configuration|stack|infrastructure)\b",
        "",
    ),
    # 9. Trailing ", not enterprise/full/admin <noun>" hedge tails.
    #    The "I do X, but to be clear I don't do the bigger thing" pattern.
    #    Catches:
    #      ", not enterprise policy management"   (the exact leak)
    #      ", not enterprise scale"
    #      ", not at the enterprise level"
    #      ", not at admin level"
    #      ", but not full enterprise IAM"
    #      ", though not production-grade deployment"
    #    Requires both a level-qualifier adjective AND a trailing noun so
    #    sentences like "I'm a manager, not an analyst" don't false-positive.
    (
        r",\s+(?:but\s+|though\s+|while\s+)?not\s+"
        r"(?:at\s+(?:the\s+)?)?"
        r"(?:full\s+)?"
        r"(?:enterprise|enterprise[- ]grade|production|production[- ]grade|"
        r"multi[- ]site|admin|administrator|tenant|fleet|"
        r"tenant[- ]wide|domain[- ]wide|kernel[- ]level)\s+"
        r"(?:scale|level|depth|tier|administration|admin|management|"
        r"configuration|infrastructure|deployment|policy\s+management|"
        r"policy|monitoring|operations|IAM|grade)\b[^.!?]*",
        "",
    ),
)


def _strip_hedge_phrases_in_text(text: str) -> Tuple[str, List[str]]:
    """Apply the phrase-level hedge patterns. Returns (new_text, notes)."""
    notes: List[str] = []
    out = text or ""
    for pattern, repl in _HEDGE_PHRASE_PATTERNS:
        new_out, n = re.subn(pattern, repl, out, flags=re.IGNORECASE)
        if n:
            notes.append(f"hedge: stripped pattern {pattern[:60]}... ({n}x)")
            out = new_out
    return out, notes


# ---------------------------------------------------------------------------
# Skill-label-in-prose normalization (issue F: title-case labels leaking)
# ---------------------------------------------------------------------------

# Function words that should be lowercase in book-title case. When the LLM
# emits one of these CAPITALIZED between two title-case words, it's the smoking
# gun for a misformatted skill-label-in-prose ("Backup And Restore Basics").
_PROSE_FUNCTION_WORDS_CASE_FIX = (
    "And", "Or", "Of", "For", "The", "In", "On", "At", "With",
    "To", "From", "By", "As", "But", "Nor", "A", "An",
)

# Proper nouns with their canonical casing — used when smart-casing prose
# and skill labels. Key is the lowercase lookup form; value is what to emit.
# This explicit map handles camelCase brand names (macOS, SharePoint, OneDrive,
# iOS) where a naive "capitalize first letter only" approach would corrupt them.
_PROSE_PROPER_NOUNS_MAP: Dict[str, str] = {
    "microsoft": "Microsoft",
    "cisco": "Cisco",
    "windows": "Windows",
    "linux": "Linux",
    "office": "Office",
    "outlook": "Outlook",
    "teams": "Teams",
    "sharepoint": "SharePoint",
    "onedrive": "OneDrive",
    "onenote": "OneNote",
    "word": "Word",
    "excel": "Excel",
    "powerpoint": "PowerPoint",
    "veeam": "Veeam",
    "reolink": "Reolink",
    "rustdesk": "RustDesk",
    "salesforce": "Salesforce",
    "unity": "Unity",
    "photon": "Photon",
    "meshy": "Meshy",
    "blender": "Blender",
    "docker": "Docker",
    "flashforge": "Flashforge",
    "flashprint": "FlashPrint",
    "orcaslicer": "OrcaSlicer",
    "ollama": "Ollama",
    "wireshark": "Wireshark",
    "sysinternals": "Sysinternals",
    "sandboxie": "Sandboxie",
    "comfyui": "ComfyUI",
    "virtualbox": "VirtualBox",
    "arduino": "Arduino",
    "exiftool": "ExifTool",
    "elgato": "Elgato",
    "sap": "SAP",
    "slack": "Slack",
    "notion": "Notion",
    "github": "GitHub",
    "gitlab": "GitLab",
    "git": "Git",
    "mac": "Mac",
    "macos": "macOS",  # camelCase canonical
    "apple": "Apple",
    "ios": "iOS",      # camelCase canonical
    "ipados": "iPadOS",
    "iphone": "iPhone",
    "ipad": "iPad",
    "android": "Android",
    "gpedit": "gpedit",  # lowercase Windows utility name
    "gpmc": "GPMC",
    "wsl": "WSL",
    "exchange": "Exchange",
    "intune": "Intune",
    "okta": "Okta",
    "sailpoint": "SailPoint",
    "azure": "Azure",
    "splunk": "Splunk",
    "datadog": "Datadog",
    "servicenow": "ServiceNow",
    "jira": "Jira",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "nodejs": "Node.js",
    "powershell": "PowerShell",
    "wireguard": "WireGuard",
    "youtube": "YouTube",
    "facebook": "Facebook",
    "linkedin": "LinkedIn",
    "twitter": "Twitter",
    "instagram": "Instagram",
    # Databases & data
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mariadb": "MariaDB",
    "sqlite": "SQLite",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "neo4j": "Neo4j",
    "cassandra": "Cassandra",
    "dynamodb": "DynamoDB",
    "couchdb": "CouchDB",
    # Web / runtime / frameworks
    "react": "React",
    "angular": "Angular",
    "vue": "Vue",
    "nextjs": "Next.js",
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "express": "Express",
    "nginx": "NGINX",
    "apache": "Apache",
    # DevOps / infra tools
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "terraform": "Terraform",
    "ansible": "Ansible",
    "jenkins": "Jenkins",
    "grafana": "Grafana",
    "prometheus": "Prometheus",
    "elasticsearch": "Elasticsearch",
    "kibana": "Kibana",
    "logstash": "Logstash",
    "vmware": "VMware",
    "vsphere": "vSphere",
    "esxi": "ESXi",
    "hyperv": "Hyper-V",
    "proxmox": "Proxmox",
    "openvpn": "OpenVPN",
    "wireguard": "WireGuard",
    # AI / ML
    "claude": "Claude",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
    "huggingface": "Hugging Face",
    "langchain": "LangChain",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "numpy": "NumPy",
    "pandas": "pandas",  # lowercase canonical
    "matplotlib": "Matplotlib",
    "jupyter": "Jupyter",
    "scikit": "scikit-learn",
    # Browsers / OSes (lowercase canonical for some)
    "chrome": "Chrome",
    "firefox": "Firefox",
    "edge": "Edge",
    "safari": "Safari",
    "ubuntu": "Ubuntu",
    "debian": "Debian",
    "centos": "CentOS",
    "fedora": "Fedora",
    "redhat": "Red Hat",
    "rhel": "RHEL",
    "alpine": "Alpine",
}

# Set form used for quick membership tests (backward compatibility).
_PROSE_PROPER_NOUNS = frozenset(_PROSE_PROPER_NOUNS_MAP.keys())

# Acronyms (preserve as uppercase when lowercased in prose).
_PROSE_ACRONYMS = frozenset({
    "dns", "tcp", "ip", "vpn", "mfa", "sso", "iam", "os", "pc", "nas",
    "sql", "aws", "azure", "gcp", "rfid", "dmx", "dante", "obs", "nuc",
    "cctv", "ssh", "http", "https", "api", "cpu", "gpu", "ram", "ssd",
    "hdd", "usb", "sla", "mou", "fema", "cbrn", "hipaa", "sop", "sops",
    "cli", "gui", "ui", "ux", "oem", "isp", "ldap", "gpo", "msi",
    "noc", "soc", "siem", "av", "csv", "json", "xml", "html", "css",
    "tls", "ssl", "pst", "pdt", "utc", "est", "cst", "mst", "pdf",
    "m365", "cuda", "rom", "nvr", "dvr", "vlan", "vlans", "ad", "wsus",
    "sccm", "mecm", "itsm", "elk", "saas", "paas", "iaas",
    # Networking & infrastructure acronyms — added after observing live runs
    # corrupt them via title-case normalization.
    "dhcp", "smb", "nfs", "ftp", "sftp", "ftps", "smtp", "imap", "pop3",
    "snmp", "rdp", "ntp", "ipsec", "vrf", "qos", "stp",
    "lacp", "ospf", "bgp", "eigrp", "mpls", "nat", "pat", "dhcpv6",
    "dnssec", "acl", "acls", "wan", "lan", "wlan", "vpc", "edr", "ids",
    "ips", "mdm", "rmm", "psa", "uac", "wmi", "uefi", "bios",
    # Storage & backup
    "raid", "lto", "lvm", "iops",
    # Programming & data
    "rest", "soap", "yaml", "regex", "orm", "etl", "udf",
    # Identity & cloud-ish
    "scim", "idp", "saml", "oauth", "oidc", "jwt", "rbac", "abac", "pim",
    "pam", "uba", "ueba",
})

# Match: lowercase letter (end of title-case word) + space + CAPITALIZED
# function word + space + capital letter (start of next title-case word).
# Replace the captured function word with its lowercase form. Does NOT fire
# at sentence start (lookbehind requires a lowercase letter, not punctuation).
_CAPITALIZED_FN_WORD_MIDPHRASE_RE = re.compile(
    r"(?<=[a-z])\s+(" + "|".join(_PROSE_FUNCTION_WORDS_CASE_FIX) + r")\s+(?=[A-Z][a-z])"
)


def _lowercase_capitalized_function_words(text: str) -> Tuple[str, int]:
    """In phrases like 'Backup And Restore Basics', lowercase capitalized
    function words ('And', 'Or', 'Of', ...) when they appear between two
    title-case words. Surgical, low-risk fix — does NOT touch sentence starts
    or stand-alone capitalized function words like "And so I did...".

    Returns (cleaned_text, replacement_count).
    """
    if not text:
        return text, 0
    count = 0

    def _replace(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        return " " + m.group(1).lower() + " "

    out = _CAPITALIZED_FN_WORD_MIDPHRASE_RE.sub(_replace, text)
    return out, count


def _classify_token(token: str) -> str:
    """Return the canonical-cased form of a single token (one whitespace-
    bounded word that may contain slashes/hyphens). Handles:
      - Acronyms (DNS, VPN, ITSM, ...) -> uppercase
      - Known proper nouns (macOS, SharePoint, ...) -> map canonical form
      - Slash/hyphen-compound tokens (Ticketing/ITSM) -> recurse into parts
      - Plain words -> lowercase
    """
    if not token:
        return token
    # Slash-compound: process each side independently then rejoin.
    if "/" in token:
        return "/".join(_classify_token(p) for p in token.split("/"))
    tl = token.lower()
    if tl in _PROSE_ACRONYMS:
        return token.upper()
    if tl in _PROSE_PROPER_NOUNS_MAP:
        return _PROSE_PROPER_NOUNS_MAP[tl]
    return tl


def _smart_lowercase_phrase(phrase: str) -> str:
    """Lowercase a multi-word phrase, preserving known proper nouns (with
    their canonical casing including camelCase brands like macOS/SharePoint)
    and known acronyms (uppercased). Words not in either list become
    lowercase regardless of input casing.
    """
    if not phrase:
        return phrase
    return " ".join(_classify_token(w) for w in phrase.split())


def _lowercase_skill_labels_in_prose(
    text: str,
    skill_labels: List[str],
) -> Tuple[str, List[str]]:
    """For each multi-word skill label, if it appears VERBATIM mid-sentence
    in `text`, replace with a lowercased version that preserves proper nouns
    and acronyms.

    Returns (cleaned_text, list_of_replaced_labels).
    """
    if not text or not skill_labels:
        return text, []
    out = text
    replaced: List[str] = []
    # Sort labels by length DESC so longer labels match first (avoids
    # partial-match conflicts).
    sorted_labels = sorted(
        (lbl for lbl in skill_labels if lbl),
        key=lambda x: -len(x),
    )
    for label in sorted_labels:
        words = label.split()
        if len(words) < 2:
            continue  # single-word labels don't need lowercasing
        # If every word is a proper noun or acronym, skip — label IS its
        # canonical casing (e.g., "Microsoft 365", "Cisco DNS").
        if all(
            w.lower() in _PROSE_PROPER_NOUNS or w.lower() in _PROSE_ACRONYMS
            for w in words
        ):
            continue
        new_label = _smart_lowercase_phrase(label)
        if new_label == label:
            continue
        # Find verbatim matches that are NOT at the very start of a sentence
        # (don't touch the summary's opening title, for example).
        # Lookbehind: a non-sentence-boundary character (not "." "!" "?" or string start).
        escaped = re.escape(label)
        pattern = re.compile(
            r"(?<![.!?]\s)(?<!\A)" + escaped + r"\b",
            flags=0,
        )
        new_out, n = pattern.subn(new_label, out)
        if n:
            replaced.append(f"{label!r} -> {new_label!r} ({n}x)")
            out = new_out
    return out, replaced


def _title_case_plain_word(w: str) -> str:
    """Capitalize first letter, lowercase the rest. Single-letter words go
    uppercase."""
    if not w:
        return w
    if len(w) == 1:
        return w.upper()
    return w[0].upper() + w[1:].lower()


def _normalize_skill_label_casing(label: str) -> str:
    """Normalize a single skill-label string to book title case, preserving
    known proper nouns (canonical-cased) and known acronyms (uppercase).

    Word classification order per whitespace-bounded token:
      1. Slash-compound (e.g. 'Ticketing/ITSM') -> recurse into each part
      2. Known acronym                          -> uppercase
      3. Known proper noun (incl. camelCase)    -> canonical-cased map value
      4. Function word, mid-label (i > 0)        -> lowercase
      5. Otherwise                               -> title-case

    Examples:
      'Backup And Restore Basics' -> 'Backup and Restore Basics'
      'USER ACCOUNT MANAGEMENT'   -> 'User Account Management'
      'Ticketing/itsm'             -> 'Ticketing/ITSM'
      'macos support'              -> 'macOS Support' (camelCase preserved)
      'sharepoint'                 -> 'SharePoint'
      'DNS'                        -> 'DNS'
      'Microsoft 365'              -> 'Microsoft 365'
    """
    if not label:
        return label
    words = label.strip().split()
    if not words:
        return label
    out: List[str] = []
    fn_words_lower = {w.lower() for w in _PROSE_FUNCTION_WORDS_CASE_FIX}

    def _classify(w: str, *, is_first: bool) -> str:
        # Slash-compound: classify each part independently then rejoin.
        if "/" in w:
            parts = w.split("/")
            return "/".join(
                _classify(p, is_first=(is_first and j == 0))
                for j, p in enumerate(parts)
            )
        wl = w.lower()
        if wl in _PROSE_ACRONYMS:
            return w.upper()
        if wl in _PROSE_PROPER_NOUNS_MAP:
            return _PROSE_PROPER_NOUNS_MAP[wl]
        if (not is_first) and wl in fn_words_lower:
            return wl
        return _title_case_plain_word(w)

    for i, w in enumerate(words):
        out.append(_classify(w, is_first=(i == 0)))
    return " ".join(out)


def normalize_skill_label_casing_in_list(
    labels: List[str],
) -> Tuple[List[str], List[str]]:
    """Apply _normalize_skill_label_casing to each item in a skill list.
    Returns (new_list, change_notes).
    """
    notes: List[str] = []
    out: List[str] = []
    for lbl in labels or []:
        new_lbl = _normalize_skill_label_casing(str(lbl))
        if new_lbl != lbl:
            notes.append(f"normalized skill label: {lbl!r} -> {new_lbl!r}")
        out.append(new_lbl)
    return out, notes


def fix_titlecase_skill_labels_in_resume(
    content: Dict[str, Any],
) -> List[str]:
    """Resume-level pass:
    1. Normalize casing of every skill-label string in skills.technical and
       skills.soft (lowercase capitalized function words in book-title case).
    2. Lowercase capitalized function words ('And', 'Or', 'Of', ...) inside
       title-case phrases mid-prose (summary, bullets, project descriptions).
    3. For each remaining multi-word skill label, if it appears VERBATIM
       mid-sentence in prose, replace with a smart-lowercased version that
       preserves proper nouns and acronyms.
    """
    notes: List[str] = []
    if not isinstance(content, dict) or content.get("error"):
        return notes
    sk = content.get("skills")
    if isinstance(sk, dict):
        for key in ("technical", "soft"):
            arr = sk.get(key)
            if isinstance(arr, list):
                normalized, n_notes = normalize_skill_label_casing_in_list(
                    [str(x) for x in arr]
                )
                sk[key] = normalized
                notes.extend(n_notes)
    # Gather all skill labels (after normalization) for the prose-pass step.
    skill_labels: List[str] = []
    if isinstance(sk, dict):
        for key in ("technical", "soft"):
            arr = sk.get(key)
            if isinstance(arr, list):
                skill_labels.extend(str(x) for x in arr if x)

    def _fix_prose(text: str, field_name: str) -> str:
        if not text:
            return text
        # 1. Lowercase capitalized function words mid-phrase
        out, n_fn = _lowercase_capitalized_function_words(text)
        if n_fn:
            notes.append(
                f"lowercased {n_fn} capitalized function word(s) in {field_name} "
                "(midphrase title-case fix)"
            )
        # 2. Skill-label verbatim mid-sentence lowercase
        out2, replaced = _lowercase_skill_labels_in_prose(out, skill_labels)
        if replaced:
            notes.append(
                f"lowercased {len(replaced)} skill-label verbatim mention(s) in "
                f"{field_name}: {'; '.join(replaced[:3])}"
                + ("..." if len(replaced) > 3 else "")
            )
        return out2

    summary = str(content.get("summary") or "")
    if summary:
        content["summary"] = _fix_prose(summary, "summary")

    exps = content.get("experience")
    if isinstance(exps, list):
        for idx, exp in enumerate(exps):
            if not isinstance(exp, dict):
                continue
            bullets = exp.get("bullets")
            if not isinstance(bullets, list):
                continue
            new_bullets: List[str] = []
            for b in bullets:
                new_bullets.append(_fix_prose(str(b), f"exp[{idx}].bullet"))
            exp["bullets"] = new_bullets

    projs = content.get("projects")
    if isinstance(projs, list):
        for idx, proj in enumerate(projs):
            if not isinstance(proj, dict):
                continue
            for field in ("description", "impact"):
                val = str(proj.get(field) or "")
                if val:
                    proj[field] = _fix_prose(val, f"project[{idx}].{field}")
    return notes


def strip_claim_audit_in_text(text: str) -> Tuple[str, List[str]]:
    """Remove generator meta-language ('X is not claimed') from candidate-facing prose.

    Order matters:
      1. Phrase-level hedge patterns first — strip clauses ("without pretending Y",
         "at small-shop scale" tails, "though not at enterprise scale") while
         preserving any legitimate lead clause in the same sentence.
      2. Sentence-level claim-audit strip — for sentences whose meta-audit
         marker isn't a tail clause (e.g. "I do not claim X" is the whole point).
      3. Clause-level semicolon strip — same family, semicolon-introduced.
      4. Defensive project disclaimer strip.
      5. Whitespace + orphan-period cleanup.
    """
    notes: List[str] = []
    out = (text or "").strip()
    if not out:
        return out, notes

    prev = out
    # 1. Phrase-level hedges (new) — keep legitimate lead, drop hedge tail.
    out, hedge_notes = _strip_hedge_phrases_in_text(out)
    notes.extend(hedge_notes)
    # 2. Sentence-level claim-audit triggers.
    out = _CLAIM_AUDIT_SENTENCE_RE.sub("", out)
    # 3. Semicolon clause-level triggers.
    out = _CLAIM_AUDIT_CLAUSE_RE.sub("", out)
    # 4. Defensive project disclaimers like "Personal project only;".
    out = _DEFENSIVE_PROJECT_DISCLAIMER_RE.sub("", out)
    # 5. Cleanup: collapse whitespace, fix orphan punctuation.
    out = re.sub(r"\s{2,}", " ", out).strip()
    # Preserve dot-prefixed tokens (".env", ".json", ".py") when collapsing orphan periods.
    out = re.sub(r"\s+\.(?!\w)", ".", out)
    # Drop dangling commas left at sentence end after a hedge strip:
    # "I worked on Cisco switches,." -> "I worked on Cisco switches."
    out = re.sub(r",\s*\.", ".", out)
    out = re.sub(r",\s*$", "", out)
    if out != prev and not hedge_notes:
        notes.append("removed claim-audit language from text")
    return out, notes


# "Supported user account support, X, Y" -> "Supported user account, X, Y".
# The LLM treats "user account support" / "desktop support" as noun phrases but
# fronts them with the verb "Supported"/"Provided", producing a verb+noun-stem
# repeat that reads awkward. Strip the trailing "support" ONLY when followed by
# sentence-list punctuation or a noun like "work"/"services"/"roles", so we don't
# damage legitimate phrasing like "Supported colleagues with technical support
# during outages."
_VERB_NOUN_DOUBLEUP_RE = re.compile(
    r"\b(Supported|Provided)\s+([\w-]+(?:\s+[\w-]+){1,3})\s+support"
    r"(?=[,;.]|\s+(?:work|services?|roles?)\b)",
)


def _strip_verb_noun_doubleup(text: str) -> Tuple[str, bool]:
    out = text or ""
    new = _VERB_NOUN_DOUBLEUP_RE.sub(r"\1 \2", out)
    return new, new != out


def strip_verb_noun_doubleups_in_resume(content: Dict[str, Any]) -> List[str]:
    """Drop the 'Supported X support' double-up from summary, bullets, projects."""
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    summary = str(content.get("summary") or "")
    if summary:
        new_summary, changed = _strip_verb_noun_doubleup(summary)
        if changed:
            content["summary"] = new_summary
            notes.append("anti-fluff: dropped 'Supported X support' double-up from summary")
    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            bullets = exp.get("bullets")
            if not isinstance(bullets, list):
                continue
            new_bullets: List[str] = []
            mutated = False
            for b in bullets:
                new_b, changed = _strip_verb_noun_doubleup(str(b))
                if changed:
                    mutated = True
                new_bullets.append(new_b)
            if mutated:
                exp["bullets"] = new_bullets
                notes.append("anti-fluff: dropped 'Supported X support' double-up from bullets")
    projs = content.get("projects")
    if isinstance(projs, list):
        for p in projs:
            if not isinstance(p, dict):
                continue
            for field in ("description", "impact"):
                val = str(p.get(field) or "")
                if not val:
                    continue
                new_val, changed = _strip_verb_noun_doubleup(val)
                if changed:
                    p[field] = new_val
                    notes.append(f"anti-fluff: dropped 'Supported X support' double-up from project.{field}")
    return notes


# Active Directory / Local Group Policy equivalence is a credibility leak — they
# are different tools at different scales. If the LLM writes "Active Directory
# basics via Local Group Policy Editor" / "AD through gpedit" / similar, strip
# the AD claim entirely so the resume doesn't equate small-shop Windows with
# enterprise AD admin. The truthful adjacent skills (user account support,
# Windows policy configuration) survive elsewhere in the doc.
_AD_GPEDIT_EQUIVALENCE_RE = re.compile(
    # The full clause: "Active Directory ... via/through Local Group Policy / gpedit ...",
    # consumed up to the natural clause boundary. The middle uses `.*?` (not the
    # punctuation-excluding class) so we can span past in-token dots like ".msc".
    # The lookahead stops at the next clause boundary: comma+conjunction, bare
    # " and "/" plus " introducing a new noun, or sentence-ending punctuation.
    r"\bActive Directory\b.*?\b(?:via|through|using|with)\b.*?"
    r"(?:Local Group Policy|gpedit(?:\.msc)?)"
    r".*?(?=,\s*(?:plus|and|including|along\s+with)\b|\s+(?:and|plus)\s+\w|[.!?;](?:\s|$))",
    re.IGNORECASE | re.DOTALL,
)


def strip_ad_gpedit_equivalence(content: Dict[str, Any]) -> List[str]:
    """Remove "Active Directory ... via Local Group Policy / gpedit" phrasings.

    AD-vs-LGP is the canonical case of equating an adjacent small-shop tool with
    an enterprise tool. A tech reviewer immediately spots it. We strip the AD
    claim from summary/bullets/projects (and prune trailing-comma fragments).
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes

    def _scrub(s: str) -> Tuple[str, bool]:
        if not s:
            return s, False
        new = _AD_GPEDIT_EQUIVALENCE_RE.sub("", s)
        if new == s:
            return s, False
        # Cleanup after the strip: the equivalence clause may leave dangling
        # connectives like "Brings plus X" or "I bring." or "Brings;" — repair them.
        # 1. Drop orphan ", plus" / ", and" leading into the remaining clause.
        new = re.sub(r"(?i)(\b(?:Brings?|Offers?|Provides?|Built|Uses?|Used|Worked)\b)\s*[,;]?\s*(plus|and|including|along with)\s+", r"\1 ", new)
        # 2. Drop empty leading-verb fragments like "I bring." or "Brings;".
        new = re.sub(r"(?i)\b(?:I\s+bring|I\s+offer|Brings?|Offers?|Provides?)\s*[.,;]\s*", "", new)
        # 3. Drop leading "and X" → "X" if the verb got swallowed.
        new = re.sub(r"^\s*(?:and|plus)\s+", "", new, flags=re.IGNORECASE)
        # 4. Whitespace + punctuation tidy.
        new = re.sub(r"\s{2,}", " ", new)
        new = re.sub(r"\s+([,.;])", r"\1", new)
        new = re.sub(r",\s*\.", ".", new)
        new = re.sub(r"\.\s*,", ".", new)
        new = re.sub(r";\s*\.", ".", new)
        return new.strip(), True

    summary = str(content.get("summary") or "")
    if summary:
        new_summary, changed = _scrub(summary)
        if changed:
            content["summary"] = new_summary
            notes.append("stripped 'Active Directory via Local Group Policy' equivalence from summary")

    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            bullets = exp.get("bullets")
            if not isinstance(bullets, list):
                continue
            new_bullets: List[str] = []
            mutated = False
            for b in bullets:
                new_b, changed = _scrub(str(b))
                if changed:
                    mutated = True
                new_bullets.append(new_b)
            if mutated:
                exp["bullets"] = new_bullets
                notes.append("stripped 'Active Directory via Local Group Policy' equivalence from bullets")

    projs = content.get("projects")
    if isinstance(projs, list):
        for p in projs:
            if not isinstance(p, dict):
                continue
            for field in ("description", "impact"):
                val = str(p.get(field) or "")
                if val:
                    new_val, changed = _scrub(val)
                    if changed:
                        p[field] = new_val
                        notes.append(f"stripped 'Active Directory via Local Group Policy' equivalence from project.{field}")
    return notes


def _strip_duplicate_phrase_in_text(text: str, min_words: int = 3) -> Tuple[str, bool]:
    """Remove repeated multi-word phrases within a single text block.

    Targets the LLM tic where the same phrase appears twice in one summary:
    "...account creation/disable workflows ... and account creation/disable
    workflows at small-shop scale." Finds the longest duplicate phrase
    (>= `min_words` words) and removes the second occurrence along with any
    leading "and"/"or" connector.

    Returns (text, changed). One pass per call — caller can loop until stable
    if multiple duplicate phrases are expected.
    """
    if not text:
        return text, False
    # Tokenize to words for n-gram comparison.
    tokens = re.findall(r"\w+(?:[-/]\w+)*", text.lower())
    n = len(tokens)
    if n < 2 * min_words:
        return text, False
    # Search longest phrases first so we strip the biggest duplicate found.
    for size in range(min(12, n // 2), min_words - 1, -1):
        seen: Dict[str, int] = {}
        for i in range(n - size + 1):
            phrase = " ".join(tokens[i : i + size])
            if phrase in seen:
                # Found a duplicate at token positions seen[phrase] and i.
                # Build a forgiving regex that allows whitespace + punctuation
                # between original words (so "creation/disable" matches as one chunk).
                parts = phrase.split()
                pattern = r"\b" + r"[\W_]+".join(re.escape(p) for p in parts) + r"\b"
                matches = list(re.finditer(pattern, text, re.IGNORECASE))
                if len(matches) >= 2:
                    second = matches[1]
                    start, end = second.span()
                    # Extend left to swallow ", and"/" and"/" or" connectors.
                    pre = re.search(
                        r"[,\s]*\s+(?:and|or|plus|including|along\s+with)\s+$",
                        text[:start],
                    )
                    if pre:
                        start = pre.start()
                    # Also extend right if followed by trailing context like
                    # "at small-shop scale" that only made sense as a modifier
                    # to the duplicate phrase. We keep this conservative: only
                    # extend to the next sentence boundary if the next chars
                    # look like a comma-list-item that's about to start.
                    new_text = (text[:start] + text[end:]).rstrip()
                    # Tidy double spaces and orphan punctuation.
                    new_text = re.sub(r"\s{2,}", " ", new_text)
                    new_text = re.sub(r"\s+([,.;])", r"\1", new_text)
                    new_text = re.sub(r",\s*\.", ".", new_text)
                    new_text = re.sub(r",\s*$", "", new_text)
                    return new_text, True
            seen[phrase] = i
    return text, False


_LIGHT_EXPOSURE_LABEL_PHRASES: Tuple[str, ...] = (
    "windows policy configuration",
    "vlan basics",
    "windows server basics",
    "backup and restore basics",
    "backup/restore basics",
    "database operational support",
    "user account and access support",
    "virtualbox (home lab)",
    "active directory basics",
    "small-shop scale",
    "single-site",
)


def collapse_light_exposure_label_dump_in_summary(content: Dict[str, Any]) -> List[str]:
    """Detect summary sentences that are 3+ Light Exposure labels in a row
    (a "label dump") and collapse them.

    The leak: my career_master Light Exposure section has labels like "VLAN
    basics", "Windows Server basics", "backup and restore basics" etc. The LLM
    honestly surfaces them — but listed in a row in one summary sentence, they
    read as auto-generated bullet-points-as-prose. Replace such sentences with
    a compact paraphrase.
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    summary = str(content.get("summary") or "").strip()
    if not summary:
        return notes
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    new_sentences: List[str] = []
    mutated = False
    for s in sentences:
        s_lower = s.lower()
        hit_count = sum(1 for phrase in _LIGHT_EXPOSURE_LABEL_PHRASES if phrase in s_lower)
        if hit_count >= 3:
            # The sentence is mostly a label dump — replace with a compact
            # paraphrase that says the same thing without keyword-piling.
            new_sentences.append(
                "Comfortable at small-shop / single-site scale across Windows "
                "policy, networking, backup-and-restore, and operational database support."
            )
            mutated = True
            notes.append(
                f"collapsed Light-Exposure label dump in summary ({hit_count} labels in one sentence)"
            )
        else:
            new_sentences.append(s)
    if mutated:
        content["summary"] = " ".join(new_sentences).strip()
    return notes


def cap_summary_sentence_count(content: Dict[str, Any], max_sentences: int = 4) -> List[str]:
    """Drop sentences past the cap. Multiple builds had 5-6 sentence summaries
    that read as keyword-stuffed. Recruiters skim — 3-4 sentences is the target.
    The opener (job-title match) and the immediately-following fit sentence
    always survive.
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    summary = str(content.get("summary") or "").strip()
    if not summary:
        return notes
    # Split on sentence boundaries.
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) <= max_sentences:
        return notes
    kept = sentences[:max_sentences]
    dropped = sentences[max_sentences:]
    content["summary"] = " ".join(kept)
    notes.append(
        f"capped summary at {max_sentences} sentences (dropped {len(dropped)})"
    )
    return notes


def flag_summary_below_minimum(
    content: Dict[str, Any],
    min_sentences: int = 2,
    min_words: int = 25,
) -> List[str]:
    """Emit a note when the summary is too thin — no in-place rewrite."""
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    summary = str(content.get("summary") or "").strip()
    if not summary:
        return notes
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", summary) if s.strip()]
    word_count = len(re.findall(r"\w+", summary))
    if len(sentences) < min_sentences or word_count < min_words:
        notes.append(
            f"summary below minimum ({len(sentences)} sentence(s), {word_count} words) — "
            "critique should expand with role keywords"
        )
    return notes


def strip_duplicate_phrases_from_summary(content: Dict[str, Any]) -> List[str]:
    """Iteratively strip duplicate phrases from summary until stable."""
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    summary = str(content.get("summary") or "")
    if not summary:
        return notes
    out = summary
    for _ in range(5):  # at most 5 dedupes per summary; prevents infinite loops
        new_out, changed = _strip_duplicate_phrase_in_text(out)
        if not changed:
            break
        out = new_out
    if out != summary:
        content["summary"] = out
        notes.append("removed duplicate phrase(s) from summary")
    return notes


_PYTHON_SUMMARY_SELF_PROMO_RE = re.compile(
    r"[^.!?]*\bPython\b[^.!?]*\b(?:self-taught|self-directed|personal projects?|practice|years of)\b[^.!?]*[.!?]\s*"
    r"|"
    r"[^.!?]*\b(?:self-taught|self-directed|years of|personal)\b[^.!?]*\bPython\b[^.!?]*[.!?]\s*",
    re.IGNORECASE,
)


def strip_python_self_promo_from_summary(
    content: Dict[str, Any], job_title: str
) -> List[str]:
    """Drop Python self-promo sentences from the summary when the target is a support role.

    Why: the tailor prompt instructs the LLM to avoid Python framing in summaries for
    help-desk / service-desk targets, but subsequent recruiter / ATS LLM passes can
    resurface it (e.g. "Supported by N years of self-taught Python on personal projects").
    This is a deterministic final-pass cleanup. The Python project itself stays in
    projects[] / skills[] — only the summary's Python-self-promo sentences are removed.
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    # Python self-promo doesn't belong in summaries for any non-developer role.
    # Originally gated to help-desk only; expanded to field tech and any
    # title that isn't explicitly a Python/dev/engineer/data role.
    if _is_python_appropriate_role(job_title):
        return notes
    summary = str(content.get("summary") or "").strip()
    if not summary:
        return notes
    new_summary = _PYTHON_SUMMARY_SELF_PROMO_RE.sub("", summary).strip()
    new_summary = re.sub(r"\s{2,}", " ", new_summary)
    new_summary = re.sub(r"\s+([.,;])", r"\1", new_summary)
    if new_summary != summary:
        content["summary"] = new_summary
        notes.append("dropped Python self-promo sentence from non-developer summary")
    return notes


def _is_python_appropriate_role(job_title: str) -> bool:
    """Return True if a Python self-promo line is genuinely relevant to the
    target role (developer / engineer / data / automation roles)."""
    tl = (job_title or "").lower()
    if not tl:
        return False
    return bool(re.search(
        r"\b(?:python|developer|software\s*engineer|backend|full[-\s]stack|"
        r"data\s*(?:engineer|scientist|analyst)|ml\s*engineer|"
        r"automation\s*engineer|devops|sre|site\s*reliability|platform\s*engineer)\b",
        tl,
    ))


_ADMIN_HYBRID_HINTS: Tuple[str, ...] = (
    "administrative assistant",
    "admin assistant",
    "admin/help",
    "admin / help",
    "admin support",
)


def _is_admin_hybrid_role(job_title: str) -> bool:
    t = (job_title or "").lower()
    return any(h in t for h in _ADMIN_HYBRID_HINTS)


_FIELD_TECH_HINTS: Tuple[str, ...] = (
    "field technician",
    "field tech",
    "field service",
    "field support",
    "traveling tech",
    "traveling field",
    "site technician",
    "site tech",
    "mobile technician",
    "onsite technician",
    "on-site technician",
)


def _is_field_tech_role(job_title: str) -> bool:
    t = (job_title or "").lower()
    return any(h in t for h in _FIELD_TECH_HINTS)


_FIELD_TECH_AUTO_SKILLS: Tuple[str, ...] = (
    "Field Support",
    "Hardware Moves/Adds/Changes",
    "Cable Troubleshooting",
    "Equipment Installation",
    "Multi-Site Support",
)


def ensure_field_tech_skills(content: Dict[str, Any], job_title: str) -> List[str]:
    """Inject field-readiness skills for traveling/field-tech roles. Carlos's
    Newport hospitality work and BTB hardware swap experience justify these.
    Without them, the resume reads as office-IT, not field-ready."""
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    if not _is_field_tech_role(job_title):
        return notes
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return notes
    tech = sk.get("technical")
    if not isinstance(tech, list):
        tech = []
        sk["technical"] = tech
    tech_lower = {str(s).lower().strip() for s in tech}
    for required in _FIELD_TECH_AUTO_SKILLS:
        if required.lower() not in tech_lower:
            tech.append(required)
            tech_lower.add(required.lower())
            notes.append(f"injected '{required}' for field-tech role")
    return notes


_NICHE_TECH_BULLET_RE = re.compile(
    r"\b(?:Linux Photon|NUC kiosk|RFID|DMX|Dante|OBS|CCTV)\b",
    re.IGNORECASE,
)


_FUNCTION_SKILL_TOKENS: Set[str] = {
    # Support functions
    "support", "troubleshooting", "documentation", "administration",
    "onboarding", "workflows", "workflow", "triage", "escalation",
    "communication", "coordination", "operations", "operation",
    "writing", "authoring", "creation", "management", "incident",
    # ITSM concepts (not branded tools)
    "ticketing", "itsm", "helpdesk", "service",
    # Generic technical functions
    "debugging", "tracing", "monitoring", "logging", "imaging",
    "repair", "calibration", "scheduling", "configuration",
}

_TOOL_BRAND_TOKENS: Set[str] = {
    # Specific branded tools that should NOT lead the skills list
    "kandji", "bitdefender", "openvpn", "rustdesk", "salesforce",
    "freshdesk", "zendesk", "servicenow", "jira", "confluence", "github",
    "gitlab", "veeam", "splunk", "datadog", "okta", "sailpoint",
    "intune", "wsus", "sccm", "mecm", "vsphere", "esxi", "hyper-v",
    "proxmox", "virtualbox", "wireshark", "nmap", "ansible", "terraform",
}

_HELP_DESK_SKILL_PRIORITY: Tuple[str, ...] = (
    "microsoft 365",
    "outlook",
    "windows os troubleshooting",
    "help desk support",
    "ticketing/itsm",
)


def _help_desk_priority_index(label: str) -> Optional[int]:
    key = _norm_skill_key(label)
    for idx, priority in enumerate(_HELP_DESK_SKILL_PRIORITY):
        if key == priority or priority in key or key in priority:
            return idx
    return None


def reorder_skills_function_first(content: Dict[str, Any], job_title: str = "") -> List[str]:
    """Reorder skills.technical so function skills (End-User Support,
    Ticketing/ITSM, Documentation, ...) lead, with specific branded tools
    second. The LLM tends to mirror the JD's tool list at the front of the
    skills section — that reads as keyword-mimicry. Recruiters want to see
    what you DO before what you USE.
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return notes
    tech = sk.get("technical")
    if not isinstance(tech, list) or len(tech) < 4:
        return notes

    def _category(label: str) -> int:
        l = str(label).lower()
        tokens = set(re.findall(r"[\w-]+", l))
        if tokens & _TOOL_BRAND_TOKENS:
            return 2  # branded tool — last
        if tokens & _FUNCTION_SKILL_TOKENS:
            return 0  # function — first
        return 1  # neutral middle (M365, DNS, TCP/IP, hardware)

    indexed = list(enumerate(tech))
    indexed.sort(key=lambda pair: (_category(pair[1]), pair[0]))
    new_order = [item for _, item in indexed]
    if new_order != list(tech):
        sk["technical"] = new_order
        notes.append("reordered technical skills: functions first, tools last")
        tech = new_order

    if _is_help_desk_role(job_title):
        priority_front: List[str] = []
        remainder: List[str] = []
        used_keys: Set[str] = set()
        for priority_key in _HELP_DESK_SKILL_PRIORITY:
            for item in tech:
                item_key = _norm_skill_key(item)
                if item_key in used_keys:
                    continue
                if (
                    item_key == priority_key
                    or priority_key in item_key
                    or item_key in priority_key
                ):
                    priority_front.append(item)
                    used_keys.add(item_key)
                    break
        for item in tech:
            if _norm_skill_key(item) not in used_keys:
                remainder.append(item)
        helpdesk_order = priority_front + remainder
        if helpdesk_order != list(tech):
            sk["technical"] = helpdesk_order
            notes.append("reordered technical skills: help-desk stack first")
    return notes


def ensure_core_soft_skills_for_support_role(
    content: Dict[str, Any], job_title: str
) -> List[str]:
    """For support / helpdesk / IT roles, ensure "Documentation" and "Written
    communication" appear in skills.soft. Recruiters for these roles screen for
    them and the LLM frequently misses them."""
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    if not _is_help_desk_role(job_title):
        return notes
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return notes
    soft = sk.get("soft")
    if not isinstance(soft, list):
        soft = []
        sk["soft"] = soft
    soft_lower = {str(s).lower().strip() for s in soft}
    for required in ("Documentation", "Written communication"):
        key = required.lower()
        # Allow common variants to satisfy the requirement.
        variants = {
            "Documentation": {"documentation", "technical documentation", "doc"},
            "Written communication": {"written communication", "writing", "clear written communication"},
        }[required]
        if not (soft_lower & variants):
            soft.append(required)
            soft_lower.add(key)
            notes.append(f"added '{required}' to skills.soft for IT-support role")
    return notes


def split_dense_compound_bullets(content: Dict[str, Any]) -> List[str]:
    """Split single bullets that pack 5+ comma-separated items into TWO bullets.

    Recruiters can't skim "Administered Linux Photon servers and NUC kiosks,
    including file management and restoration for networked facility systems
    such as CCTV, RFID, DMX, Dante audio, and OBS." Splitting after the natural
    "including"/"such as" boundary makes each bullet scannable.
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    exps = content.get("experience")
    if not isinstance(exps, list):
        return notes
    for exp in exps:
        if not isinstance(exp, dict):
            continue
        bullets = exp.get("bullets")
        if not isinstance(bullets, list):
            continue
        new_bullets: List[str] = []
        mutated = False
        for b in bullets:
            s = str(b).strip()
            # Count comma-separated items in the sentence. If >= 5 items AND
            # the sentence has a natural split phrase, split into two bullets.
            comma_count = s.count(",")
            if comma_count < 4:
                new_bullets.append(s)
                continue
            # Find the natural split: "including"/"such as"/"including:". Split
            # AT that boundary so the head bullet stays focused.
            m = re.search(r"\b(including|such as)\b\s+", s, re.IGNORECASE)
            if not m:
                new_bullets.append(s)
                continue
            head = s[: m.start()].rstrip(",. ").strip()
            tail = s[m.end():].rstrip(".").strip()
            if not head or not tail or len(tail.split()) < 4:
                new_bullets.append(s)
                continue
            # Capitalize tail; prepend a verb if possible.
            tail_clean = tail[0].upper() + tail[1:] if tail else tail
            new_bullets.append(head.rstrip(".") + ".")
            new_bullets.append(f"Worked across {tail_clean}." if not tail_clean.lower().startswith(("supported", "worked", "managed", "administered", "handled")) else (tail_clean + "."))
            mutated = True
        if mutated:
            exp["bullets"] = new_bullets
            notes.append(f"split dense compound bullet(s) in {exp.get('company') or 'experience'}")
    return notes


def reorder_bullets_niche_last(
    content: Dict[str, Any], job_title: str
) -> List[str]:
    """For non-IT-pure roles, push niche-tech bullets (Linux Photon, RFID, DMX,
    Dante audio, OBS, CCTV, NUC kiosks) to the END of each experience's bullet
    list. They're true and worth keeping, but they shouldn't lead — recruiters
    skim from the top and these terms are unrecognizable in admin/helpdesk/
    customer-service contexts.

    Pure-tech roles (helpdesk/service-desk technical) keep the original order
    since these terms ARE recognizable to a Tier 1-2 reader.
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    exps = content.get("experience")
    if not isinstance(exps, list):
        return notes
    # Only reorder for clearly non-technical roles. Pure-tech helpdesk titles
    # keep the original (LLM-chosen) order.
    if _is_help_desk_role(job_title) and not _is_admin_hybrid_role(job_title):
        return notes
    for exp in exps:
        if not isinstance(exp, dict):
            continue
        bullets = exp.get("bullets")
        if not isinstance(bullets, list) or len(bullets) < 2:
            continue
        front: List[str] = []
        tail: List[str] = []
        for b in bullets:
            (tail if _NICHE_TECH_BULLET_RE.search(str(b)) else front).append(str(b))
        if tail and front:
            new_order = front + tail
            if new_order != list(map(str, bullets)):
                exp["bullets"] = new_order
                notes.append(
                    f"reordered {exp.get('company') or 'experience'} bullets: "
                    f"moved {len(tail)} niche-tech bullet(s) to end"
                )
    return notes


def _is_btb_experience(exp: Dict[str, Any]) -> bool:
    co = str(exp.get("company") or "").lower()
    pos = str(exp.get("position") or exp.get("title") or "").lower()
    return ("beat" in co and "bomb" in co) or co.strip() == "btb" or "beat the bomb" in pos


def _is_gj_experience(exp: Dict[str, Any]) -> bool:
    co = str(exp.get("company") or "").lower().replace("-", "").replace(" ", "")
    return "gotjunk" in co or "1800gotjunk" in co


def reorder_experience_by_role_family(
    content: Dict[str, Any], job_title: str
) -> List[str]:
    """Put the most-relevant employer first based on role family.

    - Admin / admin-helpdesk hybrid: GOT-JUNK first (customer comms, Salesforce,
      scheduling, logistics — the admin signal).
    - Service desk / help desk / IT support / field tech: BTB first ALWAYS
      (hardware, RustDesk, SOPs, DNS, ticket triage — the technical signal).

    Falls back to the LLM's original ordering when neither rule applies.
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    exps = content.get("experience")
    if not isinstance(exps, list) or len(exps) < 2:
        return notes

    if _is_admin_hybrid_role(job_title):
        preferred = next(
            (i for i, e in enumerate(exps) if isinstance(e, dict) and _is_gj_experience(e)),
            -1,
        )
        if preferred > 0:
            exps.insert(0, exps.pop(preferred))
            content["experience"] = exps
            notes.append("reordered experience: GOT-JUNK first for admin/helpdesk hybrid role")
    elif _is_field_tech_role(job_title) or _is_help_desk_role(job_title):
        preferred = next(
            (i for i, e in enumerate(exps) if isinstance(e, dict) and _is_btb_experience(e)),
            -1,
        )
        if preferred > 0:
            exps.insert(0, exps.pop(preferred))
            content["experience"] = exps
            label = "field-tech" if _is_field_tech_role(job_title) else "technical support"
            notes.append(f"reordered experience: BTB first for {label} role")
        elif preferred == -1:
            notes.append("experience reorder skipped: BEAT THE BOMB entry not found")
    return notes


# Skills that career_master.md Section 3 (Honest limits) explicitly forbids
# claiming on a resume. Hand-curated from the §3 "Do not claim" directives;
# each entry is a NORMALIZED skill key (lowercased, single-spaced) that
# `strip_honest_limit_violations` matches against `skills.technical` after
# `_norm_skill_key()`. Match is substring-based so variants like
# "Linux Server Administration" / "Linux Server Troubleshooting" / "Linux
# Photon Servers" all hit the same rule. KEEP THIS LIST NARROW — every
# entry should trace to a documented §3 honest-limit, not a guess.
#
# Recurring overclaim instances that motivated this list (3+ builds each):
#   - Linux administration/server claims (§3 L181 — RustDesk install only)
#   - iOS/Android mobile support (§3 L179 — incidental only)
#   - Active Directory administration (§3 L165 — gpedit doesn't transfer)
_HONEST_LIMIT_FORBIDDEN_SKILL_KEYS: Tuple[str, ...] = (
    # Linux — production exposure limited to RustDesk install on game-backend host
    "linux server administration",
    "linux server troubleshooting",
    "linux server admin",
    "linux server support",
    "linux administration",
    "linux admin",
    "linux sysadmin",
    "linux photon servers",
    "linux photon server",
    "operator level linux",
    "operator-level linux",
    "linux service operations",
    "systemd administration",
    "bash scripting",       # §3 L176 — Carlos ran scripts, did not author
    "powershell scripting",  # §3 L176 — same
    "bash authoring",
    "powershell authoring",
    # Mobile — incidental guest-device assistance only at BTB
    "ios android mobile support",
    "ios/android mobile support",
    "ios mobile support",
    "android mobile support",
    "mobile device administration",
    "mobile device management",
    "iphone administration",
    "ios administration",
    "android administration",
    "mobile fleet management",
    "ios android device fleet",
    "intune administration",  # MDM — §3 L168
    "jamf administration",
    "workspace one administration",
    # Active Directory — gpedit (Local GPO) does NOT transfer per §3 L134, L165
    "active directory administration",
    "active directory admin",
    "ad administration",
    "gpmc administration",
    "domain gpo",
    # Hypervisor at production scale — VirtualBox/WSL home-lab only per §3 L167
    "esxi administration",
    "vsphere administration",
    "hyper-v administration",
    "proxmox administration",
    "vmware administration",
    # Enterprise IAM per §3 L168
    "okta administration",
    "entra administration",
    "azure ad administration",
    "sailpoint administration",
    # Cloud at engineer level per §3 L169
    "aws administration",
    "aws engineer",
    "azure engineer",
    "gcp engineer",
    "cloud engineer",
    # SIEM per §3 L170
    "splunk administration",
    "splunk engineer",
    "sentinel administration",
    "datadog administration",
    # Unity development per §3 L178 — file-management only
    "unity development",
    "unity developer",
    "c# game scripting",
    "game development",
    # SCCM/MECM/WSUS per §3 L166
    "sccm administration",
    "mecm administration",
    "wsus administration",
    # Cisco IOS CLI per §3 L187
    "cisco ios cli",
    "cisco ios proficiency",
    "cisco ios administration",
    "junos cli",
    "junos administration",
    # ServiceNow admin per §3 (specialist platform admin reject)
    "servicenow administration",
    "servicenow admin",
)


def strip_honest_limit_violations(content: Dict[str, Any]) -> List[str]:
    """Filter `skills.technical` against the hand-curated career_master.md §3
    Honest-limits deny-list. Removes skill items that match a forbidden phrase
    (substring on the normalized key) so the LLM tailor can't accidentally
    surface overclaims pulled from consolidated_profile.json tools/skills lists.

    Why this exists: the resume LLM has a prompt-level HONEST LIMITS RULE
    (cache_prefix.py / cover_letter_tailor.py), but it still routinely emits
    "Linux Server Administration" / "iOS/Android Mobile Support" because those
    phrases appear in the tools list of consolidated_profile.json. This is a
    deterministic post-emit gate that fires AFTER the LLM and BEFORE render.

    Returns a list of `notes` describing what was dropped (for build logs).
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return notes
    tech = sk.get("technical")
    if not isinstance(tech, list):
        return notes

    forbidden = _HONEST_LIMIT_FORBIDDEN_SKILL_KEYS
    new_tech: List[str] = []
    for item in tech:
        raw = str(item or "").strip()
        if not raw:
            continue
        key = _norm_skill_key(raw)
        # Substring match — catches "Linux Server Administration",
        # "Linux Server Troubleshooting", "Linux Photon Servers" all via the
        # "linux server" or "linux photon" key prefix.
        matched = next(
            (f for f in forbidden if f and (f in key or key in f)),
            None,
        )
        if matched:
            notes.append(
                f"honest_limits: dropped '{raw}' from skills.technical "
                f"(matches forbidden phrase '{matched}' per career_master.md §3)"
            )
            continue
        new_tech.append(raw)
    if new_tech != tech:
        sk["technical"] = new_tech
    return notes


def relocate_soft_skills_from_technical(content: Dict[str, Any]) -> List[str]:
    """Move misclassified soft-skill chips out of skills.technical."""
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    sk = content.get("skills")
    if not isinstance(sk, dict):
        return notes
    tech = sk.get("technical")
    if not isinstance(tech, list):
        return notes
    soft = sk.get("soft")
    if not isinstance(soft, list):
        soft = []
        sk["soft"] = soft
    soft_keys = {_norm_skill_key(str(s)) for s in soft}
    new_tech: List[str] = []
    for item in tech:
        raw = str(item or "").strip()
        if not raw:
            continue
        key = _norm_skill_key(raw)
        if key in _SOFT_SKILLS_MISPLACED_IN_TECHNICAL:
            canon = canonicalize_skill(raw) or raw
            canon_key = _norm_skill_key(canon)
            if canon_key not in soft_keys:
                soft.append(canon)
                soft_keys.add(canon_key)
            notes.append(f"moved '{raw}' from technical to soft skills")
        else:
            new_tech.append(raw)
    if new_tech != tech:
        sk["technical"] = new_tech
    return notes


def reorder_cover_letter_btb_before_gotjunk(
    content: Dict[str, Any], job_title: str = ""
) -> List[str]:
    """For service-desk targets, BEAT THE BOMB evidence must precede GOT-JUNK."""
    notes: List[str] = []
    if not isinstance(content, dict) or content.get("error"):
        return notes
    if not _is_help_desk_role(job_title) or _is_admin_hybrid_role(job_title):
        return notes

    opening = str(content.get("opening") or "").strip()
    bodies = content.get("body_paragraphs")
    if not isinstance(bodies, list):
        return notes
    body_texts = [str(p or "").strip() for p in bodies if str(p or "").strip()]

    def _first_employer(parts: List[str]) -> Tuple[Optional[int], Optional[str]]:
        for idx, text in enumerate(parts):
            if _BTB_TEXT_RE.search(text):
                return idx, "btb"
            if _GJ_TEXT_RE.search(text):
                return idx, "gj"
        return None, None

    all_parts = ([opening] if opening else []) + body_texts
    emp_idx, emp_type = _first_employer(all_parts)
    if emp_type != "gj":
        return notes

    first_btb_body = next(
        (i for i, p in enumerate(body_texts) if _BTB_TEXT_RE.search(p)),
        None,
    )
    if first_btb_body is None:
        notes.append("cover letter leads with GOT-JUNK but no BTB paragraph to promote")
        return notes

    btb_para = body_texts[first_btb_body]

    if emp_idx == 0 and opening and _GJ_TEXT_RE.search(opening) and not _BTB_TEXT_RE.search(opening):
        content["opening"] = btb_para
        body_texts[first_btb_body] = opening
        notes.append("swapped cover letter opening with BTB paragraph (GOT-JUNK was leading)")
    else:
        first_gj_body = next(
            (i for i, p in enumerate(body_texts) if _GJ_TEXT_RE.search(p)),
            None,
        )
        if first_gj_body is not None and first_btb_body > first_gj_body:
            body_texts.pop(first_btb_body)
            body_texts.insert(first_gj_body, btb_para)
            notes.append("reordered cover letter: BTB evidence before GOT-JUNK")

    content["body_paragraphs"] = body_texts
    return notes


def dedupe_project_description_impact(content: Dict[str, Any]) -> List[str]:
    """Strip project impact when it duplicates the description verbatim or near-verbatim.

    U.S. Courts leak: the LLM produced a project entry where the `impact` field
    was a verbatim copy of the `description` ("Built a modular Python-based
    job-application pipeline..." in both). Replace the duplicated impact with a
    short, meaningful fallback so the resume doesn't read as auto-generated.
    """
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    projs = content.get("projects")
    if not isinstance(projs, list):
        return notes

    def _normalize(s: str) -> str:
        # Lowercase, collapse whitespace, drop leading "Impact:" / "Built ...".
        out = re.sub(r"\s+", " ", (s or "").strip().lower())
        out = re.sub(r"^(?:impact[:\-]\s*)", "", out)
        return out.strip()

    for proj in projs:
        if not isinstance(proj, dict):
            continue
        desc = str(proj.get("description") or "").strip()
        impact = str(proj.get("impact") or "").strip()
        if not desc or not impact:
            continue
        desc_norm = _normalize(desc)
        impact_norm = _normalize(impact)
        if not desc_norm or not impact_norm:
            continue
        # Exact match OR impact is a strict prefix of description (>=20 chars overlap).
        is_dup = (
            desc_norm == impact_norm
            or (len(impact_norm) >= 20 and desc_norm.startswith(impact_norm))
            or (len(desc_norm) >= 20 and impact_norm.startswith(desc_norm))
        )
        if not is_dup:
            continue
        # Replace with a generic but non-empty impact that doesn't duplicate.
        proj["impact"] = (
            "Demonstrates workflow design, documentation habits, and practical "
            "automation for repeatable processes."
        )
        name = str(proj.get("name") or "project")
        notes.append(f"replaced duplicated impact (matched description) for {name}")
    return notes


def strip_claim_audit_from_resume(content: Dict[str, Any]) -> List[str]:
    """Strip internal truth-check phrasing from summary, bullets, and projects."""
    notes: List[str] = []
    summary = str(content.get("summary") or "")
    if summary:
        fixed, n = strip_claim_audit_in_text(summary)
        if fixed != summary:
            content["summary"] = fixed
        notes.extend(n)

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
                fixed, n = strip_claim_audit_in_text(str(b))
                new_bullets.append(fixed)
                notes.extend(n)
            exp["bullets"] = new_bullets

    projs = content.get("projects")
    if isinstance(projs, list):
        for proj in projs:
            if not isinstance(proj, dict):
                continue
            for field in ("description", "impact"):
                val = str(proj.get(field) or "")
                if val:
                    fixed, n = strip_claim_audit_in_text(val)
                    proj[field] = fixed
                    notes.extend(n)
    return notes


def polish_evidence_metric_bullets(content: Dict[str, Any]) -> List[str]:
    """Replace raw evidence metric snippets with polished recruiter-facing bullets."""
    notes: List[str] = []
    db = load_evidence_db()
    employers = db.get("employers") if isinstance(db.get("employers"), dict) else {}
    pairs: List[Tuple[str, str, Set[str]]] = []
    for key, rec in employers.items():
        if not isinstance(rec, dict):
            continue
        raw_metrics = rec.get("metrics") or []
        display = rec.get("metric_display") or []
        for idx, raw in enumerate(raw_metrics):
            raw_s = str(raw or "").strip()
            if not raw_s:
                continue
            disp = str(display[idx]).strip() if idx < len(display) else raw_s
            if disp.lower() == raw_s.lower():
                continue
            pairs.append((raw_s, disp, _metric_signature(raw_s)))

    exps = content.get("experience")
    if not isinstance(exps, list) or not pairs:
        return notes

    for exp in exps:
        if not isinstance(exp, dict):
            continue
        bullets = exp.get("bullets")
        if not isinstance(bullets, list):
            continue
        new_bullets: List[str] = []
        for b in bullets:
            b_str = str(b).strip()
            sig = _metric_signature(b_str)
            replaced = b_str
            for _raw, disp, raw_sig in pairs:
                if _signature_overlap(sig, raw_sig) >= 0.55:
                    replaced = disp
                    notes.append(f"polished metric bullet: {b_str[:50]}...")
                    break
            new_bullets.append(replaced)
        exp["bullets"] = new_bullets
    return notes


# ---------------------------------------------------------------------------
# Cover letter export guards
# ---------------------------------------------------------------------------

_HELP_DESK_TITLE_HINTS = (
    "help desk",
    "helpdesk",
    "service desk",
    "desktop support",
    "it support",
    "technical support",
    "tier 1",
    "tier 2",
    "tier-1",
    "tier-2",
    "desk technician",
    "support technician",
    "noc ",
    "noc technician",
)

_BTB_TEXT_RE = re.compile(r"\bbeat\s*the\s*bomb\b|\bbtb\b", re.IGNORECASE)
_GJ_TEXT_RE = re.compile(
    r"got\s*[\-\s]?junk|1[\-\s]?800[\-\s]?got[\-\s]?junk|1800gotjunk",
    re.IGNORECASE,
)

_SOFT_SKILLS_MISPLACED_IN_TECHNICAL: Set[str] = {
    "customer communication",
    "customer service",
    "verbal communication",
    "written communication",
    "problem-solving",
    "problem solving",
    "teamwork",
    "documentation",
}

_PIPELINE_TANGENT_RE = re.compile(
    r"job[- ]application pipeline|python[- ]based job|personal automation tool",
    re.IGNORECASE,
)

_CASUAL_CLOSING_RE = re.compile(
    r"if useful,\s*i can walk through",
    re.IGNORECASE,
)

# "What I bring from <Company>'s side of the work is..." — awkward LLM phrasing
# that surfaces when the model is trying to mirror the JD's perspective. Rewrite
# to the cleaner "What I bring to <Company> is..." or just "What I bring is..."
# Group 1 (when present) captures the company name. The `your` branch is listed
# FIRST so it wins under IGNORECASE — otherwise the company-name pattern's
# leading [A-Z] (case-insensitive) would swallow "your" as a company.
_AWKWARD_FROM_SIDE_RE = re.compile(
    r"\bwhat I bring from\s+(?:your|([A-Z][\w&.\- ]{0,40}?)(?:'s))\s+side of the work\b",
    re.IGNORECASE,
)

# Closing-intent sentence pattern: "I'd welcome a conversation...",
# "I would like to discuss...", "I welcome the opportunity...". Used to detect
# when the last body paragraph is already a closing AND the closing field is
# also a closing — i.e. the cover letter has two closings back-to-back.
_CLOSING_INTENT_RE = re.compile(
    r"\bI(?:'d|\s+would|\s+'?d)\s+(?:welcome|like)\b[^.!?]*"
    r"\b(?:conversation|discuss|opportunity|chat|chance)\b",
    re.IGNORECASE,
)

# Words that signal a "narrative/scene-setting" phrase the cover letter shouldn't
# repeat verbatim from the resume — e.g. "small-shop environment", "high-traffic
# facility", "live technical incidents". Tool/product names (Microsoft 365,
# RustDesk) and JD keywords are fine to repeat across docs.
_NARRATIVE_PHRASE_TAIL_WORDS: Set[str] = {
    "environment", "environments", "facility", "facilities",
    "settings", "setting", "scale", "scales", "context", "contexts",
    "incidents", "operations", "workflows", "shop", "shops", "team",
    "venue", "venues", "room", "rooms", "site", "sites",
}


def _extract_resume_text_blob(resume_content: Dict[str, Any]) -> str:
    """Concatenate resume fields the LLM can see — summary, bullets, projects."""
    if not isinstance(resume_content, dict):
        return ""
    parts: List[str] = []
    summary = resume_content.get("summary")
    if isinstance(summary, str):
        parts.append(summary)
    exps = resume_content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            bullets = exp.get("bullets")
            if isinstance(bullets, list):
                parts.extend(str(b) for b in bullets if str(b).strip())
    projs = resume_content.get("projects")
    if isinstance(projs, list):
        for p in projs:
            if not isinstance(p, dict):
                continue
            for field in ("description", "impact"):
                val = p.get(field)
                if isinstance(val, str) and val.strip():
                    parts.append(val)
    return " \n ".join(parts).lower()


def _find_narrative_phrases_in(text: str) -> Set[str]:
    """Return lowercased 2-4 word phrases ending in a narrative tail word.

    These are the candidates we'll dedupe across resume and cover letter.
    Anchored on the tail word (e.g. "environment") so we don't have to
    enumerate every possible adjective combo.
    """
    found: Set[str] = set()
    text_l = (text or "").lower()
    # Walk word positions; for each "narrative tail word", look at preceding
    # 1-3 words and record the phrase.
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'\-/]*", text_l)
    for i, tok in enumerate(tokens):
        if tok not in _NARRATIVE_PHRASE_TAIL_WORDS:
            continue
        # Build 2-, 3-, and 4-word phrases ending at position i.
        for back in (1, 2, 3):
            start = i - back
            if start < 0:
                continue
            phrase = " ".join(tokens[start : i + 1])
            # Skip if the phrase is dominated by stopwords.
            if any(w in {"the", "a", "an", "of", "in", "for"} for w in tokens[start:i]) \
                    and back == 1:
                continue
            found.add(phrase)
    return found


def strip_phrases_shared_with_resume(
    cl_content: Dict[str, Any], resume_content: Optional[Dict[str, Any]]
) -> List[str]:
    """Drop narrative phrases from the cover letter that already appear in the resume.

    Targets the "small-shop environment" leak: the LLM uses the same scene-
    setting phrase in both documents, which weakens presentation. Tool names,
    proper nouns, and JD keywords are NOT touched — only narrative phrases
    that end in a tail word like "environment" / "facility" / "incidents".

    The resume keeps the phrase (evidence belongs there); the cover letter
    has the phrase removed (and dangling articles tidied).
    """
    notes: List[str] = []
    if not isinstance(cl_content, dict) or not isinstance(resume_content, dict):
        return notes

    resume_blob = _extract_resume_text_blob(resume_content)
    if not resume_blob.strip():
        return notes

    cl_text_parts: List[Tuple[str, Any]] = []
    if isinstance(cl_content.get("opening"), str):
        cl_text_parts.append(("opening", cl_content["opening"]))
    bodies = cl_content.get("body_paragraphs")
    if isinstance(bodies, list):
        for idx, p in enumerate(bodies):
            if isinstance(p, str):
                cl_text_parts.append((f"body_paragraphs[{idx}]", p))
    if isinstance(cl_content.get("closing"), str):
        cl_text_parts.append(("closing", cl_content["closing"]))

    # Find narrative phrases in resume; those are the only candidates we'll
    # strip from the cover letter.
    resume_phrases = _find_narrative_phrases_in(resume_blob)
    if not resume_phrases:
        return notes

    def _strip_phrase(text: str, phrase: str) -> Tuple[str, bool]:
        """Remove the phrase (and an immediately preceding "in a"/"at the" etc)."""
        # Allow whitespace + punctuation flexibility between words of the phrase.
        words = phrase.split()
        pattern = r"\b" + r"[\W_]+".join(re.escape(w) for w in words) + r"\b"
        full = re.compile(
            r"(?:\b(?:in|at|across|for|on|within)\s+(?:a|an|the)?\s*)?" + pattern,
            re.IGNORECASE,
        )
        new = full.sub("", text)
        if new == text:
            return text, False
        # Tidy: collapse double spaces, fix orphan punctuation.
        new = re.sub(r"\s{2,}", " ", new)
        new = re.sub(r"\s+([,.;:])", r"\1", new)
        new = re.sub(r",\s*\.", ".", new)
        new = re.sub(r"\(\s*\)", "", new)
        return new.strip(), True

    for location, text in cl_text_parts:
        new_text = text
        changed_phrases: List[str] = []
        for phrase in resume_phrases:
            if not isinstance(new_text, str):
                continue
            # Cheap check: is the phrase present in the CL field (case-insensitive)?
            words = phrase.split()
            cheap = re.compile(
                r"\b" + r"[\W_]+".join(re.escape(w) for w in words) + r"\b",
                re.IGNORECASE,
            )
            if not cheap.search(new_text):
                continue
            new_text, changed = _strip_phrase(new_text, phrase)
            if changed:
                changed_phrases.append(phrase)

        if changed_phrases and new_text != text:
            # Write back the cleaned text.
            if location == "opening":
                cl_content["opening"] = new_text
            elif location == "closing":
                cl_content["closing"] = new_text
            elif location.startswith("body_paragraphs["):
                idx = int(location[len("body_paragraphs[") : -1])
                cl_content["body_paragraphs"][idx] = new_text
            notes.append(
                f"dropped resume-shared narrative phrase(s) from {location}: "
                + ", ".join(repr(p) for p in changed_phrases[:3])
            )
    return notes


def _is_help_desk_role(job_title: str) -> bool:
    t = (job_title or "").lower()
    return any(h in t for h in _HELP_DESK_TITLE_HINTS)


_DEFENSIVE_WHICH_IS_NOT_RE = re.compile(
    r",\s*which is [^.;!?]+,\s*not [^.;!?]+(?:\.|$)",
    re.IGNORECASE,
)
_DEFENSIVE_NOT_AD_RE = re.compile(
    r",\s*not Active Directory administration\.?",
    re.IGNORECASE,
)

# Missing-verb leak: "I also across hardware..." after a stripper or bad revise pass
# removed the verb ("worked", "resolved", etc.).
_BROKEN_I_ALSO_PREP_RE = re.compile(
    r"\b((?:And\s+)?I also)\s+(across|through|into|in|on|with|for|at|via|over)\b",
    re.IGNORECASE,
)

# Summary opens with pasted JD title: "Service Desk Technician - Digitech - Remote IT support..."
_SUMMARY_JOB_TITLE_LEAD_RE = re.compile(
    r"^(?:Service Desk Technician|Help Desk Technician|IT Support Specialist|Desktop Support)"
    r"(?:\s*[-–—]\s*[^,.]{1,50}?){0,4}\s+"
    r"(?=IT support|hands-on|with hands-on|candidate|professional)",
    re.IGNORECASE,
)


def fix_broken_missing_verb_prose(text: str) -> Tuple[str, List[str]]:
    """Repair 'I also across ...' and similar missing-verb sentence fragments."""
    notes: List[str] = []
    out = (text or "").strip()
    if not out or not _BROKEN_I_ALSO_PREP_RE.search(out):
        return out, notes

    def _repl(match: re.Match) -> str:
        prefix = match.group(1)
        prep = match.group(2)
        tail_start = match.end()
        tail = out[tail_start : tail_start + 80].lower()
        if prep.lower() == "across" and re.match(
            r"\s*(?:hardware|operating|network|systems|staff|layers|issues)",
            tail,
        ):
            return f"{prefix} resolved live incidents across"
        return f"{prefix} worked {prep}"

    new_out = _BROKEN_I_ALSO_PREP_RE.sub(_repl, out)
    if new_out != out:
        notes.append("repaired missing-verb fragment (I also + preposition)")
        out = new_out
    return out, notes


_ORPHAN_AND_I_RE = re.compile(
    r"(?<=[.!?])\s+And\s+(I\b)",
    re.IGNORECASE,
)


def scan_and_repair_prose(text: str) -> Tuple[str, List[str]]:
    """Deterministic grammar/syntax cleanup for export prose (summary, bullets, CL)."""
    notes: List[str] = []
    out = (text or "").strip()
    if not out:
        return out, notes

    fixed, n = fix_broken_missing_verb_prose(out)
    notes.extend(n)
    out = fixed

    collapsed = re.sub(r"\s{2,}", " ", out).strip()
    if collapsed != out:
        notes.append("collapsed extra whitespace in prose")
        out = collapsed

    de_orphan = _ORPHAN_AND_I_RE.sub(r" \1", out)
    if de_orphan != out:
        notes.append("normalized orphan 'And I' sentence boundary")
        out = de_orphan

    return out, notes


def run_grammar_guards_resume(content: Dict[str, Any]) -> List[str]:
    """Apply grammar repairs to resume summary and experience bullets."""
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes

    summary = str(content.get("summary") or "").strip()
    if summary:
        fixed, n = scan_and_repair_prose(summary)
        if fixed != summary:
            content["summary"] = fixed
        notes.extend(n)

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
                s = str(b).strip()
                fixed, n = scan_and_repair_prose(s)
                new_bullets.append(fixed)
                notes.extend(n)
            exp["bullets"] = new_bullets
    return notes


def run_grammar_guards_cover_letter(content: Dict[str, Any]) -> List[str]:
    """Apply grammar repairs to cover letter opening, body, and closing."""
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes

    for field in ("opening", "closing"):
        val = str(content.get(field) or "")
        if val:
            fixed, n = scan_and_repair_prose(val)
            if fixed != val:
                content[field] = fixed
            notes.extend(n)

    bodies = content.get("body_paragraphs")
    if isinstance(bodies, list):
        new_bodies: List[str] = []
        for p in bodies:
            fixed, n = scan_and_repair_prose(str(p))
            new_bodies.append(fixed)
            notes.extend(n)
        content["body_paragraphs"] = new_bodies
    return notes


def strip_job_title_leak_from_summary(
    content: Dict[str, Any], job_title: str = ""
) -> List[str]:
    """Remove pasted JD title prefix from resume summary opener."""
    notes: List[str] = []
    if not isinstance(content, dict):
        return notes
    summary = str(content.get("summary") or "").strip()
    if not summary:
        return notes
    new_summary = _SUMMARY_JOB_TITLE_LEAD_RE.sub("", summary).strip()
    stripped_prefix = new_summary != summary
    if stripped_prefix:
        new_summary = re.sub(
            r"^(?:Remote\s+)?IT support professional\s+",
            "",
            new_summary,
            flags=re.IGNORECASE,
        ).strip()
        if new_summary and new_summary[0].islower():
            new_summary = new_summary[0].upper() + new_summary[1:]
        # Compound titles like "Field Service Technician – Remote/Travel-Based"
        # need ALL separators stripped (em-dash, slash, hyphen) — splitting on
        # only "-" produced "Field Service Technician – Remote/Travel" which
        # then mashed into "Field Service Technician Remote Travel candidate".
        raw_role = (job_title or "Service Desk Technician")
        # Truncate at the first separator: any em-dash, en-dash, hyphen, slash,
        # comma, or parenthesis — whichever appears first.
        role = re.split(r"[—–\-/,(]", raw_role, maxsplit=1)[0].strip()
        if not role:
            role = "Service Desk Technician"
        role_candidate = f"{role} candidate"
        if not new_summary.lower().startswith(role_candidate.lower()):
            if new_summary.lower().startswith("with "):
                new_summary = f"{role_candidate} {new_summary}"
            else:
                body = new_summary[0].lower() + new_summary[1:] if new_summary else ""
                # Defensive: body may already begin with "with " (or "with hands-on ")
                # from an earlier scaffold pass. Trim it before we prepend our own.
                body = re.sub(r"^(?:with\s+hands-on\s+|with\s+)", "", body, flags=re.IGNORECASE)
                new_summary = f"{role_candidate} with hands-on {body}"
    elif new_summary and new_summary[0].islower():
        new_summary = new_summary[0].upper() + new_summary[1:]
    # Belt and suspenders: collapse "with hands-on with " and "hands-on with hands-on" duplicates
    # regardless of which code path produced the summary.
    new_summary = re.sub(
        r"\bwith\s+hands-on\s+with\s+",
        "with hands-on ",
        new_summary,
        flags=re.IGNORECASE,
    )
    new_summary = re.sub(
        r"\bhands-on\s+hands-on\b",
        "hands-on",
        new_summary,
        flags=re.IGNORECASE,
    )
    if new_summary != summary:
        content["summary"] = new_summary
        notes.append("removed pasted job-title prefix from summary")
    return notes


def fix_broken_prose_in_cover_letter(content: Dict[str, Any]) -> List[str]:
    """Run grammar repair across cover letter fields (legacy alias)."""
    return run_grammar_guards_cover_letter(content)


def strip_defensive_disclaimer_clause(text: str) -> Tuple[str, List[str]]:
    """Remove trailing 'which is X, not Y' audit clauses from cover-letter prose."""
    notes: List[str] = []
    out = (text or "").strip()
    if not out:
        return out, notes
    prev = out
    out = _DEFENSIVE_WHICH_IS_NOT_RE.sub("", out)
    out = _DEFENSIVE_NOT_AD_RE.sub("", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    if out and out[-1] not in ".!?":
        out += "."
    if out != prev:
        notes.append("removed defensive disclaimer clause from text")
    return out, notes


def run_cover_letter_guards(
    content: Dict[str, Any],
    *,
    job_title: str = "",
    company: str = "",
) -> List[str]:
    """Deterministic cover-letter cleanup before export."""
    notes: List[str] = []
    if not isinstance(content, dict) or content.get("error"):
        return notes

    for field in ("opening", "closing"):
        val = str(content.get(field) or "")
        if val:
            fixed, n = strip_claim_audit_in_text(val)
            fixed, n2 = strip_defensive_disclaimer_clause(fixed)
            if fixed != val:
                content[field] = fixed
            notes.extend(n)
            notes.extend(n2)

    bodies = content.get("body_paragraphs")
    if isinstance(bodies, list):
        kept: List[str] = []
        for p in bodies:
            para = str(p or "").strip()
            if not para:
                continue
            if _is_help_desk_role(job_title) and _PIPELINE_TANGENT_RE.search(para):
                notes.append("dropped Python pipeline tangent from cover letter body")
                continue
            fixed, n = strip_claim_audit_in_text(para)
            fixed, n2 = strip_defensive_disclaimer_clause(fixed)
            notes.extend(n)
            notes.extend(n2)
            kept.append(fixed)
        content["body_paragraphs"] = kept

    closing = str(content.get("closing") or "").strip()
    if closing and _CASUAL_CLOSING_RE.search(closing):
        team = (company or "your team").strip()
        content["closing"] = (
            f"I would welcome the opportunity to discuss how my hardware support, "
            f"Windows troubleshooting, ticketing, and documentation experience can support {team}."
        )
        notes.append("replaced casual cover letter closing with professional closing")

    # Rewrite "What I bring from <Company>'s side of the work is..." to
    # "What I bring to <Company> is..." across opening, body paragraphs, and
    # closing. The awkward phrasing came from a critique-loop revise pass.
    def _rewrite_from_side(s: str) -> Tuple[str, bool]:
        if not s or not _AWKWARD_FROM_SIDE_RE.search(s):
            return s, False
        def _sub(match: re.Match) -> str:
            co = (match.group(1) or "").strip()
            return f"What I bring to {co}" if co else "What I bring"
        return _AWKWARD_FROM_SIDE_RE.sub(_sub, s), True

    for field in ("opening", "closing"):
        val = str(content.get(field) or "")
        new_val, changed = _rewrite_from_side(val)
        if changed:
            content[field] = new_val
            notes.append(f"rewrote 'from <Co>'s side of the work' -> 'to <Co>' in {field}")

    bodies_for_rewrite = content.get("body_paragraphs")
    if isinstance(bodies_for_rewrite, list):
        rewritten = False
        new_bodies: List[str] = []
        for p in bodies_for_rewrite:
            new_p, changed = _rewrite_from_side(str(p))
            if changed:
                rewritten = True
            new_bodies.append(new_p)
        if rewritten:
            content["body_paragraphs"] = new_bodies
            notes.append("rewrote 'from <Co>'s side of the work' -> 'to <Co>' in body paragraphs")

    # Detect back-to-back closings: last body paragraph already has a "I'd welcome
    # a conversation" / "I'd like to discuss" sentence AND the closing field also
    # has one. Drop the closing-intent sentence from the body paragraph (keep
    # any preceding sentences in that paragraph). If the whole final body para
    # WAS just a closing, drop the whole paragraph.
    bodies_after = content.get("body_paragraphs")
    closing_after = str(content.get("closing") or "").strip()
    if (
        isinstance(bodies_after, list)
        and bodies_after
        and closing_after
        and _CLOSING_INTENT_RE.search(closing_after)
    ):
        last_body = str(bodies_after[-1] or "").strip()
        if last_body and _CLOSING_INTENT_RE.search(last_body):
            sentences = re.split(r"(?<=[.!?])\s+", last_body)
            kept_sentences = [
                s for s in sentences if s.strip() and not _CLOSING_INTENT_RE.search(s)
            ]
            if kept_sentences:
                bodies_after[-1] = " ".join(kept_sentences).strip()
                notes.append(
                    "stripped duplicate closing-intent sentence from final body paragraph"
                )
            else:
                bodies_after.pop()
                notes.append(
                    "dropped duplicate closing-intent paragraph from cover letter body"
                )
            content["body_paragraphs"] = bodies_after

    notes.extend(reorder_cover_letter_btb_before_gotjunk(content, job_title=job_title))
    notes.extend(run_grammar_guards_cover_letter(content))

    return notes


def run_pre_export_guards(
    content: Dict[str, Any],
    *,
    doc_type: str,
    job_title: str = "",
    company: str = "",
) -> List[str]:
    """Final deterministic grammar gate before PDF/markdown export."""
    notes: List[str] = []
    if not isinstance(content, dict) or content.get("error"):
        return notes

    dtype = (doc_type or "").strip().lower()
    if dtype == "resume":
        notes.extend(run_grammar_guards_resume(content))
    elif dtype in ("cover_letter", "cover letter", "cl"):
        notes.extend(run_grammar_guards_cover_letter(content))
        notes.extend(
            run_cover_letter_guards(
                content,
                job_title=job_title,
                company=company,
            )
        )
    return notes


# ---------------------------------------------------------------------------
# Orchestrator — always-on
# ---------------------------------------------------------------------------

def run_integrity_guards(
    content: Dict[str, Any],
    *,
    job_title: str = "",
) -> List[str]:
    """
    Run all Phase 0 guards. Mutates content in place. Returns notes for the build log.
    Safe to call even when optimization is disabled.

    `job_title` enables role-aware guards (e.g. drop Python self-promo from summary
    on help-desk / service-desk builds, where the LLM tends to resurface it across
    recruiter/ATS passes despite prompt-level suppression).
    """
    notes: List[str] = []
    if not isinstance(content, dict) or content.get("error"):
        return notes

    notes.extend(strip_cross_job_metric_leaks(content))
    notes.extend(dedupe_intra_job_bullets(content))
    notes.extend(strip_vague_filler_bullets(content))
    notes.extend(expand_thin_photon_infrastructure_bullet(content, job_title))
    notes.extend(dedupe_project_description_impact(content))
    notes.extend(reorder_experience_by_role_family(content, job_title))
    notes.extend(reorder_bullets_niche_last(content, job_title))
    notes.extend(split_dense_compound_bullets(content))
    notes.extend(ensure_core_soft_skills_for_support_role(content, job_title))
    notes.extend(ensure_field_tech_skills(content, job_title))
    notes.extend(collapse_light_exposure_label_dump_in_summary(content))
    notes.extend(reorder_skills_function_first(content, job_title))
    notes.extend(relocate_soft_skills_from_technical(content))
    # Honest-limits deny gate — drops Linux admin / iOS-Android mobile / AD
    # admin / etc. that the LLM tailor keeps surfacing despite prompt
    # instructions. This is the deterministic fallback for the recurring
    # overclaim pattern documented across 6+ builds today.
    notes.extend(strip_honest_limit_violations(content))
    notes.extend(strip_claim_audit_from_resume(content))
    notes.extend(strip_verb_noun_doubleups_in_resume(content))
    notes.extend(strip_ad_gpedit_equivalence(content))
    notes.extend(strip_duplicate_phrases_from_summary(content))
    notes.extend(strip_job_title_leak_from_summary(content, job_title))
    notes.extend(cap_summary_sentence_count(content))
    notes.extend(flag_summary_below_minimum(content))
    notes.extend(strip_python_self_promo_from_summary(content, job_title))
    notes.extend(polish_evidence_metric_bullets(content))
    notes.extend(clean_summary(content))
    notes.extend(run_grammar_guards_resume(content))

    skills = content.get("skills")
    if isinstance(skills, dict):
        for field in ("technical", "soft"):
            arr = skills.get(field)
            if isinstance(arr, list):
                cleaned, sk_notes = dedupe_skills_semantic(arr)
                if cleaned != arr:
                    skills[field] = cleaned
                notes.extend(sk_notes)
    return notes


# ---------------------------------------------------------------------------
# Truth-limit scrub for summary CARDS (why_match / gaps / headline)
#
# Background: the summarizer LLM does not see evidence.json truth_limits and
# routinely produces "Skills include ... Active Directory basics ..." even
# though the candidate has explicitly NO AD experience. The scrubber below
# is the deterministic gate that strips those claims from the card BEFORE
# it's persisted (and during a backfill pass for already-persisted rows).
#
# Patterns are derived from evidence.json truth_limits entries. When you add
# a new truth_limit to evidence.json, add the corresponding pattern here.
# ---------------------------------------------------------------------------

_NO_CLAIM_PHRASES: Tuple[str, ...] = (
    # Active Directory family — evidence.json says NO experience.
    r"\bActive\s+Directory(?:\s+(?:basics|administration|admin|management|knowledge|skills?|experience|support))?",
    r"\bAD[-\s]joined\s+(?:support|administration|management|devices?|endpoints?)",
    r"\bAD[-\s]based\s+(?:account\s+management|administration|support)",
    # MFA/SSO family — evidence.json says NO experience configuring/admin/troubleshoot.
    r"\bMFA\s*/\s*SSO\b",
    r"\b(?:configuring|administering|troubleshooting|managing)\s+(?:MFA|SSO)\b",
    r"\bidentity\s+provider\s+(?:administration|management|configuration)",
    # M365 admin scope — evidence.json says USE + END-USER SUPPORT only.
    r"\bM365\s+(?:tenant\s+)?admin(?:istration)?",
    r"\bMicrosoft\s+365\s+(?:tenant\s+)?admin(?:istration|\s+center)?",
    r"\bExchange\s+admin(?:istration)?",
    r"\bTeams\s+admin(?:istration)?",
    r"\bSharePoint\s+admin(?:istration)?",
    # Account lifecycle — evidence.json says SUPPORT-LEVEL ONLY.
    r"\baccount\s+(?:creation|provisioning|deactivation|lifecycle)\s*(?:and|/|,|management|workflows?)?",
    r"\bfull\s+account\s+lifecycle",
    r"\bpermission\s+management\b",
    # Local Group Policy / GPO — evidence.json says NO experience.
    r"\bLocal\s+Group\s+Policy(?:\s+Editor)?",
    r"\bgroup\s+policy\s+(?:editor|configuration|management|object|objects)",
    r"\bgpedit(?:\.msc)?",
    r"\bGPO\s+(?:configuration|management|enforcement)",
    r"\bper[-\s]machine\s+update\s+scheduling\b",
    # Routing tables / network routes — evidence.json says NO experience.
    r"\b(?:route|routing\s+table)\s+(?:adjustments?|changes?|modifications?)",
    r"\bon[-\s]the[-\s]fly\s+(?:IP\s+and\s+)?route\s+adjustments?",
    r"\b(?:BGP|OSPF|routing\s+protocol)",
)


def _compile_no_claim_res() -> List["re.Pattern[str]"]:
    return [re.compile(p, re.IGNORECASE) for p in _NO_CLAIM_PHRASES]


_NO_CLAIM_COMPILED: List["re.Pattern[str]"] = _compile_no_claim_res()


def scrub_no_claim_terms(text: str) -> Tuple[str, List[str]]:
    """Strip evidence.json-forbidden claims from a single text field.

    Two-stage:
      1. List-strip: matches the forbidden phrase when it appears inside a
         comma-separated list (", X basics, "), removes just that item, and
         cleans up dangling commas / "and ,".
      2. Sentence-drop: any sentence that still mentions a forbidden phrase
         after stage 1 is dropped entirely. Conservative — we'd rather lose
         a sentence than let a false claim ship.

    Idempotent. Returns (scrubbed_text, notes).
    """
    notes: List[str] = []
    out = (text or "").strip()
    if not out:
        return out, notes

    # Stage 1: strip from comma-lists.
    for rgx in _NO_CLAIM_COMPILED:
        phrase = rgx.pattern
        # Match the phrase preceded by a comma/semicolon (with optional " and ")
        # and followed by a comma/semicolon/period.
        list_pat = re.compile(
            rf"\s*,\s*(?:and\s+)?(?:{phrase})\s*(?=[,;.]|$)",
            re.IGNORECASE,
        )
        new, n_list = list_pat.subn("", out)
        if n_list:
            notes.append(f"no-claim list-strip: {phrase[:40]}... ({n_list}x)")
            out = new
        # Also match at the START of a list: "Skills include X basics, Y, Z."
        head_pat = re.compile(
            rf"((?:Skills?\s+(?:include|are|like|such\s+as)|include|including)\s+)(?:{phrase})\s*(?:,\s*and\s+|,\s*|\s+and\s+)",
            re.IGNORECASE,
        )
        new, n_head = head_pat.subn(r"\1", out)
        if n_head:
            notes.append(f"no-claim head-strip: {phrase[:40]}... ({n_head}x)")
            out = new

    # Cleanup list-strip artifacts.
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r",\s*\.", ".", out)
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r"\s+\.", ".", out)
    out = re.sub(r"\bincluding\s*\.", ".", out, flags=re.IGNORECASE)
    out = re.sub(r"\binclude\s*\.", ".", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip()

    # Stage 2: sentence-drop for residual mentions.
    sentences = re.split(r"(?<=[.!?])\s+", out)
    keep: List[str] = []
    for s in sentences:
        drop_phrase = None
        for rgx in _NO_CLAIM_COMPILED:
            if rgx.search(s):
                drop_phrase = rgx.pattern
                break
        if drop_phrase:
            notes.append(f"no-claim sentence-drop: ...{drop_phrase[:30]}...")
            continue
        keep.append(s)
    out = " ".join(keep).strip()

    return out, notes


# Only scrub fields that DESCRIBE THE CANDIDATE'S SKILLS (i.e. make claims).
# "gaps" / "friction" / "junk_reason" describe absences or mechanics; they're
# allowed to mention forbidden terms as things the candidate LACKS. Scrubbing
# those would destroy useful signal.
_CARD_TEXT_FIELDS_FOR_TRUTH_SCRUB: Tuple[str, ...] = (
    "why_match",
    "headline_one_line",
    "headline",
)


def scrub_card_no_claim_terms(card: Dict[str, Any]) -> List[str]:
    """Walk a summary CARD dict and scrub forbidden claims from each text field.

    Use this on freshly-generated cards (in summarize.py) and during a
    backfill over already-persisted rows. Idempotent.
    """
    notes: List[str] = []
    if not isinstance(card, dict):
        return notes
    for field in _CARD_TEXT_FIELDS_FOR_TRUTH_SCRUB:
        val = card.get(field)
        if not isinstance(val, str) or not val.strip():
            continue
        new, n = scrub_no_claim_terms(val)
        if new != val:
            card[field] = new
            for nt in n:
                notes.append(f"card.{field}: {nt}")
    return notes
