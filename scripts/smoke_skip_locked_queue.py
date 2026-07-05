"""
Smoke test for the SKIP LOCKED multi-agent queue (job_pipeline/db.py).

Runs against the live POSTGRES_* database. Uses two real pipeline rows
temporarily flipped to 'ranked' status, exercises the full claim lifecycle,
then restores their prior status. The test takes a transient-write window of
~10 seconds end to end.

Run with:
    POSTGRES_PORT=5433 POSTGRES_USER=postgres POSTGRES_PASSWORD=... \
    python scripts/smoke_skip_locked_queue.py

Exits 0 on success, 1 on any assertion failure.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Tuple

# Allow running directly with `python scripts/smoke_skip_locked_queue.py`
# without needing the project root on PYTHONPATH.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from job_pipeline.db import (
    DEFAULT_CLAIM_LEASE_MINUTES,
    claim_next_item,
    heartbeat_claim,
    list_active_claims,
    pg_connect,
    reap_stale_claims,
    release_claim,
)


def _setup_two_test_rows() -> Tuple[int, int, str, str]:
    """Pick two real pipeline rows, save their current status, and flip them
    to 'ranked' so the smoke test has eligible claim targets. Returns the
    (id_a, id_b, prior_status_a, prior_status_b) tuple so we can restore."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, status
                FROM job_pipeline_items
                WHERE claimed_by IS NULL
                ORDER BY id DESC
                LIMIT 2
                """
            )
            rows = cur.fetchall()
            if len(rows) < 2:
                raise SystemExit("Need at least 2 unclaimed rows in job_pipeline_items to run smoke test")
            (id_a, prior_a), (id_b, prior_b) = rows
            cur.execute(
                "UPDATE job_pipeline_items SET status='ranked' WHERE id IN (%s, %s)",
                (id_a, id_b),
            )
        conn.commit()
        return int(id_a), int(id_b), str(prior_a), str(prior_b)
    finally:
        conn.close()


def _restore(id_a: int, id_b: int, prior_a: str, prior_b: str) -> None:
    """Best-effort restore of original status + clear any lingering claim state."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_pipeline_items
                SET status = %s,
                    claimed_by = NULL,
                    claimed_at = NULL,
                    lease_expires_at = NULL
                WHERE id = %s
                """,
                (prior_a, id_a),
            )
            cur.execute(
                """
                UPDATE job_pipeline_items
                SET status = %s,
                    claimed_by = NULL,
                    claimed_at = NULL,
                    lease_expires_at = NULL
                WHERE id = %s
                """,
                (prior_b, id_b),
            )
        conn.commit()
    finally:
        conn.close()


def _force_lease_expired(item_id: int) -> None:
    """Backdate lease_expires_at so the reaper considers the row stale."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE job_pipeline_items SET lease_expires_at = NOW() - INTERVAL '1 minute' WHERE id = %s",
                (item_id,),
            )
        conn.commit()
    finally:
        conn.close()


def _status_of(item_id: int) -> str:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM job_pipeline_items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            return str(row[0]) if row else ""
    finally:
        conn.close()


def main() -> int:
    id_a, id_b, prior_a, prior_b = _setup_two_test_rows()
    print(f"[setup] using item ids {id_a}, {id_b} (will restore to '{prior_a}'/'{prior_b}')")

    failures = []
    try:
        # ---------------------------------------------------------------
        # Test 1: two parallel claims get DIFFERENT rows.
        # ---------------------------------------------------------------
        claim1 = claim_next_item(agent_id="smoke-agent-A", from_status="ranked", to_status="drafting")
        claim2 = claim_next_item(agent_id="smoke-agent-B", from_status="ranked", to_status="drafting")
        if claim1 is None or claim2 is None:
            failures.append(f"parallel claim returned None: {claim1=}, {claim2=}")
        elif claim1["id"] == claim2["id"]:
            failures.append(
                f"two agents got SAME row id {claim1['id']} - SKIP LOCKED broken or rows ineligible"
            )
        else:
            print(f"[test 1 OK] agent-A claimed id={claim1['id']}, agent-B claimed id={claim2['id']}")

        # ---------------------------------------------------------------
        # Test 2: a third claim attempt returns None (no more 'ranked' rows
        # left in the 2-row test set).
        # ---------------------------------------------------------------
        claim3 = claim_next_item(agent_id="smoke-agent-C", from_status="ranked", to_status="drafting")
        if claim3 is not None:
            # Could be a real unrelated 'ranked' row in the DB - tolerate but log.
            print(
                f"[test 2 INFO] third claim got id={claim3['id']} - means there are other 'ranked' rows in DB. "
                "Releasing it back to 'ranked'."
            )
            release_claim(claim3["id"], "smoke-agent-C", "ranked", require_ownership=True)
        else:
            print("[test 2 OK] third claim returned None (no more ranked rows for the 2-row test)")

        # ---------------------------------------------------------------
        # Test 3: heartbeat extends an in-flight claim.
        # ---------------------------------------------------------------
        if claim1 is not None:
            hb = heartbeat_claim(claim1["id"], "smoke-agent-A", lease_minutes=10)
            if not hb:
                failures.append(f"heartbeat returned False on owned claim id={claim1['id']}")
            else:
                print(f"[test 3 OK] heartbeat extended lease on id={claim1['id']}")

            # Wrong agent must NOT be able to heartbeat someone else's claim.
            hb_wrong = heartbeat_claim(claim1["id"], "smoke-agent-IMPOSTOR")
            if hb_wrong:
                failures.append(f"heartbeat succeeded with wrong agent_id - ownership check broken")
            else:
                print("[test 3b OK] heartbeat refused for non-owner")

        # ---------------------------------------------------------------
        # Test 4: list_active_claims surfaces our two claims.
        # ---------------------------------------------------------------
        active = list_active_claims()
        active_ids = {row["id"] for row in active}
        owned_ids = {c["id"] for c in (claim1, claim2) if c is not None}
        if not owned_ids.issubset(active_ids):
            failures.append(f"list_active_claims missing claims: owned={owned_ids}, listed={active_ids}")
        else:
            print(f"[test 4 OK] list_active_claims includes ids {sorted(owned_ids)}")

        # ---------------------------------------------------------------
        # Test 5: release_claim with correct agent finalizes the row.
        # ---------------------------------------------------------------
        if claim1 is not None:
            released = release_claim(claim1["id"], "smoke-agent-A", "ranked", require_ownership=True)
            if not released:
                failures.append(f"release_claim returned False on owned claim id={claim1['id']}")
            elif _status_of(claim1["id"]) != "ranked":
                failures.append(f"release_claim did not set status to 'ranked' on id={claim1['id']}")
            else:
                print(f"[test 5 OK] released id={claim1['id']} back to 'ranked' with claim fields cleared")

            # Wrong-agent release must NOT succeed.
            # First re-claim it so we have an in-flight claim to attack.
            reclaim = claim_next_item(agent_id="smoke-agent-A", from_status="ranked", to_status="drafting")
            if reclaim is not None and reclaim["id"] == claim1["id"]:
                stolen = release_claim(reclaim["id"], "smoke-agent-IMPOSTOR", "ranked", require_ownership=True)
                if stolen:
                    failures.append("release_claim succeeded with wrong agent_id - ownership check broken")
                else:
                    print("[test 5b OK] release_claim refused for non-owner")
                # Clean up
                release_claim(reclaim["id"], "smoke-agent-A", "ranked", require_ownership=True)

        # ---------------------------------------------------------------
        # Test 6: reap_stale_claims releases an expired claim.
        # ---------------------------------------------------------------
        if claim2 is not None:
            # claim2 is still in 'drafting' status, owned by smoke-agent-B.
            # Force its lease to be expired and run the reaper.
            _force_lease_expired(claim2["id"])
            reaped = reap_stale_claims()
            reaped_ids = {r[0] for r in reaped}
            if claim2["id"] not in reaped_ids:
                failures.append(
                    f"reaper did not release expired claim id={claim2['id']} (reaped: {reaped_ids})"
                )
            elif _status_of(claim2["id"]) != "pending_review":
                failures.append(
                    f"reaper did not revert status to 'pending_review' on id={claim2['id']} "
                    f"(actual status: {_status_of(claim2['id'])!r})"
                )
            else:
                print(
                    f"[test 6 OK] reaper released expired id={claim2['id']} back to 'pending_review'"
                )

    finally:
        _restore(id_a, id_b, prior_a, prior_b)
        print(f"[teardown] restored item ids {id_a}, {id_b} to original statuses")

    if failures:
        print(f"\nFAILED ({len(failures)} issue(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
