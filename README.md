# Leanne Vu Photo — client selections

Local Flask prototype for opening a client's Google Drive gallery by email address, selecting files, and emailing an edit list.

## Setup

1. In the same Google Cloud project, enable **Google Drive API**.
2. Create an **API key** under APIs & Services → Credentials. Because Flask calls Google from the server, set **Application restrictions** to `None` for local development (an HTTP-referrer/“Web sites” restriction will fail). Under **API restrictions**, restrict it to **Google Drive API**. The existing OAuth client-secret file is not used for public folders; keep it private.
3. Copy `.env.example` to `.env` and add the API key and email settings.
4. Add each client to `data/emails.csv`, one per line, as `email,Google Drive folder URL`. A header row is optional. Email matching is case-insensitive.
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
