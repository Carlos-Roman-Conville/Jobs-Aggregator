import os
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2
from psycopg2.extras import Json, RealDictCursor

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _json_clean(o: Any) -> Any:
    """Recursively replace NaN/Inf floats with None. JobSpy/pandas rows carry
    NaN for empty cells; Python's json emits the literal token ``NaN`` which
    PostgreSQL's json type rejects ('Token "NaN" is invalid')."""
    import math

    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: _json_clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_clean(v) for v in o]
    return o


def _safe_json_dumps(obj: Any) -> str:
    """json.dumps that tolerates the non-JSON values JobSpy puts in raw_payload:
    datetime/date objects (default=str) and NaN/Inf floats (_json_clean → null).
    Without this the upsert raised serialization errors and silently dropped the
    job, so JobSpy rows with any date or empty cell never reached the DB."""
    return json.dumps(_json_clean(obj), default=str)

# Punctuation/whitespace-collapsing pattern shared by Python and SQL
# (regexp_replace) so the company+title dedup key is identical on both sides.
_COMPANY_TITLE_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm_company_title_key(company: str, title: str) -> str:
    """Normalize company+title to a single comparable key.

    Same regex semantics as the SQL `regexp_replace(lower(x), '[^a-z0-9]+', ' ', 'g')`
    used in `find_posting_item_ids_by_company_title`.
    """
    company_n = _COMPANY_TITLE_NORM_RE.sub(" ", (company or "").lower()).strip()
    title_n = _COMPANY_TITLE_NORM_RE.sub(" ", (title or "").lower()).strip()
    return f"{company_n}::{title_n}"


def pg_connect():
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    dbname = os.getenv("POSTGRES_DB", "postgres")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        connect_timeout=5,
    )


_MIGRATION_ALTER = [
    "ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS apply_url_normalized TEXT",
    "CREATE INDEX IF NOT EXISTS idx_job_postings_apply_url_normalized ON job_postings (apply_url_normalized)",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS list_rank REAL",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS quality_bucket TEXT DEFAULT 'ok'",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS category TEXT",
    "CREATE INDEX IF NOT EXISTS idx_job_pipeline_items_category ON job_pipeline_items (category)",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS package_meta JSONB",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS resume_id_used TEXT",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS template_id_used TEXT",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS outcome TEXT",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS outcome_notes TEXT",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS outcome_recorded_at TIMESTAMPTZ",
    """
CREATE TABLE IF NOT EXISTS gap_answers (
    id               BIGSERIAL PRIMARY KEY,
    requirement_key  TEXT NOT NULL UNIQUE,
    requirement_text TEXT NOT NULL,
    answer_text      TEXT NOT NULL,
    jd_fingerprint   TEXT DEFAULT '',
    company_name     TEXT DEFAULT '',
    job_title        TEXT DEFAULT '',
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    "CREATE INDEX IF NOT EXISTS idx_gap_answers_updated ON gap_answers (updated_at DESC)",
    # SKIP LOCKED multi-agent queue columns. Three columns + one partial index:
    # - claimed_by: which agent (free-form string, e.g. "auto-apply-1") is currently working it
    # - claimed_at: when the claim was taken
    # - lease_expires_at: stale-claim cutoff; reaper releases rows past this
    # The partial index keeps claim lookups cheap by indexing only actively-claimed rows.
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS claimed_by TEXT",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ",
    "ALTER TABLE job_pipeline_items ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS idx_job_pipeline_items_claim_lookup "
    "ON job_pipeline_items (status, lease_expires_at) "
    "WHERE claimed_by IS NOT NULL",
]


def _backfill_apply_url_normalized(conn) -> None:
    from job_pipeline.normalize import normalize_apply_url

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, apply_url, job_url FROM job_postings WHERE apply_url_normalized IS NULL"
        )
        rows = cur.fetchall()
        for rid, au, ju in rows:
            raw = (au or ju or "").strip()
            n = normalize_apply_url(raw)
            val = n if n and len(n) > 12 else None
            cur.execute(
                "UPDATE job_postings SET apply_url_normalized = %s WHERE id = %s",
                (val, int(rid)),
            )


def _migrate_legacy_statuses(conn) -> None:
    """One-time idempotent mapping from pre-v2 status strings. Safe to re-run."""
    stmts = [
        "UPDATE job_pipeline_items SET status = 'ingested' WHERE status = 'new'",
        "UPDATE job_pipeline_items SET status = 'closed' WHERE status IN ('filtered','skipped','deferred')",
        "UPDATE job_pipeline_items SET status = 'drafted' WHERE status = 'needs_edits'",
        "UPDATE job_pipeline_items SET status = 'submitted' WHERE status IN ('applied','supervised_done')",
    ]
    with conn.cursor() as cur:
        for stmt in stmts:
            cur.execute(stmt)


def init_job_pipeline_schema() -> Tuple[bool, str]:
    try:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        conn = pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                for stmt in _MIGRATION_ALTER:
                    cur.execute(stmt)
            _migrate_legacy_statuses(conn)
            _backfill_apply_url_normalized(conn)
            conn.commit()
        finally:
            conn.close()
        return True, ""
    except Exception as e:
        return False, str(e)


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    d = dict(row)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


def get_posting_item_ids_by_normalized_url(normalized_url: str) -> Optional[Tuple[int, int]]:
    """If a posting already exists with this normalized apply URL, return (posting_id, item_id)."""
    nu = (normalized_url or "").strip()
    if len(nu) < 12:
        return None
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, i.id
                FROM job_postings p
                INNER JOIN job_pipeline_items i ON i.posting_id = p.id
                WHERE p.apply_url_normalized = %s
                LIMIT 1
                """,
                (nu,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return int(row[0]), int(row[1])
    finally:
        conn.close()


def find_posting_item_ids_by_company_title(
    company: str, title: str
) -> Optional[Tuple[int, int, Optional[str]]]:
    """Return (posting_id, item_id, apply_url_normalized) for an existing row
    whose `(company, title)` normalize identically to the given args, in any status.

    Used as a second-pass dedup after URL-normalized dedup misses (e.g. the same
    job reposted by Indeed with different URL params).
    """
    company_n = _COMPANY_TITLE_NORM_RE.sub(" ", (company or "").lower()).strip()
    title_n = _COMPANY_TITLE_NORM_RE.sub(" ", (title or "").lower()).strip()
    if not company_n or not title_n:
        return None
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, i.id, p.apply_url_normalized
                FROM job_postings p
                INNER JOIN job_pipeline_items i ON i.posting_id = p.id
                WHERE btrim(regexp_replace(lower(p.company_name), '[^a-z0-9]+', ' ', 'g')) = %s
                  AND btrim(regexp_replace(lower(p.title),        '[^a-z0-9]+', ' ', 'g')) = %s
                LIMIT 1
                """,
                (company_n, title_n),
            )
            row = cur.fetchone()
            if not row:
                return None
            return int(row[0]), int(row[1]), (row[2] if row[2] is not None else None)
    finally:
        conn.close()


def upsert_posting(
    source: str,
    external_id: str,
    company_name: str,
    title: str,
    apply_url: str,
    job_url: str,
    location: str,
    description_text: str,
    salary_text: str,
    raw_payload: Optional[dict],
    *,
    dedupe_by_normalized_url: bool = True,
) -> Tuple[int, int, bool, str]:
    """Insert or refresh a posting; return ``(posting_id, item_id, reused, reused_reason)``.

    ``reused_reason`` is one of ``""`` (fresh row / refreshed via ON CONFLICT),
    ``"url"`` (matched an existing row by normalized apply URL), or
    ``"company_title"`` (matched an existing row by normalized company+title
    pair even though the URL differed — covers reposts with new tracking params).
    """
    from job_pipeline.normalize import normalize_apply_url

    # --- Hard location gate ---------------------------------------------------
    # Single chokepoint for EVERY source: admit only remote or genuinely
    # <=30-min-from-Philadelphia jobs. Drops out-of-state onsite + blank-location
    # federal floods at the door so they never reach the review queue. Fails
    # OPEN (a gate bug must never silently swallow the whole ingest).
    try:
        from job_pipeline.location_policy import ingest_location_allowed

        _loc_ok, _loc_why = ingest_location_allowed(title, location, description_text)
        if not _loc_ok:
            return 0, 0, True, f"location_skip:{_loc_why}"
    except Exception:
        pass

    au = apply_url or job_url or ""
    ju = job_url or apply_url or ""
    url_norm = normalize_apply_url(au)
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            if dedupe_by_normalized_url and url_norm and len(url_norm) > 12:
                cur.execute(
                    """
                    SELECT p.id, i.id
                    FROM job_postings p
                    INNER JOIN job_pipeline_items i ON i.posting_id = p.id
                    WHERE p.apply_url_normalized = %s
                    LIMIT 1
                    """,
                    (url_norm,),
                )
                hit = cur.fetchone()
                if hit:
                    conn.commit()
                    return int(hit[0]), int(hit[1]), True, "url"

            # Second-pass dedup: same normalized company+title already in the
            # pipeline. We only reach here when the URL pass did NOT match (URL
            # dedup disabled, or apply_url empty/too short to normalize), so a
            # company+title hit is a genuine duplicate regardless of whether the
            # URLs differ. The old `differs` gate skipped dedup when both URLs
            # were empty/equal, letting reposts with no apply URL slip through as
            # duplicate rows (e.g. the same field-tech posting ingested twice).
            ct_hit = find_posting_item_ids_by_company_title(company_name, title)
            if ct_hit:
                existing_pid, existing_iid, _existing_url_norm = ct_hit
                conn.commit()
                return existing_pid, existing_iid, True, "company_title"

            cur.execute(
                """
                INSERT INTO job_postings (
                    source, external_id, company_name, title, apply_url, job_url,
                    location, description_text, salary_text, raw_payload, apply_url_normalized
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source, external_id) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    title = EXCLUDED.title,
                    apply_url = EXCLUDED.apply_url,
                    job_url = EXCLUDED.job_url,
                    location = EXCLUDED.location,
                    description_text = EXCLUDED.description_text,
                    salary_text = EXCLUDED.salary_text,
                    raw_payload = EXCLUDED.raw_payload,
                    apply_url_normalized = COALESCE(EXCLUDED.apply_url_normalized, job_postings.apply_url_normalized)
                RETURNING id
                """,
                (
                    source,
                    external_id,
                    company_name or "",
                    title,
                    au,
                    ju,
                    location or "",
                    description_text or "",
                    salary_text or "",
                    Json(raw_payload or {}, dumps=_safe_json_dumps),
                    url_norm if url_norm and len(url_norm) > 12 else None,
                ),
            )
            pid = int(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO job_pipeline_items (posting_id, status)
                VALUES (%s, 'ingested')
                ON CONFLICT (posting_id) DO NOTHING
                """,
                (pid,),
            )
            cur.execute(
                "SELECT id FROM job_pipeline_items WHERE posting_id = %s",
                (pid,),
            )
            row = cur.fetchone()
            iid = int(row[0]) if row else 0
        conn.commit()
        return pid, iid, False, ""
    finally:
        conn.close()


def list_queue(
    status: Optional[str] = None,
    limit: int = 50,
    min_list_rank: Optional[float] = None,
    order_by_rank: bool = True,
    order_by: str = "rank",
    source: Optional[str] = None,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            order_sql = _list_queue_order_sql(order_by=order_by, order_by_rank=order_by_rank)
            where_sql, params = _queue_filter_clauses(
                status=status,
                min_list_rank=min_list_rank,
                source=source,
                category=category,
            )
            params.append(limit)
            cur.execute(
                f"""
                SELECT i.id AS item_id, i.status, i.fit_score, i.list_rank, i.quality_bucket,
                       i.summary_json, i.package_meta,
                       i.recommended_resume_id, i.cover_letter_template_id,
                       i.outcome, i.applied_at, i.resume_id_used, i.template_id_used,
                       i.created_at, i.updated_at,
                       p.id AS posting_id, p.source, p.company_name, p.title,
                       p.location, p.salary_text, p.apply_url, p.job_url,
                       LEFT(p.description_text, 400) AS description_preview
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                {where_sql}
                ORDER BY {order_sql}
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_item(item_id: int) -> Optional[Dict[str, Any]]:
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.*, p.source, p.external_id, p.company_name, p.title, p.apply_url, p.job_url,
                       p.location, p.salary_text, p.description_text, p.raw_payload
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                WHERE i.id = %s
                """,
                (int(item_id),),
            )
            row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def update_item_status(item_id: int, status: str, notes: str = "") -> bool:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_pipeline_items
                SET status = %s,
                    user_notes = COALESCE(NULLIF(%s, ''), user_notes),
                    user_decision_at = CASE WHEN %s IN (
                        'drafted', 'approved', 'package_ready', 'submitted', 'responded',
                        'rejected', 'closed'
                    ) THEN NOW() ELSE user_decision_at END,
                    applied_at = CASE
                        WHEN %s IN ('submitted')
                             AND applied_at IS NULL THEN NOW()
                        ELSE applied_at
                    END,
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


def set_item_category(item_id: int, category: Optional[str] = None) -> Optional[str]:
    """Assign a lane category to an item and persist it on the row.

    If ``category`` is None, it is computed from the linked posting's
    title/description/location via ``lane_category.classify_category``. Returns
    the category written (or None if the item/posting was not found). Idempotent
    and safe to re-run; used both by the summarize hook and the backfill.
    """
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            if category is None:
                cur.execute(
                    """
                    SELECT p.title, p.description_text, p.location
                    FROM job_pipeline_items i
                    JOIN job_postings p ON p.id = i.posting_id
                    WHERE i.id = %s
                    """,
                    (int(item_id),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                # Lazy import to avoid import cycles at module load.
                from job_pipeline.lane_category import classify_category

                category = classify_category(row[0] or "", row[1] or "", row[2] or "")
            cur.execute(
                "UPDATE job_pipeline_items SET category = %s WHERE id = %s",
                (category, int(item_id)),
            )
        conn.commit()
        return category
    finally:
        conn.close()


def set_item_summary(
    item_id: int,
    fit_score: float,
    summary: dict,
    recommended_resume_id: str,
    cover_template_id: str,
    *,
    list_rank: Optional[float] = None,
    quality_bucket: str = "ok",
    target_status: str = "pending_review",
    force: bool = False,
) -> bool:
    """
    target_status:
      - 'closed' — auto-filtered / not worth review (still stores scores + summary_json for audit)
      - 'pending_review' — scored; transitions ingested → ranked → pending_review in one transaction

    force:
      When True, updates rows already in ``pending_review`` or ``ranked`` (re-summarize path).
      Does not touch ``approved`` / ``package_ready`` / etc.
    """
    conn = pg_connect()
    try:
        lr = list_rank if list_rank is not None else fit_score
        with conn.cursor() as cur:
            if force:
                if target_status == "closed":
                    cur.execute(
                        """
                        UPDATE job_pipeline_items
                        SET fit_score = %s,
                            list_rank = %s,
                            quality_bucket = %s,
                            summary_json = %s,
                            recommended_resume_id = %s,
                            cover_letter_template_id = %s,
                            status = 'closed',
                            updated_at = NOW()
                        WHERE id = %s
                          AND status IN ('pending_review', 'ranked')
                        """,
                        (
                            fit_score,
                            lr,
                            quality_bucket,
                            Json(summary),
                            recommended_resume_id,
                            cover_template_id,
                            int(item_id),
                        ),
                    )
                    n = cur.rowcount
                else:
                    cur.execute(
                        """
                        UPDATE job_pipeline_items
                        SET fit_score = %s,
                            list_rank = %s,
                            quality_bucket = %s,
                            summary_json = %s,
                            recommended_resume_id = %s,
                            cover_letter_template_id = %s,
                            status = 'pending_review',
                            updated_at = NOW()
                        WHERE id = %s
                          AND status IN ('pending_review', 'ranked')
                        """,
                        (
                            fit_score,
                            lr,
                            quality_bucket,
                            Json(summary),
                            recommended_resume_id,
                            cover_template_id,
                            int(item_id),
                        ),
                    )
                    n = cur.rowcount
            elif target_status == "closed":
                cur.execute(
                    """
                    UPDATE job_pipeline_items
                    SET fit_score = %s,
                        list_rank = %s,
                        quality_bucket = %s,
                        summary_json = %s,
                        recommended_resume_id = %s,
                        cover_letter_template_id = %s,
                        status = 'closed',
                        updated_at = NOW()
                    WHERE id = %s AND status = 'ingested'
                    """,
                    (
                        fit_score,
                        lr,
                        quality_bucket,
                        Json(summary),
                        recommended_resume_id,
                        cover_template_id,
                        int(item_id),
                    ),
                )
                n = cur.rowcount
            else:
                cur.execute(
                    """
                    UPDATE job_pipeline_items
                    SET fit_score = %s,
                        list_rank = %s,
                        quality_bucket = %s,
                        summary_json = %s,
                        recommended_resume_id = %s,
                        cover_letter_template_id = %s,
                        status = 'ranked',
                        updated_at = NOW()
                    WHERE id = %s AND status = 'ingested'
                    """,
                    (
                        fit_score,
                        lr,
                        quality_bucket,
                        Json(summary),
                        recommended_resume_id,
                        cover_template_id,
                        int(item_id),
                    ),
                )
                n_first = cur.rowcount
                if n_first > 0:
                    cur.execute(
                        """
                        UPDATE job_pipeline_items
                        SET status = 'pending_review', updated_at = NOW()
                        WHERE id = %s AND status = 'ranked'
                        """,
                        (int(item_id),),
                    )
                n = n_first
        conn.commit()
        return n > 0
    finally:
        conn.close()


def set_item_package(
    item_id: int,
    cover_letter_text: str,
    resume_id: str,
    template_id: str,
    package_meta: Optional[dict] = None,
) -> bool:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_pipeline_items
                SET cover_letter_text = %s,
                    recommended_resume_id = %s,
                    cover_letter_template_id = %s,
                    resume_id_used = %s,
                    template_id_used = %s,
                    package_meta = COALESCE(%s, '{}'::jsonb),
                    status = 'package_ready',
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    cover_letter_text,
                    resume_id,
                    template_id,
                    resume_id,
                    template_id,
                    Json(package_meta or {}),
                    int(item_id),
                ),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def set_item_outcome(item_id: int, outcome: str, notes: str = "") -> bool:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_pipeline_items
                SET outcome = %s,
                    outcome_notes = COALESCE(NULLIF(%s, ''), outcome_notes),
                    outcome_recorded_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (outcome, notes, int(item_id)),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def analytics_by_resume_template_outcome() -> Dict[str, Any]:
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT resume_id_used, template_id_used, outcome, COUNT(*) AS n
                FROM job_pipeline_items
                WHERE resume_id_used IS NOT NULL
                GROUP BY resume_id_used, template_id_used, outcome
                ORDER BY n DESC
                LIMIT 200
                """
            )
            by_combo = [_row_to_dict(r) for r in cur.fetchall()]
            cur.execute(
                """
                SELECT company_name, COUNT(*) AS applications,
                       SUM(CASE WHEN outcome IN ('interview', 'offer') THEN 1 ELSE 0 END) AS positive_outcomes
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                WHERE i.resume_id_used IS NOT NULL
                GROUP BY company_name
                ORDER BY applications DESC
                LIMIT 80
                """
            )
            by_company = [_row_to_dict(r) for r in cur.fetchall()]
        return {"by_resume_template_outcome": by_combo, "by_company": by_company}
    finally:
        conn.close()


def list_items_by_statuses(statuses: List[str], limit: int = 20) -> List[int]:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM job_pipeline_items
                WHERE status = ANY(%s)
                ORDER BY id ASC
                LIMIT %s
                """,
                (statuses, limit),
            )
            return [int(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


def list_pipeline_items_matching_submissions(
    *,
    statuses: Sequence[str] = ("submitted",),
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Submitted rows without an outcome of rejection yet (for IMAP matching)."""
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.id AS item_id, i.status, i.outcome, i.outcome_notes,
                       p.company_name, p.title, p.apply_url, p.job_url
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                WHERE i.status = ANY(%s)
                  AND COALESCE(TRIM(i.outcome), '') NOT ILIKE %s
                ORDER BY COALESCE(i.applied_at, i.updated_at, i.created_at) DESC
                LIMIT %s
                """,
                (
                    list(statuses),
                    "rejection%",
                    max(1, int(limit)),
                ),
            )
            rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_item_fit_domain_rescore(
    item_id: int,
    fit_score: float,
    list_rank: float,
    summary: dict,
) -> bool:
    """
    Update fit_score, list_rank, and full summary_json for items already past ingest
    (e.g. pending_review) after domain-only re-score.
    """
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_pipeline_items
                SET fit_score = %s,
                    list_rank = %s,
                    summary_json = %s,
                    updated_at = NOW()
                WHERE id = %s
                  AND status IN ('pending_review', 'ranked', 'approved', 'package_ready')
                """,
                (fit_score, list_rank, Json(summary), int(item_id)),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def update_item_preferences_rescore(
    item_id: int,
    fit_score: float,
    list_rank: float,
    summary: dict,
    *,
    close_for_preferences: bool = False,
) -> bool:
    """
    Persist preference-stage rescoring for eligible statuses.

    When ``close_for_preferences`` is True, eligible rows transition to ``closed``
    (same hard-close semantics as ingest-time summarization for prefs rejects).
    """
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            if close_for_preferences:
                cur.execute(
                    """
                    UPDATE job_pipeline_items
                    SET fit_score = %s,
                        list_rank = %s,
                        summary_json = %s,
                        status = CASE
                            WHEN status IN ('pending_review', 'ranked', 'approved', 'package_ready')
                            THEN 'closed' ELSE status END,
                        updated_at = NOW()
                    WHERE id = %s
                      AND status IN ('pending_review', 'ranked', 'approved', 'package_ready')
                    """,
                    (fit_score, list_rank, Json(summary), int(item_id)),
                )
            else:
                cur.execute(
                    """
                    UPDATE job_pipeline_items
                    SET fit_score = %s,
                        list_rank = %s,
                        summary_json = %s,
                        updated_at = NOW()
                    WHERE id = %s
                      AND status IN ('pending_review', 'ranked', 'approved', 'package_ready')
                    """,
                    (fit_score, list_rank, Json(summary), int(item_id)),
                )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def gap_answer_requirement_key(requirement_text: str) -> str:
    """Stable hash for deduping gap rows keyed by normalized requirement wording."""
    n = " ".join((requirement_text or "").lower().split())
    return hashlib.sha256(n.encode("utf-8")).hexdigest()


def jd_fingerprint(jd_text: str, *, max_chars: int = 8000) -> str:
    blob = (jd_text or "").strip()[:max_chars]
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def fetch_gap_answers_for_requirements(requirement_texts: Sequence[str]) -> Dict[str, str]:
    """Return mapping stripped requirement_text -> answer_text for rows that exist."""
    reqs = [str(x).strip() for x in requirement_texts if str(x).strip()]
    if not reqs:
        return {}
    keys = [(gap_answer_requirement_key(r), r) for r in reqs]
    uniq_keys = list({k for k, _ in keys})
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT requirement_key, answer_text
                FROM gap_answers
                WHERE requirement_key = ANY(%s)
                """,
                (uniq_keys,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    key_to_ans = {str(a): str(b) for a, b in rows}
    out: Dict[str, str] = {}
    for k, orig in keys:
        ans = key_to_ans.get(k)
        if ans:
            out[orig] = ans
    return out


def upsert_gap_answer(
    requirement_text: str,
    answer_text: str,
    *,
    jd_fingerprint_val: str = "",
    company_name: str = "",
    job_title: str = "",
) -> Tuple[bool, str]:
    """Insert or update one gap answer. Returns (ok, error_message)."""
    req = (requirement_text or "").strip()
    ans = (answer_text or "").strip()
    if not req or not ans:
        return False, "empty requirement or answer"
    rk = gap_answer_requirement_key(req)
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gap_answers (
                    requirement_key, requirement_text, answer_text,
                    jd_fingerprint, company_name, job_title
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (requirement_key) DO UPDATE SET
                    requirement_text = EXCLUDED.requirement_text,
                    answer_text = EXCLUDED.answer_text,
                    jd_fingerprint = EXCLUDED.jd_fingerprint,
                    company_name = EXCLUDED.company_name,
                    job_title = EXCLUDED.job_title,
                    updated_at = NOW()
                """,
                (rk, req, ans, jd_fingerprint_val or "", company_name or "", job_title or ""),
            )
        conn.commit()
        return True, ""
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        conn.close()


def persist_gap_answer_rows(
    gaps: Sequence[Dict[str, Any]],
    answers: Sequence[str],
    *,
    jd_text: str = "",
    company_name: str = "",
    job_title: str = "",
) -> int:
    """Upsert non-empty answers aligned with gaps list; returns rows written."""
    fp = jd_fingerprint(jd_text) if jd_text else ""
    n = 0
    for g, a in zip(gaps, answers):
        req = str((g or {}).get("requirement") or "").strip()
        ans = str(a or "").strip()
        if not req or not ans:
            continue
        low = ans.lower()
        if low in ("skip", "no", "n", "-", "/skip"):
            continue
        ok, _ = upsert_gap_answer(
            req,
            ans,
            jd_fingerprint_val=fp,
            company_name=company_name or "",
            job_title=job_title or "",
        )
        if ok:
            n += 1
    return n


def _list_queue_order_sql(*, order_by: str, order_by_rank: bool) -> str:
    ob = (order_by or "rank").strip().lower()
    if ob == "built_at":
        return "(i.package_meta->>'built_at') DESC NULLS LAST, i.updated_at DESC"
    if ob in ("recent", "updated_at"):
        return "i.updated_at DESC"
    if order_by_rank:
        return (
            "COALESCE(i.list_rank, i.fit_score, 0) DESC, i.fit_score DESC NULLS LAST, i.created_at DESC"
        )
    return "i.created_at DESC"


def _queue_filter_clauses(
    *,
    status: Optional[str],
    min_list_rank: Optional[float],
    source: Optional[str],
    category: Optional[str] = None,
) -> Tuple[str, List[Any]]:
    """Build SQL WHERE fragments + params for list_queue / counts."""
    clauses: List[str] = []
    params: List[Any] = []
    if status:
        clauses.append("i.status = %s")
        params.append(status)
    if min_list_rank is not None:
        clauses.append("COALESCE(i.list_rank, i.fit_score, 0) >= %s")
        params.append(min_list_rank)
    src = (source or "").strip()
    if src:
        clauses.append("p.source = %s")
        params.append(src)
    cat = (category or "").strip()
    if cat:
        clauses.append("i.category = %s")
        params.append(cat)
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def count_items_by_status(status: Optional[str] = None) -> int:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT COUNT(*) FROM job_pipeline_items WHERE status = %s",
                    (status,),
                )
            else:
                cur.execute("SELECT COUNT(*) FROM job_pipeline_items")
            row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def count_pending_review_above_rank(
    min_list_rank: float,
    *,
    source: Optional[str] = None,
) -> int:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            where_sql, params = _queue_filter_clauses(
                status="pending_review",
                min_list_rank=min_list_rank,
                source=source,
            )
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                {where_sql}
                """,
                tuple(params),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def count_queue_items(
    status: str,
    *,
    min_list_rank: Optional[float] = None,
    source: Optional[str] = None,
    category: Optional[str] = None,
) -> int:
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            where_sql, params = _queue_filter_clauses(
                status=status,
                min_list_rank=min_list_rank,
                source=source,
                category=category,
            )
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                {where_sql}
                """,
                tuple(params),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def category_counts(
    status: str,
    *,
    min_list_rank: Optional[float] = None,
    source: Optional[str] = None,
) -> Dict[str, int]:
    """Return {category: count} for items in ``status`` (lane tabs in the dashboard)."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            where_sql, params = _queue_filter_clauses(
                status=status,
                min_list_rank=min_list_rank,
                source=source,
            )
            cur.execute(
                f"""
                SELECT COALESCE(i.category, 'other') AS cat, COUNT(*)
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                {where_sql}
                GROUP BY COALESCE(i.category, 'other')
                """,
                tuple(params),
            )
            return {str(r[0]): int(r[1]) for r in cur.fetchall()}
    finally:
        conn.close()


def list_queue_source_counts(
    status: str,
    *,
    min_list_rank: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Distinct ingest sources for a queue status, with counts (sorted by count desc)."""
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            where_sql, params = _queue_filter_clauses(
                status=status,
                min_list_rank=min_list_rank,
                source=None,
            )
            cur.execute(
                f"""
                SELECT COALESCE(NULLIF(TRIM(p.source), ''), 'unknown') AS source,
                       COUNT(*) AS count
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                {where_sql}
                GROUP BY 1
                ORDER BY count DESC, source ASC
                """,
                tuple(params),
            )
            rows = cur.fetchall()
        return [{"source": r["source"], "count": int(r["count"])} for r in rows]
    finally:
        conn.close()


def count_closed_by_reason() -> Dict[str, int]:
    """Aggregate closed rows by close_reason_category (fallback filter_reason prefix)."""
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  COALESCE(
                    NULLIF(TRIM(summary_json->>'close_reason_category'), ''),
                    CASE
                      WHEN summary_json->>'filter_reason' LIKE 'search_preferences:%'
                        THEN 'search_preferences'
                      WHEN summary_json->>'filter_reason' LIKE 'location_policy:%'
                        THEN 'location'
                      WHEN summary_json->>'filter_reason' IN ('junk_or_noise', 'low_fit_or_pass', 'low_combined_score', 'auto_closed')
                        THEN 'threshold'
                      ELSE 'other'
                    END
                  ) AS cat,
                  COUNT(*) AS n
                FROM job_pipeline_items
                WHERE status = 'closed'
                GROUP BY 1
                ORDER BY n DESC
                """
            )
            rows = cur.fetchall()
        return {str(r[0]): int(r[1]) for r in rows}
    finally:
        conn.close()


def clear_all_pipeline_jobs() -> Dict[str, int]:
    """Delete pipeline items and postings except completed (submitted/responded/rejected)."""
    from job_pipeline.states import COMPLETED_STATUSES

    completed = tuple(sorted(COMPLETED_STATUSES))
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM job_pipeline_items WHERE status = ANY(%s)",
                (list(completed),),
            )
            items_preserved = int(cur.fetchone()[0])
            cur.execute(
                "SELECT COUNT(*) FROM job_pipeline_items WHERE NOT (status = ANY(%s))",
                (list(completed),),
            )
            items_deleted = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM job_postings")
            postings_before = int(cur.fetchone()[0])

            cur.execute(
                "DELETE FROM job_pipeline_items WHERE NOT (status = ANY(%s))",
                (list(completed),),
            )
            cur.execute(
                """
                DELETE FROM job_postings p
                WHERE NOT EXISTS (
                    SELECT 1 FROM job_pipeline_items i WHERE i.posting_id = p.id
                )
                """
            )
            postings_deleted = cur.rowcount
            cur.execute("SELECT COUNT(*) FROM job_postings")
            postings_preserved = int(cur.fetchone()[0])
        conn.commit()
        return {
            "items_deleted": items_deleted,
            "items_preserved": items_preserved,
            "postings_deleted": postings_deleted,
            "postings_preserved": postings_preserved,
            "postings_before": postings_before,
        }
    finally:
        conn.close()


def list_completed_jobs(
    *,
    limit: int = 100,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Jobs marked submitted or later — application history."""
    from job_pipeline.states import COMPLETED_STATUSES

    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            params: List[Any] = [list(sorted(COMPLETED_STATUSES))]
            source_clause = ""
            if (source or "").strip():
                source_clause = " AND p.source = %s"
                params.append(source.strip())
            params.append(int(limit))
            cur.execute(
                f"""
                SELECT i.id AS item_id, i.status, i.fit_score, i.list_rank,
                       i.summary_json, i.package_meta,
                       i.outcome, i.outcome_notes, i.applied_at,
                       i.resume_id_used, i.template_id_used,
                       i.created_at, i.updated_at,
                       p.id AS posting_id, p.source, p.company_name, p.title,
                       p.location, p.salary_text, p.apply_url, p.job_url
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                WHERE i.status = ANY(%s){source_clause}
                ORDER BY COALESCE(i.applied_at, i.updated_at, i.created_at) DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def count_completed_jobs(*, source: Optional[str] = None) -> int:
    from job_pipeline.states import COMPLETED_STATUSES

    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            params: List[Any] = [list(sorted(COMPLETED_STATUSES))]
            source_clause = ""
            if (source or "").strip():
                source_clause = " AND p.source = %s"
                params.append(source.strip())
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                WHERE i.status = ANY(%s){source_clause}
                """,
                tuple(params),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def list_queue_source_counts_for_statuses(
    statuses: Sequence[str],
    *,
    min_list_rank: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Source breakdown for one or more statuses (e.g. completed jobs)."""
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            params: List[Any] = [list(statuses)]
            rank_clause = ""
            if min_list_rank is not None:
                rank_clause = " AND COALESCE(i.list_rank, i.fit_score, 0) >= %s"
                params.append(min_list_rank)
            cur.execute(
                f"""
                SELECT COALESCE(NULLIF(TRIM(p.source), ''), 'unknown') AS source,
                       COUNT(*) AS count
                FROM job_pipeline_items i
                JOIN job_postings p ON p.id = i.posting_id
                WHERE i.status = ANY(%s){rank_clause}
                GROUP BY 1
                ORDER BY count DESC, source ASC
                """,
                tuple(params),
            )
            rows = cur.fetchall()
        return [{"source": r["source"], "count": int(r["count"])} for r in rows]
    finally:
        conn.close()


# ============================================================================
# SKIP LOCKED multi-agent queue
# ============================================================================
#
# Postgres-native job queue for parallel Claude Code agent sessions. Each agent
# calls claim_next_item() to atomically pick up an unclaimed eligible row, runs
# its work (heartbeating periodically via heartbeat_claim() on long jobs), and
# finishes by calling release_claim() with the final status. If an agent dies
# mid-flight, reap_stale_claims() releases its rows back to the pool.
#
# Two-system hazards (Redis/RabbitMQ + DB sync) are deliberately avoided:
# the row IS the job, status IS the queue position, no external broker needed.
# Canonical Postgres pattern at low-to-mid scale (<=100 workers / thousands
# of jobs/sec) - our scale is five orders of magnitude below the documented
# breakdown threshold.

DEFAULT_CLAIM_LEASE_MINUTES = 15

# Mapping of in-progress statuses -> status to revert to when a lease expires.
# Worker flow: pending_review -> drafting (claim) -> approved (svc_decide) ->
# package_ready (svc_build_package) -> submitted (release_claim).
# If an agent crashes at any point, the reaper releases the row:
#   - drafting / tailoring: hadn't built the package yet, revert to pending_review
#   - approved: svc_decide ran but svc_build_package didn't complete, revert
#   - package_ready: package IS built; clear claim but keep status so another
#     agent can pick it up at the human-handoff stage without rebuilding
_REAPABLE_STATUS_TO_REVERT: Dict[str, str] = {
    "drafting": "pending_review",
    "tailoring": "pending_review",
    "approved": "pending_review",
    "package_ready": "package_ready",  # preserve the built package
}


def claim_next_item(
    agent_id: str,
    from_status: str,
    to_status: str,
    lease_minutes: int = DEFAULT_CLAIM_LEASE_MINUTES,
    order_by: str = "COALESCE(i.list_rank, i.fit_score, 0) DESC NULLS LAST",
    where_extra_sql: str = "",
    where_extra_params: Optional[Sequence[Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Atomically claim the next eligible row using SELECT FOR UPDATE SKIP LOCKED.

    Parameters
    ----------
    agent_id
        Free-form identifier for the claiming agent (e.g. "auto-apply-1").
        Stored verbatim in claimed_by; used for ownership checks in
        heartbeat_claim and release_claim.
    from_status
        Status the row must currently be in to be claimable (e.g. "ranked").
    to_status
        Status the row transitions to after claim (e.g. "drafting").
    lease_minutes
        Claim lease duration. Reaper releases rows past lease_expires_at.
    order_by
        Ordering used to pick the next row. Defaults to highest list_rank /
        fit_score first.
    where_extra_sql
        Extra WHERE-clause SQL to narrow eligibility (e.g. ATS partitioning).
        Use %s placeholders. Example:
            where_extra_sql=" AND p.apply_url ILIKE %s"
            where_extra_params=("%greenhouse%",)
    where_extra_params
        Parameters interpolated into where_extra_sql.

    Returns
    -------
    Dict[str, Any] | None
        Joined item dict (same shape as get_item) or None when no eligible row.
    """
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
        FROM job_pipeline_items i
        JOIN job_postings p ON p.id = i.posting_id
        WHERE i.status = %s
          AND (i.claimed_at IS NULL OR i.lease_expires_at < NOW())
          {where_extra_sql}
        ORDER BY {order_by}
        LIMIT 1
        FOR UPDATE OF i SKIP LOCKED
    )
    UPDATE job_pipeline_items
    SET status           = %s,
        claimed_by       = %s,
        claimed_at       = NOW(),
        lease_expires_at = NOW() + (%s)::interval,
        updated_at       = NOW()
    FROM next_job
    WHERE job_pipeline_items.id = next_job.id
    RETURNING job_pipeline_items.id
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
    """Extend the lease on an in-flight claim. Returns True if our claim is
    still valid (and was extended), False if the row was reaped or stolen.

    Call this on a timer (e.g. every lease_minutes/3) inside any worker that
    holds a claim for longer than DEFAULT_CLAIM_LEASE_MINUTES.
    """
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_pipeline_items
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
    """Mark the work complete and release the claim.

    Clears claimed_by / claimed_at / lease_expires_at and transitions the row
    to final_status (e.g. "package_ready", "submitted", "rejected"). When
    require_ownership=True (default), the update only fires if claimed_by
    matches agent_id - protecting against the rare race where the reaper
    reassigned the row out from under a slow agent.
    """
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            ownership_clause = "AND claimed_by = %s" if require_ownership else ""
            params: List[Any] = [final_status, notes, final_status, final_status, int(item_id)]
            if require_ownership:
                params.append(agent_id)
            cur.execute(
                f"""
                UPDATE job_pipeline_items
                SET status            = %s,
                    user_notes        = COALESCE(NULLIF(%s, ''), user_notes),
                    user_decision_at  = CASE WHEN %s IN (
                        'drafted', 'approved', 'package_ready', 'submitted',
                        'responded', 'rejected', 'closed'
                    ) THEN NOW() ELSE user_decision_at END,
                    applied_at        = CASE
                        WHEN %s IN ('submitted')
                             AND applied_at IS NULL THEN NOW()
                        ELSE applied_at
                    END,
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
    """Find rows whose lease has expired and revert them to a re-claimable
    status. Returns a list of (item_id, prior_status, reverted_to) tuples.

    Run on a periodic timer (e.g. every 1-2 minutes). Safe to run from
    multiple places concurrently - each invocation only touches rows whose
    lease has already expired, and the UPDATE itself is atomic.
    """
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
                    UPDATE job_pipeline_items
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
    """Observability helper: list every row currently held by an agent.
    Useful for queue introspection / dashboard surfacing.
    """
    conn = pg_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, posting_id, status, claimed_by, claimed_at, lease_expires_at,
                       (lease_expires_at < NOW()) AS lease_expired
                FROM job_pipeline_items
                WHERE claimed_by IS NOT NULL
                ORDER BY claimed_at NULLS LAST
                """
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

