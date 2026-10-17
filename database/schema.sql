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
