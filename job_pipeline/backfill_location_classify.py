"""One-shot backfill: re-classify location work-mode on existing rows.

The original `classify_remote_hybrid_on_site` was over-counting bare "remote"
substrings from JD duty bullets ("support remote users", "remote
troubleshooting"), causing hybrid/onsite jobs to come out tagged Remote with
the wrong fit multiplier. Run this once after the classifier is fixed so the
dashboard's labels and rankings catch up.

What this updates per row:
  - summary_json.location_policy.classification / action / multiplier / reason_code
  - summary_json.fit_score_after_location  (recomputed from after_seniority * new_mult)
  - summary_json.fit_score_blended         (= fit_score_after_location * pref_mult)
  - summary_json.fit_score_raw             (= fit_score_blended * fit_raw / fit_blended factor)
  - DB columns: fit_score, list_rank, quality_bucket (since blended changed)

What this does NOT do:
  - Reopen auto-closed rows (only label changes; if you want hard re-filtering, re-summarize)
  - Re-run the LLM verdict (just runs the deterministic reconcile-with-scores pass)

Idempotent — re-running is a no-op.

Usage:
    python -m job_pipeline.backfill_location_classify
    python -m job_pipeline.backfill_location_classify --dry-run
    python -m job_pipeline.backfill_location_classify --item-id 1416
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
from job_pipeline.location_policy import evaluate_location_policy
from job_pipeline.summarize import (
    _compute_list_rank,
    _quality_bucket,
    _reconcile_verdict_with_scores,
)


def _fetch(item_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            base = (
                "SELECT i.id, i.summary_json, i.list_rank, i.quality_bucket, i.fit_score, "
                "       p.title, p.location, p.description_text "
                "FROM job_pipeline_items i "
                "JOIN job_postings p ON p.id = i.posting_id "
                "WHERE i.summary_json IS NOT NULL"
            )
            if item_id is not None:
                cur.execute(base + " AND i.id = %s", (int(item_id),))
            else:
                cur.execute(base)
            return [
                {
                    "id": r[0],
                    "summary_json": r[1],
                    "list_rank": r[2],
                    "quality_bucket": r[3],
                    "fit_score": float(r[4] or 0.0),
                    "title": r[5] or "",
                    "location": r[6] or "",
                    "desc": r[7] or "",
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def _load_cfg() -> Dict[str, Any]:
    """Load merged config for location_policy_settings."""
    try:
        from job_pipeline.states import load_merged_config

        return load_merged_config()
    except Exception:
        try:
            with open("job_pipeline_config.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


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

    cfg = _load_cfg()
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
        old_lp = sj.get("location_policy") if isinstance(sj.get("location_policy"), dict) else {}
        old_cls = str(old_lp.get("classification") or "")
        old_mult = float(old_lp.get("multiplier") or 1.0)

        loc_action, loc_mult, loc_cls, loc_code = evaluate_location_policy(
            row["title"], row["location"], row["desc"], cfg
        )
        loc_reject = (loc_action == "reject")
        if loc_cls == old_cls and abs(loc_mult - old_mult) < 1e-6:
            continue  # no change

        # Update location_policy block
        sj["location_policy"] = {
            "action": loc_action,
            "multiplier": float(loc_mult),
            "classification": loc_cls,
            "reason_code": loc_code,
            "reject": loc_reject,
        }

        # Recompute combined scores. Start from after_seniority * new_mult.
        after_sen = float(sj.get("fit_score_after_domain_then_seniority") or sj.get("fit_score_blended_base") or 0.0)
        new_after_loc = 0.0 if loc_reject else round(min(1.0, max(0.0, after_sen * loc_mult)), 4)
        # pref_mult is stored on the card under search_preferences.pref_multiplier
        prefs = sj.get("search_preferences") if isinstance(sj.get("search_preferences"), dict) else {}
        pref_mult = float(prefs.get("pref_multiplier") or 1.0)
        new_blended = round(min(1.0, max(0.0, new_after_loc * pref_mult)), 4)

        sj["fit_score_after_location"] = new_after_loc
        sj["fit_score_blended"] = new_blended
        # fit_score_raw preserves the original ratio so list_rank scaling stays sane.
        old_blended = float(sj.get("fit_score_blended") or 0.0)
        old_raw = float(sj.get("fit_score_raw") or old_blended)
        if old_blended > 0:
            new_raw = round(old_raw * (new_blended / old_blended), 4)
        else:
            new_raw = new_blended
        sj["fit_score_raw"] = new_raw

        # Re-run deterministic verdict reconciliation against the new score.
        ats_score = float((sj.get("ats_overlap") or {}).get("ats_score") or 0.0)
        old_verdict = str(sj.get("verdict") or "maybe").lower()
        new_verdict, reason = _reconcile_verdict_with_scores(old_verdict, new_blended, ats_score)
        if new_verdict != old_verdict:
            if "verdict_llm" not in sj:
                sj["verdict_llm"] = old_verdict
            sj["verdict"] = new_verdict
            sj["verdict_downgrade_reason"] = reason

        # Recompute list_rank + quality_bucket (verdict-weighted).
        likely_junk = bool(sj.get("likely_junk"))
        list_rank = _compute_list_rank(new_raw, sj["verdict"], likely_junk)
        qbucket = _quality_bucket(sj["verdict"], likely_junk)

        change_key = f"{old_cls or '?'} -> {loc_cls}"
        by_change[change_key] = by_change.get(change_key, 0) + 1
        changed += 1
        print(
            f"item {row['id']}: {change_key:30s}  mult {old_mult:.3f} -> {loc_mult:.3f}  "
            f"blended {old_blended:.3f} -> {new_blended:.3f}",
            file=sys.stderr,
        )

        if not args.dry_run:
            _write(row["id"], sj, new_blended, list_rank, qbucket)

    print(
        f"{'DRY-RUN: ' if args.dry_run else ''}reclassified {changed} row(s).",
        file=sys.stderr,
    )
    for k, n in sorted(by_change.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
