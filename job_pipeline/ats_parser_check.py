"""Post-render ATS parser sanity checks on PDF or markdown text."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _extract_pdf_text(path: str) -> str:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return ""
    parts: List[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(t.strip())
    except Exception:
        return ""
    return "\n".join(parts)


def experience_companies_from_content(content: Optional[Dict[str, Any]]) -> List[str]:
    """Employer names from tailored resume JSON — used for ATS parser checks."""
    if not isinstance(content, dict):
        return []
    out: List[str] = []
    seen: set = set()
    exps = content.get("experience")
    if not isinstance(exps, list):
        return out
    for exp in exps:
        if not isinstance(exp, dict):
            continue
        co = str(exp.get("company") or "").strip()
        key = co.lower()
        if co and key not in seen:
            seen.add(key)
            out.append(co)
    return out


def check_extracted_resume_text(text: str, *, expected_companies: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Assert parser-friendly structure in extracted resume text.

    Returns {ok, issues, warnings}.
    """
    issues: List[str] = []
    warnings: List[str] = []
    t = text or ""
    if len(t.strip()) < 80:
        issues.append("extracted text too short — PDF may be image-only or empty")
        return {"ok": False, "issues": issues, "warnings": warnings}

    if "\ufffe" in t or "￾" in t:
        issues.append("garbage glyph detected in extracted text")

    if re.search(r"(.{80,})\1", t):
        warnings.append("possible duplicated text block in extraction")

    expected = expected_companies or []
    for co in expected:
        if co and co.lower() not in t.lower():
            warnings.append(f"company name not found in extracted text: {co}")

    # Dates near year pattern
    if not re.search(r"20\d{2}", t):
        warnings.append("no recent year found in extracted text")

    # Broken bullet heuristic: many single-char lines
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    single_char = sum(1 for ln in lines if len(ln) <= 2)
    if single_char > len(lines) * 0.15:
        warnings.append("many fragmented lines — bullets may be broken in PDF")

    return {"ok": not issues, "issues": issues, "warnings": warnings}


def check_resume_pdf(
    pdf_path: str,
    *,
    expected_companies: Optional[List[str]] = None,
) -> Dict[str, Any]:
    text = _extract_pdf_text(pdf_path)
    result = check_extracted_resume_text(text, expected_companies=expected_companies)
    result["pdf_path"] = pdf_path
    result["extracted_chars"] = len(text)
    if not text and Path(pdf_path).is_file():
        result["warnings"] = list(result.get("warnings") or []) + [
            "pdfplumber unavailable or extraction failed — skipped deep ATS check"
        ]
    return result
