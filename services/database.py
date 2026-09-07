"""PostgreSQL gallery records and durable selection carts."""
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .stages import selection_stage


SCHEMA = """
CREATE TABLE IF NOT EXISTS public.emails (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email text NOT NULL CHECK (email = lower(btrim(email))),
    gallery text NOT NULL DEFAULT '',
    date date,
    folder_url text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('choose_edits', 'wait_for_edits', 'final_edits')),
    process text NOT NULL CHECK (process IN ('google', 'nord')),
    saved jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(saved) = 'array'),
    sent jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(sent) = 'array'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (email, folder_url)
)
"""


def enabled():
    return bool(os.getenv('DATABASE_URL', '').strip())


@contextmanager
def connection():
    try:
        with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=10,
                             row_factory=dict_row, application_name='leannevu-gallery') as conn:
            yield conn
    except psycopg.Error as exc:
        # Connection diagnostics can contain host/user details; keep them out of the UI.
        raise RuntimeError('The gallery database is unavailable. Please try again shortly.') from exc


def access_for_email(email):
    with connection() as conn:
        rows = conn.execute(
            'SELECT folder_url, gallery, date, stage, process, saved, sent FROM public.emails WHERE email = %s ORDER BY id',
            (email.strip().casefold(),),
        ).fetchall()
    return {row['folder_url']: dict(gallery=row['gallery'], date=row['date'].isoformat() if row['date'] else '',
                                    stage=selection_stage(row['stage'], row['saved'], row['sent']), process=row['process']) for row in rows} or None


def read(email, folder_url):
    with connection() as conn:
        row = conn.execute(
            'SELECT saved, sent, stage FROM public.emails WHERE email = %s AND folder_url = %s',
            (email.strip().casefold(), folder_url),
        ).fetchone()
    if row is None:
        raise RuntimeError('This gallery is no longer available. Please reopen your gallery.')
    row['stage'] = selection_stage(row['stage'], row['saved'], row['sent'])
    return row


@contextmanager
def transaction(email, folder_url):
    with connection() as conn:
        conn.execute("SET LOCAL lock_timeout = '30s'")
        row = conn.execute(
            'SELECT id, saved, sent, stage FROM public.emails WHERE email = %s AND folder_url = %s FOR UPDATE',
            (email.strip().casefold(), folder_url),
        ).fetchone()
        if row is None:
            raise RuntimeError('This gallery is no longer available. Please reopen your gallery.')
        state = dict(saved=row['saved'], sent=row['sent'], stage=row['stage'])
        yield state
        state['stage'] = selection_stage(state['stage'], state['saved'], state['sent'])
        conn.execute(
            'UPDATE public.emails SET saved = %s, sent = %s, stage = %s, updated_at = now() WHERE id = %s',
            (Jsonb(state['saved']), Jsonb(state['sent']), state['stage'], row['id']),
        )


STAGE_RULE_SQL = """
CREATE OR REPLACE FUNCTION public.sync_gallery_selection_stage()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.stage <> 'final_edits' THEN
        NEW.stage := CASE WHEN jsonb_array_length(NEW.sent) > 0
                          THEN 'wait_for_edits' ELSE 'choose_edits' END;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS gallery_selection_stage ON public.emails;
CREATE TRIGGER gallery_selection_stage
BEFORE INSERT OR UPDATE OF saved, sent, stage ON public.emails
FOR EACH ROW EXECUTE FUNCTION public.sync_gallery_selection_stage();
UPDATE public.emails
SET stage = CASE WHEN jsonb_array_length(sent) > 0
                 THEN 'wait_for_edits' ELSE 'choose_edits' END,
    updated_at = now()
WHERE stage <> 'final_edits'
  AND stage IS DISTINCT FROM CASE WHEN jsonb_array_length(sent) > 0
                                 THEN 'wait_for_edits' ELSE 'choose_edits' END;
"""
