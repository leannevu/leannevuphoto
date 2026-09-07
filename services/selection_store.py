"""Persist per-client proof carts in CSV, with locked, atomic updates."""
import csv
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from .stages import selection_stage


_lock = threading.RLock()


def read_rows(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as source:
        rows = list(csv.reader(source))
    return rows


def parse_files(value):
    try:
        files = json.loads(value or '[]')
        if not isinstance(files, list) or any(
            not isinstance(item, dict) or not isinstance(item.get('id'), str)
            or not isinstance(item.get('name'), str) for item in files
        ):
            raise ValueError()
        return list({item['id']: {'id': item['id'], 'name': item['name']} for item in files}.values())
    except (ValueError, TypeError) as exc:
        raise RuntimeError('The saved or sent column must contain a JSON photo list or be blank.') from exc


def locate(rows, email, url):
    header = [cell.strip().casefold() for cell in rows[0]] if rows else []
    if not {'email', 'folder_url', 'stage'}.issubset(header):
        raise RuntimeError('Saving selections requires an email, folder_url and stage CSV header.')
    for index, row in enumerate(rows[1:], 1):
        if len(row) <= max(header.index('email'), header.index('folder_url')):
            continue
        if row[header.index('email')].strip().casefold() == email.strip().casefold() and row[header.index('folder_url')].strip() == url:
            if len(row) > len(header) or any(key not in {'saved', 'sent'} for key in header[len(row):]):
                raise RuntimeError('This gallery row has missing or extra columns.')
            entry = dict(zip(header, row))
            return index, dict(saved=parse_files(entry.get('saved')), sent=parse_files(entry.get('sent')), stage=entry['stage'])
    return None, dict(saved=[], sent=[], stage='')


def read(path, email, url):
    if not Path(path).exists():
        return dict(saved=[], sent=[], stage='')
    return locate(read_rows(path), email, url)[1]


@contextmanager
def transaction(path, email, url, stage, allow_create=False):
    """Serialize worker updates; exceptions leave the original CSV unchanged."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.with_suffix(path.suffix + '.lock').open('a+b') as lock_file:
        lock_file.seek(0, 2)
        if lock_file.tell() == 0:
            lock_file.write(b'0')
            lock_file.flush()
        lock_file.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        temp_path = None
        try:
            rows = read_rows(path) if path.exists() else [['email', 'folder_url', 'stage', 'saved', 'sent']]
            index, state = locate(rows, email, url)
            if index is None:
                if not allow_create:
                    raise RuntimeError('This gallery is no longer in the client list. Reopen your gallery.')
                header = [cell.strip().casefold() for cell in rows[0]]
                rows.append([{'email': email.strip().casefold(), 'folder_url': url, 'stage': stage}.get(key, '') for key in header])
                index = len(rows) - 1
                state['stage'] = stage
            # Check that a temporary file can be written before any email is sent.
            fd, temp_path = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
            os.close(fd)
            yield state
            state['stage'] = selection_stage(state['stage'], state['saved'], state['sent'])
            header = [cell.strip().casefold() for cell in rows[0]]
            for key in ('saved', 'sent'):
                if key not in header:
                    header.append(key)
                    rows[0].append(key)
            row = rows[index]
            row.extend([''] * (len(header) - len(row)))
            for key in ('saved', 'sent'):
                row[header.index(key)] = json.dumps(state[key], ensure_ascii=False, separators=(',', ':'))
            row[header.index('stage')] = state['stage']
            with open(temp_path, 'w', newline='', encoding='utf-8') as output:
                csv.writer(output).writerows(rows)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            lock_file.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
