"""
Maintenance: close duplicate pending_review / ranked rows sharing the same normalized
(company + title) key, keeping the best list_rank (then newest created_at).

Dedup on ingest only affects new rows; this cleans historical duplicates.

  python -m job_pipeline.dedup_existing_company_title
  python -m job_pipeline.dedup_existing_company_title --dry-run
  python -m job_pipeline.dedup_existing_company_title --execute
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from psycopg2.extras import Json, RealDictCursor

from job_pipeline.db import _norm_company_title_key, pg_connect


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


def _fetch_eligible() -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.id AS item_id, i.status, i.created_at, i.list_rank, i.fit_score, i.summary_json,
                       p.company_name, p.title
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                WHERE i.status IN ('pending_review', 'ranked')
                """
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _close_duplicate(item_id: int, keeper_id: int) -> bool:
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT summary_json, status FROM job_pipeline_items
                WHERE id = %s AND status IN ('pending_review', 'ranked')
                """,
                (int(item_id),),
            )
            row = cur.fetchone()
            if not row:
                return False
            s = _parse_summary(row.get("summary_json"))
            s["filter_reason"] = "duplicate_company_title"
            s["auto_filtered"] = True
            s["duplicate_of_item_id"] = int(keeper_id)
            cur.execute(
                """
                UPDATE job_pipeline_items
                SET status = 'closed',
                    summary_json = %s,
                    updated_at = NOW()
                WHERE id = %s AND status IN ('pending_review', 'ranked')
                """,
                (Json(s), int(item_id)),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def _plan_groups(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        cn = str(r.get("company_name") or "").strip()
        tit = str(r.get("title") or "").strip()
        if not cn or not tit:
            continue
        key = _norm_company_title_key(cn, tit)
        ck, _, tk = key.partition("::")
        if not ck.strip() or not tk.strip():
            continue
        by_key[key].append(r)

    actions: List[Dict[str, Any]] = []
    ndup = 0

    def _lr(x: Dict[str, Any]) -> float:
        try:
            v = x.get("list_rank")
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            pass
        try:
            return float(x.get("fit_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    for key, group in by_key.items():
        if len(group) < 2:
            continue
        sorted_g = sorted(
            group,
            key=lambda x: (_lr(x), (x.get("created_at") or ""), int(x["item_id"])),
            reverse=True,
        )
        keeper = sorted_g[0]
        keeper_id = int(keeper["item_id"])
        for dup in sorted_g[1:]:
            actions.append(
                {
                    "keeper_id": keeper_id,
                    "duplicate_id": int(dup["item_id"]),
                    "key": key,
                    "keeper_list_rank": _lr(keeper),
                    "dup_list_rank": _lr(dup),
                }
            )
            ndup += 1
    return actions, ndup


def run(*, execute: bool = False) -> Dict[str, Any]:
    rows = _fetch_eligible()
    actions, ndup = _plan_groups(rows)
    closed: List[int] = []
    errors: List[Dict[str, Any]] = []
    if execute:
        for a in actions:
            ok = _close_duplicate(a["duplicate_id"], a["keeper_id"])
            if ok:
                closed.append(int(a["duplicate_id"]))
            else:
                errors.append({"action": a, "error": "update failed or ineligible status"})
    return {
        "eligible_count": len(rows),
        "duplicate_closures": ndup,
        "actions": actions,
        "closed": closed,
        "errors": errors,
        "dry_run": not execute,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Close duplicate company+title rows (keep best list_rank).")
    p.add_argument("--execute", action="store_true", help="Apply closes (default is dry-run output only).")
    p.add_argument("--dry-run", action="store_true", help="Alias for default preview-only mode.")
    args = p.parse_args()
    execute = bool(args.execute)
    if args.dry_run:
        execute = False
    out = run(execute=execute)
    print(json.dumps(out, indent=2, default=str))
    return 0 if not out.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
