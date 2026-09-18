-- Deploy the updated application before running this migration.
BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE public.emails
    DROP COLUMN IF EXISTS photographer_selected,
    DROP COLUMN IF EXISTS photographer_picks;
COMMIT;
