"""
Render a tailored resume to ATS-friendly PDF via the RenderCV CLI.

Two entry points:
- render_tailored_resume_pdf(content, contact, output_dir, ...) — tailored resume PDF.
- Cover letters use job_pipeline/cover_letter_export.render_cover_letter_pdf.

If `rendercv` is not on PATH, render calls return ("", diagnostic) and the caller
should fall back to the markdown export.

CLI note (RenderCV ~2.3 + current Typer): top-level ``rendercv --help`` (and sometimes
subcommand ``--help``) can crash inside Typer; this repo only invokes
``rendercv render <yaml>``, which works if RenderCV installed.

Markdown, YAML, and PDF share one stem via ``tailored_resume_output_basename()``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Optional, Tuple

from application_assets import load_application_assets

# Bump when rendering / skills-sanitizer logic changes. The dashboard displays
# this so you can confirm a real process restart actually loaded the new code
# (a browser refresh / "Rerun" will NOT change it).
ENGINE_BUILD = "2026-05-22-skills-sanitizer-v1"


def _digits_only(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def _normalize_phone_rendercv(raw: str) -> str:
    """RenderCV's schema often rejects NANP without country code; emit E.164 when possible."""
    p = str(raw or "").strip()
    if not p:
        return ""
    d = _digits_only(p)
    if len(d) == 10:
        return f"+1{d}"
    if len(d) == 11 and d.startswith("1"):
        return f"+{d}"
    return p


def _split_yyyy_mm_date_range(blob: str) -> Tuple[str, str]:
    """
    Models sometimes collapse a tenure into one field using an en-dash range.
    RenderCV expects separate start/end scalars (YYYY[-MM[-DD]], YYYY, or 'present').
    """
    s = (blob or "").strip()
    if not s:
        return "", ""
    norm = s.replace("\u2013", "-").replace("\u2014", "-")
    parts = re.findall(r"\b(\d{4}(?:-\d{2}(?:-\d{2})?)?)\b", norm)
    if len(parts) >= 2:
        return parts[0], parts[-1]
    if len(parts) == 1:
        return "", parts[0]
    return "", ""


_MONTH_TO_NUM: Dict[str, str] = {
    "jan": "01",
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "sept": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}


def normalize_rendercv_date(raw: str) -> str:
    """
    Convert common date strings to RenderCV-accepted forms: YYYY, YYYY-MM, YYYY-MM-DD, or present.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in ("present", "current", "now", "ongoing"):
        return "present"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}", s):
        return s

    m = re.match(r"^([A-Za-z]{3,9})\s+(\d{4})$", s.strip(), re.I)
    if m:
        mon_key = m.group(1).lower()
        num = _MONTH_TO_NUM.get(mon_key) or _MONTH_TO_NUM.get(mon_key[:3], "")
        if num:
            return f"{m.group(2)}-{num}"

    m2 = re.match(r"^(\d{4})\s*[-/]\s*(\d{1,2})$", s)
    if m2:
        return f"{m2.group(1)}-{int(m2.group(2)):02d}"

    # Fall back: extract first ISO-like token if embedded in prose
    iso = re.search(r"\b(\d{4}-\d{2}(?:-\d{2})?)\b", s)
    if iso:
        return iso.group(1)
    year = re.search(r"\b(20\d{2}|19\d{2})\b", s)
    if year:
        return year.group(1)
    return s


# ---------------------------------------------------------------------------
# YAML helpers (tiny — avoid importing PyYAML just for emission)
# ---------------------------------------------------------------------------

def _yaml_str(s: Any) -> str:
    """Quote a string for safe YAML scalar emission."""
    if s is None:
        return '""'
    t = str(s)
    if not t:
        return '""'
    # Use JSON quoting — always-safe double-quoted form.
    return json.dumps(t, ensure_ascii=False)


def _safe_filename_chunk(s: str, max_len: int = 56) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", str(s or "resume"), flags=re.UNICODE)
    return cleaned.strip("_")[:max_len] or "resume"


# Kept for callers that imported the old name.
def safe_filename_chunk(s: str, max_len: int = 32) -> str:
    return _safe_filename_chunk(s, max_len=max_len)


TAILORED_FILENAME_SEGMENT_MAX = 56


def tailored_resume_output_basename(
    *,
    company: str,
    job_title: str,
    item_id: int = 0,
) -> str:
    """
    One stem for Markdown, YAML, and PDF under generated_resumes/.

    Empty company/title collapse to the same defaults used by tailor_resume_from_jd
    synthetic rows (“the company” / “the role”) so exporters stay aligned.
    """
    cn = (company or "").strip() or "the company"
    jt = (job_title or "").strip() or "the role"
    co = _safe_filename_chunk(cn, max_len=TAILORED_FILENAME_SEGMENT_MAX)
    jo = _safe_filename_chunk(jt, max_len=TAILORED_FILENAME_SEGMENT_MAX)
    return f"tailored_{int(item_id)}_{co}_{jo}"


# ---------------------------------------------------------------------------
# YAML builder for the actual tailored content
# ---------------------------------------------------------------------------

_MIL_BRANCH_REGEXES = tuple(
    re.compile(p, re.I)
    for p in (
        r"\b(u\.s\.?\s*)?army(\s+reserve|\s+national\s+guard)?\b",
        r"\b(u\.s\.?\s*)?navy\b",
        r"\b(u\.s\.?\s*)?air\s+force\b",
        r"\b(u\.s\.?\s*)?coast\s+guard\b",
        r"\b(u\.s\.?\s*)?space\s+force\b",
        r"\bmarine\s+corps\b",
        r"\bmarines\b",
        r"\bnational\s+guard\b",
    )
)


def _canonical_company_key(name: str) -> str:
    """Normalize company name for same-employer dedup. Strips location suffix
    ("BEAT THE BOMB, Philadelphia, PA" -> "beat the bomb") and non-alphanumeric chars."""
    s = (name or "").lower().strip()
    if not s:
        return ""
    # Drop location suffix after the first comma.
    s = s.split(",")[0].strip()
    return re.sub(r"[^a-z0-9]+", "", s)


def dedupe_experience_self(
    content: Dict[str, Any],
    issues: Optional[List[str]] = None,
) -> None:
    """Drop later experience entries that re-list the same employer.

    The LLM sometimes returns two entries for the same company — a tailored one
    followed by a verbatim copy of the consolidated-profile raw bullets. Keep
    the FIRST occurrence (the tailored one in practice) and discard the rest.
    Matches by canonical company key. Idempotent.
    """
    if not isinstance(content, dict):
        return
    exps = content.get("experience")
    if not isinstance(exps, list) or len(exps) < 2:
        return

    seen: set = set()
    keep: List[Any] = []
    dropped: List[str] = []
    for exp in exps:
        if not isinstance(exp, dict):
            keep.append(exp)
            continue
        k = _canonical_company_key(str(exp.get("company") or ""))
        if k and k in seen:
            dropped.append(str(exp.get("company") or "").strip())
            continue
        if k:
            seen.add(k)
        keep.append(exp)

    if dropped:
        content["experience"] = keep
        if issues is not None:
            for co in dropped:
                issues.append(f"deduplicated duplicate experience entry: {co}")


def dedupe_experience_vs_military(
    content: Dict[str, Any],
    military_service: Optional[List[Dict[str, Any]]],
    issues: Optional[List[str]],
) -> None:
    """
    Drop civilian experience rows that duplicate consolidated military_service
    (same employer appearing twice in YAML).
    """
    ms_in = military_service if isinstance(military_service, list) else []
    exps = content.get("experience")
    if not isinstance(exps, list) or not exps:
        return
    if not ms_in:
        return

    def _matches_military_company(co: str) -> bool:
        cl = (co or "").strip().lower()
        if not cl or "federal reserve" in cl:
            return False
        for m in ms_in:
            if not isinstance(m, dict):
                continue
            br = (m.get("branch") or "").strip().lower()
            if len(br) >= 4 and (br in cl or cl in br):
                return True
        for rx in _MIL_BRANCH_REGEXES:
            if rx.search(co):
                return True
        if re.search(r"\bmarine\b", co, re.I) and not re.search(r"submarine", co, re.I):
            return True
        if re.search(r"\breserve\b", co, re.I):
            return True
        return False

    keep: List[Any] = []
    for exp in exps:
        if not isinstance(exp, dict):
            keep.append(exp)
            continue
        co = str(exp.get("company") or "").strip()
        if _matches_military_company(co):
            msg = (
                f"deduplicated {co} from experience (already in military_service)"
            )
            if issues is not None:
                issues.append(msg)
            continue
        keep.append(exp)
    content["experience"] = keep


def _split_degree_area(raw: str) -> Tuple[str, str]:
    """
    Split 'Bachelor of Arts in Political Science' → degree type + field.
    If no ' in ' delimiter, put everything in area and leave degree empty.
    """
    s = (raw or "").strip()
    if not s:
        return "", ""
    low = s.lower()
    idx = low.find(" in ")
    if idx == -1:
        return "", s
    left = s[:idx].strip()
    right = s[idx + 4 :].strip()
    if left and right:
        return left, right
    return "", s


def _emit_experience_yaml(entries: List[Dict[str, Any]]) -> str:
    """Emit RenderCV 2.x experience section (list of ExperienceEntry dicts under `experience:`)."""
    if not entries:
        return ""
    lines = ["    experience:"]
    for exp in entries:
        if not isinstance(exp, dict):
            continue
        title = (exp.get("title") or "").strip() or "Position"
        company = (exp.get("company") or "").strip() or "Company"
        loc = (exp.get("location") or "").strip()
        start_date = normalize_rendercv_date((exp.get("start_date") or "").strip())
        end_date = normalize_rendercv_date((exp.get("end_date") or exp.get("duration") or "").strip())
        if not start_date or re.search(r"[\u2013\u2014\-].*\d{4}", end_date):
            r0, r1 = _split_yyyy_mm_date_range(end_date)
            if r0 and not start_date:
                start_date = normalize_rendercv_date(r0)
            if r1:
                end_date = normalize_rendercv_date(r1)
        bullets = [str(b).strip() for b in (exp.get("bullets") or []) if str(b).strip()]
        lines.append(f"      - company: {_yaml_str(company)}")
        lines.append(f"        position: {_yaml_str(title)}")
        if loc:
            lines.append(f"        location: {_yaml_str(loc)}")
        if start_date:
            lines.append(f"        start_date: {_yaml_str(start_date)}")
        if end_date:
            lines.append(f"        end_date: {_yaml_str(end_date)}")
        if bullets:
            lines.append("        highlights:")
            for b in bullets:
                lines.append(f"          - {_yaml_str(b)}")
    return "\n".join(lines)


def _emit_education_yaml(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return ""
    lines = ["    education:"]
    for e in entries:
        if not isinstance(e, dict):
            continue
        raw_deg = (e.get("degree") or "").strip()
        deg_type, area_only = _split_degree_area(raw_deg)
        school = (e.get("school") or "").strip() or "Institution"
        loc = (e.get("location") or "").strip()
        start = (e.get("start_date") or "").strip()
        end = (e.get("end_date") or "").strip()
        details = (e.get("details") or "").strip()
        area_scalar = area_only if area_only else (raw_deg or "Degree")
        lines.append(f"      - institution: {_yaml_str(school)}")
        lines.append(f"        area: {_yaml_str(area_scalar)}")
        if deg_type:
            lines.append(f"        degree: {_yaml_str(deg_type)}")
        if loc:
            lines.append(f"        location: {_yaml_str(loc)}")
        if start:
            lines.append(f"        start_date: {_yaml_str(start)}")
        if end:
            lines.append(f"        end_date: {_yaml_str(end)}")
        if details:
            lines.append("        highlights:")
            lines.append(f"          - {_yaml_str(details)}")
    return "\n".join(lines)


def clean_skill_items(items: Any) -> List[str]:
    """Sanitize a skills list before rendering.

    Drops empty entries and "orphan annotation" junk such as "(user-level)" —
    which the tailoring model occasionally emits when a skill name is lost but
    its parenthetical qualifier survives — de-duplicates case-insensitively
    while preserving order, and applies the Phase 0 semantic deduper so concept
    variants ("Ticketing / ITSM" / "ticketing" / "ITSM") collapse to one.
    """
    pre: List[str] = []
    seen_exact: set = set()
    for raw in items if isinstance(items, list) else []:
        if raw is None:
            continue
        s = str(raw).strip().strip(",").strip()
        if not s:
            continue
        bare = re.sub(r"\([^)]*\)", "", s)
        if not re.search(r"[A-Za-z0-9]", bare):
            continue  # only a parenthetical / punctuation -> drop
        key = s.lower()
        if key in seen_exact:
            continue
        seen_exact.add(key)
        pre.append(s)

    # Semantic dedupe — collapses concept variants to canonical labels.
    try:
        from job_pipeline.integrity_guards import dedupe_skills_semantic

        cleaned, _notes = dedupe_skills_semantic(pre)
        return cleaned
    except Exception:
        return pre


def _emit_skills_yaml(skills: Dict[str, Any]) -> str:
    if not isinstance(skills, dict):
        return ""
    groups = []
    tech = skills.get("technical") if isinstance(skills.get("technical"), list) else []
    tools = skills.get("tools") if isinstance(skills.get("tools"), list) else []
    soft = skills.get("soft") if isinstance(skills.get("soft"), list) else []
    if tech:
        groups.append(("Technical", tech))
    if tools:
        groups.append(("Tools", tools))
    if soft:
        groups.append(("Soft skills", soft))
    if not groups:
        return ""
    lines = ["    skills:"]
    for label, items in groups:
        cleaned = ", ".join(clean_skill_items(items))
        if not cleaned:
            continue
        lines.append(f"      - label: {_yaml_str(label)}")
        lines.append(f"        details: {_yaml_str(cleaned)}")
    return "\n".join(lines)


def _emit_projects_yaml(projects: List[Dict[str, Any]]) -> str:
    if not projects:
        return ""
    lines = ["    projects:"]
    for p in projects:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip() or "Project"
        desc = (p.get("description") or "").strip()
        impact = (p.get("impact") or "").strip()
        lines.append(f"      - name: {_yaml_str(name)}")
        h = []
        if desc:
            h.append(desc)
        if impact:
            h.append(f"Impact: {impact}")
        if h:
            lines.append("        highlights:")
            for x in h:
                lines.append(f"          - {_yaml_str(x)}")
    return "\n".join(lines)


def _emit_certifications_yaml(certs: List[Dict[str, Any]]) -> str:
    if not certs:
        return ""
    lines = ["    certifications:"]
    for c in certs:
        if not isinstance(c, dict):
            continue
        nm = (c.get("name") or "").strip()
        if not nm:
            continue
        issuer = (c.get("issuer") or "").strip()
        date = (c.get("date") or "").strip()
        details = " — ".join([x for x in [issuer, date] if x]) or "Certification"
        lines.append(f"      - label: {_yaml_str(nm)}")
        lines.append(f"        details: {_yaml_str(details)}")
    return "\n".join(lines)


def _emit_military_yaml(
    entries: List[Dict[str, Any]],
    *,
    strategy_level: str = "balanced",
) -> str:
    if not entries:
        return ""
    sl = (strategy_level or "balanced").strip().lower()
    max_hi = None if sl == "aggressive" else 2

    lines = ["    military_service:"]
    for m in entries:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "").strip() or "Service Member"
        branch = (m.get("branch") or "").strip() or "Military"
        rank = (m.get("rank") or "").strip()
        position = f"{role}" + (f" ({rank})" if rank else "")
        start = (m.get("start_date") or "").strip()
        end = (m.get("end_date") or "").strip()
        bullets = [str(b).strip() for b in (m.get("bullets") or []) if str(b).strip()]
        if max_hi is not None:
            bullets = bullets[:max_hi]
        lines.append(f"      - company: {_yaml_str(branch)}")
        lines.append(f"        position: {_yaml_str(position)}")
        if start:
            lines.append(f"        start_date: {_yaml_str(start)}")
        if end:
            lines.append(f"        end_date: {_yaml_str(end)}")
        if bullets:
            lines.append("        highlights:")
            for b in bullets:
                lines.append(f"          - {_yaml_str(b)}")
    return "\n".join(lines)


def build_tailored_cv_yaml(
    content: Dict[str, Any],
    contact: Dict[str, Any],
    *,
    name: str = "",
    headline: str = "",
    job_title: str = "",
    military_service: Optional[List[Dict[str, Any]]] = None,
    education: Optional[List[Dict[str, Any]]] = None,
    certifications: Optional[List[Dict[str, Any]]] = None,
    theme: str = "classic",
    strategy_level: str = "balanced",
) -> str:
    """Assemble a full RenderCV YAML doc from tailored content + contact info.

    `job_title` enables role-aware export-time guards (e.g. Python-self-promo strip
    on support-role summaries).
    """
    ms_list = military_service if isinstance(military_service, list) else []
    if isinstance(content, dict) and not content.get("error"):
        # Same-employer dedup must run BEFORE the military dedup — otherwise a
        # second BTB entry (raw bullets) survives because military dedup only
        # checks military_service overlap.
        dedupe_experience_self(content, None)
        dedupe_experience_vs_military(content, ms_list, None)
        # Final-export safety net: internal claim-audit language ("X is not claimed",
        # "I have partial...", "is supported through...") must never reach the PDF,
        # even if the upstream optimizer was skipped or an LLM pass resurfaced it.
        # Idempotent — a no-op if the optimizer already scrubbed.
        from job_pipeline.integrity_guards import (
            strip_ad_gpedit_equivalence,
            strip_claim_audit_from_resume,
            strip_duplicate_phrases_from_summary,
            strip_python_self_promo_from_summary,
            strip_verb_noun_doubleups_in_resume,
        )
        strip_claim_audit_from_resume(content)
        # Drop "Supported user account support" double-ups before the PDF is emitted,
        # in case the LLM resurfaced them after the optimizer's anti-fluff pass.
        strip_verb_noun_doubleups_in_resume(content)
        # "Active Directory basics via Local Group Policy Editor (gpedit.msc)"
        # is the canonical credibility-damaging adjacency leak. Strip even if
        # the LLM produced it after every earlier pass.
        strip_ad_gpedit_equivalence(content)
        # Self-duplicate phrases in the summary read as auto-generated. Strip
        # the second occurrence so e.g. "X workflows, ..., and X workflows at
        # small-shop scale" doesn't ship to PDF.
        strip_duplicate_phrases_from_summary(content)
        # Support-role summaries must not lead with Python self-promo. The LLM
        # recruiter / ATS passes resurface it across builds, so we scrub here too.
        if job_title:
            strip_python_self_promo_from_summary(content, job_title)

    contact = contact or {}
    nm = (name or contact.get("name") or "Applicant").strip()
    email = (contact.get("email") or "").strip()
    phone = _normalize_phone_rendercv(str(contact.get("phone") or ""))
    loc = (contact.get("location") or "").strip()
    linkedin = (contact.get("linkedin") or "").strip()
    website = (contact.get("website") or "").strip()
    github = (contact.get("github") or "").strip()

    summary_line = (content.get("summary") or "").strip()
    experience_block = _emit_experience_yaml(
        content.get("experience") if isinstance(content.get("experience"), list) else []
    )
    skills_block = _emit_skills_yaml(
        content.get("skills") if isinstance(content.get("skills"), dict) else {}
    )
    projects_block = _emit_projects_yaml(
        content.get("projects") if isinstance(content.get("projects"), list) else []
    )
    education_block = _emit_education_yaml(education or [])
    certs_block = _emit_certifications_yaml(certifications or [])
    military_block = _emit_military_yaml(
        ms_list,
        strategy_level=strategy_level,
    )

    lines: List[str] = ["cv:"]
    lines.append(f"  name: {_yaml_str(nm)}")
    if email:
        lines.append(f"  email: {_yaml_str(email)}")
    if phone:
        lines.append(f"  phone: {_yaml_str(phone)}")
    if loc:
        lines.append(f"  location: {_yaml_str(loc)}")
    # RenderCV expects a list of social networks: [{ network: LinkedIn, username: ... }, ...]
    socials: List[Tuple[str, str]] = []
    if linkedin:
        # Strip URL down to username if a full URL was provided.
        username = linkedin
        m = re.search(r"linkedin\.com/(?:in|pub)/([^/?#]+)", linkedin, flags=re.IGNORECASE)
        if m:
            username = m.group(1)
        socials.append(("LinkedIn", username))
    if github:
        username = github
        m = re.search(r"github\.com/([^/?#]+)", github, flags=re.IGNORECASE)
        if m:
            username = m.group(1)
        socials.append(("GitHub", username))
    if website:
        socials.append(("Website", website))
    if socials:
        lines.append("  social_networks:")
        for net, user in socials:
            lines.append(f"    - network: {_yaml_str(net)}")
            lines.append(f"      username: {_yaml_str(user)}")

    lines.append("  sections:")
    # RenderCV 2.x: TextEntry sections are a YAML list of plain strings.
    # Order tuned for civilian hiring context: military last among bio sections.
    if summary_line:
        lines.append("    summary:")
        lines.append(f"      - {_yaml_str(summary_line)}")
    if experience_block:
        lines.append(experience_block)
    if education_block:
        lines.append(education_block)
    if skills_block:
        lines.append(skills_block)
    if military_block:
        lines.append(military_block)
    if certs_block:
        lines.append(certs_block)
    if projects_block:
        lines.append(projects_block)

    # Design block — keep small, classic theme has best PDF reliability.
    lines.append(f"design:")
    lines.append(f"  theme: {_yaml_str(theme)}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------

def _run_rendercv_cli(yaml_text: str, out_dir: Path, base_name: str, timeout_sec: int = 180) -> Tuple[str, str]:
    """Write YAML to a temp dir, run `rendercv render`, copy resulting PDF into out_dir."""
    exe = shutil.which("rendercv")
    if not exe:
        return "", "rendercv_cli_not_on_path"

    out_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="rendercv_", dir=str(out_dir)) as td:
        tdp = Path(td)
        yaml_path = tdp / "cv.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")
        try:
            subprocess.run(
                [exe, "render", str(yaml_path)],
                check=True,
                cwd=str(tdp),
                timeout=timeout_sec,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            return "", f"rendercv_failed:{exc.returncode}:{(exc.stderr or exc.stdout or '')[:1200]}"
        except FileNotFoundError:
            return "", "rendercv_executable_missing_mid_run"
        except subprocess.TimeoutExpired:
            return "", f"rendercv_timeout_after_{timeout_sec}s"
        except Exception as exc:
            return "", f"rendercv_error:{exc}"

        # Find the produced PDF — RenderCV writes into ./rendercv_output by default.
        pdfs = list(tdp.glob("**/*.pdf"))
        if not pdfs:
            return "", "rendercv_pdf_not_found_after_render"
        # Prefer non-html PDFs and the largest one (final output).
        pdfs.sort(key=lambda p: p.stat().st_size, reverse=True)
        chosen = pdfs[0]
        destination = out_dir / f"{base_name}.pdf"
        try:
            destination.write_bytes(chosen.read_bytes())
        except OSError as e:
            return "", f"rendercv_copy_failed:{e}"
        return str(destination.resolve()), ""


# ---------------------------------------------------------------------------
# Public APIs
# ---------------------------------------------------------------------------

def render_tailored_resume_pdf(
    content: Dict[str, Any],
    *,
    contact: Optional[Dict[str, Any]] = None,
    name: str = "",
    headline: str = "",
    job_title: str = "",
    company: str = "",
    item_id: Optional[int] = None,
    outputs_root: Optional[str] = None,
    military_service: Optional[List[Dict[str, Any]]] = None,
    education: Optional[List[Dict[str, Any]]] = None,
    certifications: Optional[List[Dict[str, Any]]] = None,
    theme: str = "classic",
    strategy_level: str = "balanced",
    timeout_sec: int = 180,
) -> Tuple[str, str]:
    """
    Render an actual tailored resume to PDF. Returns (pdf_path_or_empty, diagnostic).

    `content` should be the dict produced by resume_tailor.generate_tailored_sections
    (keys: summary, experience, skills, projects).
    `contact`, `name`, `military_service`, `education`, `certifications` come from
    bootstrap_resume_profile.load_consolidated_profile().
    """
    if not isinstance(content, dict) or content.get("error"):
        return "", "tailored_content_missing_or_errored"

    # Idempotent audit-language scrub at the content level — runs BEFORE YAML
    # build so skills/bullets/summary are all cleaned in one pass. Per project
    # rule (memory: "audit language is blocked at export") this is the
    # last-chance gate before anything renders.
    try:
        from job_pipeline.anti_fluff import strip_anti_fluff_content

        content, _scrub_notes = strip_anti_fluff_content(dict(content))
    except Exception:
        pass

    root = Path(outputs_root or os.getenv("JOB_PIPELINE_OUTPUTS_ROOT") or os.getcwd())
    out_dir = root / "generated_resumes"

    yaml_text = build_tailored_cv_yaml(
        content,
        contact or {},
        name=name,
        headline=headline,
        job_title=job_title,
        military_service=military_service,
        education=education,
        certifications=certifications,
        theme=theme,
        strategy_level=strategy_level,
    )

    # Belt-and-suspenders: YAML-text scrub after build, before write+render.
    # Catches anything that comes from sanitizers that bypass the content dict.
    try:
        from job_pipeline.anti_fluff import scrub_yaml_text

        yaml_text, _yaml_notes = scrub_yaml_text(yaml_text)
    except Exception:
        pass

    # Persist the YAML next to the PDF for debugging / re-rendering.
    out_dir.mkdir(parents=True, exist_ok=True)
    cn = (company or "").strip() or "the company"
    jt = (job_title or "").strip() or "the role"
    base = tailored_resume_output_basename(
        company=cn,
        job_title=jt,
        item_id=int(item_id or 0),
    )
    (out_dir / f"{base}.yaml").write_text(yaml_text, encoding="utf-8")

    pdf_path, diag = _run_rendercv_cli(yaml_text, out_dir, base, timeout_sec=timeout_sec)
    return pdf_path, diag


# Back-compat alias so callers that imported `cv_yaml_skeleton` keep working.
def cv_yaml_skeleton(display_name: str, bullet_skills_yaml_block: str) -> str:
    return (
        "cv:\n"
        f"  name: {_yaml_str(display_name)}\n"
        "  sections:\n"
        "    skills:\n"
        f"      - label: {_yaml_str('Core skills')}\n"
        f"        details: {_yaml_str(bullet_skills_yaml_block)}\n"
        "design:\n"
        "  theme: classic\n"
    )
