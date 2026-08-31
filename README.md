# Leanne Vu Photo — client selections

Local Flask prototype for opening a client's Google Drive gallery by email address, selecting files, and emailing an edit list.

## Setup

1. In the same Google Cloud project, enable **Google Drive API**.
2. Create an **API key** under APIs & Services → Credentials. Because Flask calls Google from the server, set **Application restrictions** to `None` for local development (an HTTP-referrer/“Web sites” restriction will fail). Under **API restrictions**, restrict it to **Google Drive API**. The existing OAuth client-secret file is not used for public folders; keep it private.
3. Copy `.env.example` to `.env` and add the API key and email settings.
4. Add each client gallery to `data/emails.csv`, one per line, using the columns `email,folder_url,stage`. Valid stages are `choose_edits`, `wait_for_edits`, and `final_edits`. Email matching is case-insensitive. A folder URL can have only one current stage.

```csv
email,folder_url,stage
client@example.com,https://drive.google.com/drive/folders/PROOFS_ID,choose_edits
client@example.com,https://drive.google.com/drive/folders/FINALS_ID,final_edits
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

## Railway client galleries

This repository is ready for Railway's Railpack builder. Connect the GitHub
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

Do not set `PORT`; Railway provides it automatically. The local `remove.py`
utility is ignored by Git and is not included in GitHub-based deployments.

Because `data/emails.csv` contains private client information and is excluded from Git, store the production mapping in Railway as a service variable named `CLIENT_GALLERIES_JSON`. Its value must be a JSON object:

```json
{"client@example.com":{"https://drive.google.com/drive/folders/PROOFS_ID":"choose_edits","https://drive.google.com/drive/folders/FINALS_ID":"final_edits"}}
```

Add more clients as additional properties in the same object. When this variable exists, it takes precedence over the local CSV file.
