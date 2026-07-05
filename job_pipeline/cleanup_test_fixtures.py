"""
Close pipeline rows whose postings are GateTestCo* smoke-test fixtures.

Does not DROP tables — updates ``status`` to ``closed`` and patches ``summary_json``,
matching other maintenance scripts in this package.

  python -m job_pipeline.cleanup_test_fixtures --dry-run
  python -m job_pipeline.cleanup_test_fixtures --execute
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from psycopg2.extras import Json, RealDictCursor

from job_pipeline.db import pg_connect


def _parse_summary(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            o = json.loads(raw)
            return o if isinstance(o, dict) else {}
        except Exception:
            return {}
    return {}


def find_gate_test_rows() -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.id AS item_id, i.status, p.company_name, p.title
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                WHERE p.company_name LIKE 'GateTestCo%%'
                ORDER BY i.id
                """
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _close_fixture_row(item_id: int) -> bool:
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT summary_json FROM job_pipeline_items WHERE id = %s",
                (int(item_id),),
            )
            row = cur.fetchone()
            if not row:
                return False
            s = _parse_summary(row.get("summary_json"))
            s["filter_reason"] = "test_fixture"
            s["auto_filtered"] = True
            cur.execute(
                """
                UPDATE job_pipeline_items
                SET status = 'closed',
                    summary_json = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (Json(s), int(item_id)),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def run(*, execute: bool = False) -> Dict[str, Any]:
    rows = find_gate_test_rows()
    affected: List[int] = []
    if execute:
        for r in rows:
            if _close_fixture_row(int(r["item_id"])):
                affected.append(int(r["item_id"]))
    return {
        "gate_testco_rows": rows,
        "count": len(rows),
        "closed": affected if execute else [],
        "dry_run": not execute,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Close GateTestCo smoke-test pipeline rows.")
    p.add_argument(
        "--execute",
        action="store_true",
        help="Apply status=closed updates (default: list rows only).",
    )
    args = p.parse_args()
    out = run(execute=bool(args.execute))
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
