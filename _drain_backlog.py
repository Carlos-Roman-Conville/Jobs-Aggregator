"""Drain the unsummarized backlog by bulk-marking off-target titles as auto_closed.

This runs the live narrow-target filter against every `ingested`-status item in
the queue and flips off-target ones to `auto_closed` with reason
`title_off_target` — no LLM cost. Items that PASS the title filter remain
`ingested` so the next summarize run picks them up.
"""
import os
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "5433")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_DB", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "yourpassword")

from job_pipeline.db import pg_connect
from job_pipeline.search_preferences import passes_target_title_filter, load_search_preferences

load_search_preferences(reload=True)

conn = pg_connect()
try:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT i.id, p.title
              FROM job_pipeline_items i
              JOIN job_postings p ON p.id = i.posting_id
             WHERE i.status = 'ingested'
        """)
        rows = cur.fetchall()

    drop_ids = []
    keep_ids = []
    for item_id, title in rows:
        if passes_target_title_filter(title or ""):
            keep_ids.append(item_id)
        else:
            drop_ids.append(item_id)

    print(f"Inspected: {len(rows)} ingested items")
    print(f"  Keep (would PASS filter): {len(keep_ids)}")
    print(f"  Drop (would be REJECTED): {len(drop_ids)}")

    if drop_ids:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_pipeline_items
                   SET status = 'auto_closed',
                       user_notes = COALESCE(NULLIF(user_notes, ''), '')
                                       || CASE WHEN user_notes IS NULL OR user_notes = ''
                                               THEN '' ELSE ' | ' END
                                       || 'title_off_target (bulk-applied 2026-06-01 PM after narrow filter)',
                       updated_at = NOW()
                 WHERE id = ANY(%s)
                """,
                (drop_ids,),
            )
            updated = cur.rowcount
        conn.commit()
        print(f"  Updated rows: {updated}")
    else:
        print("  Nothing to drop.")
finally:
    conn.close()
print("Done.")
