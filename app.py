import ast
import csv
import hashlib
import io
import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from nordlocker import bridge, NordLockerError

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
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
    return {'stage': stage, 'process': process}


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
    if header in (["email", "folder_url", "stage"], ["email", "folder_url", "stage", "process"]):
        access = {}
        for row_number, row in enumerate(rows[1:], start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if row[0].strip().casefold() != normalized_email:
                continue
            if len(row) != len(header):
                raise GalleryConfigurationError(f"The gallery configuration on row {row_number} has missing or extra columns. Please contact Leanne.")
            email, folder_url, stage = (cell.strip() for cell in row[:3])
            if email.casefold() != normalized_email:
                continue
            config = gallery_config(folder_url, {'stage':stage, 'process':row[3]} if len(header) == 4 else stage)
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


def send_selection_email(client_email: str, folder_url: str, files: list[dict]) -> None:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    recipient = os.getenv("PORTFOLIO_OWNER_EMAIL")
    if not all((smtp_host, smtp_user, smtp_password, recipient)):
        raise RuntimeError("Email delivery is not configured yet.")

    message = EmailMessage()
    message["Subject"] = f"Photo edit selection — {len(files)} image{'s' if len(files) != 1 else ''}"
    # Authenticated SMTP providers generally require From to match SMTP_USER.
    message["From"] = smtp_user
    message["To"] = recipient
    message["Reply-To"] = client_email
    rows = "\n".join(
        f"{index}. {item['name']}\n   {item['viewUrl']}"
        for index, item in enumerate(files, 1)
    )
    message.set_content(
        f"A client submitted photos for editing.\n\n"
        f"Client: {client_email}\nFolder: {folder_url}\n"
        f"Selected: {len(files)}\n\n{rows}"
    )

    port = int(os.getenv("SMTP_PORT", "587"))
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(smtp_host, port, context=context, timeout=20) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, port, timeout=20) as server:
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)
            server.send_message(message)


@app.get("/")
def index():
    return render_template("index.html")


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
        stage, folder_url = current_stage(access)
        if gallery_process(access, folder_url) != 'nord' or share_fingerprint(folder_url) != fingerprint or stage == 'wait_for_edits':
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


@app.post("/api/gallery")
def gallery():
    data = request.get_json(silent=True) or {}
    client_email, access, error_response = client_folder_or_error(data)
    if error_response:
        return error_response
    stage, folder_url = current_stage(access)
    if stage == "wait_for_edits":
        return jsonify(images=[], count=0, stage=stage)
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
        return jsonify(images=images, count=len(images), stage=stage, source='nordlocker', lazy=True)
    folder_id = folder_id_from_url(folder_url)
    try:
        images = list_images(folder_id)
    except (ValueError, RuntimeError) as exc:
        return jsonify(error=str(exc)), 400
    except requests.RequestException:
        return jsonify(error="Google Drive did not respond. Please try again."), 502
    if not images:
        return jsonify(error="No images were found in this folder."), 404
    return jsonify(images=images, count=len(images), stage=stage)


@app.post("/api/submit")
def submit():
    data = request.get_json(silent=True) or {}
    client_email, access, error_response = client_folder_or_error(data)
    if error_response:
        return error_response
    stage, folder_url = current_stage(access)
    if stage != "choose_edits":
        return jsonify(error="Edit selections are only available during the choose edits stage."), 409
    files = data.get("files") or []
    if not isinstance(files, list) or not files:
        return jsonify(error="Select at least one image."), 400
    if len(files) > 1000:
        return jsonify(error="Too many files selected."), 400
    clean_files = []
    available = None
    if gallery_process(access, folder_url) == 'nord':
        try:
            available = {item['id']: item for item in bridge.list_images(folder_url)}
        except NordLockerError as exc:
            return jsonify(error=str(exc)), 502
    for item in files:
        if available is not None:
            if not isinstance(item, dict) or str(item.get('id', '')) not in available:
                return jsonify(error='Select valid photos from your gallery.'), 400
            actual = available[str(item['id'])]
            if not any(selected['id'] == actual['id'] for selected in clean_files):
                clean_files.append(dict(id=actual['id'], name=actual['name'], viewUrl=folder_url))
            continue
        if not isinstance(item, dict):
            continue
        file_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(item.get("id", "")))
        if file_id:
            clean_files.append(
                {
                    "id": file_id,
                    "name": str(item.get("name", "Untitled"))[:300],
                    "viewUrl": f"https://drive.google.com/file/d/{file_id}/view",
                }
            )
    if not clean_files:
        return jsonify(error="Select at least one valid image."), 400
    try:
        send_selection_email(client_email, folder_url, clean_files)
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503
    except (OSError, smtplib.SMTPException):
        return jsonify(error="The email could not be sent. Check the SMTP settings."), 502
    return jsonify(message="Your edit list was sent successfully.")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
