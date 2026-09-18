"""Gallery visits kept separately from photo selection records."""
import uuid
from . import database

SCHEMA = """
CREATE TABLE IF NOT EXISTS public.gallery_visits (
    id uuid PRIMARY KEY,
    gallery_row_id bigint NOT NULL REFERENCES public.galleries(id),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    ended_at timestamptz,
    is_active boolean NOT NULL DEFAULT false,
    sequence bigint NOT NULL DEFAULT 0 CHECK (sequence >= 0)
);
CREATE INDEX IF NOT EXISTS gallery_visits_started_idx ON public.gallery_visits (started_at DESC);
CREATE INDEX IF NOT EXISTS gallery_visits_gallery_idx ON public.gallery_visits (gallery_row_id, started_at DESC);
"""


def visit_id(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError('Invalid visit identifier.') from exc


def start(identifier, email, folder_url):
    identifier = visit_id(identifier)
    access = database.access_for_email(email) or {}
    config = access.get(folder_url)
    if not config:
        raise ValueError('This gallery visit is unavailable.')
    with database.connection() as conn:
        original = conn.execute('SELECT folder_url FROM public.galleries WHERE id = %s', (config['group_id'],)).fetchone()
        folder_url = original['folder_url']
    with database.connection() as conn:
        row = conn.execute('''INSERT INTO public.gallery_visits (id, gallery_row_id)
            SELECT %s, id FROM public.galleries WHERE email = %s AND folder_url = %s
            ON CONFLICT (id) DO NOTHING RETURNING id''', (identifier, email.strip().casefold(), folder_url)).fetchone()
        if row is None:
            existing = conn.execute('''SELECT v.id FROM public.gallery_visits v
                JOIN public.galleries e ON e.id = v.gallery_row_id
                WHERE v.id = %s AND e.email = %s AND e.folder_url = %s''',
                (identifier, email.strip().casefold(), folder_url)).fetchone()
            if not existing:
                raise ValueError('This gallery visit is unavailable.')
    return str(identifier)


def heartbeat(identifier, sequence, active, ended):
    identifier = visit_id(identifier)
    with database.connection() as conn:
        row = conn.execute('''UPDATE public.gallery_visits SET
            sequence = %s, last_seen_at = clock_timestamp(), is_active = %s,
            ended_at = CASE WHEN %s THEN clock_timestamp() ELSE NULL END
            WHERE id = %s AND sequence < %s AND ended_at IS NULL RETURNING id''',
            (sequence, active and not ended, ended, identifier, sequence)).fetchone()
        if row is None and not conn.execute('SELECT id FROM public.gallery_visits WHERE id = %s', (identifier,)).fetchone():
            raise ValueError('This gallery visit is unavailable.')


def overview():
    with database.connection() as conn:
        return conn.execute("""WITH recent AS (
            SELECT v.id, e.email, e.gallery, v.started_at, v.last_seen_at,
                CASE WHEN v.ended_at IS NOT NULL OR v.last_seen_at <= now() - interval '45 seconds' THEN 'left'
                     WHEN v.is_active THEN 'viewing' ELSE 'idle' END AS status
                FROM public.gallery_visits v JOIN public.galleries e ON e.id = v.gallery_row_id
                WHERE v.started_at >= now() - interval '7 days'
            ), latest AS (SELECT * FROM recent ORDER BY started_at DESC, id LIMIT 200)
            SELECT jsonb_build_object(
                'visits', count(*), 'clients', count(DISTINCT email),
                'active_now', count(*) FILTER (WHERE status = 'viewing')
            ) AS summary,
            COALESCE((SELECT jsonb_agg(to_jsonb(latest) ORDER BY started_at DESC, id) FROM latest), '[]'::jsonb) AS visits
            FROM recent""").fetchone()
