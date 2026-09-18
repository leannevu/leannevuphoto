# Leanne Vu Photo

Client galleries for Google Drive and NordLocker, with PostgreSQL-backed saved
carts and sent edit lists.

## Project layout

```text
app.py                    Flask routes and application entry point
services/
  database.py             PostgreSQL gallery and selection storage
  selection_store.py      Optional CSV compatibility storage
  nordlocker/
    bridge.py             Isolated browser, decryption and preview cache
    bootstrap.js          NordLocker browser integration
static/                   Browser JavaScript and styles
templates/                Page templates
docs/NORDLOCKER.md         NordLocker implementation notes
Dockerfile                Production image with explicit runtime copies
requirements.txt          Runtime dependencies
Procfile / railway.json   Hosting configuration
```

Private development tools, tests, old migration scripts, credentials and visual
checks live under `.local/` and are ignored by Git. `.test-tools/` contains local
browser-test dependencies and is also ignored. Neither directory enters the
Docker build context. Generated screenshots belong in `.local/artifacts/`.

## Run locally

Create a `.env` file with `DATABASE_URL`, `GOOGLE_API_KEY`, `SECRET_KEY`,
`RESEND_API_KEY` and `EMAIL_FROM`. Keep credentials out of Git.
Selection notifications go to `leannevuphoto@gmail.com`. Photo names appear as
`[name, name, name]` without individual photo links; the gallery folder link is retained.
Client update emails include added and removed counts and lists, followed by the
complete current sent list and its count. Previously sent photos are not counted
as new additions.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app app run --debug
```

Open http://127.0.0.1:5000. Google Drive folders need **Anyone with the link -
Viewer** sharing and a Google API key with Drive API enabled. Restrict the key to
Drive API; browser-referrer restrictions do not work for requests from Flask.
## Email delivery (Resend HTTPS API)

Email uses Resend over HTTPS, including on Railway Hobby. The old `SMTP_*`
variables are no longer used.

1. Create a Resend account and verify a sending domain at https://resend.com/domains
   by adding the DNS records Resend provides.
2. Create a sending API key at https://resend.com/api-keys.
3. In Railway's service Variables, set `RESEND_API_KEY` to that key and
   `EMAIL_FROM` to a sender on the verified domain, for example
   `Leanne Vu Photo <notifications@leannevuphoto.com>`.
4. Deploy this code and apply the variables. For local development, set the same
   variables in `.env`. Never commit the API key.

Your detailed notifications still go to `leannevuphoto@gmail.com`, with the client's
address as Reply-To. Clients receive a separate branded HTML and plain-text
confirmation with additions, removals, and the complete current selection; replies
go to `leannevuphoto@gmail.com`. Both use `EMAIL_FROM` on your verified domain.
Submitting additions or removing sent photos sends both messages through Resend's
batch API. Saving drafts does not send email.
Resend must return an email ID for each message before the selection change is
saved. Acceptance does not guarantee inbox delivery; check
Resend's email dashboard for delivery or bounce status. Requests time out after
20 seconds. A timeout or process crash after acceptance can still result in a
notification arriving without the corresponding selection change being saved.

API reference: https://resend.com/docs/api-reference/emails/send-email

## PostgreSQL

Gallery access uses one row per shoot in `public.galleries`. `DATABASE_URL` is
required in production. The related tables use `gallery_id` foreign keys:

- `galleries.saved_selection`: JSONB draft selections; `bookmark` stays separate.
- `client_selection.sent_selection`: JSONB submitted selections.
- `my_selection.selection`: JSONB photographer picks, shown read-only in proofs.
- `client_selection.stage` and `galleries.client_stage`: synchronized in both
  directions by database triggers. Photographer selection stages are independent.
- Each selection table has its own `production_link`. A published collection is
  available only when its own stage is `final_edits` and its link is nonblank.

Apply `docs/update_gallery_schema.sql` once, in a transaction, to update the
existing tables in place. The SQL retains selection contents and production links,
adds gallery-ID relationships, converts selection text to JSONB, and installs the
stage triggers. `client_gallery` remains a display label rather than a join key.
New selection records should use `gallery_id`. Gallery IDs keep shoots separate
even when their names, dates, or provider links match.

Sending selections advances the client stage to `wait_for_edits`; removing the
last sent selection returns it to `choose_edits`. `final_edits` remains an explicit
publication status. Unpublished production links are excluded from gallery access.
Published client selections and photographer picks share the download layout,
with **My selections** and **Photographer picks** tabs when available.

### Gallery activity

Open `/photographer/activity` using **View gallery activity** in the photographer
workspace. It shows arrival timestamps, current viewing status, and a searchable
list of the latest 200 visits in the last seven days. Summary counts cover all
visits in that period. The page refreshes every five seconds; gallery tabs check
in every 15 seconds. Hidden or unfocused tabs are away, and visits without a
check-in for 45 seconds stop showing as viewing. Closing or leaving the gallery
sends a best-effort final check-in. No viewing duration or click history is stored.
Visits from the photographer workspace are excluded. Refreshing a client page
and reopening its gallery creates a new arrival; choosing more edits in the same
gallery keeps the existing visit.

Visits identify the email entered for the gallery; this is not verified identity.
The activity page and its API require the photographer session. No new Railway
variables or services are needed. Apply `docs/gallery_activity.sql` once before
deploying. Its separate `gallery_visits` table never updates gallery selections.
Tracking begins with the updated website; past visits cannot be reconstructed.

### Photographer workspace

Use the same home-page email field for both clients and the photographer.
Entering `leannevuphoto@gmail.com` opens `/photographer`; other addresses open
their assigned client galleries. No verification email or separate sign-in is
required. This is email-based routing, not identity verification: anyone who
enters the photographer address can access the workspace.
The gallery list, activity page/API, and photographer
mode on the gallery API require the resulting eight-hour session. Direct visits
without that session return to the home-page email field. Resend is used only for
selection emails. Keep `SECRET_KEY` persistent across server restarts.

Leanne can select photographs in every gallery, save and send her picks to
`my_selection`, view a read-only **Client picks** tab, and copy client filenames.
The authenticated photographer endpoint always writes photographer selections;
client endpoints write client selections. Photographer emails go only to Leanne. Client selection emails and
bookmarks continue as before.

The `data/` folder is not needed with PostgreSQL. Historical CSV import tools
are kept locally under `.local/archive/`.

## NordLocker

The complete public share URL, including its fragment, stays in `folder_url`.
`services/nordlocker/` lists metadata, decrypts previews on demand and uses a
bounded memory cache. Original downloads are available only at the final stage.
NordLocker runs in a dedicated browser thread; preserve the single-worker,
multiple-thread Gunicorn configuration.

Windows development uses Microsoft Edge. Docker installs Chromium and its
system dependencies. `NORDLOCKER_BROWSER_CHANNEL` can override the browser
channel. Set a persistent `SECRET_KEY` so signed photo URLs survive restarts.
See [NordLocker notes](docs/NORDLOCKER.md) for provider-specific details.

## Deployment

The Dockerfile copies only `app.py`, `services/`, `static/`, `templates/` and
runtime dependencies. `.dockerignore` also restricts the build context to those
inputs. Local tools, secrets, screenshots, documentation and tests are excluded.

Set the same environment variables on the web service, using a Railway database
variable reference for `DATABASE_URL`, then deploy. Railway provides `PORT`.
The existing database is already populated; deployment does not run migrations
or modify client records. The web service does not need a data volume.

The app starts as `gunicorn app:app` with one worker, eight threads, and a
200-second request timeout. `/health` is the hosting health endpoint.

## Local checks

### Retrieve selected originals

Run `python retrieve_selected_edits.py` with the project dependencies installed
and `DATABASE_URL` configured in `.env` (use a database address reachable from
your computer). Enter your local photo folder, choose a numbered gallery from
the photographer workspace list, then choose client sent edits, saved drafts,
all client picks, or photographer picks. Filenames come directly
from PostgreSQL; the script does not change database records.

Matching originals are copied from your folder and its subfolders to
`Documents/leannevuphoto/YYYY-MM-DD selected edits`. Missing or ambiguous
filenames are reported and skipped. Existing output folders and original photos
are preserved. The database stores filenames, not image files.

On this workstation, the ignored `.local/` directory provides:

```powershell
python .local/run.py tests
python .local/run.py check_database
python .local/run.py check_gallery_choices_ui
python .local/run.py check_gallery_activity
```

The database check rolls back all test data and mocks email delivery. The browser
check uses a temporary CSV and sample photos. NordLocker performance diagnostics
are available through `python .local/run.py check_nordlocker_performance --help`.
These local tools are intentionally not part of a fresh clone or deployment.
