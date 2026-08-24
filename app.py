import csv
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

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


def folder_url_for_email(client_email: str) -> str | None:
    normalized_email = client_email.strip().casefold()
    with EMAILS_CSV.open(newline="", encoding="utf-8-sig") as csv_file:
        for row in csv.reader(csv_file):
            if len(row) < 2:
                continue
            email, folder_url = row[0].strip(), row[1].strip()
            if email.casefold() in {"email", "email address"}:
                continue
            if email.casefold() == normalized_email and folder_id_from_url(folder_url):
                return folder_url
    return None


def client_folder_or_error(data: dict):
    client_email = str(data.get("email", "")).strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", client_email):
        return None, None, (jsonify(code="INVALID_EMAIL", error="Please enter a valid email address."), 400)
    try:
        folder_url = folder_url_for_email(client_email)
    except OSError:
        return None, None, (jsonify(code="CLIENT_LIST_UNAVAILABLE", error="We couldn't check your gallery right now. Please try again shortly."), 503)
    if not folder_url:
        return None, None, (jsonify(code="EMAIL_NOT_FOUND", error="Sorry, that email isn't in our system. Please check the address or contact Leanne for help."), 404)
    return client_email, folder_url, None


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


@app.post("/api/gallery")
def gallery():
    data = request.get_json(silent=True) or {}
    _, folder_url, error_response = client_folder_or_error(data)
    if error_response:
        return error_response
    folder_id = folder_id_from_url(folder_url)
    try:
        images = list_images(folder_id)
    except (ValueError, RuntimeError) as exc:
        return jsonify(error=str(exc)), 400
    except requests.RequestException:
        return jsonify(error="Google Drive did not respond. Please try again."), 502
    if not images:
        return jsonify(error="No images were found in this folder."), 404
    return jsonify(images=images, count=len(images))


@app.post("/api/submit")
def submit():
    data = request.get_json(silent=True) or {}
    client_email, folder_url, error_response = client_folder_or_error(data)
    if error_response:
        return error_response
    files = data.get("files") or []
    if not isinstance(files, list) or not files:
        return jsonify(error="Select at least one image."), 400
    if len(files) > 1000:
        return jsonify(error="Too many files selected."), 400
    clean_files = []
    for item in files:
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
