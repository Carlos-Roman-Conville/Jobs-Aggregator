"""One-shot backfill: reconcile persisted verdicts with their numeric scores.

The summarizer LLM overrates fit ~25% of the time. Rows already in the DB carry
those over-rated verdicts; the dashboard reads them as-is. Run this after
deploying the verdict-score reconciliation so existing rows show consistent
labels.

Idempotent — re-running is a no-op once rows are reconciled. Updates:
  - card.verdict (in-place downgrade)
  - card.verdict_llm (preserved original)
  - card.verdict_downgrade_reason (audit trail)
  - quality_bucket (depends on verdict)
  - list_rank (depends on verdict)

Auto_filtered rows are NOT reopened — the reconciliation only changes the
LABEL, not the closed/open state, to avoid surprising the user.

Usage:
    python -m job_pipeline.backfill_verdict_reconcile
    python -m job_pipeline.backfill_verdict_reconcile --dry-run
    python -m job_pipeline.backfill_verdict_reconcile --item-id 1416
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

from job_pipeline.db import pg_connect
from job_pipeline.summarize import (
    _compute_list_rank,
    _quality_bucket,
    _reconcile_verdict_with_scores,
)


def _fetch(item_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            if item_id is not None:
                cur.execute(
                    "SELECT id, summary_json, list_rank, quality_bucket "
                    "FROM job_pipeline_items WHERE id = %s",
                    (int(item_id),),
                )
            else:
                cur.execute(
                    "SELECT id, summary_json, list_rank, quality_bucket "
                    "FROM job_pipeline_items WHERE summary_json IS NOT NULL"
                )
            return [
                {
                    "id": r[0],
                    "summary_json": r[1],
                    "list_rank": r[2],
                    "quality_bucket": r[3],
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def _write(item_id: int, payload: Dict[str, Any], list_rank: float, qbucket: str) -> None:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE job_pipeline_items "
                "SET summary_json = %s, list_rank = %s, quality_bucket = %s, "
                "    updated_at = NOW() "
                "WHERE id = %s",
                (
                    json.dumps(payload, ensure_ascii=False),
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

    rows = _fetch(args.item_id)
    print(f"Found {len(rows)} rows with summary_json", file=sys.stderr)

    changed = 0
    by_change: Dict[str, int] = {}
    for row in rows:
        sj = row["summary_json"]
        if isinstance(sj, str):
            try:
                sj = json.loads(sj)
            except json.JSONDecodeError:
                continue
        if not isinstance(sj, dict):
            continue

        original_verdict = str(sj.get("verdict") or "").strip().lower()
        if not original_verdict:
            continue
        blended = float(sj.get("fit_score_blended") or 0.0)
        ats_score = float((sj.get("ats_overlap") or {}).get("ats_score") or 0.0)
        likely_junk = bool(sj.get("likely_junk"))
        fit_raw = float(sj.get("fit_score_raw") or blended)

        new_verdict, reason = _reconcile_verdict_with_scores(
            original_verdict, blended, ats_score
        )
        if new_verdict == original_verdict:
            continue

        # Preserve original LLM verdict only if not already preserved.
        if "verdict_llm" not in sj:
            sj["verdict_llm"] = original_verdict
        sj["verdict"] = new_verdict
        sj["verdict_downgrade_reason"] = reason

        # Recompute downstream fields that depend on verdict.
        new_list_rank = _compute_list_rank(fit_raw, new_verdict, likely_junk)
        new_qbucket = _quality_bucket(new_verdict, likely_junk)

        change_key = f"{original_verdict} -> {new_verdict}"
        by_change[change_key] = by_change.get(change_key, 0) + 1
        changed += 1
        print(f"item {row['id']}: {change_key} | {reason}", file=sys.stderr)

        if not args.dry_run:
            _write(row["id"], sj, new_list_rank, new_qbucket)

    print(
        f"{'DRY-RUN: ' if args.dry_run else ''}reconciled {changed} row(s).",
        file=sys.stderr,
    )
    for k, n in sorted(by_change.items()):
        print(f"  {k}: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
