# Station Operations

Station registry, maintenance planning, inspections, equipment inventory, reporting,
and team discussions for an observation network.

For the full user, administrator, API, database, and file-by-file guide, see
[`APP_DOCUMENTATION.md`](APP_DOCUMENTATION.md).

## Local Setup

Requirements: Python 3.10 or later, MySQL or MariaDB, and a browser.

```powershell
python -m pip install -r backend/requirements.txt
```

Configure `.env` using the keys in `.env.example`. For a new installation, run
`database/database.sql` with a database administrator account, then grant the
application account access to `Weather_Stations_App`.

Start the Windows launcher or run:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/app/login.html`. The first account receives the Admin role.
See `DEPLOYMENT.md` for network and hosted deployments.

## Activity And Notifications

Committed operational changes record the person, action, record reference, affected
row count, and time. Activity records and notifications share the transaction with
the change. Rollbacks produce no activity or notifications. Startup migrations are
excluded from the activity log. Existing record edit history remains available.

Notifications go to active record owners, associated station staff, discussion
participants, relevant workflow roles, and administrators. The person performing
the action is excluded from their own alerts. Existing workflow alerts are retained.
Reading notifications is recorded without generating further alerts. Successful
sign-in, sign-out, and password changes are recorded without storing credentials.
Ordinary page views and unsuccessful requests are not operational change events.

The Notifications panel provides unread filtering, paging, manual refresh, and
read controls. Administrators can open Activity log from this panel. Activity begins
when this version is installed; prior actions are not reconstructed.

## Existing Database Migration

The current local account may not have permission to create a database. A database
administrator must first grant access to the new schema. For the current local
account, execute this in an administrator SQL session:

```sql
GRANT ALL PRIVILEGES ON Weather_Stations_App.* TO 'Chris32'@'localhost';
```

Stop the application before migration so no new writes occur before switching.
Then run:

```powershell
python -m backend.migrate_database --target Weather_Stations_App
```

The migration verifies table counts and retains the original database. It refuses
to overwrite an existing target. After success, set `DB_NAME=Weather_Stations_App`
in `.env` and restart the application. Existing databases with views, triggers,
routines, or scheduled events require a full database backup and restore instead.

## Verification

```powershell
python -m unittest discover -s tests -q
node --check frontend/js/api.js
```

## Publish Changes

Review the changes and keep `.env` out of version control:

```powershell
git status --short
git add backend database frontend tests launcher.py .env.example README.md DEPLOYMENT.md .gitignore
git diff --cached --stat
git commit -m "Standardize Station Operations naming and track operational activity"
git push origin main
```
