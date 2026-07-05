"""
Gap-fill flow for the manual resume tailor.

Given a JD and the tailored resume content (or the profile text), surface
requirements the JD asks for that are not visible in the candidate material.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from job_pipeline.genai_settings import gemini_model_for
from job_pipeline.llm_provider import LLMWritingError, generate_json, writing_providers_available
from job_pipeline.named_requirements import (
    COMMON_REQ_TERMS,
    extract_requirements,
    light_exposure_prompt_block,
    named_requirement_gaps,
    parse_light_exposure,
)
from job_pipeline.truth_classifier import classifications_to_gap_records, classify_jd_requirements

logger = logging.getLogger(__name__)


# Use [ \t] (not \s) so regex captures stop at newlines.
_HARD_REQ_PATTERNS = [
    re.compile(r"(?:must|required to|need to)[ \t]+have[ \t]+([\w \t\-\+\#\./]{3,80})", re.IGNORECASE),
    re.compile(r"(\d+\+?[ \t]+years?)[ \t]+(?:of[ \t]+)?(?:experience|exp)[ \t]+(?:with|in)[ \t]+([\w \t\-\+\#\./]{3,60})", re.IGNORECASE),
    re.compile(r"(?:active|current)[ \t]+(secret|top[ \t]+secret|ts/sci|public[ \t]+trust)[ \t]+clearance", re.IGNORECASE),
    re.compile(r"(?:certified|certification)[ \t]+in[ \t]+([\w \t\-\+\#\./]{3,60})", re.IGNORECASE),
    re.compile(r"(?:bachelor.?s|master.?s|associate.?s)[ \t]+degree[ \t]+in[ \t]+([\w \t\-\+\#\./]{3,60})", re.IGNORECASE),
    re.compile(r"willing[ \t]+to[ \t]+(travel|relocate|work[ \t]+on[-\t ]?call|work[ \t]+nights?|work[ \t]+weekends?)", re.IGNORECASE),
]


def _profile_blob(profile_text, content):
    parts = [profile_text or ""]
    if isinstance(content, dict):
        try:
            parts.append(json.dumps(content, ensure_ascii=False))
        except Exception:
            pass
    return "\n".join(parts).lower()


def _normalize_gap_phrase(s: str) -> str:
    """Collapse whitespace/newlines so regex captures cannot span noisy multi-line blobs."""
    return " ".join((s or "").replace("\r", " ").replace("\n", " ").split()).strip()


def _hard_req_phrase(match: Any) -> str:
    """Prefer tight captured groups; keep full snippet when a single capture would be vague."""
    text = _normalize_gap_phrase(match.group(0))
    groups = tuple(x for x in match.groups() if x)
    if len(groups) >= 2:
        left, right = groups[0].strip(), groups[1].strip()
        if re.match(r"^\d", left):
            return _normalize_gap_phrase(f"{left} experience with {right}")
    if len(groups) == 1:
        lone = _normalize_gap_phrase(groups[0])
        if "clearance" in text.lower():
            return text
        return lone if lone else text
    return text


def _heuristic_gaps(job_description, profile_blob_lower, profile_text=""):
    gaps = []
    seen = set()
    for g in named_requirement_gaps(job_description, profile_text=profile_text):
        req = (g.get("requirement") or "").strip()
        if not req:
            continue
        key = "named:" + req.lower()
        if key in seen:
            continue
        seen.add(key)
        gaps.append(g)
    for term in extract_requirements(job_description):
        if term in profile_blob_lower:
            continue
        key = "kw:" + term.lower()
        if key in seen:
            continue
        seen.add(key)
        gaps.append({
            "requirement": term,
            "category": "keyword",
            "severity": "medium",
            "question": "Do you have hands-on experience with " + term + "? If yes, a one-line example.",
            "source": "heuristic",
        })
    for pat in _HARD_REQ_PATTERNS:
        for m in pat.finditer(job_description or ""):
            phrase = _hard_req_phrase(m)
            if len(phrase) < 3:
                continue
            if phrase.lower() in profile_blob_lower:
                continue
            dedupe_key = "re:" + phrase.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            gaps.append({
                "requirement": phrase,
                "category": "hard_req",
                "severity": "high",
                "question": (
                    "The JD mentions this requirement: "
                    + phrase
                    + ". Can you honestly speak to it? (Y/N + one honest line.)"
                ),
                "source": "heuristic",
            })
    return gaps


def _llm_gaps(job_description, profile_text, content, heuristic_gaps, max_items=6):
    if not writing_providers_available():
        return []
    model = gemini_model_for("gaps")
    already = [g.get("requirement") for g in heuristic_gaps]
    system = (
        "Compare a job description to a candidate profile. Identify HARD requirements "
        "in the JD that are NOT backed by the profile. Return exactly one valid JSON object."
    )
    user = (
        "Output ONE JSON object only: "
        "{ \"gaps\": [ {\"requirement\": str, \"severity\": \"high\"|\"medium\"|\"low\", "
        "\"question\": str, \"why\": str }, ... ] }\n\n"
        "Already flagged - DO NOT repeat: " + str(already) + "\n\n"
        "Return at most " + str(max_items) + " new gaps.\n\n"
        "PROFILE:\n" + profile_text[:30000] + "\n\n"
        "TAILORED_CONTENT:\n" + json.dumps(content or {}, ensure_ascii=False)[:3000] + "\n\n"
        "JOB_DESCRIPTION:\n" + job_description[:16000]
    )
    try:
        obj = generate_json(
            "gaps",
            system=system,
            user=user,
            label="gap_detect",
            gemini_model=model,
            gemini_max_output_tokens=4096,
        )
    except (LLMWritingError, Exception):
        return []
    gaps_raw = obj.get("gaps") if isinstance(obj, dict) else []
    if not isinstance(gaps_raw, list):
        return []
    out = []
    for g in gaps_raw[:max_items]:
        if not isinstance(g, dict):
            continue
        req = (g.get("requirement") or "").strip()
        if not req:
            continue
        out.append({
            "requirement": req,
            "category": "llm",
            "severity": g.get("severity") or "medium",
            "question": (g.get("question") or ("Can you address this requirement: " + req + "?")).strip(),
            "why": (g.get("why") or "").strip(),
            "source": "llm",
        })
    return out


def _prepare_json_candidate(text: str) -> str:
    """Strip markdown fences so brace-regex finds model JSON."""
    t = (text or "").strip()
    if "```" not in t:
        return t
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
    return (m.group(1).strip() if m else t)


def _extract_json_object_raw(text: str) -> Optional[Dict[str, Any]]:
    candidate = _prepare_json_candidate(text)
    m = re.search(r"\{[\s\S]*\}", candidate)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None

def _suggest_answers_for_gaps(gaps, profile_text):
    """
    Single-shot LLM pass that fills `suggested_answer` for each gap, drawn
    strictly from PROFILE text. Pays special attention to career_master sections
    like 'Honest limits', 'No exposure', 'Touched but cannot claim', and
    'Tools I have actually used' — those contain pre-written honest framings
    the candidate has already approved. Empty string when no support exists;
    never fabricates.
    """
    if not gaps:
        return gaps
    if not writing_providers_available():
        return gaps

    primary_model = gemini_model_for("gaps")

    gap_list = [
        {"i": i, "requirement": g.get("requirement"), "question": g.get("question")}
        for i, g in enumerate(gaps)
    ]

    light_block = light_exposure_prompt_block(parse_light_exposure(profile_text or ""))

    system = (
        "For each gap, propose a SUGGESTED ANSWER the candidate can confirm or edit. "
        "The answer MUST be grounded in PROFILE text. Return exactly one valid JSON object."
    )
    user = (
        "Rules:\n"
        "- If PROFILE contains a section titled 'Honest limits', 'do NOT claim', "
        "'No exposure', or 'Touched but cannot claim' that addresses the gap, "
        "use that pre-written framing verbatim or near-verbatim. These are the "
        "candidate's own honest disclosures.\n"
        "- If PROFILE contains LIGHT EXPOSURE approved phrasing for the gap's skill, "
        "use that framing verbatim — do not upgrade to full expertise.\n"
        "- If PROFILE shows actual usage of the requirement (e.g. in 'Tools I have "
        "actually used' or experience bullets), quote or tightly paraphrase the "
        "concrete detail (duration, context, scale).\n"
        "- If PROFILE has nothing relevant to the gap, return an empty string. "
        "Do not guess, do not invent, do not pattern-match from adjacent skills.\n"
        "- Each answer under 280 characters. Plain prose, no markdown.\n\n"
        + (light_block + "\n\n" if light_block else "")
        + "Output ONE JSON object only: "
        "{ \"answers\": [ {\"i\": int, \"suggested_answer\": str}, ... ] }\n\n"
        "GAPS:\n" + json.dumps(gap_list, ensure_ascii=False) + "\n\n"
        "PROFILE:\n" + (profile_text or "")[:30000]
    )

    try:
        obj = generate_json(
            "gaps",
            system=system,
            user=user,
            label="gap_suggestions",
            gemini_model=primary_model,
            gemini_max_output_tokens=4096,
        )
        model_used = primary_model
    except Exception as exc:
        logger.warning("gap_suggestions generate failed model=%s: %s", primary_model, exc)
        return gaps

    pv = json.dumps(obj, ensure_ascii=False)[:600]
    logger.info(
        "gap_suggestions model=%s raw_chars=%s preview=%s",
        model_used,
        len(pv),
        pv,
    )

    answers = obj.get("answers") if isinstance(obj, dict) else []
    if not isinstance(answers, list):
        logger.info("gap_suggestions answers field not a list; keys=%s", list(obj.keys()))
        return gaps

    logger.info("gap_suggestions parsed answers len=%s payload=%s", len(answers), repr(answers)[:1800])

    by_index = {}
    for a in answers:
        if not isinstance(a, dict):
            continue
        try:
            idx = int(a.get("i"))
        except (TypeError, ValueError):
            continue
        suggestion = (a.get("suggested_answer") or "").strip()
        if suggestion:
            by_index[idx] = suggestion[:280]

    enriched = []
    for i, g in enumerate(gaps):
        if i in by_index:
            g = dict(g)
            g["suggested_answer"] = by_index[i]
        enriched.append(g)
    return enriched


def detect_gaps(job_description, profile_text="", tailored_content=None, use_llm=True, max_total=10):
    pb = _profile_blob(profile_text, tailored_content)
    heuristic = _heuristic_gaps(job_description or "", pb, profile_text=profile_text or "")
    truth = classifications_to_gap_records(
        classify_jd_requirements(job_description or "", profile_text or "")
    )
    if use_llm:
        llm = _llm_gaps(job_description or "", profile_text or "", tailored_content, heuristic + truth)
    else:
        llm = []
    combined = heuristic + truth + llm
    sev_order = {"high": 0, "medium": 1, "low": 2}
    combined.sort(key=lambda g: (sev_order.get(g.get("severity"), 2), g.get("category") == "llm"))
    deduped = []
    norm_seen = set()
    for g in combined:
        req = _normalize_gap_phrase(str(g.get("requirement") or ""))
        if not req:
            continue
        nk = req.lower()
        if nk in norm_seen:
            continue
        norm_seen.add(nk)
        if req != g.get("requirement"):
            g = dict(g)
            g["requirement"] = req
        deduped.append(g)
    deduped = deduped[:max_total]

    if use_llm and deduped:
        deduped = _suggest_answers_for_gaps(deduped, profile_text or "")

    return deduped


def format_gaps_for_chat(gaps):
    if not gaps:
        return "_No obvious gaps detected. The tailored draft looks aligned with the JD._"
    lines = ["**Gap-fill questions** (" + str(len(gaps)) + "):", ""]
    any_suggestions = False
    for i, g in enumerate(gaps, start=1):
        sev = g.get("severity") or "medium"
        marker = "[!]" if sev == "high" else "[?]"
        lines.append(str(i) + ". " + marker + " **" + str(g.get("requirement")) + "** (" + sev + ")")
        lines.append("   " + str(g.get("question")))
        why = (g.get("why") or "").strip()
        if why:
            lines.append("   _why:_ " + why)
        suggestion = (g.get("suggested_answer") or "").strip()
        if suggestion:
            any_suggestions = True
            lines.append("   _suggested:_ " + suggestion)
        lines.append("")
    if any_suggestions:
        lines.append(
            "Answer one per line: type 'y' to accept the suggested answer, "
            "type your own to override, or 'skip' to drop. Then re-run with --answers answers.txt."
        )
    else:
        lines.append("Answer with one fact per line, then re-run the tailor with --answers answers.txt.")
    return "\n".join(lines)


def answers_to_extra_facts(gaps, answers):
    out = []
    for i, g in enumerate(gaps):
        ans = answers[i] if i < len(answers) else ""
        ans = (ans or "").strip()
        if not ans:
            continue
        low = ans.lower()
        if low in ("skip", "no", "n", "-", "/skip"):
            continue
        req = g.get("requirement") or "requirement"
        suggestion = (g.get("suggested_answer") or "").strip()
        if low in ("y", "yes"):
            if suggestion:
                out.append(str(req) + ": " + suggestion)
            else:
                out.append("Candidate confirms: " + str(req) + " - direct experience, no extra detail provided.")
        else:
            out.append(str(req) + ": " + ans)
    return out
