"""
Export tailored cover letters to Markdown and PDF.

PDF path (Step 0 verdict leaf a): rendercv TextEntry under sections.cover_letter,
classic theme, shared basename with tailored resumes via tailored_resume_output_basename().
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from job_pipeline.bootstrap_resume_profile import load_consolidated_profile
from job_pipeline.cover_letter_tailor import cover_letter_prose_blocks
from job_pipeline.rendercv_export import (
    _normalize_phone_rendercv,
    _run_rendercv_cli,
    _yaml_str,
    tailored_resume_output_basename,
)


def _profile_salutation(profile: Dict[str, Any], salutation_override: str = "") -> str:
    override = (salutation_override or "").strip()
    if override:
        return override if override.endswith(",") else f"{override},"
    contact = profile.get("contact") if isinstance(profile.get("contact"), dict) else {}
    sal = str(contact.get("salutation") or "").strip()
    if sal:
        return sal if sal.endswith(",") else f"{sal},"
    return "Dear Hiring Team,"


def _profile_signoff(profile: Dict[str, Any]) -> str:
    contact = profile.get("contact") if isinstance(profile.get("contact"), dict) else {}
    sign = str(contact.get("signoff") or "").strip()
    if sign:
        return sign
    name = str(profile.get("name") or "").strip() or "Applicant"
    return f"Sincerely,\n{name}"


def _recipient_line(company: str) -> str:
    co = (company or "").strip()
    if co and co.lower() not in ("the company", "company"):
        return f"Hiring Team, {co}"
    return "Hiring Team"


def _interleave_paragraphs(blocks: List[str]) -> List[str]:
    """Insert a blank line between every two consecutive prose blocks so the
    rendered output shows real paragraph breaks (recruiters skim — wall-of-text
    cover letters lose readers)."""
    out: List[str] = []
    for i, b in enumerate(blocks):
        if i > 0:
            out.append("")
        out.append(b)
    return out


def assemble_cover_letter_markdown(
    content: Dict[str, Any],
    *,
    company: str = "",
    salutation_override: str = "",
    profile: Optional[Dict[str, Any]] = None,
    letter_date: Optional[date] = None,
) -> str:
    prof = profile if isinstance(profile, dict) else load_consolidated_profile()
    d = letter_date or date.today()
    date_line = d.strftime("%B %d, %Y")
    salutation = _profile_salutation(prof, salutation_override)
    signoff = _profile_signoff(prof)
    recipient = _recipient_line(company)

    parts: List[str] = [date_line, "", recipient, "", salutation, ""]
    parts.extend(_interleave_paragraphs(cover_letter_prose_blocks(content)))
    parts.extend(["", signoff])
    return "\n".join(parts).strip() + "\n"


def cover_letter_plain_text_for_storage(
    content: Dict[str, Any],
    *,
    company: str = "",
    salutation_override: str = "",
    profile: Optional[Dict[str, Any]] = None,
) -> str:
    """Letter body suitable for DB cover_letter_text (no date/header block)."""
    prof = profile if isinstance(profile, dict) else load_consolidated_profile()
    salutation = _profile_salutation(prof, salutation_override)
    signoff = _profile_signoff(prof)
    blocks = (
        [salutation, ""]
        + _interleave_paragraphs(cover_letter_prose_blocks(content))
        + ["", signoff]
    )
    _ = _recipient_line(company)
    return "\n".join(blocks).strip()


def export_cover_letter_markdown(
    content: Dict[str, Any],
    *,
    company: str,
    job_title: str,
    item_id: int = 0,
    outputs_root: Optional[str] = None,
    salutation_override: str = "",
    profile: Optional[Dict[str, Any]] = None,
) -> str:
    md = assemble_cover_letter_markdown(
        content,
        company=company,
        salutation_override=salutation_override,
        profile=profile,
    )
    root = Path(outputs_root or os.getenv("JOB_PIPELINE_OUTPUTS_ROOT") or os.getcwd())
    out_dir = root / "generated_resumes"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = tailored_resume_output_basename(company=company, job_title=job_title, item_id=item_id)
    path = out_dir / f"{base}_cover_letter.md"
    path.write_text(md, encoding="utf-8")
    return str(path.resolve())


def _build_rendercv_cover_yaml(
    letter_body: str,
    profile: Dict[str, Any],
    *,
    theme: str = "classic",
) -> str:
    contact = profile.get("contact") if isinstance(profile.get("contact"), dict) else {}
    name = str(profile.get("name") or "Applicant").strip()
    email = str(contact.get("email") or "").strip()
    location = str(contact.get("location") or "").strip()
    phone_raw = str(contact.get("phone") or "").strip()
    phone = _normalize_phone_rendercv(phone_raw)

    lines = [
        "cv:",
        f"  name: {_yaml_str(name)}",
    ]
    if location:
        lines.append(f"  location: {_yaml_str(location)}")
    if email:
        lines.append(f"  email: {_yaml_str(email)}")
    if phone:
        lines.append(f"  phone: {_yaml_str(phone)}")

    linkedin = str(contact.get("linkedin") or "").strip()
    github = str(contact.get("github") or "").strip()
    website = str(contact.get("website") or "").strip()
    socials: List[Tuple[str, str]] = []
    if linkedin:
        user = linkedin
        m = re.search(r"linkedin\.com/(?:in|pub)/([^/?#]+)", linkedin, flags=re.IGNORECASE)
        if m:
            user = m.group(1)
        socials.append(("LinkedIn", user))
    if github:
        user = github
        m = re.search(r"github\.com/([^/?#]+)", github, flags=re.IGNORECASE)
        if m:
            user = m.group(1)
        socials.append(("GitHub", user))
    if website:
        socials.append(("Website", website))
    if socials:
        lines.append("  social_networks:")
        for net, user in socials:
            lines.append(f"    - network: {_yaml_str(net)}")
            lines.append(f"      username: {_yaml_str(user)}")

    lines.append("  sections:")
    lines.append("    cover_letter:")
    # RenderCV renders each TextEntry list item as its own paragraph in the PDF.
    # Splitting on blank-line boundaries makes the PDF show real paragraph breaks
    # instead of one wall of text — a single scalar with embedded "\n\n" was being
    # collapsed by the classic theme to a single flowing block.
    paragraphs = [p.strip() for p in letter_body.split("\n\n") if p.strip()]
    for para in paragraphs or [letter_body]:
        lines.append(f"      - {_yaml_str(para)}")
    lines.append("design:")
    lines.append(f"  theme: {_yaml_str(theme)}")
    return "\n".join(lines) + "\n"


def _render_weasyprint_pdf(html: str, dest: Path) -> Tuple[str, str]:
    try:
        from weasyprint import HTML
    except ImportError:
        return "", "weasyprint_not_installed"
    try:
        HTML(string=html).write_pdf(str(dest))
        return str(dest.resolve()), ""
    except Exception as exc:
        return "", f"weasyprint_failed:{exc}"


def render_cover_letter_pdf(
    content: Dict[str, Any],
    *,
    company: str,
    job_title: str,
    item_id: int = 0,
    outputs_root: Optional[str] = None,
    salutation_override: str = "",
    profile: Optional[Dict[str, Any]] = None,
    theme: str = "classic",
    timeout_sec: int = 180,
    prefer_weasyprint: bool = False,
) -> Tuple[str, str]:
    """
    Render cover letter PDF. Default: rendercv TextEntry (leaf a).
    Returns (pdf_path_or_empty, diagnostic).
    """
    prof = profile if isinstance(profile, dict) else load_consolidated_profile()
    md = assemble_cover_letter_markdown(
        content,
        company=company,
        salutation_override=salutation_override,
        profile=prof,
    )

    root = Path(outputs_root or os.getenv("JOB_PIPELINE_OUTPUTS_ROOT") or os.getcwd())
    out_dir = root / "generated_resumes"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = tailored_resume_output_basename(company=company, job_title=job_title, item_id=item_id)
    yaml_path = out_dir / f"{base}_cover_letter.yaml"
    pdf_path = out_dir / f"{base}_cover_letter.pdf"

    if prefer_weasyprint:
        html = f"<!DOCTYPE html><html><body><pre style='font-family:serif;white-space:pre-wrap'>{md}</pre></body></html>"
        path, diag = _render_weasyprint_pdf(html, pdf_path)
        if path:
            return path, ""
        return "", diag or "weasyprint_fallback_failed"

    yaml_text = _build_rendercv_cover_yaml(md, prof, theme=theme)
    yaml_path.write_text(yaml_text, encoding="utf-8")
    pdf_path_str, diag = _run_rendercv_cli(yaml_text, out_dir, f"{base}_cover_letter", timeout_sec=timeout_sec)
    if pdf_path_str:
        return pdf_path_str, ""
    return "", diag or "rendercv_failed"
