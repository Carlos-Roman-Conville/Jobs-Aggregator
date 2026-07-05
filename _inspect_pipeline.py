"""Temporary pipeline inspection — gets DB state for narrow-filter audit."""
import os

os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "5433")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_DB", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "yourpassword")

from job_pipeline.db import (
    count_items_by_status,
    count_closed_by_reason,
    pg_connect,
)
from job_pipeline.search_preferences import passes_target_title_filter

print("=" * 70)
print("STATUS COUNTS:")
print("=" * 70)
for st in [
    "ingested", "summarized", "pending_review", "auto_closed",
    "package_ready", "submitted", "responded", "rejected", "completed",
]:
    n = count_items_by_status(st)
    if n:
        print(f"  {st:<20} {n}")

print()
print("=" * 70)
print("AUTO-CLOSED REASONS:")
print("=" * 70)
for reason, n in sorted(count_closed_by_reason().items(), key=lambda x: -x[1]):
    print(f"  {reason:<30} {n}")

print()
print("=" * 70)
print("INGEST BY DAY (last 14):")
print("=" * 70)
conn = pg_connect()
try:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DATE(p.discovered_at) d, COUNT(*)
              FROM job_postings p
             GROUP BY d
             ORDER BY d DESC
             LIMIT 14
        """)
        for d, n in cur.fetchall():
            print(f"  {d}  {n}")

    print()
    print("=" * 70)
    print("UNSUMMARIZED TITLES — how many would the NEW filter reject if it ran?")
    print("=" * 70)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.title, p.id
              FROM job_postings p
              JOIN job_pipeline_items i ON i.posting_id = p.id
             WHERE i.status = 'ingested'
        """)
        rows = cur.fetchall()
    pass_n = 0
    reject_n = 0
    sample_passes = []
    sample_rejects = []
    for title, pid in rows:
        if passes_target_title_filter(title):
            pass_n += 1
            if len(sample_passes) < 15:
                sample_passes.append(title)
        else:
            reject_n += 1
            if len(sample_rejects) < 20:
                sample_rejects.append(title)
    total = pass_n + reject_n
    print(f"  Total unsummarized   {total}")
    if total:
        print(f"  Would PASS filter    {pass_n}  ({100*pass_n/total:.0f}%)")
        print(f"  Would be REJECTED    {reject_n}  ({100*reject_n/total:.0f}%)")
    print()
    print("Sample PASSES (titles that would survive the new filter):")
    for t in sample_passes:
        print(f"    PASS   {t}")
    print()
    print("Sample REJECTS (titles in the backlog that don't fit Carlos anymore):")
    for t in sample_rejects:
        print(f"    REJECT {t}")
finally:
    conn.close()
