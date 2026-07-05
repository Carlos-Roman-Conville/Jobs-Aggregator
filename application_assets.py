import json
import os
from typing import Any, Dict, List, Optional
from pathlib import Path


ASSETS_PATH = os.path.join(os.path.dirname(__file__), "application_assets.json")

_TEXT_TEMPLATE_EXTS = {".txt", ".md"}
_DOCX_TEMPLATE_EXTS = {".docx"}
_PDF_TEMPLATE_EXTS = {".pdf"}


def _safe_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index}"


def _discover_files(root: str, allowed_exts: List[str], recursive: bool) -> List[str]:
    if not root:
        return []
    rp = Path(root)
    if not rp.exists() or not rp.is_dir():
        return []
    ext_set = {e.lower() for e in (allowed_exts or [])}
    pattern_iter = rp.rglob("*") if recursive else rp.glob("*")
    out: List[str] = []
    for p in pattern_iter:
        if not p.is_file():
            continue
        if p.suffix.lower() in ext_set:
            out.append(str(p.resolve()))
    out.sort(key=lambda s: s.lower())
    return out


def _apply_auto_discovery(data: Dict[str, Any]) -> Dict[str, Any]:
    auto = data.get("auto_discovery") or {}
    if not isinstance(auto, dict):
        return data
    if not bool(auto.get("enabled", False)):
        return data

    recursive = bool(auto.get("recursive", True))
    resume_root = str(auto.get("resumes_root", "") or "").strip()
    cover_root = str(auto.get("cover_letters_root", "") or "").strip()
    resume_exts = auto.get("resume_extensions") or [".pdf", ".doc", ".docx"]
    cover_exts = auto.get("cover_letter_extensions") or [".txt", ".md", ".docx", ".pdf"]

    resume_files = _discover_files(resume_root, list(resume_exts), recursive)
    cover_files = _discover_files(cover_root, list(cover_exts), recursive)

    if resume_files:
        meta_defaults: Dict[str, Any] = {
            "summary": "",
            "target_roles": ["Tech", "Operations"],
            "key_skills": [],
        }
        rmd = auto.get("resume_metadata_defaults")
        if isinstance(rmd, dict):
            if rmd.get("summary") is not None:
                meta_defaults["summary"] = str(rmd.get("summary") or "")
            if isinstance(rmd.get("target_roles"), list):
                meta_defaults["target_roles"] = [str(x) for x in rmd["target_roles"] if x]
            if isinstance(rmd.get("key_skills"), list):
                meta_defaults["key_skills"] = [str(x) for x in rmd["key_skills"] if x]
        resumes: List[Dict[str, Any]] = []
        for idx, path in enumerate(resume_files, start=1):
            resumes.append(
                {
                    "id": _safe_id("resume", idx),
                    "path": path,
                    "metadata": dict(meta_defaults),
                    "suggest_when": {},
                }
            )
        data["resumes"] = resumes

    if cover_files:
        templates: List[Dict[str, Any]] = []
        for idx, path in enumerate(cover_files, start=1):
            templates.append(
                {
                    "id": _safe_id("template", idx),
                    "path": path,
                    "metadata": {"tone": "professional", "notes": "Auto-discovered template file."},
                    "maps_to_job_families": ["general_engineering"],
                    "maps_to_title_keywords_any": ["engineer", "developer", "software", "operations"],
                }
            )
        data["cover_letter_templates"] = templates

    return data


def _load_assets() -> Dict[str, Any]:
    if not os.path.exists(ASSETS_PATH):
        raise FileNotFoundError(
            f"Missing application assets file at '{ASSETS_PATH}'. Create it (see starter schema) and add resume/template paths."
        )

    with open(ASSETS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("application_assets.json must be a JSON object at the top level.")
    return _apply_auto_discovery(data)


def load_application_assets_dict() -> Dict[str, Any]:
    """Full parsed application_assets.json (for strategy matching and tooling)."""
    return _load_assets()


def _resolve_local_path(path: str) -> str:
    """
    Resolve absolute/relative paths against the repo root (C:\\AI).
    """
    if not path:
        return path
    if os.path.isabs(path):
        return path
    base_dir = os.path.dirname(__file__)
    return os.path.normpath(os.path.join(base_dir, path))


def load_application_assets() -> str:
    """
    Returns compact asset metadata for Gemini to choose:
    - available resume ids + metadata
    - available cover letter template ids + metadata
    - profile (if present)
    """
    assets = _load_assets()

    profile = assets.get("profile") or {}
    resumes = assets.get("resumes") or []
    templates = assets.get("cover_letter_templates") or []

    resume_summaries = []
    for r in resumes:
        if not isinstance(r, dict) or "id" not in r:
            continue
        resume_summaries.append(
            {
                "id": r.get("id"),
                "metadata": r.get("metadata") or {},
                "suggest_when": r.get("suggest_when") if isinstance(r.get("suggest_when"), dict) else {},
                "filename": os.path.basename(_resolve_local_path(r.get("path", ""))),
            }
        )

    template_summaries = []
    for t in templates:
        if not isinstance(t, dict) or "id" not in t:
            continue
        template_summaries.append(
            {
                "id": t.get("id"),
                "metadata": t.get("metadata") or {},
                "maps_to_job_families": t.get("maps_to_job_families") if isinstance(t.get("maps_to_job_families"), list) else [],
                "maps_to_title_keywords_any": t.get("maps_to_title_keywords_any")
                if isinstance(t.get("maps_to_title_keywords_any"), list)
                else [],
                "filename": os.path.basename(_resolve_local_path(t.get("path", ""))),
                "has_text": bool(t.get("text")),
            }
        )

    strat = assets.get("asset_strategy") or {}
    fam_keys = []
    if isinstance(strat, dict) and isinstance(strat.get("job_families"), dict):
        fam_keys = [str(k) for k in strat["job_families"].keys()]

    payload = {
        "profile": profile,
        "resumes": resume_summaries,
        "cover_letter_templates": template_summaries,
        "job_family_ids": fam_keys,
    }
    return json.dumps(payload, ensure_ascii=False)


def get_cover_letter_template(template_id: str) -> str:
    """
    Returns the raw cover letter template text for a given template id.
    Your config entry can provide either:
    - "text": "...", or
    - "path": "relative/or/absolute/path/to/template.txt"
    """
    assets = _load_assets()
    templates = assets.get("cover_letter_templates") or []

    for t in templates:
        if not isinstance(t, dict):
            continue
        if str(t.get("id")) != str(template_id):
            continue

        if t.get("text"):
            return str(t["text"])

        path = t.get("path")
        if not path:
            raise ValueError(f"Template '{template_id}' must have either 'text' or 'path'.")

        resolved = _resolve_local_path(path)
        if not os.path.exists(resolved):
            raise FileNotFoundError(f"Template file not found: '{resolved}'")

        suffix = Path(resolved).suffix.lower()
        if suffix in _TEXT_TEMPLATE_EXTS:
            with open(resolved, "r", encoding="utf-8") as f:
                return f.read()
        if suffix in _DOCX_TEMPLATE_EXTS:
            try:
                from docx import Document  # type: ignore
            except Exception as e:
                raise RuntimeError("python-docx is required to read .docx cover letter templates.") from e
            doc = Document(resolved)
            return "\n".join([p.text for p in doc.paragraphs]).strip()
        if suffix in _PDF_TEMPLATE_EXTS:
            try:
                import pdfplumber  # type: ignore
            except Exception as e:
                raise RuntimeError("pdfplumber is required to read .pdf cover letter templates.") from e
            pages: List[str] = []
            with pdfplumber.open(resolved) as pdf:
                for page in pdf.pages:
                    txt = page.extract_text() or ""
                    if txt.strip():
                        pages.append(txt.strip())
            return "\n\n".join(pages).strip()
        raise ValueError(f"Unsupported template file extension: '{suffix}'")

    raise ValueError(f"Unknown template_id: '{template_id}'")


def _get_resume_entry(resume_id: str) -> Dict[str, Any]:
    assets = _load_assets()
    resumes = assets.get("resumes") or []
    for r in resumes:
        if not isinstance(r, dict):
            continue
        if str(r.get("id")) != str(resume_id):
            continue
        return r
    raise ValueError(f"Unknown resume_id: '{resume_id}'")


def _get_profile_value(key: str, default: str = "") -> str:
    assets = _load_assets()
    profile = assets.get("profile") or {}
    v = profile.get(key, default)
    return "" if v is None else str(v)


def _resolve_resume_path(resume_id: str) -> str:
    r = _get_resume_entry(resume_id)
    path = r.get("path")
    if not path:
        raise ValueError(f"Resume '{resume_id}' missing 'path' in application_assets.json.")
    resolved = _resolve_local_path(path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Resume file not found: '{resolved}'")
    return resolved


def _get_profile() -> Dict[str, str]:
    assets = _load_assets()
    profile = assets.get("profile") or {}
    return {k: ("" if v is None else str(v)) for k, v in profile.items()}


def get_applicant_profile() -> Dict[str, str]:
    """
    Public wrapper around the internal profile loader.
    Used by tools so they don't depend on private helpers.
    """
    return _get_profile()


def resolve_resume_path(resume_id: str) -> str:
    """Public wrapper for resolving resume files from application_assets.json."""
    return _resolve_resume_path(resume_id)


def get_default_apply_asset_ids() -> tuple[str, str]:
    """First resume id and first cover letter template id (deterministic pipeline defaults)."""
    assets = _load_assets()
    resumes = [r for r in (assets.get("resumes") or []) if isinstance(r, dict) and r.get("id")]
    templates = [t for t in (assets.get("cover_letter_templates") or []) if isinstance(t, dict) and t.get("id")]
    if not resumes:
        raise ValueError("application_assets.json: add at least one resume with 'id'.")
    if not templates:
        raise ValueError("application_assets.json: add at least one cover_letter_templates entry with 'id'.")
    return str(resumes[0]["id"]), str(templates[0]["id"])


def list_reference_sources() -> List[Dict[str, Any]]:
    """
    Non-application documents (e.g. LinkedIn PDF export) kept for tooling such as a resume generator.
    Deliberately excluded from job-pipeline resume picking and load_application_assets() Gemini payload.
    """
    assets = _load_assets()
    raw = assets.get("reference_sources") or []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        path = str(item.get("path") or "").strip()
        resolved = _resolve_local_path(path) if path else ""
        out.append(
            {
                "id": str(item.get("id")),
                "kind": str(item.get("kind") or ""),
                "path": resolved,
                "exists": bool(resolved and os.path.isfile(resolved)),
                "notes": str(item.get("notes") or ""),
            }
        )
    return out


def resolve_reference_source_path(source_id: str) -> str:
    """Absolute path to a reference_sources entry; raises if missing or not on disk."""
    for x in list_reference_sources():
        if str(x.get("id")) != str(source_id):
            continue
        p = str(x.get("path") or "")
        if not x.get("exists"):
            raise FileNotFoundError(f"Reference source '{source_id}' file missing: {p}")
        return p
    raise ValueError(f"Unknown reference_source id: '{source_id}'")

