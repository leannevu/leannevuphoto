"""PostgreSQL gallery records and durable selection carts."""
import os
import json
import logging
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
    bookmark jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (email, folder_url)
)
"""


def enabled():
    return bool(os.getenv('DATABASE_URL', '').strip())


class DatabaseUnavailableError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('The gallery database is unavailable. Please try again shortly.')


def error_code(exc):
    codes = {'28P01': 'DB_AUTH_FAILED', '28000': 'DB_AUTH_FAILED',
             '3D000': 'DB_NAME_INVALID', '42P01': 'DB_TABLE_MISSING',
             '42703': 'DB_SCHEMA_MISMATCH', '42501': 'DB_PERMISSION_DENIED'}
    if exc.sqlstate in codes:
        return codes[exc.sqlstate]
    # libpq connection errors often have no SQLSTATE. Inspect locally, but never
    # return or log the raw message: it can contain connection credentials.
    detail = str(exc).casefold()
    for fragments, code in (
        (('password authentication failed',), 'DB_AUTH_FAILED'),
        (('could not translate host name', 'failed to resolve host', 'name or service not known'), 'DB_HOST_UNRESOLVED'),
        (('timeout expired', 'connection timed out'), 'DB_CONNECTION_TIMEOUT'),
        (('connection refused', 'network is unreachable'), 'DB_CONNECTION_REFUSED'),
        (('invalid connection option', 'missing "="', 'invalid integer value', 'invalid uri'), 'DB_URL_INVALID'),
    ):
        if any(fragment in detail for fragment in fragments):
            return code
    return 'DB_UNAVAILABLE'


@contextmanager
def connection():
    try:
        with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=10,
                             row_factory=dict_row, application_name='leannevu-gallery') as conn:
            yield conn
    except psycopg.Error as exc:
        code = error_code(exc)
        logging.getLogger(__name__).error('Gallery database request failed [%s]', code)
        raise DatabaseUnavailableError(code) from exc


def access_for_email(email):
    with connection() as conn:
        rows = conn.execute(
            'SELECT folder_url, gallery, date, stage, process, saved, sent FROM public.emails WHERE email = %s ORDER BY id',
            (email.strip().casefold(),),
        ).fetchall()
    return {row['folder_url']: dict(gallery=row['gallery'], date=row['date'].isoformat() if row['date'] else '',
                                    stage=selection_stage(row['stage'], row['saved'], row['sent']), process=row['process']) for row in rows} or None


def normalize_bookmark(value):
    # Older databases used TEXT. Jsonb writes to TEXT were read back as strings
    # and escaped again on every cart update, doubling the response size.
    if isinstance(value, str) and value.strip('"\\ ').casefold() == 'null':
        return None
    for _ in range(64):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except (ValueError, RecursionError) as exc:
            raise RuntimeError('Your saved bookmark could not be read. Please contact Leanne.') from exc
    if value is None:
        return None
    if isinstance(value, dict) and isinstance(value.get('id'), str) and isinstance(value.get('name'), str):
        return {'id': value['id'], 'name': value['name']}
    raise RuntimeError('Your saved bookmark could not be read. Please contact Leanne.')


def read(email, folder_url):
    with connection() as conn:
        row = conn.execute(
            'SELECT saved, sent, stage, bookmark FROM public.emails WHERE email = %s AND folder_url = %s',
            (email.strip().casefold(), folder_url),
        ).fetchone()
    if row is None:
        raise RuntimeError('This gallery is no longer available. Please reopen your gallery.')
    row['bookmark'] = normalize_bookmark(row['bookmark'])
    row['stage'] = selection_stage(row['stage'], row['saved'], row['sent'])
    return row


@contextmanager
def transaction(email, folder_url):
    with connection() as conn:
        conn.execute("SET LOCAL lock_timeout = '30s'")
        row = conn.execute(
            'SELECT id, saved, sent, stage, bookmark FROM public.emails WHERE email = %s AND folder_url = %s FOR UPDATE',
            (email.strip().casefold(), folder_url),
        ).fetchone()
        if row is None:
            raise RuntimeError('This gallery is no longer available. Please reopen your gallery.')
        state = dict(saved=row['saved'], sent=row['sent'], stage=row['stage'], bookmark=normalize_bookmark(row['bookmark']))
        yield state
        state['stage'] = selection_stage(state['stage'], state['saved'], state['sent'])
        conn.execute(
            'UPDATE public.emails SET saved = %s, sent = %s, stage = %s, bookmark = %s, updated_at = now() WHERE id = %s',
            (Jsonb(state['saved']), Jsonb(state['sent']), state['stage'], Jsonb(state.get('bookmark')), row['id']),
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
