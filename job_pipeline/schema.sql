-- Job pipeline (Postgres). Run once via init_job_pipeline_schema() or psql.

CREATE TABLE IF NOT EXISTS job_postings (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    company_name    TEXT,
    title           TEXT NOT NULL,
    apply_url       TEXT,
    job_url         TEXT,
    location        TEXT,
    description_text TEXT,
    salary_text     TEXT,
    raw_payload     JSONB,
    discovered_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_job_postings_source ON job_postings (source);
CREATE INDEX IF NOT EXISTS idx_job_postings_discovered ON job_postings (discovered_at DESC);

CREATE TABLE IF NOT EXISTS job_pipeline_items (
    id                      BIGSERIAL PRIMARY KEY,
    posting_id              BIGINT NOT NULL REFERENCES job_postings (id) ON DELETE CASCADE,
    status                  TEXT NOT NULL DEFAULT 'ingested',
    fit_score               REAL,
    list_rank               REAL,
    quality_bucket          TEXT DEFAULT 'ok',
    summary_json            JSONB,
    recommended_resume_id   TEXT,
    cover_letter_template_id TEXT,
    cover_letter_text       TEXT,
    package_meta            JSONB,
    user_notes              TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    user_decision_at        TIMESTAMPTZ,
    applied_at              TIMESTAMPTZ,
    resume_id_used          TEXT,
    template_id_used        TEXT,
    outcome                 TEXT,
    outcome_notes           TEXT,
    outcome_recorded_at     TIMESTAMPTZ,
    UNIQUE (posting_id)
);

CREATE INDEX IF NOT EXISTS idx_job_pipeline_items_status ON job_pipeline_items (status);
CREATE INDEX IF NOT EXISTS idx_job_pipeline_items_fit ON job_pipeline_items (fit_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_job_pipeline_items_list_rank ON job_pipeline_items (list_rank DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_job_pipeline_items_outcome ON job_pipeline_items (outcome);

-- Saved gap-fill answers for manual JD tailoring (CLI + dashboard reuse).
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
);

CREATE INDEX IF NOT EXISTS idx_gap_answers_updated ON gap_answers (updated_at DESC);
