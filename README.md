# Leanne Vu Photo — client selections

Flask app for opening a client's Google Drive or public NordLocker gallery by email address, selecting files, and emailing an edit list.

## Setup

1. In the same Google Cloud project, enable **Google Drive API**.
2. Create an **API key** under APIs & Services → Credentials. Because Flask calls Google from the server, set **Application restrictions** to `None` for local development (an HTTP-referrer/“Web sites” restriction will fail). Under **API restrictions**, restrict it to **Google Drive API**. The existing OAuth client-secret file is not used for public folders; keep it private.
3. Copy `.env.example` to `.env` and add the API key and email settings.
4. Add each client gallery to `data/emails.csv`, one per line, using the columns `email,folder_url,stage,process`. Valid stages are `choose_edits`, `wait_for_edits`, and `final_edits`. Set `process` to `google` for Google Drive or `nord` for NordLocker. The process must match the link. Email matching is case-insensitive. A folder URL can have only one current stage and process.

```csv
email,folder_url,stage,process
client@example.com,https://drive.google.com/drive/folders/PROOFS_ID,choose_edits,google
client@example.com,https://drive.google.com/drive/folders/FINALS_ID,final_edits,google
```
5. For Gmail SMTP, enable 2-Step Verification and create an app password. Put the app password in `SMTP_PASSWORD`.
6. Install and run:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app app run --debug
```

Open http://127.0.0.1:5000. The Drive folder must be shared as **Anyone with the link → Viewer**.

Email is sent from the authenticated SMTP account with the client's address in `Reply-To`; mail providers do not allow arbitrary visitors to send as themselves.

## Public NordLocker galleries

Use the complete public share URL, including its `#` fragment, in `data/emails.csv`
or `CLIENT_GALLERIES_JSON`. The existing stages work with either provider.
Set the CSV `process` column to `nord` for these links. The older three-column
CSV format remains supported and infers the process from the link.

NordLocker photos are decrypted on demand using NordLocker's own web client in
an isolated headless browser. The server lists metadata first, then loads small
stored thumbnails as cards enter the screen. Opening a photo fetches and decrypts
that original in memory and returns a resized preview. Final-stage downloads
return the original bytes. Missing thumbnails fall back to resizing one original.
No ZIP archive or photo files are saved; previews use a bounded 32 MB memory cache.
The share key stays on the server. Signed photo URLs expire after 24 hours and are
checked against the current client mapping and stage on every request.

Install `requirements.txt` and ensure Microsoft Edge is installed on Windows.
On Linux, install the browser with `python -m playwright install --with-deps chromium`.
`NORDLOCKER_BROWSER_CHANNEL` can override the browser channel; an empty value uses
Playwright's Chromium. Set a persistent random `SECRET_KEY` in production so
signed photo URLs survive restarts. Use one Gunicorn worker with multiple threads
as configured in `Procfile`.

The browser integration depends on NordLocker's current public client interfaces,
not a documented third-party API. An upstream client change may require an adapter
update. Password-protected shares and recursive subfolders are not supported.
Browser sessions/metadata refresh after five minutes of use. Photo filenames in
selection emails come from the server's gallery list, alongside the share link.

Checks:

```powershell
python -m unittest test_gallery_config test_nordlocker
python check_google_live.py
python check_google_ui.py
python check_nordlocker_live.py
python check_nordlocker_ui.py
```

The opt-in live checks use the configured Google/NordLocker galleries and mock
email delivery. They transfer thumbnails and individual previews but never
download an archive. The Google browser test intercepts original-download clicks.

## Railway client galleries

This repository uses a Dockerfile that installs Chromium and its system dependencies.
Connect the GitHub
repository to a Railway service (or run `railway up`), add the variables below,
and generate a public domain under **Settings -> Networking**. Railway starts
the app with Gunicorn and checks `/health` before routing traffic to it.

Required production variables:

- `GOOGLE_API_KEY`
- `CLIENT_GALLERIES_JSON`
- `SMTP_HOST`
- `SMTP_PORT` (usually `587`, or `465` for SMTP over SSL)
- `SMTP_USER`
- `SMTP_PASSWORD`
- `PORTFOLIO_OWNER_EMAIL`
- `SECRET_KEY` (persistent random signing secret for NordLocker photo URLs)

Do not set `PORT`; Railway provides it automatically. The local `remove.py`
utility is ignored by Git and is not included in GitHub-based deployments.

Because `data/emails.csv` contains private client information and is excluded from Git, store the production mapping in Railway as a service variable named `CLIENT_GALLERIES_JSON`. Its value must be a JSON object:

```json
{"client@example.com":{"https://drive.google.com/drive/folders/PROOFS_ID":{"stage":"choose_edits","process":"google"},"https://drive.google.com/drive/folders/FINALS_ID":{"stage":"final_edits","process":"google"}}}
```

Add more clients as additional properties in the same object. When this variable exists, it takes precedence over the local CSV file.
Older JSON entries whose values are stage strings still work; their process is
inferred from the link. Incorrect process values or process/link mismatches return
a specific gallery-configuration message rather than silently selecting a provider.
