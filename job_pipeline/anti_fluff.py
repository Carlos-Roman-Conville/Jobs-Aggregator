"""Extended anti-fluff / red-flag detection and deterministic cleanup."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from job_pipeline.named_requirements import (
    HYPE_BANNED_WORDS,
    VAGUE_VERB_BANNED,
    find_hype_violations,
    find_jd_years_echo_violations,
    find_project_jargon_violations,
    find_vague_verb_violations,
)

RED_FLAG_PHRASES: Tuple[str, ...] = (
    "confident in my ability",
    "enthusiastic interest",
    "perfectly aligns",
    "perfect fit",
    "aligns perfectly",
    "aligns well with",
    "i am excited to apply",
    "i am writing to express",
    "wealth of experience",
    "proven track record",
    "at your earliest convenience",
    "fast-paced environment",
    "team player",
    "passionate about",
    "dynamic professional",
    "results-driven professional",
    "detail-oriented professional",
    "self-starter",
    "hit the ground running",
)

PREFERRED_VERBS: Tuple[str, ...] = (
    "built",
    "supported",
    "resolved",
    "documented",
    "reduced",
    "coordinated",
    "improved",
    "maintained",
    "troubleshot",
    "authored",
)

_REPLACEMENTS: Tuple[Tuple[str, str], ...] = (
    (r"\brevolutionized?\w*\b", "improved"),
    (r"\btransformed\b", "improved"),
    (r"\bleveraged\b", "used"),
    (r"\butilized\b", "used"),
    (r"\bspearheaded\b", "led"),
    (r"\bconfident in my ability to\b", "experience with"),
    (r"\benthusiastic interest in\b", "interest in"),
    (r"\bperfectly aligns with\b", "matches"),
    (r"\baligns perfectly with\b", "matches"),
    (r"\bproven track record of\b", "experience"),
    (r"\bpassionate about\b", "focused on"),
    # More AI-tell phrases the LLM keeps reaching for.
    (r"\bin\s+order\s+to\b", "to"),
    (r"\bgoing forward\b", ""),
    (r"\bmoving forward\b", ""),
    (r"\bat\s+the\s+end\s+of\s+the\s+day\b", ""),
    (r"\bwide\s+range\s+of\b", "many"),
    (r"\bwide\s+variety\s+of\b", "many"),
    (r"\bacross\s+the\s+board\b", "broadly"),
    (r"\bin\s+my\s+recent\s+role\b", "recently"),
    (
        r"\bIn\s+my\s+most\s+recent\s+(?:operations|support|technical|ops|management|admin|coordination)\s+(?:work|role|position)\s+at\s+([A-Z][\w\.&-]+(?:\s+[A-Z][\w\.&-]+){0,3})\b",
        r"At \1",
    ),
    (r"\bdeep\s+understanding\s+of\b", "experience with"),
    (r"\bsolid\s+understanding\s+of\b", "experience with"),
    (r"\bsolid\s+grasp\s+of\b", "experience with"),
    (r"\bdemonstrated\s+ability\s+to\b", "experience with"),
    (r"\bability\s+to\s+effectively\b", "ability to"),
    (r"\bability\s+to\s+successfully\b", "ability to"),
    (r"\bsuccessfully\s+(?=\w+ed)\b", ""),  # "successfully implemented" -> "implemented"
    (r"\beffectively\s+(?=\w+ed)\b", ""),  # "effectively managed" -> "managed"
    (r"\bdetail-oriented\b", "thorough"),
    (r"\bgo-to\s+person\b", "primary point of contact"),
    (r"\bsynergy\b", "coordination"),
    (r"\bsynergize\b", "align"),
    # Defensive background hedge — "While my background is not from..." dilutes
    # the cover letter's confidence. Drop the lead clause entirely.
    (
        r"\bwhile\s+my\s+background\s+is\s+not\s+(?:from|in)\s+[^,.;]{1,80}[,;]\s*",
        "",
    ),
    (
        r"\balthough\s+i\s+(?:do\s+not|don't)\s+have\s+formal\s+[^,.;]{1,60}[,;]\s*",
        "",
    ),
    # Generic cover-letter openings.
    (r"\bI\s+am\s+writing\s+to\s+apply\s+(?:for\s+)?\b", "I'm applying for "),
    (r"\bI\s+am\s+excited\s+to\s+apply\b", "I'm applying"),
    (r"\bIt\s+is\s+with\s+great\s+enthusiasm\b", "I'm interested"),
    (r"\bI\s+am\s+the\s+perfect\s+(?:fit|candidate)\b", "I match the role"),
    # "Helping users through" is generic LLM filler; force a specific noun.
    (r"\bat\s+your\s+earliest\s+convenience\b", "when convenient"),
    # Drop quantifier inflation.
    (r"\btens\s+of\s+(?:thousands|millions)\b", "many"),
    # Database-formatted date ranges leaking into bullets — Arize leak:
    # "Reduced lost-job rates by approximately 15% over the 2019-03 – 2021-06 period."
    # YYYY-MM looks unmistakably like a YAML/DB artifact in a candidate-facing bullet.
    (
        r"\s+(?:over|during|across|throughout|in)\s+the\s+\d{4}-\d{2}\s*[–—\-]\s*\d{4}-\d{2}\s+(?:period|timeframe|range|window)\.?",
        ".",
    ),
    (
        r"\s+(?:over|during|in)\s+the\s+\d{4}-\d{2}\s+(?:period|timeframe|month|window)\.?",
        ".",
    ),
    # Bare ", YYYY-MM – YYYY-MM" trailing — same DB-format leak in a different shape.
    (r",\s+\d{4}-\d{2}\s*[–—\-]\s*\d{4}-\d{2}\b\.?", "."),
    # Generic environment tails at end of sentences — the LLM closes every
    # summary with "...in fast-paced environments" / "in high-traffic
    # environments" / "in dynamic environments". These add no information.
    (r"\s+in\s+fast-paced\s+(?:environments?|settings?)\b", ""),
    (r"\s+in\s+dynamic\s+(?:environments?|settings?)\b", ""),
    (r"\s+in\s+high-traffic\s+(?:environments?|settings?)\b", ""),
    (r"\s+in\s+demanding\s+(?:environments?|settings?)\b", ""),
    # "Helping users through" is generic; the resume should say what specifically.
    # We don't auto-replace because we don't know the specific. Leaving as a
    # critique-loop signal only.
    # "Supported X support" / "Provided X support" double-up: the LLM treats
    # "user account support" as a noun phrase but fronts it with "Supported",
    # producing the awkward verb+noun-stem repeat. Drop the trailing "support"
    # ONLY when it's followed by sentence-list punctuation or a noun like
    # "work" / "services" / "roles" so we don't damage legitimate phrases
    # like "Supported colleagues with technical support during outages."
    (
        r"\b(Supported|Provided)\s+([\w-]+(?:\s+[\w-]+){1,3})\s+support(?=[,;.]|\s+(?:work|services?|roles?)\b)",
        r"\1 \2",
    ),
    # "handed scripts" / "handed MySQL scripts" reads as if the scripts were
    # passed off awkwardly. Career_master uses this as shorthand for "scripts
    # provided by another team," but it leaks into resumes/cover letters where
    # it's confusing. Rewrite to "provided" deterministically.
    (r"\b(ran|running|run)\s+handed\s+", r"\1 provided "),
    (r"\bhanded\s+(MySQL|SQL|Bash|PowerShell|shell|script)\s+scripts?\b", r"provided \1 scripts"),
    (r"\bhanded\s+scripts?\b", r"provided scripts"),
    # "as documented in prior [X] work" / "as evidenced in prior [X] work" — the
    # LLM uses this as a defensive justification when surfacing a keyword it
    # doesn't have strong proof for ("...brings practical support for VPN, as
    # documented in prior technical operations work."). Strip the hedge; the
    # claim either stands on its own or shouldn't be there.
    # "operations" deliberately NOT in the closing-keyword set — in this idiom
    # it always precedes "work"/"role" (e.g. "technical operations work").
    (
        r",?\s*(?:as|per)\s+(?:documented|evidenced|noted|reflected|described|shown)\s+"
        r"in\s+(?:prior|previous|earlier|past|the)?\s*[\w\s/-]{0,40}?"
        r"\b(?:work|roles?|experience|positions?|engagements?)\b\.?",
        "",
    ),
    # "at the user level only" / "at the end-user level only" — defensive truth-
    # checking that leaked into a U.S. Courts cover letter ("printer/copier
    # incidents at the user level only when they came up"). The "only" qualifier
    # weakens the sentence. Strip the hedge.
    (
        r"\s*\bat\s+the\s+(?:end[- ]?)?user[- ]level\s+only\b",
        "",
    ),
    # ---- Calibration / audit-language leakage (2026-05-30 sweep) ----
    # "small bare-metal" / "basic facility-system" — size/depth hedges in front
    # of valid technical nouns. Strip the leading hedge, keep the noun.
    (r"\b(?:a\s+)?small\s+(?=bare[-\s]metal\b)", ""),
    (r"\b(?:a\s+)?basic\s+(?=facility[-\s]system\b)", ""),
    (r"\b(?:a\s+)?small\s+(?=server\s+setup\b)", ""),
    # "more consistently" / "more reliably" / "more efficiently" trailing weasels
    # — vague-improvement claims with no metric. Drop the qualifier.
    (r"\s+(?:more\s+)(?:consistently|reliably|efficiently|effectively)\b(?=[,.;]|\s+(?:and|or|than|for|to)\b|$)", ""),
    # "as a user" / "as an end user" qualifier — "Used Salesforce as a user
    # to log jobs" was the leak. The qualifier signals "I wasn't admin"
    # but in a finished bullet it just weakens the verb. Catch when followed
    # by sentence-list punctuation, end-of-sentence period, semicolon, or EOL.
    (r"\s+as\s+(?:a|an)\s+(?:end[-\s])?user\b(?=\s+to\b|\s+for\b|[,.;]|$)", ""),
    # "small-shop" hedge variant (hyphenated form of the "small bare-metal" case).
    (r"\bsmall-shop\s+", ""),
    # "with hands-on at <CompanyOrPhrase> with" — the LLM occasionally writes
    # the candidate summary as if the candidate worked at the TARGET company
    # (recently observed: "candidate with hands-on at CloudHaven Technologies
    # with 3 years of..."). The "at X with" double-preposition is grammatically
    # broken and the implication is false. Collapse to "with hands-on experience
    # with" — neutral, true, recruiter-friendly.
    (
        r"\bwith\s+hands-on\s+at\s+[\w&.,'\-/ ]{2,80}?\s+with\s+",
        "with hands-on experience with ",
    ),
    # "X Basics on Y" mid-skill hedge ("VLAN Basics on Cisco Switches" -> "VLAN
    # configuration on Cisco Switches"). "Basics" inside a skill string still
    # reads as a hedge.
    (r"\bBasics\s+on\b", "configuration on"),
    # Capitalized "X basics" mid-prose hedge ("supports Windows Server basics
    # and operational backup") — keep the noun, drop "basics".
    (r"\b([A-Z][\w/]+(?:\s+[A-Z][\w/]+){0,3})\s+basics\b", r"\1"),
    # "operational database support" / "operational X support" — vague
    # calibration phrase that crept into a summary line.
    (r"\boperational\s+database\s+support\b", "MySQL"),
    # "Across industrial and field settings" / "in industrial and field
    # environments" — Carlos's field/maintenance/military experience is real,
    # but BTB + hospitality + Army Reserve isn't truly "industrial" in the
    # plant/manufacturing sense. Drop "industrial and" so the surrounding
    # phrasing stays accurate.
    (
        r"\b(across|in|throughout)\s+industrial\s+and\s+field\s+(settings?|environments?|contexts?|roles?)",
        r"\1 field \2",
    ),
    # Bare "industrial environment" / "industrial setting" without anchoring
    # context — only rewrites when the claim attributes to the candidate,
    # NOT when the JD is being quoted.
    (
        r"\bI\s+(?:have\s+)?(?:worked|operated|served|managed)\s+in\s+industrial\s+(environments?|settings?)",
        r"I have worked in field \1",
    ),
    # "Field Service Technician with experience in remote and travel-based
    # technical operations" overclaims travel-based BTB experience (BTB was
    # site-based). Use a broader, defensible framing instead.
    (
        r"\bField\s+Service\s+Technician\s+with\s+experience\s+in\s+remote\s+and\s+travel-?based\s+technical\s+operations",
        "Technical Operations / Field Support Technician with experience supporting on-site, remote, and field-based operations",
    ),
    # Variants: "Field Service Technician candidate with hands-on remote and travel-based"
    (
        r"\bField\s+Service\s+Technician\s+candidate\s+with\s+hands-on\s+remote\s+and\s+travel-?based\s+",
        "Technical Operations / Field Support Technician candidate with hands-on on-site, remote, and field-based ",
    ),
    # Generic "remote and travel-based" -> "on-site, remote, and field-based".
    # Captures the preposition phrase ("grounded in", "experience in", etc.)
    # so we don't double up the verb.
    (
        r"\b(experience|grounded|skilled|background|expertise|rooted)\s+in\s+remote\s+and\s+travel-?based\b",
        r"\1 in on-site, remote, and field-based",
    ),
    # Bare "remote and travel-based <noun>" — rewrite to broader truthful phrasing.
    (
        r"\bremote\s+and\s+travel-?based\s+(field\s+support|technical\s+operations|operations|work)",
        r"on-site, remote, and field-based \1",
    ),
    # Defensive: "and field-based field support" duplicate-noun left by upstream
    # rewriters — collapse to single "field support".
    (
        r"\b(on-site, remote, and field-based)\s+field\s+(support|work|operations)\b",
        r"\1 \2",
    ),
    # "during the {role} phase" / "during the {role} stage" — calibration
    # phrasing leaked from a multi-role evolution at one employer.
    (
        r"\s+during\s+the\s+[A-Z][A-Za-z\s\-]+\s+(?:phase|stage|period)\b\.?",
        ".",
    ),
    # "Worked in helper, driver, and Junior Operations Coordinator
    # responsibilities" — internal calibration role-list leaked verbatim.
    # The whole bullet is audit-grade; drop it. Caller can detect empty
    # bullets and request a replacement.
    (
        r"^\s*Worked\s+in\s+[A-Za-z\s,]+responsibilities(?:\s+as\s+the\s+role\s+progressed)?\.?\s*$",
        "",
    ),
    # "during the Junior Operations Coordinator phase" inside a longer bullet.
    (
        r"\s+during\s+the\s+Junior\s+Operations\s+Coordinator\s+phase\b\.?",
        ".",
    ),
    # "hands-on N years of hands-on X" — LLM stutter where the modifier is
    # repeated on both sides of a years-of phrase. Collapse to one instance.
    # Was caught by the critique loop pre-disable; needed as deterministic
    # backstop now that critique is off by default.
    #
    # Allow "N", "N+", "N-M" (e.g. "3", "3+", "2-3") for the year count, and
    # tolerate "year" or "years". An earlier version only matched bare
    # "\d+ years" and missed the very common "3+ years" variant.
    (
        r"\bhands-on\s+(\d+(?:\s*[-+]\s*\d*)?\s+years?\s+of\s+)hands-on\s+",
        r"hands-on \1",
    ),
    # Same idea, opposite word order: "N years of hands-on hands-on" or
    # other simple double-modifier stutters at sentence start.
    (
        r"\bhands-on\s+hands-on\b",
        "hands-on",
    ),
    # "Made on-the-fly IP and route adjustments" — implies routing-table edits
    # (BGP/OSPF, gateway path) Carlos did NOT touch per evidence.json. Rewrite
    # to the safer scope he can defend: static IP / DNS / local network only.
    (
        r"\b(?:made|making)?\s*on[-\s]the[-\s]fly\s+(?:IP\s+and\s+)?route\s+adjustments?",
        "adjusted static IP, gateway, DNS, and local network settings",
    ),
    (
        r"\b(?:made|making)\s+(?:IP\s+and\s+)?route\s+adjustments?",
        "adjusted static IP, DNS, and local network settings",
    ),
    # Local Group Policy / Editor — never touched. Drop any sentence
    # mentioning it (mid-prose or otherwise). The skill-list scrubber
    # handles the skill-item form.
    (
        r"[^.!?]*\b(?:Local\s+)?[Gg]roup\s+[Pp]olicy(?:\s+Editor)?[^.!?]*[.!?]\s*",
        "",
    ),
    (
        r"[^.!?]*\bper[-\s]machine\s+update\s+scheduling[^.!?]*[.!?]\s*",
        "",
    ),
    (
        r"[^.!?]*\bgpedit(?:\.msc)?[^.!?]*[.!?]\s*",
        "",
    ),
    # Generic years-in-vague-role claims — e.g. "I also bring 4+ years in
    # cross-functional roles, which gives me a practical base for escalated
    # support work." The sentence makes a fuzzy claim without naming a role
    # or employer, weakening surrounding specifics. Drop the whole sentence.
    # Catches "N years", "N+ years", "N years in/of/across <vague> roles".
    (
        r"[^.!?]*\b\d+\+?\s*years?\s+(?:in|of|across)\s+"
        r"(?:cross[-\s]functional|varied|diverse|mixed|multiple|different)\s+roles?"
        r"[^.!?]*[.!?]\s*",
        "",
    ),
)


# Skill-item audit suffixes. Skills are rendered as comma-joined lists; we
# scrub each item BEFORE the join so the final string never carries hedge
# words. Match the suffix only when it terminates the item (end of string).
_SKILL_HEDGE_SUFFIX_RES: Tuple[re.Pattern, ...] = (
    re.compile(r"\s+Exposure$", re.IGNORECASE),
    re.compile(r"\s+Familiarity$", re.IGNORECASE),
    re.compile(r"\s+Basics$", re.IGNORECASE),
    re.compile(r"\s+Operational\s+Support$", re.IGNORECASE),
    re.compile(r"\s+\(user-level\)$", re.IGNORECASE),
)

# Over-specific brand SKUs the LLM emits when a generic name is correct.
_SKILL_OVERSPECIFIC_MAP: Dict[str, str] = {
    "veeam agent for microsoft windows": "Veeam",
    "veeam agent for windows": "Veeam",
}

# Skill items that should be DROPPED entirely from the skills list.
# Either vague ("Customer Service Systems"), untrue ("Local Group Policy"),
# or implying scope Carlos doesn't have.
_SKILL_DROP_PATTERNS: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"^Customer\s+Service\s+Systems?$", re.IGNORECASE),
    re.compile(r"^Local\s+Group\s+Policy(?:\s+Editor)?$", re.IGNORECASE),
    re.compile(r"^Group\s+Policy(?:\s+Editor|\s+Objects?)?$", re.IGNORECASE),
    re.compile(r"^GPO$", re.IGNORECASE),
    re.compile(r"^gpedit(?:\.msc)?$", re.IGNORECASE),
    # Industrial controls / instrumentation — Carlos doesn't have direct
    # I&C experience documented. LLMs surface these as JD-keyword bleed
    # for industrial field-tech roles.
    re.compile(r"^Instrumentation(?:\s+and\s+Controls?)?$", re.IGNORECASE),
    re.compile(r"^Controls$", re.IGNORECASE),
    re.compile(r"^I\s*&\s*C$", re.IGNORECASE),
    re.compile(r"^Industrial\s+Controls?$", re.IGNORECASE),
    re.compile(r"^Process\s+Controls?$", re.IGNORECASE),
    # Commissioning / System Commissioning — defensible as a forward-looking
    # field-tech action but not directly documented in career_master.md.
    # Dropping from skills; "I'd be commissioning systems in this role" can
    # stay implicit. Compounds like "Commissioning Reports" still pass.
    re.compile(r"^System\s+Commissioning$", re.IGNORECASE),
    re.compile(r"^Commissioning$", re.IGNORECASE),
    re.compile(r"^Equipment\s+Commissioning$", re.IGNORECASE),
)


def strip_skill_item_hedges(item: str) -> str:
    """Trim audit-language hedge suffixes and overspecific SKUs from one skill item.
    Returns empty string for items that should be DROPPED entirely (vague or
    forbidden by truth_limits)."""
    out = (item or "").strip().strip(",").strip()
    if not out:
        return out
    # Drop entirely if it matches a forbidden / vague pattern.
    for rgx in _SKILL_DROP_PATTERNS:
        if rgx.match(out):
            return ""
    overspec = _SKILL_OVERSPECIFIC_MAP.get(out.lower())
    if overspec:
        return overspec
    prev = None
    while prev != out:
        prev = out
        for rgx in _SKILL_HEDGE_SUFFIX_RES:
            out = rgx.sub("", out).strip()
    return out


def find_red_flags(text: str) -> List[str]:
    t = (text or "").lower()
    hits: List[str] = []
    for phrase in RED_FLAG_PHRASES + HYPE_BANNED_WORDS + VAGUE_VERB_BANNED:
        if phrase in t:
            hits.append(phrase)
    hits.extend(find_project_jargon_violations(text))
    return sorted(set(hits))


def strip_anti_fluff_in_text(text: str) -> Tuple[str, List[str]]:
    """Deterministic replacements for known AI-ish phrases.

    Also fixes casing drift the writer LLM introduces: brand names that
    have specific official casing (iCIMS, iOS, GitHub), all-caps
    acronyms ("Kpi" → "KPI"), and month-abbreviation inconsistency
    ("Sept" → "Sep"). These are deterministic — same input, same output —
    so they belong in the post-LLM scrubber rather than the prompt.
    """
    notes: List[str] = []
    out = text or ""
    for pattern, repl in _REPLACEMENTS:
        new_out, n = re.subn(pattern, repl, out, flags=re.IGNORECASE)
        if n:
            notes.append(f"anti-fluff: replaced {pattern} -> {repl} ({n}x)")
            out = new_out
    # Casing + format normalizers (applied after the main scrubbers so
    # they catch anything the replacements leave behind).
    out, casing_notes = _apply_casing_normalizers(out)
    notes.extend(casing_notes)
    return out, notes


# Brand names with non-standard casing. Each entry maps a regex pattern
# (case-insensitive whole-word) to the canonical brand string. We use
# whole-word boundaries to avoid mangling words that contain these as
# substrings ("ICIMS" but not "MEDICIMSAGE" — fictional but illustrates).
_BRAND_CASING: Dict[str, str] = {
    r"\bICIMS\b": "iCIMS",
    r"\bicims\b": "iCIMS",
    r"\bIcims\b": "iCIMS",
    r"\bIOS\b": "iOS",
    r"\bios\b": "iOS",  # contextual — careful not to hit "IOS" from "BIOS"
    r"\bMACOS\b": "macOS",
    r"\bMacos\b": "macOS",
    r"\bIPHONE\b": "iPhone",
    r"\bIphone\b": "iPhone",
    r"\bIPAD\b": "iPad",
    r"\bIpad\b": "iPad",
    r"\bIMAC\b": "iMac",
    r"\bImac\b": "iMac",
    r"\bGITHUB\b": "GitHub",
    r"\bGithub\b": "GitHub",
    r"\bGitlab\b": "GitLab",
    r"\bGITLAB\b": "GitLab",
    r"\bLINKEDIN\b": "LinkedIn",
    r"\bLinkedin\b": "LinkedIn",
    r"\bPAYPAL\b": "PayPal",
    r"\bPaypal\b": "PayPal",
    r"\bEBAY\b": "eBay",
    r"\bEbay\b": "eBay",
    r"\bITUNES\b": "iTunes",
    r"\bItunes\b": "iTunes",
    r"\bDEVOPS\b": "DevOps",
    r"\bDevops\b": "DevOps",
    r"\bMACBOOK\b": "MacBook",
    r"\bMacbook\b": "MacBook",
    r"\bPOWERPOINT\b": "PowerPoint",
    r"\bPowerpoint\b": "PowerPoint",
    r"\bONEDRIVE\b": "OneDrive",
    r"\bOnedrive\b": "OneDrive",
    r"\bSHAREPOINT\b": "SharePoint",
    r"\bSharepoint\b": "SharePoint",
}

# Acronyms that must stay all-caps. The LLM often title-cases these
# inside skill lists ("Kpi Tracking", "Api Integration"). Match
# case-insensitively but emit uppercase. Word-boundary anchored so we
# don't damage normal words that contain the letters.
_ACRONYM_PRESERVE: Tuple[str, ...] = (
    "KPI", "API", "SLA", "SSO", "MFA", "CRM", "ERP", "ATS",
    "TCP", "DNS", "IP", "VPN", "VLAN", "DHCP", "FTP", "SSH",
    "OS", "PC", "SOP", "CI", "CD", "ETL", "SQL", "NOSQL",
    "HTML", "CSS", "JSON", "XML", "YAML", "CSV", "PDF",
    "AWS", "GCP", "IT", "QA", "UI", "UX", "MVP",
    "CBRN", "CPR", "EMS", "PPE", "DOT", "OSHA",
    "USB", "GPU", "CPU", "RAM", "SSD", "HDD", "NIC",
    "RFID", "DMX", "OBS", "NUC",
    "M365",  # Microsoft 365 short form
)

# Month abbreviation: the LLM sometimes emits "Sept" (4 chars) for
# September while every other month uses 3 chars. Normalize to 3-char
# form to keep date strings visually consistent.
_MONTH_NORMALIZE: Dict[str, str] = {
    r"\bSept\b": "Sep",
    r"\bSEPT\b": "Sep",
}

# COHERENCE DETECTORS — broken-sentence patterns we've seen recur across
# builds. These are NOT auto-fixed (we don't know what the missing word
# should be) — they're *detected* by `find_coherence_breaks()` which
# returns a list of issues for the regression-check step to fail-loud on.
#
# Pattern catalog:
#   - "supported user so" — should be "supported user onboarding so" or
#     similar; LLM word-drop when listing two parallel objects.
#   - "kept a NN-to-NN-person running" — missing "team" / "shift".
#   - "the user[s] [verb]" trailing nothing — incomplete noun phrase.
_COHERENCE_BREAK_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (
        r"\bsupported\s+user\s+(?:so|and|while|to)\s+",
        "word-drop after 'supported user' (missing 'onboarding'/'accounts'/etc.)",
    ),
    (
        r"\bkept\s+a\s+\d+[\s\-–to]+\d+[\s\-–]*person\s+running\b",
        "missing noun after '<N>-person' (likely 'team' or 'shift')",
    ),
    (
        r"\bsupport(?:ed|ing)\s+user\s+(?:was|were|is|are)\b",
        "word-drop after 'support[ed/ing] user' before linking verb",
    ),
    # Generic floating-clause detector: a sentence-internal subject like
    # ", user," or ", workflow," with no verb attached.
)


def find_coherence_breaks(text: str) -> List[str]:
    """Return a list of human-readable issue descriptions when known
    broken-sentence patterns appear in text. Used by regression_check
    to fail-loud on artifacts that contain LLM word-drop coherence
    errors (which can't be auto-fixed deterministically because we
    don't know what word was dropped).
    """
    hits: List[str] = []
    src = text or ""
    for pattern, label in _COHERENCE_BREAK_PATTERNS:
        if re.search(pattern, src, flags=re.IGNORECASE):
            hits.append(label)
    return hits

# PST/EST/CST time-range format: the LLM sometimes emits hours as a
# parenthetical with a comma between start and end ("(06:00 AM, 03:30
# PM)") which reads as two timestamps rather than a range. Rewrite to
# a clean dash-separated range with explicit timezone tag.
#   Group 1 = leading "PST/EST/CST business hours" prefix
#   Groups 2-5 = HH MM HH MM of start/end times
#   Middle separator: matches "," "-" "–" "—" (each w/ optional whitespace),
#   or word forms "to" / "until" / "thru" / "through" (which need bounding
#   whitespace so they don't trigger on "tomorrow" etc.).
_TIME_RANGE_SEP = (
    r"(?:\s*,\s*|\s*[-–—]\s*|\s+to\s+|\s+until\s+|\s+thru\s+|\s+through\s+)"
)

_TIME_RANGE_FIXES: Tuple[Tuple[str, str], ...] = (
    # PST/EST/CST business-hours range: any separator → standardized dash + TZ.
    # Examples that all normalize to "(6:00 AM – 3:30 PM PST)":
    #   "(06:00 AM, 03:30 PM)"      ← original Solera/iCIMS LLM output
    #   "(06:00 AM to 03:30 PM PST)" ← variant that appeared after the first fix
    #   "(6:00 AM until 3:30 PM)"
    #   "(6:00 AM – 3:30 PM)"        ← missing TZ → add it
    (
        rf"(\bPST business hours\b)\s*\(0?(\d{{1,2}}):(\d{{2}})\s*AM{_TIME_RANGE_SEP}0?(\d{{1,2}}):(\d{{2}})\s*PM(?:\s*PST)?\)",
        r"\1 (\2:\3 AM – \4:\5 PM PST)",
    ),
    (
        rf"(\bEST business hours\b)\s*\(0?(\d{{1,2}}):(\d{{2}})\s*AM{_TIME_RANGE_SEP}0?(\d{{1,2}}):(\d{{2}})\s*PM(?:\s*EST)?\)",
        r"\1 (\2:\3 AM – \4:\5 PM EST)",
    ),
    (
        rf"(\bCST business hours\b)\s*\(0?(\d{{1,2}}):(\d{{2}})\s*AM{_TIME_RANGE_SEP}0?(\d{{1,2}}):(\d{{2}})\s*PM(?:\s*CST)?\)",
        r"\1 (\2:\3 AM – \4:\5 PM CST)",
    ),
    # Generic catch-all for "(HH:MM AM <sep> HH:MM PM)" without a TZ-context
    # word in front. Change separator to dash; don't invent a timezone.
    (
        rf"\(0?(\d{{1,2}}):(\d{{2}})\s*AM{_TIME_RANGE_SEP}0?(\d{{1,2}}):(\d{{2}})\s*PM\)",
        r"(\1:\2 AM – \3:\4 PM)",
    ),
)


def _apply_casing_normalizers(text: str) -> Tuple[str, List[str]]:
    """Apply brand-casing, acronym-preserve, month-abbrev, and time-range
    fixes. Run in this order so brand-casing claims its matches first
    (some brands include acronym substrings)."""
    notes: List[str] = []
    out = text or ""

    # Brand casing — explicit per-pattern emit.
    for pattern, replacement in _BRAND_CASING.items():
        new_out, n = re.subn(pattern, replacement, out)
        if n:
            notes.append(f"casing: {pattern} -> {replacement} ({n}x)")
            out = new_out

    # Acronyms — match the lowercase/title-case variants and emit upper.
    # Pattern: \b<lower>\b or \b<title>\b matches; we replace with the
    # uppercase form. Skip anything already uppercase (no-op match would
    # still fire and just rewrite to itself, harmless but noisy).
    for acro in _ACRONYM_PRESERVE:
        # Match title-case version (e.g. "Kpi") and lowercase ("kpi")
        # but NOT already-uppercase (avoid spurious replacements).
        title = acro.title()  # "Kpi"
        lower = acro.lower()  # "kpi"
        if title != acro:
            new_out, n = re.subn(rf"\b{re.escape(title)}\b", acro, out)
            if n:
                notes.append(f"acronym: {title} -> {acro} ({n}x)")
                out = new_out
        if lower != acro:
            # Lowercase variants are riskier (could appear inside normal
            # prose as a common word). Limit to skill-list-style contexts
            # by requiring an adjacent capital letter or comma — these
            # are the contexts where the LLM uses the lowercase form.
            new_out, n = re.subn(
                rf"(?<=[A-Z,]\s)\b{re.escape(lower)}\b",
                acro,
                out,
            )
            if n:
                notes.append(f"acronym: {lower} -> {acro} ({n}x)")
                out = new_out

    # Month abbreviation normalization.
    for pattern, repl in _MONTH_NORMALIZE.items():
        new_out, n = re.subn(pattern, repl, out)
        if n:
            notes.append(f"month: {pattern} -> {repl} ({n}x)")
            out = new_out

    # Time-range punctuation fixes.
    for pattern, repl in _TIME_RANGE_FIXES:
        new_out, n = re.subn(pattern, repl, out)
        if n:
            notes.append(f"time-range: comma -> dash ({n}x)")
            out = new_out

    return out, notes


def strip_anti_fluff_content(content: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Apply anti-fluff cleanup across resume JSON content."""
    notes: List[str] = []
    if content.get("error"):
        return content, notes

    summary = str(content.get("summary") or "")
    if summary:
        fixed, n = strip_anti_fluff_in_text(summary)
        content["summary"] = fixed
        notes.extend(n)

    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            bullets = exp.get("bullets")
            if isinstance(bullets, list):
                new_bullets = []
                for b in bullets:
                    fixed, n = strip_anti_fluff_in_text(str(b))
                    fixed = fixed.strip()
                    if fixed:  # drop bullets that scrubbed down to nothing
                        new_bullets.append(fixed)
                    notes.extend(n)
                exp["bullets"] = new_bullets
            desc = exp.get("description")
            if isinstance(desc, str) and desc:
                fixed, n = strip_anti_fluff_in_text(desc)
                exp["description"] = fixed
                notes.extend(n)
            # Structured fields that previously bypassed the scrubber.
            # `duration` is where date-format drift like "Sept 2024 – Mar 2026"
            # was surviving — the LLM writes the string here directly and the
            # scrubber never touched it. `title` + `company` similarly need
            # brand-casing fixes (e.g. "ICIMS" should still become "iCIMS"
            # even when the field is the experience title, not a bullet).
            for struct_key in ("duration", "title", "company"):
                val = exp.get(struct_key)
                if isinstance(val, str) and val:
                    fixed, n = strip_anti_fluff_in_text(val)
                    exp[struct_key] = fixed
                    notes.extend(n)

    skills = content.get("skills")
    if isinstance(skills, dict):
        for bucket_key in ("technical", "tools", "soft"):
            items = skills.get(bucket_key)
            if not isinstance(items, list):
                continue
            cleaned: List[str] = []
            for raw in items:
                s = strip_skill_item_hedges(str(raw))
                s, n = strip_anti_fluff_in_text(s)
                s = s.strip().strip(",").strip()
                if s:
                    cleaned.append(s)
                notes.extend(n)
            skills[bucket_key] = cleaned

    projs = content.get("projects")
    if isinstance(projs, list):
        for p in projs:
            if not isinstance(p, dict):
                continue
            for field in ("description", "impact"):
                val = str(p.get(field) or "")
                if val:
                    fixed, n = strip_anti_fluff_in_text(val)
                    p[field] = fixed
                    notes.extend(n)

    return content, notes


def scrub_yaml_text(yaml_text: str) -> Tuple[str, List[str]]:
    """Final-gate scrubber that runs against the raw YAML right before write+render.

    This catches any audit-language hedge that survived earlier stages (e.g.
    skills strings produced by a separate sanitizer, summary phrases injected
    after content-level cleanup). Per project rule, audit phrasing must NEVER
    reach a final PDF — this is the idempotent guard at the export boundary.

    Applies replacements LINE BY LINE so patterns containing `\\s+` can never
    bridge across YAML lines and destroy structure.
    """
    notes: List[str] = []
    out = yaml_text or ""
    if not out:
        return out, notes

    details_re = re.compile(r'^(\s*details:\s*"\s*)([^"\n]+?)(\s*"\s*)$')

    new_lines: List[str] = []
    for line in out.splitlines(keepends=True):
        eol = ""
        body = line
        if body.endswith("\n"):
            eol = "\n"
            body = body[:-1]
        # Skip empty / structural lines fast.
        if not body.strip():
            new_lines.append(line)
            continue

        # Skill-details lines get item-level scrub first.
        m = details_re.match(body)
        if m:
            head, items_blob, tail = m.group(1), m.group(2), m.group(3)
            items = [s.strip() for s in items_blob.split(",")]
            cleaned: List[str] = []
            for it in items:
                it2 = strip_skill_item_hedges(it)
                it2, _ = strip_anti_fluff_in_text(it2)
                it2 = it2.strip().strip(",").strip()
                if it2:
                    cleaned.append(it2)
            body = f"{head}{', '.join(cleaned)}{tail}"
            notes.append("yaml-scrub: details line scrubbed")

        # Apply the standard replacements to this single line.
        for pattern, repl in _REPLACEMENTS:
            new_body, n = re.subn(pattern, repl, body, flags=re.IGNORECASE)
            if n:
                body = new_body
        new_lines.append(body + eol)
    out = "".join(new_lines)

    # Final cleanup: collapse double horizontal spaces and stray punctuation
    # produced by mid-sentence regex deletions. CRITICAL: must not touch YAML
    # indentation (leading whitespace on each line) and must not touch newlines.
    # Apply per-line, only to the post-indent portion.
    cleanup = [
        (re.compile(r"[ \t]{2,}"), " "),
        (re.compile(r"[ \t]+\."), "."),
        (re.compile(r"[ \t]+,"), ","),
        (re.compile(r"\.\.+"), "."),
        (re.compile(r"\.[ \t]*\."), "."),
    ]
    cleaned_lines: List[str] = []
    for line in out.splitlines(keepends=True):
        eol = ""
        body = line
        if body.endswith("\n"):
            eol = "\n"
            body = body[:-1]
        indent_m = re.match(r"^([ \t]*)(.*)$", body, flags=re.DOTALL)
        if not indent_m:
            cleaned_lines.append(line)
            continue
        indent, rest = indent_m.group(1), indent_m.group(2)
        for rgx, repl in cleanup:
            rest = rgx.sub(repl, rest)
        cleaned_lines.append(f"{indent}{rest}{eol}")
    out = "".join(cleaned_lines)
    return out, notes


def red_flag_report(
    content: Dict[str, Any],
    job_description: str = "",
    profile_text: str = "",
) -> List[str]:
    """Collect all red-flag hits for rubric / gating."""
    import json

    blob = json.dumps(content, ensure_ascii=False)
    issues: List[str] = []
    issues.extend(find_red_flags(blob))
    issues.extend(find_hype_violations(blob))
    issues.extend(find_vague_verb_violations(blob))
    summary = str(content.get("summary") or "")
    if job_description:
        issues.extend(find_jd_years_echo_violations(summary, job_description, profile_text))
    return sorted(set(issues))
