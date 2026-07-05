#!/usr/bin/env python3
"""Run calibrated judge over anchor files and optional golden corpus."""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from job_pipeline.quality_judge import (  # noqa: E402
    judge_enabled,
    judge_markdown_anchor,
    judge_quality,
    load_judge_anchors,
    opt_judge_min,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate calibrated quality judge")
    parser.add_argument(
        "--anchors-only",
        action="store_true",
        help="Score each anchor markdown file and print expected vs actual",
    )
    args = parser.parse_args()

    anchors = load_judge_anchors(force_reload=True)
    resume_n = len(anchors.get("resume") or [])
    cl_n = len(anchors.get("cover_letter") or [])
    print(f"Loaded {resume_n} resume + {cl_n} cover letter anchors from judge_anchors/")
    print(f"Judge enabled: {judge_enabled()}  min gate: {opt_judge_min()}")

    if not judge_enabled():
        print("Judge disabled (RESUME_OPT_JUDGE=0 or no anchors).")
        return 1

    print("\n--- Anchor calibration check ---")
    for key, doc_type in (("resume", "resume"), ("cover_letter", "cover_letter")):
        for a in anchors.get(key) or []:
            result = judge_markdown_anchor(a, doc_type=doc_type)
            if not result.get("ok"):
                print(f"  FAIL {a['filename']}: {result.get('reason')}")
                continue
            expected = a["score"] * 10  # anchor scores are /10, judge is /100
            actual = result.get("score", 0)
            delta = actual - expected
            flag = "OK" if abs(delta) <= 15 else "DRIFT"
            print(
                f"  [{flag}] {a['filename']}: expected~{expected:.0f} "
                f"got={actual} delta={delta:+.0f} verdict={result.get('verdict')}"
            )

    if args.anchors_only:
        return 0

    golden = os.path.join(_ROOT, "tests", "golden")
    if os.path.isdir(golden):
        print("\n--- Golden corpus (if JSON pairs present) ---")
        for name in sorted(os.listdir(golden)):
            if not name.endswith("_resume.json"):
                continue
            base = name.replace("_resume.json", "")
            rpath = os.path.join(golden, name)
            cpath = os.path.join(golden, f"{base}_cover_letter.json")
            import json

            with open(rpath, encoding="utf-8") as fh:
                resume = json.load(fh)
            cl = None
            if os.path.isfile(cpath):
                with open(cpath, encoding="utf-8") as fh:
                    cl = json.load(fh)
            result = judge_quality(resume, cover_letter_content=cl, job_title=base)
            if result.get("ok"):
                print(f"  {base}: judge={result.get('score')} verdict={result.get('verdict')}")
            else:
                print(f"  {base}: skipped ({result.get('reason')})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
