"""One-shot backfill: penalize jobs whose REQUIRED skills are on the
candidate's evidence.json no-claim list (AD admin, M365 admin, etc.).

The summarizer LLM doesn't see evidence.json truth_limits and routinely scores
jobs as Strong/Maybe even when half the JD's named requirements are
structurally forbidden. The deterministic penalty multiplier — 25% off per
truth-limit-blocked requirement, 8% off per other NOT_TRUE — runs in the
summarize chain now. This backfill applies it retroactively to existing rows.

Per row this updates:
  - summary_json.truth_limit_fit (new card block: multiplier + blocked skills)
  - summary_json.fit_score_blended (multiplied through tl_mult)
  - summary_json.fit_score_after_location (the input to the multiply step)
  - summary_json.fit_score_raw (scaled proportionally)
  - DB columns: fit_score, list_rank, quality_bucket (verdict-driven)

Verdicts are re-reconciled against the new blended score. Auto-closed rows
are NOT reopened — only labels change. Idempotent.

Usage:
    python -m job_pipeline.backfill_truth_limit_fit
    python -m job_pipeline.backfill_truth_limit_fit --dry-run
    python -m job_pipeline.backfill_truth_limit_fit --item-id 1416
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

from job_pipeline.bootstrap_resume_profile import load_consolidated_profile_text
from job_pipeline.db import pg_connect
from job_pipeline.summarize import (
    _compute_list_rank,
    _evaluate_truth_limit_fit,
    _quality_bucket,
    _reconcile_verdict_with_scores,
)


def _fetch(item_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            base = (
                "SELECT i.id, i.summary_json, i.fit_score, "
                "       p.title, p.description_text "
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
                    "fit_score": float(r[2] or 0.0),
                    "title": r[3] or "",
                    "desc": r[4] or "",
                }
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

    profile_text = load_consolidated_profile_text() or ""
    if not profile_text:
        print("WARNING: empty profile_text — multipliers will all be 1.0", file=sys.stderr)

    rows = _fetch(args.item_id)
    print(f"Found {len(rows)} rows with summary_json", file=sys.stderr)

    changed = 0
    big_penalty = 0
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

        tlfit = _evaluate_truth_limit_fit(row["desc"], profile_text)
        tl_mult = float(tlfit.get("multiplier") or 1.0)

        old_block = sj.get("truth_limit_fit") if isinstance(sj.get("truth_limit_fit"), dict) else {}
        old_mult = float(old_block.get("multiplier") or 1.0)

        if abs(tl_mult - old_mult) < 1e-6 and old_block:
            continue  # already applied

        old_blended = float(sj.get("fit_score_blended") or 0.0)
        after_loc = float(sj.get("fit_score_after_location") or old_blended)

        # Recompute downstream: after_location * tl_mult, then * pref_mult.
        prefs = sj.get("search_preferences") if isinstance(sj.get("search_preferences"), dict) else {}
        pref_mult = float(prefs.get("pref_multiplier") or 1.0)
        new_after_tl = round(min(1.0, max(0.0, after_loc * tl_mult)), 4)
        new_blended = round(min(1.0, max(0.0, new_after_tl * pref_mult)), 4)
        # fit_score_raw scales proportionally with blended change so list_rank
        # remains comparable across rows.
        old_raw = float(sj.get("fit_score_raw") or old_blended)
        if old_blended > 0:
            new_raw = round(old_raw * (new_blended / old_blended), 4)
        else:
            new_raw = new_blended

        sj["truth_limit_fit"] = {
            "multiplier": tl_mult,
            "blocked_required_skills": tlfit.get("blocked") or [],
            "not_true_required_skills": tlfit.get("not_true") or [],
            "direct_supported_skills": tlfit.get("direct") or [],
        }
        sj["fit_score_after_truthlimits"] = new_after_tl
        sj["fit_score_blended"] = new_blended
        sj["fit_score_raw"] = new_raw

        # Re-reconcile verdict against the new score.
        ats_score = float((sj.get("ats_overlap") or {}).get("ats_score") or 0.0)
        old_verdict = str(sj.get("verdict") or "maybe").lower()
        new_verdict, reason = _reconcile_verdict_with_scores(old_verdict, new_blended, ats_score)
        if new_verdict != old_verdict:
            if "verdict_llm" not in sj:
                sj["verdict_llm"] = old_verdict
            sj["verdict"] = new_verdict
            sj["verdict_downgrade_reason"] = (
                (reason + " + truth_limit_penalty") if reason else "truth_limit_penalty"
            )
            change_key = f"{old_verdict} -> {new_verdict}"
            by_change[change_key] = by_change.get(change_key, 0) + 1

        likely_junk = bool(sj.get("likely_junk"))
        list_rank = _compute_list_rank(new_raw, sj["verdict"], likely_junk)
        qbucket = _quality_bucket(sj["verdict"], likely_junk)

        changed += 1
        if tl_mult <= 0.5:
            big_penalty += 1
        blocked = tlfit.get("blocked") or []
        not_true = tlfit.get("not_true") or []
        print(
            f"item {row['id']}: mult {old_mult:.3f} -> {tl_mult:.3f}  "
            f"blended {old_blended:.3f} -> {new_blended:.3f}  "
            f"blocked={blocked or '-'}  not_true={not_true or '-'}",
            file=sys.stderr,
        )

        if not args.dry_run:
            _write(row["id"], sj, new_blended, list_rank, qbucket)

    print(
        f"{'DRY-RUN: ' if args.dry_run else ''}penalized {changed} row(s); "
        f"{big_penalty} dropped >=50%",
        file=sys.stderr,
    )
    for k, n in sorted(by_change.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
