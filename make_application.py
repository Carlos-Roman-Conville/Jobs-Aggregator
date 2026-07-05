"""
Manual application builder — paste a JD, get resume and/or cover letter artifacts.

Usage:

    python make_application.py --jd-file job.txt --title "IT Support" --company "Acme"
    python make_application.py --mode cover_letter --jd-file job.txt --attached-resume resume.pdf
    python make_application.py --mode resume --jd-file job.txt --no-pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

_THIS = Path(__file__).resolve().parent
if str(_THIS) not in sys.path:
    sys.path.insert(0, str(_THIS))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(_THIS / ".env", override=True)
    except ImportError:
        pass


def _read_jd(args: argparse.Namespace) -> str:
    if args.jd_file:
        return Path(args.jd_file).read_text(encoding="utf-8")
    if args.jd:
        return args.jd
    if not sys.stdin.isatty():
        return sys.stdin.read()
    print("Paste the full job description below. End with a line containing only 'END':")
    lines: List[str] = []
    try:
        while True:
            line = input()
            if line.strip().upper() == "END":
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines)


def _read_answers_file(p: str) -> List[str]:
    if not p:
        return []
    raw = Path(p).read_text(encoding="utf-8")
    return [
        line.rstrip("\r\n")
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _prompt_for_answers(gaps, prefilled=None) -> List[str]:
    if not gaps:
        return []
    prefilled = prefilled or []
    print()
    print("=" * 60)
    print(f"Gap-fill questions ({len(gaps)}). Answer each, or type 'skip' to omit.")
    print("=" * 60)
    answers: List[str] = []
    for i, g in enumerate(gaps, start=1):
        saved = (prefilled[i - 1] if i - 1 < len(prefilled) else "") or ""
        suggested = (g.get("suggested_answer") or "").strip()
        default = (saved or suggested).strip()
        print(f"\n{i}. {g.get('requirement')}")
        print(f"   {g.get('question')}")
        if default:
            print(f"   [default] {default}")
        try:
            ans = input("   > ").strip()
        except EOFError:
            ans = ""
        answers.append(ans or default)
    return answers


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Paste a JD, get tailored resume and/or cover letter artifacts."
    )
    parser.add_argument("--jd-file", type=str, default="")
    parser.add_argument("--jd", type=str, default="")
    parser.add_argument("--title", type=str, default="")
    parser.add_argument("--company", type=str, default="")
    parser.add_argument("--location", type=str, default="")
    parser.add_argument(
        "--mode",
        type=str,
        default="both",
        choices=["both", "resume", "cover_letter"],
        help="both | resume | cover_letter (default: both)",
    )
    parser.add_argument(
        "--template-id",
        type=str,
        default="",
        help="Cover letter template id from application_assets.json",
    )
    parser.add_argument(
        "--attached-resume",
        type=str,
        default="",
        help="Path to PDF/DOCX for cover_letter mode grounding",
    )
    parser.add_argument("--no-gaps", action="store_true", help="Skip interactive gap-fill prompts.")
    parser.add_argument("--answers-file", type=str, default="")
    parser.add_argument("--no-pdf", action="store_true", help="Skip all PDF rendering.")
    parser.add_argument("--no-cover-pdf", action="store_true", help="Skip cover letter PDF only.")
    parser.add_argument("--strategy", type=str, default="balanced", choices=["conservative", "balanced", "aggressive"])
    parser.add_argument("--theme", type=str, default="classic")
    parser.add_argument("--output-dir", type=str, default="")
    args = parser.parse_args(argv)

    _load_env()

    from application_assets import get_default_apply_asset_ids
    from job_pipeline.bootstrap_resume_profile import consolidated_profile_stale_warning, load_consolidated_profile_text
    from job_pipeline.resume_tailor import _load_grounded_profile_text
    from job_pipeline.resume_gaps import answers_to_extra_facts, detect_gaps
    from job_pipeline.service import build_application_artifacts

    jd = _read_jd(args).strip()
    if not jd:
        print("ERROR: no job description provided.", file=sys.stderr)
        return 2
    if len(jd) < 60:
        print("WARNING: JD is suspiciously short.", file=sys.stderr)

    profile_text = _load_grounded_profile_text()
    if not profile_text:
        print("ERROR: no consolidated profile. Run: python -m job_pipeline.bootstrap_resume_profile", file=sys.stderr)
        return 3

    stale = consolidated_profile_stale_warning()
    if stale:
        print(f"WARNING: {stale}", file=sys.stderr)

    mode_map = {"both": "both", "resume": "resume_only", "cover_letter": "cover_letter_only"}
    mode = mode_map[args.mode]
    dr, dt = get_default_apply_asset_ids()
    template_id = (args.template_id or dt or "").strip()

    extra_facts = None
    if not args.no_gaps and mode in ("both", "cover_letter_only"):
        gaps = detect_gaps(jd, profile_text=profile_text, tailored_content=None)
        if gaps:
            if args.answers_file:
                answers = _read_answers_file(args.answers_file)
            else:
                prefilled: List[str] = []
                try:
                    from job_pipeline.db import fetch_gap_answers_for_requirements

                    reqs = [(g.get("requirement") or "").strip() for g in gaps]
                    smap = fetch_gap_answers_for_requirements(reqs)
                    prefilled = [smap.get(r, "") for r in reqs]
                except Exception:
                    prefilled = [""] * len(gaps)
                answers = _prompt_for_answers(gaps, prefilled=prefilled)
            extra_facts = answers_to_extra_facts(gaps, answers)
            try:
                from job_pipeline.db import persist_gap_answer_rows

                persist_gap_answer_rows(
                    gaps,
                    answers,
                    jd_text=jd,
                    company_name=args.company,
                    job_title=args.title,
                )
            except Exception:
                pass

    out_root = str(Path(args.output_dir).resolve()) if (args.output_dir or "").strip() else str(_THIS)
    render_cover = not args.no_cover_pdf and not args.no_pdf

    built = build_application_artifacts(
        mode=mode,
        tailor_resume=(mode != "cover_letter_only"),
        title=args.title,
        company=args.company,
        location=args.location,
        description=jd,
        resume_id=dr or "",
        template_id=template_id,
        attached_resume_path=(args.attached_resume or "").strip() or None,
        strategy_level=args.strategy,
        render_cover_pdf=render_cover,
        render_resume_pdf=not args.no_pdf,
        theme=args.theme,
        extra_facts=extra_facts,
        outputs_root=out_root,
    )

    if not built.get("ok"):
        print(f"ERROR: {built.get('error')}", file=sys.stderr)
        return 4

    art = built.get("artifacts") or {}
    if args.no_pdf and mode in ("both", "resume_only"):
        print("NOTE: --no-pdf skips resume PDF; re-run without flag to render.")

    print(f"\nMode: {built.get('mode')}")
    for key in ("resume_md", "resume_pdf", "resume_file", "cover_letter_md", "cover_pdf"):
        if art.get(key):
            print(f"  {key}: {art[key]}")
    if built.get("letter"):
        print(f"\nCover letter preview:\n{built['letter'][:900]}")
    for w in art.get("warnings") or []:
        print(f"  warning: {w}", file=sys.stderr)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
