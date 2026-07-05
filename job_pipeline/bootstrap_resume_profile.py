"""
Bootstrap a consolidated career profile from every PDF resume in ./resume/.

Why this exists:
- career_understanding.py reads ONE reference PDF (a LinkedIn export). You have
  ~10 distinct resume PDFs sitting in /resume that contain richer/varied facts.
- resume_tailor.py needs a grounded "PROFILE_TEXT" source it can trust.
- This script extracts text from every PDF, asks Gemini to merge them into ONE
  consolidated profile (no invented facts), and writes:
    job_pipeline/consolidated_profile.md   (human-readable + LLM input)
    job_pipeline/consolidated_profile.json (structured for rendercv + tailoring)
  It does **not** modify `job_pipeline/career_master.md` or
  `job_pipeline/search_preferences.md` (those are hand-edited authorities).

Run:
    python -m job_pipeline.bootstrap_resume_profile
    python -m job_pipeline.bootstrap_resume_profile --dry-run
    python -m job_pipeline.bootstrap_resume_profile --resume-dir "E:/path/to/resumes"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from job_pipeline.genai_settings import gemini_model_for
from job_pipeline.llm_provider import LLMWritingError, generate_json, writing_providers_available, writing_providers_missing_error


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_resume_dir() -> Path:
    return _repo_root() / "resume"


def _default_md_path() -> Path:
    return _repo_root() / "job_pipeline" / "consolidated_profile.md"


def _default_json_path() -> Path:
    return _repo_root() / "job_pipeline" / "consolidated_profile.json"


def _backup_path(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return p.with_suffix(p.suffix + f".{ts}.bak")


def extract_pdf_text(path: Path) -> str:
    """Pull text from a single PDF. Returns '' if the PDF is image-only or unreadable."""
    try:
        import pdfplumber  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "pdfplumber not installed. Run: pip install pdfplumber"
        ) from e

    parts: List[str] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(t.strip())
    except Exception as e:
        print(f"  [WARN] could not parse {path.name}: {e}", file=sys.stderr)
        return ""
    return "\n\n".join(parts).strip()


def discover_resume_pdfs(resume_dir: Path) -> List[Path]:
    if not resume_dir.exists():
        return []
    pdfs = sorted(
        [p for p in resume_dir.glob("*.pdf") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,  # newest first — most recent resume becomes the "primary" view
    )
    return pdfs


def gather_pdf_corpus(resume_dir: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    """
    Returns (extracted_docs, skipped_files).
    Each doc: {"filename": ..., "text": ..., "modified": ISO date}
    """
    pdfs = discover_resume_pdfs(resume_dir)
    docs: List[Dict[str, str]] = []
    skipped: List[str] = []

    for p in pdfs:
        print(f"  extracting: {p.name}")
        text = extract_pdf_text(p)
        if len(text) < 80:
            print(f"  [SKIP] {p.name}: too little text (likely image-only PDF)")
            skipped.append(p.name)
            continue
        docs.append(
            {
                "filename": p.name,
                "text": text,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return docs, skipped


def _build_consolidation_prompt(docs: List[Dict[str, str]]) -> str:
    sources_block = []
    for i, d in enumerate(docs, start=1):
        sources_block.append(
            f"=== SOURCE {i}: {d['filename']} (modified {d['modified']}) ===\n{d['text']}\n"
        )
    sources_text = "\n".join(sources_block)
    # Hard-cap so we don't blow context. Newest PDFs are first; older versions are
    # truncated by character count, not by importance.
    sources_text = sources_text[:60000]

    return (
        "You are consolidating multiple versions of the SAME PERSON's resume into "
        "ONE grounded master profile. Newer documents take precedence on conflicts. "
        "DO NOT INVENT facts. If something appears in only one source and looks "
        "thin, include it but note `low_confidence: true`.\n\n"
        "Output ONE valid JSON object only (no markdown fences). Schema:\n"
        "{\n"
        '  "name": string,\n'
        '  "headline": string (one-line professional summary, <=120 chars),\n'
        '  "contact": {"email": string, "phone": string, "location": string, "linkedin": string, "website": string, "github": string},\n'
        '  "summary": string (3-5 sentences, grounded in sources, no buzzwords),\n'
        '  "experience": [\n'
        '    {"title": string, "company": string, "location": string, "start_date": string, "end_date": string, "bullets": [string, ...], "low_confidence": bool}\n'
        "  ],\n"
        '  "education": [\n'
        '    {"degree": string, "school": string, "location": string, "start_date": string, "end_date": string, "details": string}\n'
        "  ],\n"
        '  "military_service": [\n'
        '    {"branch": string, "role": string, "rank": string, "start_date": string, "end_date": string, "bullets": [string, ...]}\n'
        "  ],\n"
        '  "skills": {\n'
        '    "technical": [string, ...],\n'
        '    "soft": [string, ...],\n'
        '    "tools": [string, ...]\n'
        "  },\n"
        '  "light_exposure": [{"skill": string, "framing": string}],\n'
        '  "certifications": [{"name": string, "issuer": string, "date": string}],\n'
        '  "projects": [{"name": string, "description": string, "impact": string}],\n'
        '  "achievements": [string, ...],\n'
        '  "conflicting_facts": [{"fact": string, "sources": [string, ...]}],\n'
        '  "source_files_consolidated": [string, ...]\n'
        "}\n\n"
        "Rules:\n"
        "- Dates use 'YYYY-MM' or 'YYYY' format; 'present' for current. If only year known, use year.\n"
        "- Deduplicate identical experience entries across sources.\n"
        "- If two sources disagree on a date or title, prefer the NEWER source and record the disagreement under conflicting_facts.\n"
        "- Bullets must be paraphrased from source text — no fabricated metrics.\n"
        "- skills.technical: hard skills with tool/tech names. skills.soft: communication/leadership/etc.\n"
        "- If a field has no evidence in sources, use empty string or empty array. Never null.\n\n"
        f"SOURCES:\n{sources_text}\n"
    )


def consolidate_with_gemini(docs: List[Dict[str, str]]) -> Dict[str, Any]:
    if not docs:
        return {"error": "no_documents_to_consolidate"}

    if not writing_providers_available():
        return {"error": writing_providers_missing_error()}

    model = gemini_model_for("bootstrap")
    prompt = _build_consolidation_prompt(docs)
    system = (
        "You consolidate resume source documents into one structured profile. "
        "Return exactly one valid JSON object with no markdown fences or commentary."
    )

    try:
        obj = generate_json(
            "bootstrap",
            system=system,
            user=prompt,
            label="bootstrap",
            gemini_model=model,
            gemini_max_output_tokens=8192,
        )
    except LLMWritingError as e:
        return {"error": f"llm_call_failed: {e}"}
    except Exception as e:
        return {"error": f"llm_call_failed: {e}"}

    if not isinstance(obj, dict):
        return {"error": "model_returned_non_object"}

    obj["source_files_consolidated"] = [d["filename"] for d in docs]
    obj["consolidated_at"] = datetime.now().isoformat(timespec="seconds")
    return obj


def render_profile_markdown(profile: Dict[str, Any]) -> str:
    """Render the consolidated profile as a human-readable markdown document.
    This is what resume_tailor will read as PROFILE_TEXT."""
    lines: List[str] = []

    name = (profile.get("name") or "").strip()
    headline = (profile.get("headline") or "").strip()
    contact = profile.get("contact") if isinstance(profile.get("contact"), dict) else {}

    if name:
        lines.append(f"# {name}")
    if headline:
        lines.append(f"_{headline}_")
    lines.append("")

    contact_bits = []
    for k in ("email", "phone", "location", "linkedin", "website", "github"):
        v = (contact.get(k) or "").strip()
        if v:
            contact_bits.append(f"**{k.title()}:** {v}")
    if contact_bits:
        lines.append(" · ".join(contact_bits))
        lines.append("")

    summary = (profile.get("summary") or "").strip()
    if summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(summary)
        lines.append("")

    # Military service before regular experience — vet-preference flag for federal apps
    mil = profile.get("military_service") if isinstance(profile.get("military_service"), list) else []
    if mil:
        lines.append("## Military Service")
        lines.append("")
        for m in mil:
            if not isinstance(m, dict):
                continue
            head = f"**{m.get('branch') or ''} — {m.get('role') or ''}**"
            rank = (m.get("rank") or "").strip()
            if rank:
                head += f" ({rank})"
            lines.append(head)
            start = m.get("start_date") or ""
            end = m.get("end_date") or ""
            if start or end:
                lines.append(f"_{start} – {end}_")
            for b in m.get("bullets") or []:
                if b:
                    lines.append(f"- {str(b).strip()}")
            lines.append("")

    experiences = profile.get("experience") if isinstance(profile.get("experience"), list) else []
    if experiences:
        lines.append("## Experience")
        lines.append("")
        for exp in experiences:
            if not isinstance(exp, dict):
                continue
            title = (exp.get("title") or "").strip()
            company = (exp.get("company") or "").strip()
            loc = (exp.get("location") or "").strip()
            start = (exp.get("start_date") or "").strip()
            end = (exp.get("end_date") or "").strip()
            head = f"**{title}**"
            if company:
                head += f" — {company}"
            if loc:
                head += f", {loc}"
            lines.append(head)
            if start or end:
                lines.append(f"_{start} – {end}_")
            for b in exp.get("bullets") or []:
                if b:
                    lines.append(f"- {str(b).strip()}")
            if exp.get("low_confidence"):
                lines.append("_(low confidence — verify against original source)_")
            lines.append("")

    edu = profile.get("education") if isinstance(profile.get("education"), list) else []
    if edu:
        lines.append("## Education")
        lines.append("")
        for e in edu:
            if not isinstance(e, dict):
                continue
            head = f"**{(e.get('degree') or '').strip()}** — {(e.get('school') or '').strip()}"
            lines.append(head)
            dates = " – ".join([x for x in [e.get("start_date") or "", e.get("end_date") or ""] if x])
            if dates:
                lines.append(f"_{dates}_")
            det = (e.get("details") or "").strip()
            if det:
                lines.append(det)
            lines.append("")

    skills = profile.get("skills") if isinstance(profile.get("skills"), dict) else {}
    if skills:
        lines.append("## Skills")
        lines.append("")
        for k in ("technical", "tools", "soft"):
            arr = skills.get(k) if isinstance(skills.get(k), list) else []
            if arr:
                lines.append(f"**{k.title()}:** {', '.join(str(x) for x in arr if x)}")
        lines.append("")

    light = profile.get("light_exposure") if isinstance(profile.get("light_exposure"), list) else []
    if light:
        lines.append("## Light exposure (approved phrasing)")
        lines.append("")
        for row in light:
            if not isinstance(row, dict):
                continue
            skill = (row.get("skill") or "").strip()
            framing = (row.get("framing") or row.get("approved_framing") or "").strip()
            if skill and framing:
                lines.append(f"- **{skill}**: {framing}")
        lines.append("")

    certs = profile.get("certifications") if isinstance(profile.get("certifications"), list) else []
    if certs:
        lines.append("## Certifications")
        lines.append("")
        for c in certs:
            if not isinstance(c, dict):
                continue
            issuer = (c.get("issuer") or "").strip()
            date = (c.get("date") or "").strip()
            tail = ""
            if issuer:
                tail += f" — {issuer}"
            if date:
                tail += f" ({date})"
            lines.append(f"- {(c.get('name') or '').strip()}{tail}")
        lines.append("")

    projects = profile.get("projects") if isinstance(profile.get("projects"), list) else []
    if projects:
        lines.append("## Projects")
        lines.append("")
        for p in projects:
            if not isinstance(p, dict):
                continue
            lines.append(f"**{(p.get('name') or '').strip()}**")
            desc = (p.get("description") or "").strip()
            if desc:
                lines.append(desc)
            impact = (p.get("impact") or "").strip()
            if impact:
                lines.append(f"_Impact:_ {impact}")
            lines.append("")

    achievements = profile.get("achievements") if isinstance(profile.get("achievements"), list) else []
    if achievements:
        lines.append("## Achievements")
        lines.append("")
        for a in achievements:
            if a:
                lines.append(f"- {str(a).strip()}")
        lines.append("")

    conflicts = profile.get("conflicting_facts") if isinstance(profile.get("conflicting_facts"), list) else []
    if conflicts:
        lines.append("## Conflicting facts (review)")
        lines.append("")
        for c in conflicts:
            if isinstance(c, dict) and c.get("fact"):
                srcs = ", ".join(c.get("sources") or [])
                lines.append(f"- {c['fact']}  _(sources: {srcs})_")
        lines.append("")

    srcs = profile.get("source_files_consolidated") if isinstance(profile.get("source_files_consolidated"), list) else []
    if srcs:
        lines.append("---")
        lines.append(f"_Consolidated from: {', '.join(srcs)}_")
        consolidated_at = (profile.get("consolidated_at") or "").strip()
        if consolidated_at:
            lines.append(f"_Generated: {consolidated_at}_")

    return "\n".join(lines).rstrip() + "\n"


def consolidated_profile_md_age_days(
    md_path: Optional[Path] = None,
) -> Optional[float]:
    """
    Age of consolidated_profile.md on disk (mtime), in days.
    Returns None if the file does not exist.
    """
    p = md_path or _default_md_path()
    if not p.exists():
        return None
    age_sec = max(0.0, datetime.now().timestamp() - p.stat().st_mtime)
    return age_sec / 86400.0


def consolidated_profile_stale_warning(
    threshold_days: int = 30,
    *,
    md_path: Optional[Path] = None,
) -> str:
    """
    Human-readable warning when the consolidated markdown profile is older than
    threshold_days. Empty string when fresh enough or missing (missing is handled elsewhere).
    """
    age = consolidated_profile_md_age_days(md_path)
    if age is None or age <= threshold_days:
        return ""
    rounded = int(round(age))
    return (
        f"Your consolidated_profile.md is about {rounded} days old "
        f"(more than {threshold_days} days). Re-run "
        "`python -m job_pipeline.bootstrap_resume_profile` so new tailoring matches "
        "your latest resume PDFs."
    )


def _career_master_primary_name() -> str:
    """
    Pull the candidate's primary name from career_master.md when present.
    The user's hand-edited file is the authoritative source — the LLM-extracted
    name in consolidated_profile.json may be wrong or partial.

    Looks for, in order:
      1. '## Career Master — <Name>'   (H2 with em/en/hyphen dash)
      2. First non-generic H1
    Returns '' if no match.
    """
    master_path = _repo_root() / "job_pipeline" / "career_master.md"
    if not master_path.exists():
        return ""
    try:
        text = master_path.read_text(encoding="utf-8")
    except Exception:
        return ""

    m = re.search(r"^##\s+Career Master\s+[—\-–]\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            candidate = line[2:].strip()
            if candidate and "career master" not in candidate.lower():
                return candidate
    return ""


def load_consolidated_profile() -> Dict[str, Any]:
    """Public loader used by resume_tailor for the manual-JD flow."""
    p = _default_json_path()
    if not p.exists():
        profile: Dict[str, Any] = {}
    else:
        try:
            profile = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            profile = {}

    master_name = _career_master_primary_name()
    if master_name:
        profile["name"] = master_name

    return profile


def load_consolidated_profile_text() -> str:
    """Public loader for the markdown profile (what the LLM tailor reads)."""
    p = _default_md_path()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def run_bootstrap(
    resume_dir: Optional[Path] = None,
    md_path: Optional[Path] = None,
    json_path: Optional[Path] = None,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    rdir = resume_dir or _default_resume_dir()
    mdp = md_path or _default_md_path()
    jsp = json_path or _default_json_path()

    print(f"Resume dir: {rdir}")
    if not rdir.exists():
        return {"ok": False, "error": f"resume directory not found: {rdir}"}

    print("Step 1/3: Extracting text from PDFs...")
    docs, skipped = gather_pdf_corpus(rdir)
    if not docs:
        return {
            "ok": False,
            "error": f"No readable PDFs in {rdir}. Skipped: {skipped}",
            "skipped": skipped,
        }
    print(f"  Extracted {len(docs)} usable PDF(s). Skipped {len(skipped)}.")

    print("Step 2/3: Consolidating with Gemini...")
    profile = consolidate_with_gemini(docs)
    if profile.get("error"):
        return {"ok": False, "error": profile["error"], "raw": profile.get("raw")}

    md = render_profile_markdown(profile)

    print("Step 3/3: Writing outputs...")
    if dry_run:
        print("[DRY RUN] would write:")
        print(f"  {jsp}")
        print(f"  {mdp}")
        print()
        print("--- Markdown preview (first 60 lines) ---")
        for i, line in enumerate(md.splitlines()[:60]):
            print(f"  {line}")
        return {"ok": True, "dry_run": True, "profile": profile, "markdown": md, "skipped": skipped}

    # Backup existing files
    for p in (jsp, mdp):
        if p.exists():
            bak = _backup_path(p)
            p.replace(bak)
            print(f"  backed up {p.name} -> {bak.name}")

    jsp.parent.mkdir(parents=True, exist_ok=True)
    jsp.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    mdp.write_text(md, encoding="utf-8")

    print(f"  wrote {jsp}")
    print(f"  wrote {mdp}")

    return {
        "ok": True,
        "dry_run": False,
        "json_path": str(jsp.resolve()),
        "markdown_path": str(mdp.resolve()),
        "skipped": skipped,
        "source_files_consolidated": profile.get("source_files_consolidated") or [],
        "experience_count": len(profile.get("experience") or []),
        "skill_count": sum(
            len(profile.get("skills", {}).get(k) or [])
            for k in ("technical", "tools", "soft")
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate every PDF in /resume into one grounded master profile."
    )
    parser.add_argument(
        "--resume-dir",
        type=str,
        default="",
        help="Override the resume directory (default: ./resume next to this repo).",
    )
    parser.add_argument(
        "--md-path",
        type=str,
        default="",
        help="Override the markdown output path.",
    )
    parser.add_argument(
        "--json-path",
        type=str,
        default="",
        help="Override the JSON output path.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without changing anything on disk.",
    )
    args = parser.parse_args(argv)

    # Load .env if available, so GEMINI_API_KEY / GOOGLE_API_KEY work without manual export.
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(_repo_root() / ".env")
    except ImportError:
        pass

    result = run_bootstrap(
        resume_dir=Path(args.resume_dir) if args.resume_dir else None,
        md_path=Path(args.md_path) if args.md_path else None,
        json_path=Path(args.json_path) if args.json_path else None,
        dry_run=args.dry_run,
    )

    if not result.get("ok"):
        print(f"\nFAILED: {result.get('error')}", file=sys.stderr)
        if result.get("raw"):
            print(f"\nRaw model output (first 1k):\n{result['raw'][:1000]}", file=sys.stderr)
        return 2

    print("\nDone.")
    if not result.get("dry_run"):
        print(f"  experience entries: {result.get('experience_count')}")
        print(f"  total skills: {result.get('skill_count')}")
        print(f"  source PDFs: {len(result.get('source_files_consolidated') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
