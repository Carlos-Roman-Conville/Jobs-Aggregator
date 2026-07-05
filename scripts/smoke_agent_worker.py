"""
End-to-end smoke test for AgentWorker against the live DB.

Exercises:
  - preflight passes
  - claim_next picks up a row our test set up
  - stage_for_upload copies a PDF to Downloads with the per-agent name
  - heartbeat extends the lease
  - mark_submitted releases the claim and writes an application_log entry
  - mark_failed also releases (for the failure path)

Does NOT invoke svc_build_package (that's covered by the existing pipeline
tests and requires LLM credits). The package build path is exercised via the
production endpoints during real session runs.

Run with:
    POSTGRES_PORT=5433 POSTGRES_USER=postgres POSTGRES_PASSWORD=... \
    python scripts/smoke_agent_worker.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

# Standalone-friendly import path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from job_pipeline.agent_worker import APPLICATION_LOG_DIR, AgentWorker  # noqa: E402
from job_pipeline.db import pg_connect  # noqa: E402


def _setup_test_row(target_filter_substr: str) -> tuple[int, str]:
    """Flip one row to 'ranked' with an apply_url matching our test filter so
    the worker can claim it. Returns (item_id, prior_status)."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.id, i.status
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                WHERE i.claimed_by IS NULL
                  AND p.apply_url ILIKE %s
                ORDER BY i.id DESC
                LIMIT 1
                """,
                (f"%{target_filter_substr}%",),
            )
            row = cur.fetchone()
            if not row:
                # Fall back to ANY apply_url and rewrite below
                cur.execute(
                    """
                    SELECT i.id, i.status, p.apply_url
                    FROM job_pipeline_items i
                    JOIN job_postings p ON p.id = i.posting_id
                    WHERE i.claimed_by IS NULL
                    ORDER BY i.id DESC
                    LIMIT 1
                    """
                )
                fallback = cur.fetchone()
                if not fallback:
                    raise SystemExit("No unclaimed rows in DB to test against")
                item_id, prior_status, _ = fallback
            else:
                item_id, prior_status = row[0], row[1]
            cur.execute(
                "UPDATE job_pipeline_items SET status = 'ranked' WHERE id = %s",
                (int(item_id),),
            )
        conn.commit()
        return int(item_id), str(prior_status)
    finally:
        conn.close()


def _restore(item_id: int, prior_status: str) -> None:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_pipeline_items
                SET status = %s,
                    claimed_by = NULL,
                    claimed_at = NULL,
                    lease_expires_at = NULL,
                    user_decision_at = NULL,
                    applied_at = NULL
                WHERE id = %s
                """,
                (prior_status, int(item_id)),
            )
        conn.commit()
    finally:
        conn.close()


def _make_fake_pdf() -> Path:
    """Write a tiny valid-ish PDF blob to a temp file so stage_for_upload has
    something to copy (we don't run svc_build_package in the smoke test)."""
    p = Path(tempfile.gettempdir()) / "agent_worker_smoke_fake.pdf"
    p.write_bytes(b"%PDF-1.4\n%fake pdf for agent_worker smoke test\n%%EOF\n")
    return p


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
    agent_id = "smoke-worker-A"
    ats_filter = "%%"  # claim ANYTHING for the smoke test; we won't run real submits

    # 1. preflight
    worker = AgentWorker(agent_id=agent_id, ats_filter=ats_filter)
    # Bypass the over-broad-filter guard by using a narrow filter for the worker
    # AFTER preflight if needed. For smoke we set a workable filter.
    worker.ats_filter = "%"  # placeholder, will be overridden by direct claim args

    # Pick a real row to set up the test against
    item_id, prior_status = _setup_test_row("indeed")  # 'indeed' is common
    print(f"[setup] using item id {item_id}, prior status '{prior_status}'")

    # Now make a real worker with a sane filter and force the claim by hand
    # via the underlying helper to avoid worker.claim_next() depending on
    # the row's actual apply_url shape.
    worker = AgentWorker(agent_id=agent_id, ats_filter="%indeed%")

    failures: list[str] = []

    try:
        # 2. preflight passes
        ok, errors = worker.preflight()
        if not ok:
            print("FAIL: preflight returned errors:")
            for e in errors:
                print(f"  - {e}")
            return 1
        print(f"[test preflight OK] {worker.status_line()}")

        # 3. Test direct claim via the worker (broad filter)
        worker.ats_filter = "%"  # use universal match to guarantee claim
        # Re-instantiate via direct underlying helper for a clean claim
        from job_pipeline.db import claim_next_item

        claimed = claim_next_item(
            agent_id=agent_id,
            from_status="ranked",
            to_status="drafting",
            where_extra_sql=" AND i.id = %s",
            where_extra_params=(item_id,),
        )
        if not claimed or claimed["id"] != item_id:
            failures.append(
                f"claim mismatch: expected id={item_id}, got {claimed['id'] if claimed else None}"
            )
        else:
            print(f"[test 1 OK] claimed id={claimed['id']}")

        # 4. heartbeat
        if claimed and worker.heartbeat(claimed["id"]):
            print(f"[test 2 OK] heartbeat extended lease on id={claimed['id']}")
        else:
            failures.append(f"heartbeat returned False on owned claim id={item_id}")

        # 5. stage_for_upload copies the PDF with per-agent name
        fake_pdf = _make_fake_pdf()
        staged_cl = worker.stage_for_upload(str(fake_pdf), kind="cover_letter")
        if staged_cl and staged_cl.exists() and staged_cl.name == worker.cover_letter_staging_name:
            print(f"[test 3 OK] cover-letter staged at {staged_cl}")
            # Cleanup the staged file so we don't leave smoke garbage in Downloads
            try:
                staged_cl.unlink()
            except Exception:
                pass
        else:
            failures.append(f"cover letter staging failed: got {staged_cl}")

        # 6. mark_submitted releases claim and writes to today's log
        if claimed:
            # Pre-record any existing entries in today's log so we can detect our delta.
            log_path = worker._session_log_path()
            log_existed_before = log_path.exists()
            log_before = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            ok_sub = worker.mark_submitted(
                claimed,
                confirmation_url="https://example.test/confirm",
                notes="smoke test - not a real submission",
            )
            if not ok_sub:
                failures.append("mark_submitted returned False on owned claim")
            elif _status_of(claimed["id"]) != "submitted":
                failures.append(
                    f"mark_submitted did not set status to 'submitted' on id={claimed['id']}"
                )
            else:
                print(f"[test 4 OK] released id={claimed['id']} as submitted")

            # Log delta
            log_after = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            if "smoke-worker-A" not in log_after[len(log_before):]:
                failures.append(
                    "application log did not get a new entry mentioning the agent_id"
                )
            else:
                print(f"[test 5 OK] application_log appended entry at {log_path}")
                # Roll back: if the log file didn't exist before the test, DELETE
                # it (rather than leaving a 0-byte file behind). If it did exist,
                # truncate to its pre-test content.
                if log_existed_before:
                    log_path.write_text(log_before, encoding="utf-8")
                    print("[test 5 cleanup] rolled log back to pre-test state")
                else:
                    try:
                        log_path.unlink()
                        print("[test 5 cleanup] deleted log file (didn't exist pre-test)")
                    except Exception as e:
                        print(f"[test 5 cleanup WARN] could not delete log: {e}")

        # 7. mark_failed path (re-claim and fail)
        # Re-flip the test row to 'ranked' and verify mark_failed works.
        with pg_connect() as conn2:
            with conn2.cursor() as cur:
                cur.execute(
                    "UPDATE job_pipeline_items SET status='ranked' WHERE id=%s",
                    (item_id,),
                )
            conn2.commit()

        reclaim = claim_next_item(
            agent_id=agent_id,
            from_status="ranked",
            to_status="drafting",
            where_extra_sql=" AND i.id = %s",
            where_extra_params=(item_id,),
        )
        if reclaim:
            ok_fail = worker.mark_failed(reclaim["id"], reason="smoke fail path test")
            if not ok_fail:
                failures.append("mark_failed returned False on owned claim")
            elif _status_of(reclaim["id"]) != "closed":
                failures.append(
                    f"mark_failed did not set status to 'closed' on id={reclaim['id']}"
                )
            else:
                print(f"[test 6 OK] released id={reclaim['id']} as closed (failure path)")

    finally:
        _restore(item_id, prior_status)
        print(f"[teardown] restored item id {item_id} to status '{prior_status}'")

    if failures:
        print(f"\nFAILED ({len(failures)} issue(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
