"""Loop: ingest -> drain off-target -> measure pending_review. Repeat 3x or until stable."""
import os
import sys
import time

os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "5433")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_DB", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "yourpassword")

from job_pipeline.db import count_items_by_status, pg_connect
from job_pipeline.search_preferences import (
    passes_target_title_filter,
    load_search_preferences,
)
from job_pipeline.ingest import run_ingest_all


def drain_off_target() -> int:
    """Bulk-mark ingested items whose titles don't pass the live filter."""
    load_search_preferences(reload=True)
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.id, p.title
                  FROM job_pipeline_items i
                  JOIN job_postings p ON p.id = i.posting_id
                 WHERE i.status = 'ingested'
                """
            )
            rows = cur.fetchall()
        drop_ids = [i for i, t in rows if not passes_target_title_filter(t or "")]
        if drop_ids:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE job_pipeline_items
                       SET status='auto_closed',
                           user_notes = COALESCE(NULLIF(user_notes, ''), '')
                                          || CASE WHEN user_notes IS NULL OR user_notes = ''
                                                  THEN '' ELSE ' | ' END
                                          || 'title_off_target (loop fixer)',
                           updated_at = NOW()
                     WHERE id = ANY(%s)
                    """,
                    (drop_ids,),
                )
            conn.commit()
        return len(drop_ids)
    finally:
        conn.close()


def snapshot(label: str) -> dict:
    s = {st: count_items_by_status(st) for st in (
        "ingested", "pending_review", "auto_closed", "submitted", "completed"
    )}
    print(f"[{label}]  ingested={s['ingested']:>4}  "
          f"pending={s['pending_review']:>4}  "
          f"auto_closed={s['auto_closed']:>4}  "
          f"submitted={s['submitted']:>2}  completed={s['completed']}")
    return s


# Loop body
ACCEPTABLE_PENDING = 100  # target minimum
prev_pending = -1
for run in range(1, 4):
    print(f"\n========== RUN {run} ==========")
    snapshot("before ingest")

    print(f"Starting ingest...")
    t0 = time.time()
    try:
        rep = run_ingest_all()
    except Exception as e:
        print(f"  INGEST FAILED: {type(e).__name__}: {e}")
        break
    print(f"  Ingest done in {time.time()-t0:.0f}s.")
    if isinstance(rep, dict):
        keys_of_interest = [
            "postings_seen", "postings_upserted", "ingested",
            "indeed_jobs_touched", "jobspy_jobs_touched",
            "usajobs_jobs_touched",
            "skipped_validation", "errors",
        ]
        for k in keys_of_interest:
            if k in rep:
                v = rep[k]
                if isinstance(v, list):
                    print(f"  {k}: {len(v)} entries")
                else:
                    print(f"  {k}: {v}")

    snapshot("after ingest")
    dropped = drain_off_target()
    print(f"Drained off-target: {dropped}")
    snapshot("after drain")

    cur_pending = count_items_by_status("pending_review")
    cur_ingested = count_items_by_status("ingested")
    print(f"\nDelta: pending {prev_pending} -> {cur_pending}; "
          f"unsummarized backlog awaiting LLM: {cur_ingested}")

    if cur_ingested >= ACCEPTABLE_PENDING:
        print(f"\n>>> RUN {run}: enough survived filter ({cur_ingested}); summarize "
              "will likely push pending_review well past {ACCEPTABLE_PENDING}.")
        break
    if cur_pending >= ACCEPTABLE_PENDING:
        print(f"\n>>> RUN {run}: pending_review {cur_pending} >= {ACCEPTABLE_PENDING}. STABLE.")
        break
    prev_pending = cur_pending

print("\n========== LOOP DONE ==========")
snapshot("final")
