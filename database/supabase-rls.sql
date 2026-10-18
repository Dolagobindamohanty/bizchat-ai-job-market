-- Run after database/schema.sql in the Supabase SQL editor as the table owner.
-- Browser PostgREST requests use the publishable/anon key plus the signed-in
-- user's bearer token. Never expose a service-role key in a browser.
BEGIN;

ALTER TABLE public.saved_user_data
    ALTER COLUMN id SET DEFAULT gen_random_uuid(),
    ALTER COLUMN user_id SET DEFAULT auth.uid()::text,
    ALTER COLUMN created_at SET DEFAULT now(),
    ALTER COLUMN updated_at SET DEFAULT now();

-- Existing deployments may contain multiple historical rows. Keep the most
-- recently updated row for each owner/type, then enforce the upsert key.
WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (
        PARTITION BY user_id, data_type
        ORDER BY updated_at DESC, created_at DESC, id DESC
    ) AS row_number
    FROM public.saved_user_data
)
DELETE FROM public.saved_user_data AS saved
USING ranked
WHERE saved.id = ranked.id AND ranked.row_number > 1;

CREATE UNIQUE INDEX IF NOT EXISTS saved_user_data_user_type_uidx
    ON public.saved_user_data(user_id, data_type);

ALTER TABLE public.job_runs
    ALTER COLUMN id SET DEFAULT gen_random_uuid(),
    ALTER COLUMN started_at SET DEFAULT now();

ALTER TABLE public.saved_user_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skills ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.job_skills ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.job_runs ENABLE ROW LEVEL SECURITY;

-- Remove existing policies: permissive policies combine with OR and could
-- otherwise bypass the ownership restriction. Ingestion tables have no policies.
DO $$
DECLARE existing_policy record;
BEGIN
    FOR existing_policy IN
        SELECT tablename, policyname FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename IN ('saved_user_data', 'jobs', 'skills', 'job_skills', 'job_runs')
    LOOP
        EXECUTE format('DROP POLICY %I ON public.%I',
                       existing_policy.policyname, existing_policy.tablename);
    END LOOP;
END $$;

REVOKE ALL ON TABLE public.saved_user_data FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.saved_user_data TO authenticated;

CREATE POLICY saved_user_data_owner ON public.saved_user_data
    FOR ALL TO authenticated
    USING (user_id = auth.uid()::text)
    WITH CHECK (user_id = auth.uid()::text);

REVOKE ALL ON TABLE public.jobs, public.skills, public.job_skills, public.job_runs
    FROM PUBLIC, anon, authenticated;

COMMIT;
