"""
Career understanding layer: grounded analysis from reference_sources (e.g. LinkedIn PDF export)
plus resume metadata in application_assets.json. Not a substitute for human judgment.
"""
import json
import re
from typing import Any, Dict, List, Tuple

from application_assets import list_reference_sources, resolve_reference_source_path, load_application_assets_dict
from job_pipeline.genai_settings import gemini_model_for
from job_pipeline.llm_provider import LLMWritingError, generate_json, writing_providers_available, writing_providers_missing_error

MAX_PROFILE_CHARS = 30000


def _extract_pdf_text(path: str) -> str:
    try:
        import pdfplumber  # type: ignore
    except ImportError as e:
        raise RuntimeError("pdfplumber is required to read profile PDFs.") from e
    parts: List[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t.strip())
    return "\n\n".join(parts).strip()


def _first_existing_reference_id() -> str:
    for r in list_reference_sources():
        if r.get("exists") and r.get("id"):
            return str(r["id"])
    raise FileNotFoundError("No reference_sources file found on disk.")


def get_profile_text_from_reference(source_id: str = "") -> Tuple[str, str]:
    """
    Returns (resolved_path, extracted_text_truncated).
    """
    sid = (source_id or "").strip() or _first_existing_reference_id()
    path = resolve_reference_source_path(sid)
    text = _extract_pdf_text(path)
    if len(text) < 80:
        raise ValueError(
            "Extracted almost no text from the profile PDF. If it is image-only, use a text-based export or add a .txt reference."
        )
    return path, text[:MAX_PROFILE_CHARS]


def _resume_metadata_context() -> str:
    assets = load_application_assets_dict()
    blobs = []
    for r in assets.get("resumes") or []:
        if isinstance(r, dict) and r.get("id"):
            blobs.append(
                {
                    "id": r.get("id"),
                    "metadata": r.get("metadata") or {},
                    "suggest_when": r.get("suggest_when") or {},
                }
            )
    if not blobs:
        return ""
    return json.dumps(blobs, ensure_ascii=False)[:4000]


def analyze_career_with_gemini(profile_text: str, user_angle: str = "") -> Dict[str, Any]:
    if not writing_providers_available():
        return {"ok": False, "error": writing_providers_missing_error()}
    meta_ctx = _resume_metadata_context()
    model = gemini_model_for("career")
    angle = (user_angle or "").strip()[:500]
    system = "Return exactly one valid JSON object with no markdown fences or commentary."
    user = (
        "You are a career strategist. The ONLY verified facts are in PROFILE_TEXT and RESUME_METADATA_JSON. "
        "Do not invent employers, dates, degrees, or metrics. If something is unclear, list it under optional_gaps_or_risks.\n"
        "Output ONE JSON object ONLY (no markdown). Keys:\n"
        "career_headline_one_line (string, max 120 chars),\n"
        "uniqueness_bullets (array of 3-5 short strings: grounded differentiators),\n"
        "trajectory_summary (string: 2-4 sentences),\n"
        "themes_skills (array of strings),\n"
        "emphasis_for_applications (string: what to lead with),\n"
        "optional_gaps_or_risks (array of strings),\n"
        "recruiter_framing (string: one paragraph pitch),\n"
        "ats_keyword_themes (array of strings: phrases that literally appear or closely paraphrase PROFILE_TEXT only).\n\n"
    )
    if angle:
        user += f"USER_FOCUS: {angle}\n\n"
    user += f"RESUME_METADATA_JSON:\n{meta_ctx or '[]'}\n\nPROFILE_TEXT:\n{profile_text}\n"
    try:
        obj = generate_json(
            "career",
            system=system,
            user=user,
            label="career",
            gemini_model=model,
            gemini_max_output_tokens=4096,
        )
        if not isinstance(obj, dict):
            return {"ok": False, "error": "model JSON not an object"}
        obj["ok"] = True
        return obj
    except LLMWritingError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def format_career_report(d: Dict[str, Any]) -> str:
    if not d.get("ok"):
        err = d.get("error", "unknown")
        raw = d.get("raw")
        extra = f"\n\nRaw snippet: {raw}" if raw else ""
        return f"Career analysis failed: {err}{extra}"
    lines = [
        "**Career understanding** (grounded on your `reference_sources` PDF + resume metadata in `application_assets.json`)",
        "",
        f"_{d.get('career_headline_one_line', '')}_",
        "",
        "**What stands out**",
    ]
    for b in d.get("uniqueness_bullets") or []:
        if b:
            lines.append(f"- {b}")
    lines.extend(["", "**Trajectory**", "", (d.get("trajectory_summary") or "").strip() or "(none)", ""])
    ts = d.get("themes_skills") or []
    if ts:
        lines.append("**Themes / skills**")
        lines.append(", ".join(str(x) for x in ts if x))
        lines.append("")
    lines.extend(
        [
            "**Lead with (applications)**",
            (d.get("emphasis_for_applications") or "").strip() or "(none)",
            "",
            "**Recruiter-style pitch**",
            (d.get("recruiter_framing") or "").strip() or "(none)",
            "",
        ]
    )
    ak = d.get("ats_keyword_themes") or []
    if ak:
        lines.append("**ATS-style themes (from your material only)**")
        lines.append(", ".join(str(x) for x in ak if x))
        lines.append("")
    gaps = d.get("optional_gaps_or_risks") or []
    if gaps:
        lines.append("**Honesty / gaps**")
        for g in gaps:
            if g:
                lines.append(f"- {g}")
        lines.append("")
    lines.append(
        "_This is inference from the files above—not verified truth. Enrich `metadata.key_skills` or improve source text for tighter output._"
    )
    return "\n".join(lines)


def looks_like_career_understanding_request(text: str) -> bool:
    t = (text or "").lower()
    if len(t) > 400:
        return False
    triggers = (
        "analyze my career",
        "my career and tell",
        "career trajectory",
        "career story",
        "what makes me unique",
        "makes me unique as a candidate",
        "understand my career",
        "who am i as a candidate",
        "my unique value",
        "value proposition as a candidate",
        "strategic career",
        "career communication",
    )
    return any(k in t for k in triggers)


def run_career_understanding_chat(user_input: str) -> str:
    refs = list_reference_sources()
    if not refs:
        return (
            "No **reference_sources** in `application_assets.json`. Add your LinkedIn export PDF there "
            "(e.g. id `linkedin_profile_pdf`)."
        )
    if not any(r.get("exists") for r in refs):
        paths = ", ".join(str(r.get("path", "")) for r in refs)
        return f"Reference file(s) missing on disk. Configured paths: {paths}"
    try:
        source_id = ""
        for r in refs:
            if r.get("exists") and str(r.get("id")) == "linkedin_profile_pdf":
                source_id = "linkedin_profile_pdf"
                break
        if not source_id:
            source_id = _first_existing_reference_id()
        _, text = get_profile_text_from_reference(source_id)
    except ValueError as e:
        return str(e)
    except FileNotFoundError as e:
        return str(e)
    d = analyze_career_with_gemini(text, user_angle=user_input)
    return format_career_report(d)
