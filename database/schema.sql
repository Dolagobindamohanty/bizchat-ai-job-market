CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    source_job_id TEXT,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    salary_min_lpa NUMERIC(10, 2),
    salary_max_lpa NUMERIC(10, 2),
    experience_min INTEGER,
    remote_type TEXT,
    source TEXT NOT NULL,
    -- Provenance is required for every ingested job record.
    source_url TEXT NOT NULL,
    description TEXT,
    posted_date DATE,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description_hash TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS skills (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_skills (
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    skill_id BIGINT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    extraction_method TEXT NOT NULL DEFAULT 'phrase_matcher',
    confidence NUMERIC(4, 3),
    PRIMARY KEY (job_id, skill_id)
);

CREATE INDEX IF NOT EXISTS jobs_posted_date_idx ON jobs(posted_date);
CREATE INDEX IF NOT EXISTS jobs_location_idx ON jobs(location);
CREATE INDEX IF NOT EXISTS jobs_source_idx ON jobs(source);
-- A provider's stable identifier is the preferred deduplication key.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_source_job_id_uidx
    ON jobs(source, source_job_id)
    WHERE source_job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS job_runs (
    id UUID PRIMARY KEY,
    run_date DATE NOT NULL,
    status TEXT NOT NULL,
    jobs_seen INTEGER NOT NULL DEFAULT 0,
    jobs_saved INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS job_runs_run_date_idx ON job_runs(run_date DESC);

CREATE TABLE IF NOT EXISTS saved_user_data (
    id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    data_type TEXT NOT NULL CHECK (data_type IN ('profile', 'search', 'learning_plan')),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS saved_user_data_user_type_idx ON saved_user_data(user_id, data_type, updated_at DESC);
