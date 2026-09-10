-- Additive migration. Never rewrites client saved, sent, stage or bookmark.
ALTER TABLE public.emails ADD COLUMN IF NOT EXISTS photographer_picks text NOT NULL DEFAULT 'no';
ALTER TABLE public.emails ADD COLUMN IF NOT EXISTS photographer_selected jsonb;
UPDATE public.emails SET photographer_picks = 'yes'
WHERE email = 'socheata.p113@gmail.com' AND photographer_picks = 'no';
DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.emails'::regclass AND conname = 'photographer_picks_valid') THEN
ALTER TABLE public.emails ADD CONSTRAINT photographer_picks_valid CHECK (
  photographer_picks IN ('yes', 'no') AND
  (photographer_selected IS NULL OR
   (photographer_picks = 'yes' AND jsonb_typeof(photographer_selected) = 'array'))
);
END IF;
END $$;
