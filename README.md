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
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and
`PORTFOLIO_OWNER_EMAIL`. Keep credentials out of Git.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app app run --debug
```

Open http://127.0.0.1:5000. Google Drive folders need **Anyone with the link -
Viewer** sharing and a Google API key with Drive API enabled. Restrict the key to
Drive API; browser-referrer restrictions do not work for requests from Flask.
Use a Gmail app password if sending through Gmail SMTP.

## PostgreSQL

The existing `public.emails` table is the source of gallery access and cart
state. `DATABASE_URL` takes precedence over any legacy CSV or JSON mapping.
Each row represents one email/folder pair and contains:

- `email`, `gallery`, `date`, `folder_url`, `stage`, `process`
- `saved` and `sent` JSONB arrays, each defaulting to `[]`
- `bookmark` JSONB containing a photo reference or null (never TEXT)
- `photographer_picks` is `yes` or `no` (default `no`)
- `photographer_selected` is a separate JSONB photo list, or SQL NULL when empty;
  it must be NULL when photographer picks are disabled
- `id`, `created_at`, and `updated_at`

An email can have multiple galleries. Use `google` or `nord` for `process` and
`choose_edits`, `wait_for_edits`, or `final_edits` for `stage`.
The database stores photo references, not the image files:

```json
[{"id":"provider-photo-id","name":"Portrait.jpg"}]
```

For legacy databases with a TEXT bookmark column, run
`docs/repair_bookmarks.sql` inside a transaction. It unwraps repeatedly encoded
bookmarks and converts the column to JSONB while preserving saved/sent lists.
Preview the conversion first; invalid bookmark references abort the repair.

```sql
SELECT id, gallery, date, saved, sent
FROM public.emails
WHERE email = 'client@example.com'
ORDER BY id;
```

Selecting a proof saves it immediately and keeps the gallery in `choose_edits`.
Any photos in `sent` set the gallery to `wait_for_edits`. Unsending the last
photo returns it to `choose_edits`, even when saved drafts remain. PostgreSQL
automatically synchronizes the stage on changes to these lists. `final_edits`
remains an explicit completed-gallery stage. **Choose more edits** reopens the
proofs. **Unsend** removes an item and emails the updated complete edit list;
it cannot recall earlier email. A completed gallery cannot change edit requests.
Database row locks serialize updates. Failed email delivery leaves the stored
list unchanged. A process crash between SMTP delivery and database commit can
still require checking the latest list.

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
The activity page follows the photographer workspace's direct-access setting:
anyone with access to that page/API can see the visit list. No new Railway
variables or services are needed. Apply `docs/gallery_activity.sql` once before
deploying. Its separate `gallery_visits` table never updates gallery selections.
Tracking begins with the updated website; past visits cannot be reconstructed.

### Photographer workspace

Open `/photographer` or use **Photographer workspace**. It opens directly without
sign-in or email verification. Anyone who can access the workspace or its API
can view the client gallery list and change enabled photographer picks.
SMTP is used only when sending selections, not for workspace access.

Leanne can select photos in enabled galleries, see a read-only **Client picks**
tab (saved and sent), and **Email my photographer picks** to herself. Photographer
picks save immediately and clients see them in a read-only **Photographer picks**
tab, including while awaiting edits. Emailing these picks never changes the
client's saved/sent lists, stage, or bookmark. Client email notifications continue
as before. The cart shows photo previews and distinguishes drafts from sent picks.
**Save position** stores a single reading position separately from photo selections.

For existing databases, back up `public.emails`, then apply
`docs/photographer_picks.sql` in a transaction before deploying this code. It adds
only the two photographer columns and enables Socheata's two galleries; all
other galleries default to `no`. No existing client columns are rewritten.
The populated database was migrated with a private local backup and an exact
comparison of every original row value before commit.

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
