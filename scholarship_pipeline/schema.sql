-- Scholarship Pipeline schema.
-- Parallel to job_pipeline: same Postgres DB, separate tables.
-- Idempotent (CREATE TABLE IF NOT EXISTS).

-- Raw scholarship records ingested from aggregators.
CREATE TABLE IF NOT EXISTS scholarship_postings (
    id                       BIGSERIAL PRIMARY KEY,
    source                   TEXT NOT NULL,          -- 'careeronestop', 'apify', 'bold', 'manual', etc.
    external_id              TEXT,                   -- aggregator's own ID, used for dedup
    title                    TEXT NOT NULL,
    provider                 TEXT,                   -- sponsoring organization
    description_text         TEXT,
    apply_url                TEXT,
    apply_url_normalized     TEXT,                   -- normalized form for cross-source dedup
    award_amount_min         INTEGER,                -- USD; NULL if "varies" / unspecified
    award_amount_max         INTEGER,
    award_count              INTEGER,                -- # of recipients per cycle, if known
    deadline_at              TIMESTAMPTZ,            -- next application deadline; NULL if rolling/unknown
    rolling_deadline         BOOLEAN DEFAULT FALSE,  -- true if no fixed deadline
    renewable                BOOLEAN DEFAULT FALSE,  -- multi-year award
    degree_level             TEXT,                   -- 'undergraduate', 'graduate', 'any', 'high_school'
    field_of_study           TEXT,                   -- 'cybersecurity', 'stem', 'any', etc.
    geographic_restriction   TEXT,                   -- 'PA', 'US', 'any', or comma-separated list
    eligibility_criteria     TEXT,                   -- free-text paragraph from source
    min_gpa                  REAL,
    essay_required           BOOLEAN DEFAULT FALSE,
    essay_prompt             TEXT,                   -- the prompt if known
    essay_word_min           INTEGER,
    essay_word_max           INTEGER,
    recommendations_required INTEGER DEFAULT 0,      -- # of letters of rec required
    transcript_required      BOOLEAN DEFAULT FALSE,
    raw_payload              JSONB,                  -- full source record for re-parsing later
    discovered_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_scholarship_postings_deadline ON scholarship_postings (deadline_at);
CREATE INDEX IF NOT EXISTS idx_scholarship_postings_apply_url_norm ON scholarship_postings (apply_url_normalized);
CREATE INDEX IF NOT EXISTS idx_scholarship_postings_source ON scholarship_postings (source);

-- Pipeline state machine (mirrors job_pipeline_items).
CREATE TABLE IF NOT EXISTS scholarship_pipeline_items (
    id                        BIGSERIAL PRIMARY KEY,
    posting_id                BIGINT NOT NULL REFERENCES scholarship_postings(id) ON DELETE CASCADE,
    status                    TEXT NOT NULL DEFAULT 'ingested',
    -- Scoring
    eligibility_fit_score     REAL,                  -- 0-1, LLM-scored against Carlos's profile
    deadline_urgency          REAL,                  -- 0-1, exp(-days_to_deadline/30)
    priority_score            REAL,                  -- eligibility_fit * deadline_urgency
    eligibility_notes         TEXT,                  -- LLM's reasoning (gates, gaps, edge cases)
    -- Package
    essay_draft_path          TEXT,                  -- path to generated essay file
    package_meta              JSONB,
    -- SKIP LOCKED multi-agent queue (mirrors job_pipeline_items)
    claimed_by                TEXT,
    claimed_at                TIMESTAMPTZ,
    lease_expires_at          TIMESTAMPTZ,
    -- Standard lifecycle
    applied_at                TIMESTAMPTZ,
    awarded                   BOOLEAN,                -- NULL while pending; TRUE/FALSE on outcome
    award_amount_received     INTEGER,                -- USD actually received
    outcome                   TEXT,                   -- short label, e.g. 'awarded', 'not_selected', 'expired'
    outcome_notes             TEXT,
    outcome_recorded_at       TIMESTAMPTZ,
    user_notes                TEXT,
    user_decision_at          TIMESTAMPTZ,
    created_at                TIMESTAMPTZ DEFAULT NOW(),
    updated_at                TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (posting_id)
);

CREATE INDEX IF NOT EXISTS idx_scholarship_items_status ON scholarship_pipeline_items (status);
CREATE INDEX IF NOT EXISTS idx_scholarship_items_priority ON scholarship_pipeline_items (priority_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_scholarship_items_deadline_urgency ON scholarship_pipeline_items (deadline_urgency DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_scholarship_items_claim_lookup
    ON scholarship_pipeline_items (status, lease_expires_at)
    WHERE claimed_by IS NOT NULL;
