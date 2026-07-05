"""One-shot backfill: re-apply YOE (years-of-experience) penalty across rows.

The original `_apply_yoe_cap` did nothing because `profile.constraints` was
empty (claim <= 0 short-circuit) and the extractor took the first/loosest
year mention in the JD. Both are now fixed and there's a proportional
penalty (-12% per year of gap) instead of the binary cap.

Per row this updates:
  - summary_json.yoe_fit (jd_min_years / claim / gap / penalty / span)
  - summary_json.fit_score_model (the llm_fit that yoe scales)
  - summary_json.fit_score_blended (cascaded through all downstream multipliers)
  - summary_json.fit_score_raw (scaled proportionally)
  - DB columns: fit_score, list_rank, quality_bucket

Verdict is re-reconciled against the new blended score. Idempotent.

Usage:
    python -m job_pipeline.backfill_yoe_penalty
    python -m job_pipeline.backfill_yoe_penalty --dry-run
    python -m job_pipeline.backfill_yoe_penalty --item-id 1416
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(override=True)
except ImportError:
    pass

from job_pipeline.bootstrap_resume_profile import load_consolidated_profile
from job_pipeline.db import pg_connect
from job_pipeline.summarize import (
    _apply_yoe_cap,
    _compute_list_rank,
    _quality_bucket,
    _reconcile_verdict_with_scores,
)


def _fetch(item_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            base = (
                "SELECT i.id, i.summary_json, p.description_text "
                "FROM job_pipeline_items i "
                "JOIN job_postings p ON p.id = i.posting_id "
                "WHERE i.summary_json IS NOT NULL"
            )
            if item_id is not None:
                cur.execute(base + " AND i.id = %s", (int(item_id),))
            else:
                cur.execute(base)
            return [
                {"id": r[0], "summary_json": r[1], "desc": r[2] or ""}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def _write(item_id: int, payload: Dict[str, Any], fit_score: float, list_rank: float, qbucket: str) -> None:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE job_pipeline_items "
                "SET summary_json = %s, fit_score = %s, list_rank = %s, quality_bucket = %s, "
                "    updated_at = NOW() "
                "WHERE id = %s",
                (
                    json.dumps(payload, ensure_ascii=False),
                    float(fit_score),
                    float(list_rank),
                    qbucket,
                    int(item_id),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--item-id", type=int, default=None)
    args = ap.parse_args(argv)

    profile = load_consolidated_profile()
    rows = _fetch(args.item_id)
    print(f"Found {len(rows)} rows with summary_json", file=sys.stderr)

    changed = 0
    by_change: Dict[str, int] = {}
    big_drops = 0
    for row in rows:
        sj = row["summary_json"]
        if isinstance(sj, str):
            try:
                sj = json.loads(sj)
            except json.JSONDecodeError:
                continue
        if not isinstance(sj, dict):
            continue

        old_yoe = sj.get("yoe_fit") if isinstance(sj.get("yoe_fit"), dict) else {}
        old_pct = float(old_yoe.get("penalty_pct") or 0.0)
        old_model = float(sj.get("fit_score_model") or 0.0)
        if old_model <= 0:
            continue  # nothing to scale

        # Apply yoe to the ORIGINAL llm_fit (model fit before any yoe cap).
        # We approximate by reversing any old binary cap: if old_model is exactly
        # 0.45 we can't recover the original — use it as the floor.
        baseline = old_model
        if old_pct > 0:
            # old penalty was applied to llm_fit. Reverse it:
            baseline = round(old_model / (1.0 - old_pct / 100.0), 4)
            baseline = min(1.0, baseline)

        new_fit, note, block = _apply_yoe_cap(baseline, row["desc"], profile)
        new_pct = float(block.get("penalty_pct") or 0.0)

        if abs(new_pct - old_pct) < 1e-3 and old_yoe:
            continue  # unchanged

        # Cascade through downstream multipliers using ratios stored in card.
        old_blended = float(sj.get("fit_score_blended") or 0.0)
        if old_model > 0:
            blended_ratio = old_blended / old_model
        else:
            blended_ratio = 1.0
        new_blended = round(min(1.0, max(0.0, new_fit * blended_ratio)), 4)
        old_raw = float(sj.get("fit_score_raw") or old_blended)
        if old_blended > 0:
            new_raw = round(old_raw * (new_blended / old_blended), 4)
        else:
            new_raw = new_blended

        sj["yoe_fit"] = block
        sj["fit_score_model"] = new_fit
        sj["fit_score_blended"] = new_blended
        sj["fit_score_raw"] = new_raw
        if note:
            sj["deterministic_yoe_cap"] = note

        ats_score = float((sj.get("ats_overlap") or {}).get("ats_score") or 0.0)
        old_verdict = str(sj.get("verdict") or "maybe").lower()
        new_verdict, reason = _reconcile_verdict_with_scores(old_verdict, new_blended, ats_score)
        if new_verdict != old_verdict:
            if "verdict_llm" not in sj:
                sj["verdict_llm"] = old_verdict
            sj["verdict"] = new_verdict
            sj["verdict_downgrade_reason"] = (
                (reason + " + yoe_penalty") if reason else "yoe_penalty"
            )
            change_key = f"{old_verdict} -> {new_verdict}"
            by_change[change_key] = by_change.get(change_key, 0) + 1

        likely_junk = bool(sj.get("likely_junk"))
        list_rank = _compute_list_rank(new_raw, sj["verdict"], likely_junk)
        qbucket = _quality_bucket(sj["verdict"], likely_junk)

        changed += 1
        if new_pct >= 25.0:
            big_drops += 1
        print(
            f"item {row['id']}: pct {old_pct:.0f}% -> {new_pct:.0f}%  "
            f"blended {old_blended:.3f} -> {new_blended:.3f}  jd_min={block.get('jd_min_years')} claim={block.get('candidate_claim_years')}",
            file=sys.stderr,
        )

        if not args.dry_run:
            _write(row["id"], sj, new_blended, list_rank, qbucket)

    print(
        f"{'DRY-RUN: ' if args.dry_run else ''}penalized {changed} row(s); "
        f"{big_drops} hit >=25% penalty",
        file=sys.stderr,
    )
    for k, n in sorted(by_change.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
