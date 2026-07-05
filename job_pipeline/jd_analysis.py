"""JD parsing for optimization pipeline (cached per build)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set

from job_pipeline.named_requirements import detect_named_in_jd, extract_requirements

_CULTURE_WORDS = (
    "ownership",
    "accountability",
    "impact",
    "scaling",
    "scale",
    "growth",
    "mission",
    "collaboration",
    "communication",
    "customer",
    "remote",
    "fast-paced",
    "innovation",
    "integrity",
    "team",
    "autonomy",
    "initiative",
)

_BUSINESS_OUTCOME_PATTERNS = (
    r"(?:ensure|maintain|protect|improve|reduce|increase|support)\s+[^.\n]{10,80}",
    r"(?:productivity|uptime|efficiency|experience|operations|workforce)[^.\n]{0,60}",
)


def parse_job_description(job_description: str) -> Dict[str, Any]:
    """Extract must-haves, culture signals, and business outcome from JD text."""
    jd = (job_description or "").strip()
    jd_lower = jd.lower()
    named = detect_named_in_jd(jd)
    must_haves = [nr.label for nr in named]
    preferred = extract_requirements(jd)[:20]

    culture: List[str] = []
    seen: Set[str] = set()
    for word in _CULTURE_WORDS:
        if word in jd_lower and word not in seen:
            seen.add(word)
            culture.append(word)
        if len(culture) >= 5:
            break

    business_outcome = ""
    for pat in _BUSINESS_OUTCOME_PATTERNS:
        m = re.search(pat, jd, re.I)
        if m:
            business_outcome = re.sub(r"\s+", " ", m.group(0).strip())[:120]
            break

    mission_hook = ""
    for pat in (
        r"(?:our mission|we are|we're building|helping)[^.?\n]{10,120}",
        r"(?:join us|about (?:the )?company)[^.?\n]{10,120}",
    ):
        m = re.search(pat, jd, re.I)
        if m:
            mission_hook = re.sub(r"\s+", " ", m.group(0).strip())[:140]
            break

    technical = must_haves[:5]

    return {
        "must_haves": must_haves,
        "preferred": preferred,
        "culture_words": culture[:3],
        "technical_requirements": technical[:3],
        "business_outcome": business_outcome,
        "mission_hook": mission_hook,
    }


_THESIS_USABLE_TECH_BLACKLIST = (
    "pst",
    "time-zone",
    "time zone",
    "/",
)


def _thesis_pick_culture(culture_words: List[str]) -> str:
    """Pick one grammatical culture noun-phrase for the thesis (avoid abstract triplets)."""
    preferred_order = (
        "ownership",
        "accountability",
        "impact",
        "scaling",
        "scale",
        "growth",
        "collaboration",
        "communication",
        "team",
        "autonomy",
        "initiative",
        "remote",
        "mission",
        "customer",
    )
    lowered = {str(w).strip().lower() for w in culture_words or []}
    for word in preferred_order:
        if word in lowered:
            if word in ("scale", "scaling", "growth"):
                return "as the company scales"
            if word == "remote":
                return "a remote-first environment"
            if word in ("ownership", "accountability"):
                return "ownership and accountability"
            if word in ("collaboration", "team"):
                return "team collaboration"
            if word in ("communication",):
                return "clear communication"
            if word == "impact":
                return "measurable impact"
            if word == "autonomy":
                return "self-directed work"
            if word == "initiative":
                return "proactive problem-solving"
            if word == "mission":
                return "the company's mission"
            if word == "customer":
                return "the customer experience"
    return "clear communication"


def _thesis_pick_tech(technical_requirements: List[str]) -> str:
    """Pick one grammatical tech phrase; reject slashy / fragment labels for the thesis."""
    for raw in technical_requirements or []:
        s = str(raw or "").strip()
        if not s:
            continue
        sl = s.lower()
        if any(bad in sl for bad in _THESIS_USABLE_TECH_BLACKLIST):
            continue
        if len(s.split()) > 5:
            continue
        return sl
    return "end-user support"


def build_role_thesis(job_title: str, jd_analysis: Dict[str, Any]) -> str:
    """Generate one controlling thesis sentence (heuristic; LLM may refine in FULL mode).

    Designed to be grammatical even when the JD culture/tech lists are abstract
    noun-fragments — never produces comma-salad output.
    """
    title_l = (job_title or "").lower()
    culture_phrase = _thesis_pick_culture(jd_analysis.get("culture_words") or [])
    tech_phrase = _thesis_pick_tech(jd_analysis.get("technical_requirements") or [])

    if any(h in title_l for h in ("help desk", "helpdesk", "it support", "desktop support", "service desk")):
        return (
            f"Help desk candidate who troubleshoots under pressure, communicates clearly, "
            f"documents fixes, and improves workflows — bringing {tech_phrase} experience "
            f"and a focus on {culture_phrase}."
        )
    return (
        f"Technical operations professional with hands-on {tech_phrase} experience, "
        f"a documented process-improvement track record, and a focus on {culture_phrase}."
    )


def voice_mirroring_block(jd_analysis: Dict[str, Any]) -> str:
    """Structured voice signals for cover letter prompts."""
    cw = jd_analysis.get("culture_words") or []
    tr = jd_analysis.get("technical_requirements") or []
    bo = jd_analysis.get("business_outcome") or ""
    mh = jd_analysis.get("mission_hook") or ""
    lines = ["VOICE MIRRORING (from JD analysis):"]
    if cw:
        lines.append(f"- Culture words: {', '.join(cw[:3])}")
    if tr:
        lines.append(f"- Technical requirements: {', '.join(tr[:3])}")
    if bo:
        lines.append(f"- Business outcome: {bo}")
    if mh:
        lines.append(f"- Mission hook: {mh}")
    lines.append(
        "- Build the opening hook from these signals using ONLY evidence-backed claims."
    )
    return "\n".join(lines)
