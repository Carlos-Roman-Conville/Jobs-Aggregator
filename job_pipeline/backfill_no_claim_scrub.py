"""One-shot backfill: scrub forbidden-claim phrasing from existing summary_json rows.

Run after deploying scrub_card_no_claim_terms so the dashboard stops showing
"Active Directory basics" (and similar) on already-summarized rows.

Idempotent — re-running is a no-op once rows are clean.

Usage:
    python -m job_pipeline.backfill_no_claim_scrub
    python -m job_pipeline.backfill_no_claim_scrub --dry-run
    python -m job_pipeline.backfill_no_claim_scrub --item-id 1416
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
from job_pipeline.integrity_guards import scrub_card_no_claim_terms


def _fetch_all_summaries(item_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            if item_id is not None:
                cur.execute(
                    "SELECT id, summary_json FROM job_pipeline_items WHERE id = %s",
                    (int(item_id),),
                )
            else:
                cur.execute(
                    "SELECT id, summary_json FROM job_pipeline_items "
                    "WHERE summary_json IS NOT NULL"
                )
            return [{"id": r[0], "summary_json": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def _write_summary(item_id: int, payload: Dict[str, Any]) -> None:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE job_pipeline_items SET summary_json = %s, updated_at = NOW() "
                "WHERE id = %s",
                (json.dumps(payload, ensure_ascii=False), int(item_id)),
            )
        conn.commit()
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    ap.add_argument("--item-id", type=int, default=None, help="Scrub a single item only.")
    args = ap.parse_args(argv)

    rows = _fetch_all_summaries(args.item_id)
    print(f"Found {len(rows)} rows with summary_json", file=sys.stderr)

    changed = 0
    for row in rows:
        sj = row["summary_json"]
        if isinstance(sj, str):
            try:
                sj = json.loads(sj)
            except json.JSONDecodeError:
                continue
        if not isinstance(sj, dict):
            continue
        before = json.dumps(sj, ensure_ascii=False)
        notes = scrub_card_no_claim_terms(sj)
        after = json.dumps(sj, ensure_ascii=False)
        if before == after:
            continue
        changed += 1
        print(f"item {row['id']}: {len(notes)} note(s)", file=sys.stderr)
        for n in notes:
            print(f"  - {n}", file=sys.stderr)
        if not args.dry_run:
            _write_summary(row["id"], sj)
    print(
        f"{'DRY-RUN: ' if args.dry_run else ''}scrubbed {changed} row(s).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
