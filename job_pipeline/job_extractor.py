"""
Extract structured job fields from pasted posting HTML/text via Gemini (JSON-only output).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from job_pipeline.genai_settings import (
    gemini_model_for,
    google_api_key,
    google_api_key_missing_error,
)

_KEYS = (
    "company",
    "title",
    "apply_url",
    "location",
    "salary",
    "description",
    "job_type",
    "work_mode",
)


def _empty() -> Dict[str, str]:
    return {k: "" for k in _KEYS}


def _fail(reason: str) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(_empty())
    out["error"] = reason
    return out


_MAX_PASTE_CHARS = 120_000

_INDEED_NAV_LINE_RES = tuple(
    re.compile(p)
    for p in (
        r"(?i)^(sign\s+in|log\s+in|register|join\s+now)\s*$",
        r"(?i)^(save\s+job|saved\s+job|undo)\s*$",
        r"(?i)^(apply\s+now)\s*$",
        r"(?i)^(cookie|privacy)\s+(policy|preferences)\s*$",
        r"(?i)^indeed\s+home\s*$",
        r"(?i)^(company\s+reviews|popular\s+searches)\s*$",
        r"(?i)^(similar\s+jobs\b.*|jobs\s+you\s+might\s+like\b.*)$",
        r"(?i)^was\s+this\s+helpful\??\s*$",
        r"(?i)^(skip\s+to\s+main\s+content)\s*$",
        r"(?i)^(.*©\s*\d{4}.*indeed.*)$",
        r"(?i)^results\s+by\s+indeed\s+semantic\s+search\s*$",
    )
)


def preclean_pasted_job_posting(raw: str) -> str:
    """Drop obvious Indeed/LinkedIn chrome lines before sending to the LLM."""
    lines_out: List[str] = []
    for line in (raw or "").splitlines():
        t = line.strip()
        if not t:
            lines_out.append("")
            continue
        if any(rx.match(t) for rx in _INDEED_NAV_LINE_RES):
            continue
        lines_out.append(line.rstrip())
    # Collapse huge blank runs from removed chrome
    collapsed: List[str] = []
    blank_run = 0
    for ln in lines_out:
        if not ln.strip():
            blank_run += 1
            if blank_run <= 2:
                collapsed.append("")
        else:
            blank_run = 0
            collapsed.append(ln)
    return "\n".join(collapsed).strip()


def _build_prompt(pasted_text: str, strict: bool) -> str:
    strict_note = ""
    if strict:
        strict_note = (
            "\nCRITICAL: Reply with ONLY one JSON object. No markdown fences, no prose, "
            "no code blocks. Start with { and end with }.\n"
        )
    return (
        "You extract structured fields from a pasted job posting (Indeed, LinkedIn, "
        "company careers site, etc.). Return ONLY one JSON object with EXACTLY these keys "
        "(all string values; use empty string \"\" when unknown — never null, never omit keys):\n"
        '  "company", "title", "apply_url", "location", "salary", "description", '
        '"job_type", "work_mode"\n\n'
        "Rules:\n"
        "- apply_url: Must be a URL that opens THIS specific job posting (e.g. indeed.com/rc/clk?"
        "jk=..., indeed.com/viewjob?, linkedin.com/jobs/view/, greenhouse.io embed, lever.co, "
        "boards.greenhouse.io job URLs). If you only see a company profile URL "
        "(e.g. indeed.com/cmp/...) or generic homepage, leave apply_url empty.\n"
        "- description: The REAL job posting body only. Prefer starting at section cues "
        'such as "About the job", "Job details", "Full job description", "Overview", '
        '"Responsibilities", "Qualifications", "Requirements", or "What you\'ll do". '
        "Include bullets through end of qualifications/skills — omit footer/legal blocks.\n"
        "- Strip aggressively: Indeed or LinkedIn navigation, cookie banners, Chrome-extension "
        "noise (resume-match percentages), salary estimator widgets unless clearly labeled "
        "compensation FOR THIS ROLE, ratings snippets, employer branding fluff after the JD ends, "
        '"Similar jobs", social-share chrome, "Was this helpful?", chat bubbles.\n'
        "- title: Role title only — typically the first substantive heading line after stripping chrome "
        '(often mirrors an <h1> on web pages); drop trailing "| Indeed", IDs, salary hints.\n'
        "- company: Employer name only — not ratings or subsidiary fluff unless clearly the legal employer.\n"
        "- salary: Compensation text if explicitly stated, else \"\".\n"
        "- location: City/state/ZIP or remote wording as stated.\n"
        '- job_type: one of "full-time", "part-time", "contract", "internship", or "".\n'
        '- work_mode: one of "remote", "hybrid", "in-person", or "".\n'
        f"{strict_note}\n"
        "PASTED TEXT:\n---\n"
        f"{pasted_text}\n---\n"
    )


def _parse_llm_json(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    if "```" in t:
        fm = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
        if fm:
            t = fm.group(1).strip()
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        raise ValueError("no_json_in_response")
    return json.loads(m.group(0))


def _normalize(obj: Any) -> Dict[str, str]:
    out = _empty()
    if not isinstance(obj, dict):
        raise ValueError("model_returned_non_object")
    for k in _KEYS:
        v = obj.get(k, "")
        if v is None:
            out[k] = ""
        elif k == "description":
            out[k] = str(v).strip()[:100000]
        else:
            out[k] = str(v).strip()[:8000]
    return out


def _call_gemini(prompt: str) -> str:
    key = google_api_key().strip()
    if not key:
        raise RuntimeError(google_api_key_missing_error())

    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError("google-genai package not installed") from e

    model = gemini_model_for("career")
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
    )
    return getattr(resp, "text", None) or ""


def extract_job_fields(pasted_text: str) -> Dict[str, Any]:
    """
    Parse a pasted job posting into structured fields using Gemini.

    Returns a dict with string keys:
        company, title, apply_url, location, salary, description, job_type, work_mode

    On failure returns the same keys as empty strings plus 'error' (str).
    Never raises.
    """
    raw_in = (pasted_text or "").strip()
    if not raw_in:
        return _fail("empty_input")

    clipped_in = raw_in[:_MAX_PASTE_CHARS]
    clipped = preclean_pasted_job_posting(clipped_in)

    try:
        text = _call_gemini(_build_prompt(clipped, strict=False))
        obj = _parse_llm_json(text)
        return _normalize(obj)
    except Exception as first_exc:
        try:
            text2 = _call_gemini(_build_prompt(clipped, strict=True))
            obj2 = _parse_llm_json(text2)
            return _normalize(obj2)
        except Exception as second_exc:
            err = f"{type(second_exc).__name__}: {second_exc} (after retry: {first_exc})"
            return _fail(err)
