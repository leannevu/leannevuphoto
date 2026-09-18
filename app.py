import ast
import csv
import hashlib
import io
import json
import os
import re
from email.message import EmailMessage
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

import requests
from services import activity_routes
from services import photographer_auth
from services import selection_store
from services import database as database_store
from services.stages import selection_stage
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from services.nordlocker import bridge, NordLockerError

load_dotenv(Path(__file__).resolve().parent / '.env')

app = Flask(__name__)
APP_JS_VERSION = hashlib.sha256((Path(__file__).resolve().parent / 'static' / 'app.js').read_bytes()).hexdigest()[:12]
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
photographer_auth.register(app)
photo_tokens = URLSafeTimedSerializer(os.getenv('SECRET_KEY') or os.urandom(32), salt='nordlocker-photos')

FOLDER_PATTERNS = (
    r"/folders/([a-zA-Z0-9_-]+)",
    r"[?&]id=([a-zA-Z0-9_-]+)",
)
EMAILS_CSV = Path(__file__).resolve().parent / "data" / "emails.csv"


def folder_id_from_url(value: str) -> str | None:
    value = (value or "").strip()
    for pattern in FOLDER_PATTERNS:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    return None


VALID_STAGES = {"choose_edits", "wait_for_edits", "final_edits"}


def is_nordlocker_share(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc == "cloud.nordlocker.com"
        and re.fullmatch(r"/shares/unlock/[a-fA-F0-9-]{36}", parsed.path) is not None
        and re.fullmatch(r"[A-Za-z0-9_-]{43}", parsed.fragment) is not None
    )


def valid_gallery_url(value: str) -> bool:
    return is_nordlocker_share(value) or bool(folder_id_from_url(value))


class GalleryConfigurationError(RuntimeError):
    pass


def gallery_config(folder_url, value):
    """Normalize old stage-only entries and explicit stage/process entries."""
    if isinstance(value, dict):
        stage = str(value.get('stage', '')).strip().casefold()
        process = str(value.get('process', '')).strip().casefold()
    else:
        stage = str(value).strip().casefold()
        process = 'nord' if is_nordlocker_share(folder_url) else 'google'
    if stage not in VALID_STAGES:
        raise GalleryConfigurationError('This gallery has an invalid stage. Please contact Leanne.')
    if process not in {'google', 'nord'}:
        raise GalleryConfigurationError("The gallery process must be 'google' or 'nord'. Please contact Leanne.")
    try:
        parsed = urlsplit(folder_url)
        google_url = parsed.scheme == 'https' and parsed.netloc == 'drive.google.com' and bool(folder_id_from_url(folder_url))
    except ValueError:
        google_url = False
    matches = google_url if process == 'google' else is_nordlocker_share(folder_url)
    if not matches:
        raise GalleryConfigurationError('The gallery link does not match its configured process (google or nord). Please contact Leanne.')
    result = {'stage': stage, 'process': process}
    if isinstance(value, dict):
        result.update({key: str(value.get(key, '')).strip() for key in ('gallery', 'date')})
    return result


def gallery_process(access, folder_url):
    return gallery_config(folder_url, access[folder_url])['process']


def normalize_client_access(value) -> dict:
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("{"):
            try:
                value = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("A client gallery entry is not a valid dictionary.") from exc
        else:
            value = {raw: "choose_edits"}
    if not isinstance(value, dict):
        raise RuntimeError("Each client gallery entry must be a dictionary.")
    return {str(url).strip(): gallery_config(str(url).strip(), config)
            for url, config in value.items()}


def access_for_email(client_email: str) -> dict | None:
    normalized_email = client_email.strip().casefold()
    if database_store.enabled():
        access = database_store.access_for_email(normalized_email)
        return {url: gallery_config(url, config) for url, config in access.items()} if access else None

    galleries_json = os.getenv("CLIENT_GALLERIES_JSON", "").strip()
    if galleries_json:
        try:
            galleries = json.loads(galleries_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CLIENT_GALLERIES_JSON is not valid JSON.") from exc
        if not isinstance(galleries, dict):
            raise RuntimeError("CLIENT_GALLERIES_JSON must be a JSON object.")
        for email, access_value in galleries.items():
            if str(email).strip().casefold() == normalized_email:
                return normalize_client_access(access_value) or None
        return None

    with EMAILS_CSV.open(newline="", encoding="utf-8-sig") as csv_file:
        rows = list(csv.reader(csv_file))

    # Preferred format: one explicit gallery per row. This avoids embedding a
    # Python dictionary inside a CSV cell and makes the active stage unambiguous.
    header = [cell.strip().casefold() for cell in rows[0]] if rows else []
    if {"email", "folder_url", "stage"}.issubset(header):
        access = {}
        for row_number, row in enumerate(rows[1:], start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) <= header.index("email") or row[header.index("email")].strip().casefold() != normalized_email:
                continue
            if len(row) > len(header) or any(key not in {"saved", "sent", "bookmark"} for key in header[len(row):]):
                raise GalleryConfigurationError(f"The gallery configuration on row {row_number} has missing or extra columns. Please contact Leanne.")
            entry = dict(zip(header, (cell.strip() for cell in row)))
            folder_url, stage = entry['folder_url'], entry['stage']
            config = gallery_config(folder_url, {
                'stage': stage,
                'process': entry.get('process', 'nord' if is_nordlocker_share(folder_url) else 'google'),
                'gallery': entry.get('gallery', ''), 'date': entry.get('date', '')})
            if folder_url in access and access[folder_url] != config:
                raise RuntimeError(
                    f"Folder URL on row {row_number} has more than one stage."
                )
            access[folder_url] = config
        return access or None

    # Backward compatibility for the original email,{URL: stage} format.
    for row in rows:
        if len(row) < 2:
            continue
        email = row[0].strip()
        if email.casefold() in {"email", "email address"}:
            continue
        if email.casefold() == normalized_email:
            return normalize_client_access(",".join(row[1:])) or None
    return None


def client_folder_or_error(data: dict):
    client_email = str(data.get("email", "")).strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", client_email):
        return None, None, (jsonify(code="INVALID_EMAIL", error="Please enter a valid email address."), 400)
    try:
        access = access_for_email(client_email)
    except GalleryConfigurationError as exc:
        return None, None, (jsonify(code='GALLERY_CONFIG_ERROR', error=str(exc)), 503)
    except database_store.DatabaseUnavailableError as exc:
        return None, None, (jsonify(code=exc.code, error=str(exc)), 503)
    except (OSError, RuntimeError):
        return None, None, (jsonify(code="CLIENT_LIST_UNAVAILABLE", error="We couldn't check your gallery right now. Please try again shortly."), 503)
    if not access:
        return None, None, (jsonify(code="EMAIL_NOT_FOUND", error="Sorry, that email isn't in our system. Please check the address or contact Leanne for help."), 404)
    return client_email, access, None


def current_stage(access: dict) -> tuple[str, str]:
    priority = {"choose_edits": 0, "wait_for_edits": 1, "final_edits": 2}
    folder_url, config = max(access.items(), key=lambda item: priority[item[1]['stage'] if isinstance(item[1], dict) else item[1]])
    return config['stage'] if isinstance(config, dict) else config, folder_url


def list_images(folder_id: str) -> list[dict]:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not configured on the server.")

    images = []
    page_token = None
    while True:
        params = {
            "key": api_key,
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken,files(id,name,mimeType,thumbnailLink,webViewLink,imageMediaMetadata)",
            "pageSize": 1000,
            "orderBy": "name_natural",
        }
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(
            "https://www.googleapis.com/drive/v3/files", params=params, timeout=20
        )
        if response.status_code in (400, 403):
            try:
                google_error = response.json().get("error", {})
                google_message = str(google_error.get("message", ""))
                details = google_error.get("errors") or []
                reason = str(details[0].get("reason", "")) if details else ""
            except (ValueError, AttributeError, IndexError):
                google_message, reason = "", ""
            diagnostic = f"{reason} {google_message}".lower()
            if "referer" in diagnostic or "api key" in diagnostic:
                raise ValueError(
                    "The Google API key was rejected. In Google Cloud, set its Application restriction to ‘None’ (or a server IP restriction), and restrict the key to Google Drive API."
                )
            if "accessnotconfigured" in diagnostic or "has not been used" in diagnostic or "disabled" in diagnostic:
                raise ValueError(
                    "Google Drive API is not enabled for this key’s project. Enable it in Google Cloud, wait a minute, then try again."
                )
            if "quota" in diagnostic or "ratelimit" in diagnostic:
                raise ValueError("The Google Drive API quota was reached. Please try again later.")
            if "insufficient" in diagnostic or "permission" in diagnostic:
                raise ValueError(
                    "Google cannot access this folder. Set General access to ‘Anyone with the link’ → Viewer, then try again."
                )
            raise ValueError(
                f"Google rejected the request: {google_message or 'check the API key and folder sharing settings.'}"
            )
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("files", []):
            if not item.get("mimeType", "").startswith("image/"):
                continue
            metadata = item.get("imageMediaMetadata") or {}
            images.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "thumbnail": f"https://drive.google.com/thumbnail?id={item['id']}&sz=w1600",
                    "viewUrl": item.get("webViewLink")
                    or f"https://drive.google.com/file/d/{item['id']}/view",
                    "downloadUrl": f"https://drive.google.com/uc?export=download&id={item['id']}",
                    "width": metadata.get("width"),
                    "height": metadata.get("height"),
                }
            )
        page_token = payload.get("nextPageToken")
        if not page_token:
            return images


class EmailDeliveryError(RuntimeError):
    """The email provider did not confirm acceptance."""


def send_selection_email(client_email: str, folder_url: str, files: list[dict], removed=None, added=None) -> None:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("EMAIL_FROM", "").strip()
    recipient = photographer_auth.OWNER_EMAIL
    if not all((api_key, sender, recipient)):
        raise RuntimeError("Email delivery is not configured yet.")

    message = EmailMessage()
    message["Subject"] = f"Photo edit selection — {len(files)} image{'s' if len(files) != 1 else ''}"
    message["From"] = sender
    message["To"] = recipient
    message["Reply-To"] = client_email
    def names(items):
        return '[' + ', '.join(item['name'] for item in items) + ']'

    added, removed = added or [], removed or []
    changes = (
        f'Update: {len(added)} added, {len(removed)} removed.\n\n'
        f'Added ({len(added)}): {names(added)}\n'
        f'Removed ({len(removed)}): {names(removed)}\n\n'
    )
    message.set_content(
        "A client updated their photo edit list. This complete list replaces previous selections for this gallery.\n\n" +
        f"{changes}"
        f"Client: {client_email}\nFolder: {folder_url}\n"
        f"Current list ({len(files)}): {names(files)}"
    )

    emails = [{"from": sender, "to": [recipient], "reply_to": client_email,
               "subject": str(message["Subject"]), "text": message.get_content()}]
    current = '\n'.join(f"- {item['name']}" for item in files) or 'No photos are currently selected for editing.'
    client_text = (
        "Hi there,\n\nThank you for updating your photo selections! "
        "Here is a copy of your latest selection update for your records.\n\n"
        f"Added to your edit list ({len(added)}): {names(added)}\n"
        f"Removed from your edit list ({len(removed)}): {names(removed)}\n\n"
        f"Your current edit selection ({len(files)} photos):\n{current}\n\n"
        "This is your complete current list, including any photos you submitted earlier. "
        "You can return to the gallery website to review your selections.\n\n"
        "If you have any questions or would like to discuss a change, simply reply to this email.\n\n"
        "Warmly,\nLeanne\nLeanne Vu Photo"
    )
    client_html = (
        '<html><body style="margin:0;background:#f7f5f1;color:#292722;font-family:Arial,sans-serif">'
        '<div style="max-width:600px;margin:32px auto;padding:32px;background:#ffffff">'
        '<p style="font-size:12px;letter-spacing:2px">LEANNE VU PHOTO</p>'
        '<h1 style="font-family:Georgia,serif;font-size:28px">Your photo selection update</h1>'
        + ''.join(f'<p style="line-height:1.7">{escape(p).replace(chr(10), "<br>")}</p>' for p in client_text.split('\n\n'))
        + '</div></body></html>'
    )
    emails.append({"from": sender, "to": [client_email], "reply_to": recipient,
                   "subject": "Your photo selection update | Leanne Vu Photo",
                   "text": client_text, "html": client_html})

    try:
        response = requests.post(
            "https://api.resend.com/emails/batch",
            headers={"Authorization": f"Bearer {api_key}"},
            json=emails,
            timeout=20,
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            app.logger.error("Resend rejected email: HTTP %s", response.status_code)
            raise EmailDeliveryError("Email delivery failed.")
        result = response.json()
        accepted = result.get('data') if isinstance(result, dict) else None
        if (not isinstance(accepted, list) or len(accepted) != len(emails)
                or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] for item in accepted)):
            raise EmailDeliveryError("Email delivery was not confirmed.")
    except (requests.RequestException, ValueError) as exc:
        app.logger.error("Resend request failed: %s", type(exc).__name__)
        raise EmailDeliveryError("Email delivery was not confirmed.") from exc


@app.get("/photographer")
@app.get("/")
def index():
    return render_template("index.html", photographer_mode=request.path == "/photographer", app_js_version=APP_JS_VERSION), 200, {'Cache-Control': 'no-store'}


@app.get("/health")
def health():
    return jsonify(status="ok")


def share_fingerprint(url):
    return hashlib.sha256(url.encode()).hexdigest()


@app.get('/api/nordlocker/photos/<token>')
def nordlocker_photo(token):
    try:
        email, fingerprint, file_id = photo_tokens.loads(token, max_age=86400)
        access = access_for_email(email)
        if not access:
            raise ValueError()
        folder_url = next((url for url in access if share_fingerprint(url) == fingerprint), None)
        if not folder_url:
            raise ValueError()
        config = gallery_config(folder_url, access[folder_url])
        stage = config['stage']
        if config['process'] != 'nord':
            raise ValueError()
        kind = request.args.get('kind', 'thumbnail')
        if kind not in {'thumbnail', 'preview', 'original'}:
            raise ValueError()
        if kind == 'original' and stage != 'final_edits':
            return jsonify(error='Original downloads are available when your final edits are ready.'), 409
    except (BadSignature, ValueError, TypeError, OSError, RuntimeError):
        return jsonify(error='This photo link is unavailable. Please reopen your gallery.'), 404
    try:
        content, mime_type, name = bridge.photo(folder_url, file_id, kind)
    except NordLockerError as exc:
        return jsonify(error=str(exc)), 502
    response = send_file(io.BytesIO(content), mimetype=mime_type,
                         as_attachment=kind == 'original', download_name=name)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def gallery_entries(access):
    return [dict(id=share_fingerprint(url), gallery=config.get('gallery') or f'Gallery {index}',
                 date=config.get('date', ''), stage=config['stage'])
            for index, (url, value) in enumerate(access.items(), 1)
            for config in [gallery_config(url, value)]]


def gallery_choices(access):
    groups = {}
    for entry in gallery_entries(access):
        config = access[next(url for url in access if share_fingerprint(url) == entry['id'])]
        name = config.get('gallery', '') if isinstance(config, dict) else ''
        key = (name.strip().casefold(), entry['date']) if name.strip() and entry['date'] else entry['id']
        groups.setdefault(key, []).append(entry)
    priority = {'choose_edits': 0, 'wait_for_edits': 1, 'final_edits': 2}
    choices = []
    for entries in groups.values():
        default = max(entries, key=lambda entry: priority[entry['stage']])
        # Preserve every folder ID: selections and downloads remain folder-specific.
        choices.append(dict(default, stages=entries))
    return choices


def selected_folder(access, gallery_id):
    if gallery_id:
        return next((url for url in access if share_fingerprint(url) == gallery_id), None)
    return next(iter(access)) if len(access) == 1 else None


def selection_location():
    # Environment and legacy mappings keep state in a separate writable CSV.
    fallback = Path(os.getenv('SELECTIONS_CSV') or EMAILS_CSV.with_name('selections.csv'))
    if os.getenv('CLIENT_GALLERIES_JSON', '').strip():
        return fallback, True
    if EMAILS_CSV.exists():
        rows = selection_store.read_rows(EMAILS_CSV)
        header = {cell.strip().casefold() for cell in rows[0]} if rows else set()
        if {'email', 'folder_url', 'stage'}.issubset(header):
            return EMAILS_CSV, False
    return fallback, True


def selection_state(email, folder_url, configured_stage):
    if database_store.enabled():
        return database_store.read(email, folder_url)
    path, _ = selection_location()
    state = selection_store.read(path, email, folder_url)
    state['stage'] = selection_stage(configured_stage, state['saved'], state['sent'])
    return state


@app.post("/api/gallery")
def gallery():
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict) and not data.get('photographer_mode'):
        workspace = photographer_auth.open_workspace(data.get('email', ''))
        if workspace is not None:
            return workspace
    client_email, access, error_response = client_folder_or_error(data)
    if error_response:
        return error_response
    choices = gallery_choices(access)
    if len(choices) > 1 and not data.get('gallery_id'):
        return jsonify(galleries=choices)
    folder_url = selected_folder(access, data.get('gallery_id') or choices[0]['id'])
    if not folder_url:
        return jsonify(error="Please choose one of your galleries."), 400
    metadata = next(entry for entry in gallery_entries(access) if entry['id'] == share_fingerprint(folder_url))
    metadata['stages'] = next(choice['stages'] for choice in choices
                              if any(entry['id'] == metadata['id'] for entry in choice['stages']))
    try:
        selections = selection_state(client_email, folder_url, metadata['stage'])
    except (OSError, RuntimeError) as exc:
        return jsonify(error=str(exc)), 503
    owner_mode = data.get('photographer_mode') is True
    if owner_mode:
        selections = dict(saved=[], sent=[], stage='choose_edits', bookmark=None, client_saved=selections['saved'], client_sent=selections['sent'])
    stage = selections['stage']
    metadata['stage'] = stage
    if stage == "wait_for_edits" and not data.get('choose_more'):
        return jsonify(images=[], count=0, stage=stage, gallery=metadata, galleries=choices, selections=selections)
    if gallery_process(access, folder_url) == 'nord':
        try:
            files = bridge.list_images(folder_url)
        except NordLockerError as exc:
            return jsonify(error=str(exc)), 502
        images = []
        for item in files:
            token = photo_tokens.dumps([client_email, share_fingerprint(folder_url), item['id']])
            images.append(dict(id=item['id'], name=item['name'],
                               thumbnail=url_for('nordlocker_photo', token=token),
                               previewUrl=url_for('nordlocker_photo', token=token, kind='preview'),
                               downloadUrl=url_for('nordlocker_photo', token=token, kind='original')))
        if not images:
            return jsonify(error='No supported photos were found in this folder.'), 404
        return jsonify(images=images, count=len(images), stage=stage, source='nordlocker', lazy=True, gallery=metadata, galleries=choices, selections=selections)
    folder_id = folder_id_from_url(folder_url)
    try:
        images = list_images(folder_id)
    except (ValueError, RuntimeError) as exc:
        return jsonify(error=str(exc)), 400
    except requests.RequestException:
        return jsonify(error="Google Drive did not respond. Please try again."), 502
    if not images:
        return jsonify(error="No images were found in this folder."), 404
    return jsonify(images=images, count=len(images), stage=stage, gallery=metadata, galleries=choices, selections=selections)


def validated_selection_files(access, folder_url, files):
    if not isinstance(files, list) or not files or len(files) > 1000:
        raise ValueError('Select between 1 and 1000 photos.')
    images = (bridge.list_images(folder_url) if gallery_process(access, folder_url) == 'nord'
              else list_images(folder_id_from_url(folder_url)))
    available = {str(item['id']): item for item in images}
    clean = {}
    for item in files:
        if not isinstance(item, dict) or str(item.get('id', '')) not in available:
            raise ValueError('Select valid photos from your gallery.')
        actual = available[str(item['id'])]
        clean[str(actual['id'])] = dict(id=str(actual['id']), name=actual['name'])
    return list(clean.values())


def email_files(access, folder_url, files):
    nord = gallery_process(access, folder_url) == 'nord'
    return [dict(item, viewUrl=folder_url if nord else f"https://drive.google.com/file/d/{item['id']}/view") for item in files]


@app.get('/api/photographer/galleries')
def photographer_galleries():
    if not database_store.enabled():
        return jsonify(error='Photographer management requires PostgreSQL.'), 503
    try:
        return jsonify(galleries=[dict(id=share_fingerprint(row['folder_url']), email=row['email'], gallery=row['gallery'], date=str(row['date'] or '')) for row in database_store.photographer_galleries()])
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503


@app.post('/api/bookmark')
def bookmark():
    data = request.get_json(silent=True) or {}
    email, access, error = client_folder_or_error(data)
    if error:
        return error
    url = selected_folder(access, data.get('gallery_id'))
    if not url:
        return jsonify(error='Please choose one of your galleries.'), 400
    try:
        file_id = data.get('file_id')
        if 'file_id' not in data:
            raise ValueError('Choose a photo to bookmark, or clear your bookmark.')
        photo = validated_selection_files(access, url, [{'id': file_id}])[0] if file_id is not None else None
        if database_store.enabled():
            transaction = database_store.transaction(email, url)
        else:
            path, allow_create = selection_location()
            transaction = selection_store.transaction(path, email, url, gallery_config(url, access[url])['stage'], allow_create)
        with transaction as selections:
            selections['bookmark'] = photo
        return jsonify(bookmark=photo)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (NordLockerError, requests.RequestException):
        return jsonify(error='Your photo provider did not respond. Please try again.'), 502
    except (OSError, RuntimeError):
        return jsonify(error='Your bookmark could not be saved. Please try again.'), 503


@app.post('/api/selections')
@app.post('/api/submit')
def submit():
    data = request.get_json(silent=True) or {}
    client_email, access, error_response = client_folder_or_error(data)
    if error_response:
        return error_response
    folder_url = selected_folder(access, data.get('gallery_id'))
    if not folder_url:
        return jsonify(error='Please choose one of your galleries.'), 400
    stage = gallery_config(folder_url, access[folder_url])['stage']
    if stage == 'final_edits':
        return jsonify(error='This gallery is complete. Edit selections can no longer be changed.'), 409
    action = 'send' if request.path == '/api/submit' else data.get('action')
    if action not in {'save', 'remove', 'send', 'unsend'}:
        return jsonify(error='Choose a valid selection action.'), 400
    try:
        clean_files = validated_selection_files(access, folder_url, data.get('files')) if action in {'save', 'send'} else []
        file_id = data.get('file_id')
        if action in {'remove', 'unsend'} and (not isinstance(file_id, str) or not file_id):
            raise ValueError('Choose a photo to remove.')
        if database_store.enabled():
            transaction = database_store.transaction(client_email, folder_url)
        else:
            path, allow_create = selection_location()
            transaction = selection_store.transaction(path, client_email, folder_url, stage, allow_create)
        with transaction as selections:
            if selections['stage'] == 'final_edits':
                return jsonify(error='This gallery is complete. Reopen your gallery.'), 409
            saved = {item['id']: item for item in selections['saved']}
            sent = {item['id']: item for item in selections['sent']}
            if action == 'save':
                saved.update({item['id']: item for item in clean_files if item['id'] not in sent})
                status_message = 'Your selection is saved.'
            elif action == 'remove':
                saved.pop(file_id, None)
                status_message = 'Photo removed from your saved selections.'
            elif action == 'send':
                additions = {item['id']: item for item in clean_files if item['id'] not in sent}
                if additions:
                    sent.update(additions)
                    if len(sent) > 1000:
                        raise ValueError('A gallery can contain at most 1000 sent selections.')
                    send_selection_email(client_email, folder_url, email_files(access, folder_url, list(sent.values())), added=list(additions.values()))
                for item in clean_files:
                    saved.pop(item['id'], None)
                selections['stage'] = 'wait_for_edits'
                status_message = 'Your edit list was sent successfully.'
            else:
                removed = sent.pop(file_id, None)
                if removed:
                    send_selection_email(client_email, folder_url, email_files(access, folder_url, list(sent.values())), removed=[removed])
                status_message = 'Photo unsent. Leanne has been notified.' if removed else 'This photo is already outside your sent list.'
            if len(saved) > 1000 or len(sent) > 1000:
                raise ValueError('A gallery can contain at most 1000 saved and 1000 sent selections.')
            selections['saved'], selections['sent'] = list(saved.values()), list(sent.values())
        return jsonify(message=status_message, selections=selections)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (NordLockerError, requests.RequestException):
        return jsonify(error='Your photo provider did not respond. Please try again.'), 502
    except EmailDeliveryError:
        return jsonify(error='The notification could not be sent. Your edit list has not changed. Please try again.'), 502
    except (OSError, RuntimeError) as exc:
        return jsonify(error=str(exc) or 'Your changes could not be saved. Please try again.'), 503


activity_routes.register(app, client_folder_or_error, selected_folder)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
