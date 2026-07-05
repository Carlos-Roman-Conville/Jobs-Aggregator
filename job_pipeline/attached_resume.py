"""
Parse attached resume files (PDF/DOCX) for cover-letter grounding.
Sidecar .txt cache lives next to the source file.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple


MIN_CHARS = 400
MAX_CONTROL_RATIO = 0.05


def _control_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    ctrl = 0
    for ch in text:
        o = ord(ch)
        if o <= 0x1F and ch not in "\t\n\r":
            ctrl += 1
    return ctrl / max(len(text), 1)


def _sidecar_path(source: Path) -> Path:
    return source.with_suffix(source.suffix + ".parsed.txt")


def _read_pdf(path: Path) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
    return "\n\n".join(parts)


def _read_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_raw(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"unsupported resume format: {suffix}")


def validate_parsed_resume_text(text: str) -> Tuple[bool, str]:
    t = (text or "").strip()
    if len(t) < MIN_CHARS:
        return False, f"parsed text too short ({len(t)} chars, need >={MIN_CHARS})"
    ratio = _control_char_ratio(t)
    if ratio >= MAX_CONTROL_RATIO:
        return False, f"control character ratio too high ({ratio:.1%})"
    return True, ""


def parse_attached_resume(path: str, *, use_cache: bool = True) -> Tuple[str, Optional[str]]:
    """
    Return (text, warning). warning is set when parsing fails quality gates.
    """
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        return "", f"attached resume not found: {src}"

    cache = _sidecar_path(src)
    if use_cache and cache.is_file():
        cached = cache.read_text(encoding="utf-8", errors="replace")
        ok, reason = validate_parsed_resume_text(cached)
        if ok:
            return cached, None
        return "", f"cached parse failed quality gate: {reason}"

    try:
        raw = _extract_raw(src)
    except Exception as exc:
        return "", f"resume parse error: {exc}"

    raw = re.sub(r"\r\n?", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()

    ok, reason = validate_parsed_resume_text(raw)
    if ok:
        try:
            cache.write_text(raw, encoding="utf-8")
        except OSError:
            pass
        return raw, None
    return "", f"resume parse quality gate failed: {reason}"
