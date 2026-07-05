"""
Agent preflight - runs the checks an AgentWorker needs before claiming work.

Use from a fresh Claude Code session as the first thing the agent does:
    python scripts/agent_preflight.py auto-apply-greenhouse-1 '%greenhouse%'

Exits 0 if the environment is ready, 1 with a list of issues otherwise.
Also prints a snapshot of what's claimable for the given ATS slice so the
session knows whether there's actually work to pick up.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow standalone invocation.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from job_pipeline.agent_worker import AgentWorker  # noqa: E402
from job_pipeline.db import list_active_claims, pg_connect  # noqa: E402


def _ranked_count_for_filter(ats_filter: str) -> int:
    """Count claimable pending_review rows. If ats_filter is empty/None,
    counts the whole pool."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            if not ats_filter or ats_filter == "%":
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM job_pipeline_items
                    WHERE status = 'pending_review'
                      AND (claimed_at IS NULL OR lease_expires_at < NOW())
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM job_pipeline_items i
                    JOIN job_postings p ON p.id = i.posting_id
                    WHERE i.status = 'pending_review'
                      AND (i.claimed_at IS NULL OR i.lease_expires_at < NOW())
                      AND p.apply_url ILIKE %s
                    """,
                    (ats_filter,),
                )
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: python scripts/agent_preflight.py AGENT_ID ['ATS_FILTER']\n"
            "  ATS_FILTER is optional. Omit for 'no partition' (recommended\n"
            "  default - SKIP LOCKED prevents double-grabs without it).\n"
            "examples:\n"
            "  python scripts/agent_preflight.py auto-apply-1\n"
            "  python scripts/agent_preflight.py auto-apply-1 ''\n"
            "  python scripts/agent_preflight.py auto-apply-workday-1 '%myworkdayjobs%'"
        )
        return 1

    agent_id = argv[1]
    ats_filter = argv[2] if len(argv) >= 3 else ""

    print(f"=== preflight for {agent_id} (filter: {ats_filter}) ===\n")

    try:
        worker = AgentWorker(agent_id=agent_id, ats_filter=ats_filter)
    except ValueError as e:
        print(f"FAIL: invalid worker config: {e}")
        return 1

    ok, errors = worker.preflight()
    if not ok:
        print("FAIL: preflight errors:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("OK:", worker.status_line())

    # Active claims (any agent)
    active = list_active_claims()
    print(f"\nactive claims across all agents: {len(active)}")
    for c in active[:10]:
        flag = " (LEASE EXPIRED)" if c.get("lease_expired") else ""
        print(
            f"  #{c['id']} status={c['status']} claimed_by={c['claimed_by']} "
            f"claimed_at={c['claimed_at']}{flag}"
        )

    # Work available for this filter (or the whole pool if no filter)
    available = _ranked_count_for_filter(ats_filter)
    label = ats_filter if ats_filter else "<no filter - whole pool>"
    print(f"\nclaimable rows for filter {label!r}: {available}")
    if available == 0:
        print(
            "WARN: no immediately claimable rows. Either run ingestion to "
            "refill the pool, or check that 'ranked' status rows exist."
        )

    print(f"\ncover-letter staging name: {worker.cover_letter_staging_name}")
    print(f"resume staging name:        {worker.resume_staging_name}")
    print(f"staging dir:                {worker.downloads_dir}")

    print("\nready. Continue with the work loop documented in MULTI_AGENT_APPLY_RUNBOOK.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
