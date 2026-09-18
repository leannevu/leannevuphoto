"""PostgreSQL gallery records and durable selection carts."""
import os
import json
import logging
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .stages import selection_stage


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


GALLERY_QUERY = """
SELECT g.*, c.sent_selection, c.stage AS selection_stage, c.production_link,
       m.selection AS photographer_selection, m.stage AS photographer_stage,
       m.production_link AS photographer_link
FROM public.galleries g
LEFT JOIN public.client_selection c ON c.gallery_id = g.id
LEFT JOIN public.my_selection m ON m.gallery_id = g.id
"""


def published(stage, link):
    return stage == 'final_edits' and isinstance(link, str) and bool(link.strip())


def gallery_access(rows):
    access = {}
    for row in rows:
        stage = row['selection_stage'] or row['client_stage']
        # A final status alone never makes the proof folder a published gallery.
        proof_stage = stage if stage != 'final_edits' else 'wait_for_edits'
        config = dict(gallery=row['gallery'], date=str(row['date'] or ''),
                      group_id=row['id'], collection='proofs', stage=proof_stage,
                      process=row['process'], source_url=row['folder_url'])
        access[f"gallery:{row['id']}:proofs"] = config
        for collection, status, link in (
            ('client', stage, row['production_link']),
            ('photographer', row['photographer_stage'], row['photographer_link']),
        ):
            if published(status, link):
                url = link.strip()
                access[f"gallery:{row['id']}:{collection}"] = dict(config, collection=collection, stage='final_edits', source_url=url,
                                   process='nord' if url.startswith('https://cloud.nordlocker.com/') else 'google')
    return access


def access_for_email(email):
    with connection() as conn:
        rows = conn.execute(GALLERY_QUERY + ' WHERE g.email = %s ORDER BY g.id',
                            (email.strip().casefold(),)).fetchall()
    return gallery_access(rows) or None


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


def photographer_galleries():
    with connection() as conn:
        return conn.execute('SELECT id, email, folder_url, gallery, date, client_stage AS stage, process FROM public.galleries ORDER BY email, id').fetchall()


def read(email, folder_url):
    with connection() as conn:
        rows = conn.execute(GALLERY_QUERY + ' WHERE g.email = %s ORDER BY g.id',
                            (email.strip().casefold(),)).fetchall()
    for row in rows:
        access = gallery_access([row])
        if folder_url not in access:
            continue
        config = access[folder_url]
        if config['collection'] != 'proofs':
            return dict(saved=[], sent=[], stage='final_edits', bookmark=None,
                        photographer_selection=[])
        return dict(saved=row['saved_selection'] or [], sent=row['sent_selection'] or [],
                    stage=config['stage'], bookmark=normalize_bookmark(row['bookmark']),
                    photographer_selection=row['photographer_selection'] or [])
    raise RuntimeError('This gallery is no longer available. Please reopen your gallery.')


@contextmanager
def transaction(email, folder_url):
    config = (access_for_email(email) or {}).get(folder_url)
    if not config or config['collection'] != 'proofs':
        raise RuntimeError('Selections can only be changed in the proof gallery.')
    with connection() as conn:
        conn.execute("SET LOCAL lock_timeout = '30s'")
        row = conn.execute('SELECT * FROM public.galleries WHERE email = %s AND folder_url = %s FOR UPDATE',
                           (email.strip().casefold(), config['source_url'])).fetchone()
        if row is None:
            raise RuntimeError('Selections can only be changed in the proof gallery.')
        selected = conn.execute('SELECT sent_selection, stage FROM public.client_selection WHERE gallery_id = %s FOR UPDATE',
                                (row['id'],)).fetchone()
        state = dict(saved=row['saved_selection'] or [], sent=(selected['sent_selection'] or []) if selected else [],
                     stage=selected['stage'] if selected else row['client_stage'], bookmark=normalize_bookmark(row['bookmark']))
        yield state
        state['stage'] = selection_stage(state['stage'], state['saved'], state['sent'])
        conn.execute('UPDATE public.galleries SET saved_selection = %s, bookmark = %s WHERE id = %s',
                     (Jsonb(state['saved']), Jsonb(state.get('bookmark')), row['id']))
        conn.execute("""INSERT INTO public.client_selection (gallery_id, client_gallery, sent_selection, stage)
                        VALUES (%s, %s, %s, %s) ON CONFLICT (gallery_id) DO UPDATE
                        SET sent_selection = EXCLUDED.sent_selection, stage = EXCLUDED.stage""",
                     (row['id'], '_'.join(filter(None, [row['name'], row['gallery']])), Jsonb(state['sent']), state['stage']))


@contextmanager
def photographer_transaction(email, gallery_key):
    config = (access_for_email(email) or {}).get(gallery_key)
    if not config or config['collection'] != 'proofs':
        raise ValueError('Choose photographs in the original gallery.')
    with connection() as conn:
        conn.execute("SET LOCAL lock_timeout = '30s'")
        gallery = conn.execute('SELECT id, name, gallery FROM public.galleries WHERE id = %s AND email = %s FOR UPDATE',
                               (config['group_id'], email.strip().casefold())).fetchone()
        if gallery is None:
            raise ValueError('This gallery is no longer available.')
        row = conn.execute('SELECT selection, stage FROM public.my_selection WHERE gallery_id = %s FOR UPDATE',
                           (gallery['id'],)).fetchone()
        state = dict(saved=(row['selection'] or []) if row else [], sent=[], stage='choose_edits',
                     production_stage=(row['stage'] or 'choose_edits') if row else 'choose_edits')
        yield state
        conn.execute("""INSERT INTO public.my_selection (gallery_id, client_gallery, selection, stage)
                        VALUES (%s, %s, %s, %s) ON CONFLICT (gallery_id) DO UPDATE
                        SET selection = EXCLUDED.selection, stage = EXCLUDED.stage""",
                     (gallery['id'], '_'.join(filter(None, [gallery['name'], gallery['gallery']])),
                      Jsonb(state['saved']), state['production_stage']))
