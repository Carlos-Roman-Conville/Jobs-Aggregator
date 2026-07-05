"""
Scholarship pipeline DB layer.

Mirrors the shape of job_pipeline/db.py but operates on
`scholarship_postings` and `scholarship_pipeline_items` tables. Shares the
same Postgres connection (via job_pipeline.db.pg_connect) so there is one
DB to back up and one connection pool to monitor.

Functions:
  - init_scholarship_schema() -> (ok, err): runs schema.sql idempotently
  - upsert_posting(...): insert/update a scholarship_postings row + ensure
    a scholarship_pipeline_items row exists
  - get_item(item_id) / list_queue(status, limit)
  - update_item_status / set_item_scoring / set_item_outcome
  - claim_next_item / heartbeat_claim / release_claim / reap_stale_claims
    (SKIP LOCKED queue helpers, same semantics as job_pipeline)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from psycopg2.extras import Json, RealDictCursor

from job_pipeline.db import pg_connect  # reuse the same connection helper

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Reaper revert map: when an agent crashes mid-flight, return rows to a
# re-claimable status. Mirrors job_pipeline's mapping.
_REAPABLE_STATUS_TO_REVERT: Dict[str, str] = {
    "drafting": "pending_review",
    "tailoring": "pending_review",
    "approved": "pending_review",
    "package_ready": "package_ready",  # preserve built essay
}

DEFAULT_CLAIM_LEASE_MINUTES = 15


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def init_scholarship_schema() -> Tuple[bool, str]:
    """Run schema.sql idempotently. Returns (ok, err_msg)."""
    try:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        conn = pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        finally:
            conn.close()
        return True, ""
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def upsert_posting(
    *,
    source: str,
    external_id: str,
    title: str,
    provider: Optional[str] = None,
    description_text: Optional[str] = None,
    apply_url: Optional[str] = None,
    apply_url_normalized: Optional[str] = None,
    award_amount_min: Optional[int] = None,
    award_amount_max: Optional[int] = None,
    award_count: Optional[int] = None,
    deadline_at: Optional[Any] = None,
    rolling_deadline: bool = False,
    renewable: bool = False,
    degree_level: Optional[str] = None,
    field_of_study: Optional[str] = None,
    geographic_restriction: Optional[str] = None,
    eligibility_criteria: Optional[str] = None,
    min_gpa: Optional[float] = None,
    essay_required: bool = False,
    essay_prompt: Optional[str] = None,
    essay_word_min: Optional[int] = None,
    essay_word_max: Optional[int] = None,
    recommendations_required: int = 0,
    transcript_required: bool = False,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int]:
    """
    Insert or update a scholarship_postings row, then ensure a paired
    scholarship_pipeline_items row exists with status='ingested'.
    Returns (posting_id, item_id).
    """
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scholarship_postings (
                    source, external_id, title, provider, description_text,
                    apply_url, apply_url_normalized,
                    award_amount_min, award_amount_max, award_count,
                    deadline_at, rolling_deadline, renewable,
                    degree_level, field_of_study, geographic_restriction,
                    eligibility_criteria, min_gpa,
                    essay_required, essay_prompt, essay_word_min, essay_word_max,
                    recommendations_required, transcript_required,
                    raw_payload
                ) VALUES (
                    %s,%s,%s,%s,%s,
                    %s,%s,
                    %s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s,%s,
                    %s
                )
                ON CONFLICT (source, external_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    provider = COALESCE(EXCLUDED.provider, scholarship_postings.provider),
                    description_text = COALESCE(EXCLUDED.description_text, scholarship_postings.description_text),
                    apply_url = COALESCE(EXCLUDED.apply_url, scholarship_postings.apply_url),
                    apply_url_normalized = COALESCE(EXCLUDED.apply_url_normalized, scholarship_postings.apply_url_normalized),
                    award_amount_min = COALESCE(EXCLUDED.award_amount_min, scholarship_postings.award_amount_min),
                    award_amount_max = COALESCE(EXCLUDED.award_amount_max, scholarship_postings.award_amount_max),
                    award_count = COALESCE(EXCLUDED.award_count, scholarship_postings.award_count),
                    deadline_at = COALESCE(EXCLUDED.deadline_at, scholarship_postings.deadline_at),
                    rolling_deadline = EXCLUDED.rolling_deadline,
                    renewable = EXCLUDED.renewable,
                    degree_level = COALESCE(EXCLUDED.degree_level, scholarship_postings.degree_level),
                    field_of_study = COALESCE(EXCLUDED.field_of_study, scholarship_postings.field_of_study),
                    geographic_restriction = COALESCE(EXCLUDED.geographic_restriction, scholarship_postings.geographic_restriction),
                    eligibility_criteria = COALESCE(EXCLUDED.eligibility_criteria, scholarship_postings.eligibility_criteria),
                    min_gpa = COALESCE(EXCLUDED.min_gpa, scholarship_postings.min_gpa),
                    essay_required = EXCLUDED.essay_required,
                    essay_prompt = COALESCE(EXCLUDED.essay_prompt, scholarship_postings.essay_prompt),
                    essay_word_min = COALESCE(EXCLUDED.essay_word_min, scholarship_postings.essay_word_min),
                    essay_word_max = COALESCE(EXCLUDED.essay_word_max, scholarship_postings.essay_word_max),
                    recommendations_required = EXCLUDED.recommendations_required,
                    transcript_required = EXCLUDED.transcript_required,
                    raw_payload = COALESCE(EXCLUDED.raw_payload, scholarship_postings.raw_payload)
                RETURNING id
                """,
                (
                    source, external_id, title, provider, description_text,
                    apply_url, apply_url_normalized,
                    award_amount_min, award_amount_max, award_count,
                    deadline_at, rolling_deadline, renewable,
                    degree_level, field_of_study, geographic_restriction,
                    eligibility_criteria, min_gpa,
                    essay_required, essay_prompt, essay_word_min, essay_word_max,
                    recommendations_required, transcript_required,
                    Json(raw_payload) if raw_payload is not None else None,
                ),
            )
            posting_id = int(cur.fetchone()[0])

            # Ensure paired pipeline item exists.
            cur.execute(
                """
                INSERT INTO scholarship_pipeline_items (posting_id, status)
                VALUES (%s, 'ingested')
                ON CONFLICT (posting_id) DO UPDATE SET updated_at = NOW()
                RETURNING id
                """,
                (posting_id,),
            )
            item_id = int(cur.fetchone()[0])
        conn.commit()
        return posting_id, item_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


def get_item(item_id: int) -> Optional[Dict[str, Any]]:
    """Return joined item + posting fields, or None."""
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.*, p.source, p.external_id, p.title, p.provider,
                       p.description_text, p.apply_url, p.apply_url_normalized,
                       p.award_amount_min, p.award_amount_max, p.award_count,
                       p.deadline_at, p.rolling_deadline, p.renewable,
                       p.degree_level, p.field_of_study, p.geographic_restriction,
                       p.eligibility_criteria, p.min_gpa,
                       p.essay_required, p.essay_prompt,
                       p.essay_word_min, p.essay_word_max,
                       p.recommendations_required, p.transcript_required,
                       p.raw_payload
                FROM scholarship_pipeline_items i
                JOIN scholarship_postings p ON p.id = i.posting_id
                WHERE i.id = %s
                """,
                (int(item_id),),
            )
            row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_queue(
    status: Optional[str] = None,
    limit: int = 50,
    order_by: str = "i.priority_score DESC NULLS LAST",
) -> List[Dict[str, Any]]:
    """List items in the queue, joined with posting fields, ordered by priority."""
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if status:
                cur.execute(
                    f"""
                    SELECT i.*, p.title, p.provider, p.apply_url,
                           p.award_amount_min, p.award_amount_max,
                           p.deadline_at, p.essay_required
                    FROM scholarship_pipeline_items i
                    JOIN scholarship_postings p ON p.id = i.posting_id
                    WHERE i.status = %s
                    ORDER BY {order_by}
                    LIMIT %s
                    """,
                    (status, int(limit)),
                )
            else:
                cur.execute(
                    f"""
                    SELECT i.*, p.title, p.provider, p.apply_url,
                           p.award_amount_min, p.award_amount_max,
                           p.deadline_at, p.essay_required
                    FROM scholarship_pipeline_items i
                    JOIN scholarship_postings p ON p.id = i.posting_id
                    ORDER BY {order_by}
                    LIMIT %s
                    """,
                    (int(limit),),
                )
            rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_item_status(item_id: int, status: str, notes: str = "") -> bool:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scholarship_pipeline_items
                SET status = %s,
                    user_notes = COALESCE(NULLIF(%s, ''), user_notes),
                    user_decision_at = CASE
                        WHEN %s IN ('approved','package_ready','submitted','awarded','not_selected','expired','closed')
                        THEN NOW() ELSE user_decision_at END,
                    applied_at = CASE
                        WHEN %s IN ('submitted') AND applied_at IS NULL THEN NOW()
                        ELSE applied_at END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (status, notes, status, status, int(item_id)),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def set_item_scoring(
    item_id: int,
    *,
    eligibility_fit_score: Optional[float] = None,
    deadline_urgency: Optional[float] = None,
    priority_score: Optional[float] = None,
    eligibility_notes: Optional[str] = None,
    new_status: Optional[str] = None,
) -> bool:
    """Set LLM-derived scoring fields. If new_status is given, also bump status
    (typically from 'ingested' to 'ranked')."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scholarship_pipeline_items
                SET eligibility_fit_score = COALESCE(%s, eligibility_fit_score),
                    deadline_urgency      = COALESCE(%s, deadline_urgency),
                    priority_score        = COALESCE(%s, priority_score),
                    eligibility_notes     = COALESCE(NULLIF(%s, ''), eligibility_notes),
                    status                = COALESCE(NULLIF(%s, ''), status),
                    updated_at            = NOW()
                WHERE id = %s
                """,
                (
                    eligibility_fit_score, deadline_urgency, priority_score,
                    eligibility_notes, new_status,
                    int(item_id),
                ),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def set_item_outcome(
    item_id: int,
    outcome: str,
    notes: str = "",
    *,
    awarded: Optional[bool] = None,
    award_amount_received: Optional[int] = None,
) -> bool:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scholarship_pipeline_items
                SET outcome              = %s,
                    outcome_notes        = COALESCE(NULLIF(%s, ''), outcome_notes),
                    outcome_recorded_at  = NOW(),
                    awarded              = COALESCE(%s, awarded),
                    award_amount_received = COALESCE(%s, award_amount_received),
                    updated_at           = NOW()
                WHERE id = %s
                """,
                (outcome, notes, awarded, award_amount_received, int(item_id)),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def status_counts() -> Dict[str, int]:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) FROM scholarship_pipeline_items GROUP BY status"
            )
            return {str(s): int(n) for s, n in cur.fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SKIP LOCKED multi-agent queue (mirrors job_pipeline)
# ---------------------------------------------------------------------------


def claim_next_item(
    agent_id: str,
    from_status: str = "pending_review",
    to_status: str = "drafting",
    lease_minutes: int = DEFAULT_CLAIM_LEASE_MINUTES,
    order_by: str = "COALESCE(i.priority_score, i.eligibility_fit_score, 0) DESC NULLS LAST",
    where_extra_sql: str = "",
    where_extra_params: Optional[Sequence[Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Atomic SKIP LOCKED claim for the scholarship queue. See job_pipeline.db
    claim_next_item docstring for full semantics — this is the exact same
    pattern targeting scholarship_pipeline_items."""
    where_extra_sql = (where_extra_sql or "").strip()
    if where_extra_sql and not where_extra_sql.lower().startswith("and "):
        where_extra_sql = "AND " + where_extra_sql

    params: List[Any] = [from_status]
    if where_extra_params:
        params.extend(where_extra_params)
    params.extend([to_status, agent_id, f"{lease_minutes} minutes"])

    sql = f"""
    WITH next_job AS (
        SELECT i.id
        FROM scholarship_pipeline_items i
        JOIN scholarship_postings p ON p.id = i.posting_id
        WHERE i.status = %s
          AND (i.claimed_at IS NULL OR i.lease_expires_at < NOW())
          {where_extra_sql}
        ORDER BY {order_by}
        LIMIT 1
        FOR UPDATE OF i SKIP LOCKED
    )
    UPDATE scholarship_pipeline_items
    SET status           = %s,
        claimed_by       = %s,
        claimed_at       = NOW(),
        lease_expires_at = NOW() + (%s)::interval,
        updated_at       = NOW()
    FROM next_job
    WHERE scholarship_pipeline_items.id = next_job.id
    RETURNING scholarship_pipeline_items.id
    """
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        claimed_id = int(row[0])
    finally:
        conn.close()
    return get_item(claimed_id)


def heartbeat_claim(
    item_id: int,
    agent_id: str,
    lease_minutes: int = DEFAULT_CLAIM_LEASE_MINUTES,
) -> bool:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scholarship_pipeline_items
                SET lease_expires_at = NOW() + (%s)::interval,
                    updated_at       = NOW()
                WHERE id = %s
                  AND claimed_by = %s
                  AND (lease_expires_at IS NULL OR lease_expires_at >= NOW())
                """,
                (f"{lease_minutes} minutes", int(item_id), agent_id),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def release_claim(
    item_id: int,
    agent_id: str,
    final_status: str,
    notes: str = "",
    require_ownership: bool = True,
) -> bool:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            ownership_clause = "AND claimed_by = %s" if require_ownership else ""
            params: List[Any] = [final_status, notes, final_status, final_status, int(item_id)]
            if require_ownership:
                params.append(agent_id)
            cur.execute(
                f"""
                UPDATE scholarship_pipeline_items
                SET status            = %s,
                    user_notes        = COALESCE(NULLIF(%s, ''), user_notes),
                    user_decision_at  = CASE
                        WHEN %s IN ('approved','package_ready','submitted','awarded','not_selected','expired','closed')
                        THEN NOW() ELSE user_decision_at END,
                    applied_at        = CASE
                        WHEN %s IN ('submitted') AND applied_at IS NULL THEN NOW()
                        ELSE applied_at END,
                    claimed_by        = NULL,
                    claimed_at        = NULL,
                    lease_expires_at  = NULL,
                    updated_at        = NOW()
                WHERE id = %s
                  {ownership_clause}
                """,
                tuple(params),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def reap_stale_claims(
    status_revert_map: Optional[Dict[str, str]] = None,
) -> List[Tuple[int, str, str]]:
    mapping = dict(status_revert_map or _REAPABLE_STATUS_TO_REVERT)
    if not mapping:
        return []
    reaped: List[Tuple[int, str, str]] = []
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            for stale_status, revert_to in mapping.items():
                cur.execute(
                    """
                    UPDATE scholarship_pipeline_items
                    SET status           = %s,
                        claimed_by       = NULL,
                        claimed_at       = NULL,
                        lease_expires_at = NULL,
                        updated_at       = NOW()
                    WHERE status = %s
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < NOW()
                    RETURNING id
                    """,
                    (revert_to, stale_status),
                )
                for (rid,) in cur.fetchall():
                    reaped.append((int(rid), stale_status, revert_to))
        conn.commit()
    finally:
        conn.close()
    return reaped


def list_active_claims() -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, posting_id, status, claimed_by, claimed_at, lease_expires_at,
                       (lease_expires_at < NOW()) AS lease_expired
                FROM scholarship_pipeline_items
                WHERE claimed_by IS NOT NULL
                ORDER BY claimed_at NULLS LAST
                """
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
