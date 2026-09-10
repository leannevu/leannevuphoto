-- Additive migration: no existing gallery or selection values are changed.

CREATE TABLE IF NOT EXISTS public.gallery_visits (
    id uuid PRIMARY KEY,
    gallery_row_id bigint NOT NULL REFERENCES public.emails(id),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    ended_at timestamptz,
    is_active boolean NOT NULL DEFAULT false,
    sequence bigint NOT NULL DEFAULT 0 CHECK (sequence >= 0)
);
CREATE INDEX IF NOT EXISTS gallery_visits_started_idx ON public.gallery_visits (started_at DESC);
CREATE INDEX IF NOT EXISTS gallery_visits_gallery_idx ON public.gallery_visits (gallery_row_id, started_at DESC);
