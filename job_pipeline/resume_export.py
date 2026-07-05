"""
Export tailored resume JSON to Markdown on disk.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from job_pipeline.rendercv_export import clean_skill_items, tailored_resume_output_basename


def _fmt_date_range(start: str, end: str) -> str:
    """Best-effort 'Sep 2024 – Mar 2026' / 'Sep 2024 – Present' from raw YAML dates."""
    MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    def fmt_one(d: str) -> str:
        d = (d or "").strip()
        if not d:
            return ""
        if d.lower() in ("present", "current", "now"):
            return "Present"
        # YYYY-MM
        if len(d) >= 7 and d[4] == "-":
            try:
                y, m = int(d[:4]), int(d[5:7])
                if 1 <= m <= 12:
                    return f"{MONTHS[m-1]} {y}"
            except ValueError:
                pass
        # YYYY
        if len(d) == 4 and d.isdigit():
            return d
        return d
    s, e = fmt_one(start), fmt_one(end)
    if s and e:
        return f"{s} – {e}"
    return s or e


def export_tailored_resume_markdown(
    content: Dict[str, Any],
    *,
    company: str,
    job_title: str,
    item_id: int,
    outputs_root: str,
    education: Optional[List[Dict[str, Any]]] = None,
    military_service: Optional[List[Dict[str, Any]]] = None,
    certifications: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Writes Markdown under outputs_root/generated_resumes/.
    Returns absolute path to the file.

    Education + military_service + certifications are passed in from the
    consolidated profile because they're not part of the LLM-tailored
    `content` dict but DO belong on the resume.
    """
    # Audit-language scrub at the MD export boundary. The PDF path scrubs in
    # rendercv_export; this gate keeps the MD output in lockstep so reviewers
    # never see hedges that the rendered PDF doesn't.
    try:
        from job_pipeline.anti_fluff import strip_anti_fluff_content

        content, _ = strip_anti_fluff_content(dict(content))
    except Exception:
        pass

    root = Path(outputs_root) / "generated_resumes"
    root.mkdir(parents=True, exist_ok=True)
    fn = f"{tailored_resume_output_basename(company=company, job_title=job_title, item_id=item_id)}.md"
    path = root / fn

    lines: list[str] = []
    summ = (content.get("summary") or "").strip()
    if summ:
        lines.append("# Professional summary\n\n")
        lines.append(summ + "\n\n")

    exps = content.get("experience") if isinstance(content.get("experience"), list) else []
    if exps:
        lines.append("## Experience\n\n")
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            title = (exp.get("title") or "").strip()
            comp = (exp.get("company") or "").strip()
            dur = (exp.get("duration") or "").strip()
            lines.append(f"### {title}" + (f" — {comp}" if comp else "") + "\n\n")
            if dur:
                lines.append(f"*{dur}*\n\n")
            for b in exp.get("bullets") or []:
                if b:
                    lines.append(f"- {str(b).strip()}\n")
            lines.append("\n")

    skills = content.get("skills") if isinstance(content.get("skills"), dict) else {}
    if skills:
        lines.append("## Skills\n\n")
        tech = skills.get("technical") if isinstance(skills.get("technical"), list) else []
        soft = skills.get("soft") if isinstance(skills.get("soft"), list) else []
        tech_clean = clean_skill_items(tech)
        soft_clean = clean_skill_items(soft)
        if tech_clean:
            lines.append("**Technical:** " + ", ".join(tech_clean) + "\n\n")
        if soft_clean:
            lines.append("**Soft:** " + ", ".join(soft_clean) + "\n\n")

    # Education — NEW. Previously dropped from MD even though YAML/PDF had it.
    edu_list = education if isinstance(education, list) else []
    if edu_list:
        lines.append("## Education\n\n")
        for edu in edu_list:
            if not isinstance(edu, dict):
                continue
            school = (edu.get("school") or edu.get("institution") or "").strip()
            degree = (edu.get("degree") or "").strip()
            area = (edu.get("area") or edu.get("field_of_study") or "").strip()
            loc = (edu.get("location") or "").strip()
            grad = (edu.get("graduation_display") or edu.get("end_date") or "").strip()
            heading_bits = [b for b in (degree, area) if b]
            heading = " in ".join(heading_bits) if heading_bits else school
            if school and heading != school:
                heading = f"{heading} — {school}"
            elif not heading:
                heading = "(no school)"
            lines.append(f"### {heading}\n\n")
            meta_bits = [x for x in (loc, grad) if x and x.lower() != "present"]
            if meta_bits:
                lines.append(f"*{' · '.join(meta_bits)}*\n\n")
            details = (edu.get("details") or "").strip()
            if details:
                lines.append(f"{details}\n\n")
            highlights = edu.get("highlights") or []
            if isinstance(highlights, list):
                for h in highlights:
                    if h:
                        lines.append(f"- {str(h).strip()}\n")
                if highlights:
                    lines.append("\n")

    # Military — NEW. Previously dropped from MD.
    mil_list = military_service if isinstance(military_service, list) else []
    if mil_list:
        lines.append("## Military Service\n\n")
        for mil in mil_list:
            if not isinstance(mil, dict):
                continue
            branch = (mil.get("branch") or mil.get("company") or "").strip()
            role = (mil.get("role") or mil.get("title") or mil.get("position") or "").strip()
            start = (mil.get("start_date") or "").strip()
            end = (mil.get("end_date") or "").strip()
            heading = role or branch
            if branch and heading != branch:
                heading = f"{heading} — {branch}"
            lines.append(f"### {heading}\n\n")
            dur = _fmt_date_range(start, end)
            if dur:
                lines.append(f"*{dur}*\n\n")
            bullets = mil.get("bullets") or mil.get("highlights") or []
            if isinstance(bullets, list):
                for b in bullets:
                    if b:
                        lines.append(f"- {str(b).strip()}\n")
                if bullets:
                    lines.append("\n")

    # Certifications — emit if present.
    cert_list = certifications if isinstance(certifications, list) else []
    if cert_list:
        lines.append("## Certifications\n\n")
        for c in cert_list:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or c.get("title") or "").strip()
            issuer = (c.get("issuer") or c.get("organization") or "").strip()
            year = (c.get("year") or c.get("date") or "").strip()
            row_bits = [b for b in (name, issuer, year) if b]
            if row_bits:
                lines.append("- " + " · ".join(row_bits) + "\n")
        if cert_list:
            lines.append("\n")

    projects = content.get("projects") if isinstance(content.get("projects"), list) else []
    if projects:
        lines.append("## Projects\n\n")
        for p in projects:
            if not isinstance(p, dict):
                continue
            nm = (p.get("name") or "").strip()
            if nm:
                lines.append(f"### {nm}\n\n")
            if p.get("description"):
                lines.append(str(p["description"]).strip() + "\n\n")
            if p.get("impact"):
                lines.append(f"*Impact:* {str(p['impact']).strip()}\n\n")

    path.write_text("".join(lines), encoding="utf-8")
    return str(path.resolve())
