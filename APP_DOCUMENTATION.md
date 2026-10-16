# Station Operations: Complete Application Guide

This document describes the application as implemented in this repository on 12 October 2026. It is a guide to the code, not a substitute for a production security review. `README.md` is the short setup guide; `DEPLOYMENT.md` covers hosting. Keep this document updated when the application changes.

## 1. What the application does

Station Operations is a browser-based system for managing an observation-station network. It covers station and site records, equipment, maintenance, inspections, quality-control issues, monthly reporting and data counts, data requests, visitors, volunteers, discussions, notifications, and administrative activity. Users sign in with accounts created inside the application. The backend reads and writes MySQL/MariaDB; the frontend is static HTML, CSS, and JavaScript served by the same FastAPI process at `/app/`.

The application is not an offline spreadsheet. A browser page can load while an API call fails; the API process and database must both be reachable for sign-in and data operations.

## 2. Architecture and request flow

### Site Distance Search

On Sites, enter latitude (-90 to 90) and longitude (-180 to 180), then choose Find nearest sites. Matching sites are ordered by straight-line great-circle distance in kilometres, before pagination. Sites with missing/invalid coordinates appear last with Unknown distance. Existing station/site text search, district/status filters, and assigned-station permissions remain in effect. The distance header reverses nearest/farthest order; Clear location restores ordinary sorting. CSV/PDF exports include distances when a search location is active. GET `/sites` and `/sites/export` accept `latitude` and `longitude` together.

### Shared Workspace

Home and sign-in retain their original layout. Statistics panels and charts stay visible. On the other pages, entry forms, reports, and result registers start as collapsed icon tiles styled consistently with Home. Expand a tile to see its section, then expand its field panel when needed. Edit/Review actions reveal the relevant section and editor automatically, and browser validation reveals any field that needs correction. Role-hidden forms and sections remain hidden. Shared implementation: `initializeCollapsibleFields()` and `revealPagePanel()` in `frontend/js/api.js`.

### Ideas

Each idea also has an idea category. Reuse an existing value from the category suggestions or enter a new category. The register supports searchable multi-category filtering; category is included in sorting, exports, and edit history. Existing ideas migrate to General. GET `/ideas/categories` lists categories already used in records.

The Ideas page records an idea title, date, description, priority, expected benefit, and next steps. Statuses are Proposed, Under review, Accepted, Implemented, and Not pursued. Everyone signed in can view and create ideas; the creator or Admin can edit/delete. The page includes multi-select filters, date ranges, search, sorting, CSV/PDF export with optional tabular edit history, and Admin recovery through Deleted items. Edits require a reason. Ideas are stored in the `ideas` table; GET/POST `/ideas`, PUT/DELETE `/ideas/{id}`, and GET `/ideas/export` provide the API. `frontend/ideas.html` uses `frontend/js/workspace-pages.js`; `backend/workspace.py` defines its validation and schema.

### Station Performance

### Station Performance by Data Upload

The separate `station-performance-upload.html` page automates the supplied `Missing_DH` macro. Upload UTF-8 CSV, XLSX, or XLSM (20 MB maximum, 500,000 non-empty observation rows, at most 12 months and 240 station-month assessments). Columns must begin with `station_id, element_id, element_name, date_time, value`; station_id means registered station code, not an internal database ID. Excel's active worksheet is read. DD/MM/YYYY HH:MM, seconds, real Excel dates, and common ISO forms are supported. Legacy XLS must first be saved as XLSX. Macros and formulas are not executed.

Legacy rules reproduced the original 11 station profiles, 10 daily and 30 hourly parameters. Current defaults instead use catalog-derived identities and minute-specific intervals/fixed times; see Observation Schedules below. Any finite numeric value, including zero, is available. Duplicates retain a numeric value over a blank, with the last numeric parent-cloud value determining exemptions. Full calendar months are assessed, including dates with no source rows. Existing daylight and station-specific hour exceptions and cloud-parent rules are retained. Category eligibility and current Operational instrument assignments determine which elements are expected; station selections remain editable. Original exclusions that do not apply to the catalog are report annotations rather than missing observations.

Admin can enable stations, add existing registered stations to profiles, rename report labels, add/edit/disable parameters, choose eligible stations, edit expected hours, daily exclusions, hourly excluded annotations, count-in-summary flags, and parent-zero conditions. Saving requires an edit reason and checks the configuration version against concurrent changes. Every upload snapshots its rules. Availability equals available/expected observations, including combined daily+hourly weighting; it is not a range/plausibility QC score. Latest uploads replace only the automated station-month view. Manual Station Performance records are not overwritten. Old reports retain their original calculations and rule version. Re-upload to calculate under updated settings.

PDF summaries are generated and stored automatically. Downloads provide PDF, summary CSV, detailed Word, or a ZIP of summary/daily/hourly CSV files. Exclusions are separate from missing observations, and Word marks excluded entries red. Invalid rows, duplicate keys, ignored codes, source filename, and rule version are retained. Assigned-station access is enforced on uploads, results, configuration views, and report contents; reports spanning multiple stations are filtered before download for restricted accounts. Source files are stored server-side and not exposed through a download endpoint. Generated records are immutable and versioned by upload rather than manually editable.

Implementation: `backend/data_upload_engine.py` (defaults, validation, parsing, calculations, reports), `backend/data_upload_api.py` (authorization, settings, immutable runs/latest results, downloads), `frontend/js/station-performance-upload.js` and its CSS (upload/configuration UI and charts). Tables: `data_upload_config`, `data_upload_runs`, `data_upload_results`. Tests: `tests/test_data_upload.py`, `tests/test_performance_upload_ui.cjs`.

Word detail downloads are limited to 20,000 exception rows to protect server memory. Larger runs remain fully downloadable through Details ZIP and the PDF/CSV summaries. PDF generation and Word XML structure are tested; native Word-page rendering could not be verified on the development machine because LibreOffice is not installed.

### Manual Station Performance

Comparison tables start with the highest fiscal-year station average first, using station name as the tie-breaker. Click Station, any month heading, or FY average to sort either direction. Missing scores remain last when sorting. The monthly register also starts with the highest individual score. New monthly records automatically fill the appropriate July-June column, and new fiscal years automatically produce new comparison tables and graph periods; no fixed month/year list needs updating.

The July-to-June comparison matrix shows all twelve fiscal months side by side, each station's monthly score, and its fiscal-year average. The footer shows the overall average percentage for each month and the fiscal year. Monthly, quarterly, and yearly graph headings also display the overall average for the period. These percentages average recorded station-month scores, not sums of percentages; missing records are shown as a dash and excluded. The matrix scrolls horizontally on narrow screens with station names fixed in view. Monthly graph groups have no internal height limit, so periods remain visible on the page together.

Bulk month correction: select editable rows individually in their Actions cells, or use Select filtered records in Bulk month update to select all editable matches across pages. Enter the destination month and a required reason, then confirm. Scores and reports are retained, every changed row gets edit history, and conflicts reject the entire correction. Creators/Admin ownership and assigned-station access are checked for every selected record. Monthly performance reports lists each shared report once with download links, months, and covered station names; report filenames in the main register also download the report.

Monthly charts display each station's recorded score separately under its month. Quarter and fiscal-year charts display a separate average for every station with records in each period, grouped under the period heading. They do not combine stations into one bar. All active filters apply, and all matching records are included regardless of table pagination; missing station-month records are excluded rather than counted as zero.

Batch entry supports selecting up to 100 stations, with a separate score for each and one shared month, notes, and uploaded report. The report bytes are stored once in `station_performance_reports` and linked to each station assessment. Duplicate station-month records are rejected atomically: the entire batch is rolled back. Editing a station assessment changes only that row; replacing its report does not change reports on other stations. Deleted-item backups copy the shared file bytes into the archived assessment so restoration remains independent of other assessments. Endpoint: `POST /station-performance/batch`.

`station-performance.html` records one assessment per station and month, with a score from 0 to 100 and a required initial report up to 20 MB. Editors can retain or replace the report. Creators and Admin can edit/delete; assigned-station restrictions apply to records, reports, exports, and history. Edits require reasons and are retained in tabular history. Deleted assessments and report bytes can be restored through Deleted items. Filter by station, district, category, search, or month range; download CSV/PDF and optional history. Charts use all filtered records, not just the displayed page, showing station averages and monthly, fiscal-quarter, and fiscal-year averages. Averages weight each station-month assessment equally; scores are never summed. Missing assessments are not treated as zero. Q1 is July-September, Q2 October-December, Q3 January-March, and Q4 April-June. Schema/validation/aggregation: `backend/performance.py`; endpoints: `backend/main.py`; controller: `frontend/js/station-performance.js`.

### Documents and Agenda Usage

Documents and Agenda are available to every signed-in user. Everyone can view and create entries; only the creator or Admin can edit or delete an entry. Admin can restore deleted entries, including document bytes and edit history, through Deleted items.

- **Documents:** enter title, document date, a defined document category, description, and a file. Admin defines categories in the separate Document categories panel; definitions remain available without any uploads. Existing documents retain their categories, with General available by default. Supported files include PDF, Excel/CSV, Word, presentations, ZIP/7z/RAR, common images, and text. Files are limited to 20 MB and downloaded as attachments. Editing metadata does not require another upload; replacing a file increases its version. History records metadata changes, not copies of previous file contents.
- **File uploads:** every file field supports drag-and-drop as well as the standard file chooser. Drop areas preserve the field's allowed types and single/multiple selection rules. Size limits and permissions remain enforced by the existing upload endpoints.
- **Agenda:** enter an item, due date, description, priority, status, percentage complete, and tracking notes. Status is Open, In progress, Completed, or Cancelled. Completed items are always 100%; Open items are 0%. Active items past their due date are overdue; those due within seven days are marked due soon. These indicators are calculated when the register loads, not scheduled reminder messages.
- **Registers:** search, combine filter selections, select date ranges, show only your entries, sort column headings, and download CSV/PDF registers with optional tabular edit history. Original document files are downloaded separately. Edits require a reason; initial creation does not.

API routes are GET/POST `/documents` and `/agenda`, PUT/DELETE `/documents/{id}` and `/agenda/{id}`, GET `/documents/{id}/download`, and GET `/documents/export` or `/agenda/export` with `format=csv` or `format=pdf`. Document uploads use a raw request body with metadata query parameters; Agenda uses JSON. All routes require an authenticated session. The new tables are `shared_documents` and `agenda_items`; existing installations create them through the normal additive schema checks.

```text
Browser: /app/*.html
  -> frontend/js/config.js selects API origin
  -> frontend/js/api.js adds the session bearer token and shared UI behavior
  -> page-specific frontend/js/*.js calls backend/main.py routes
  -> backend/database.py opens a MariaDB connection
  -> backend/activity.py records committed actions and notifications
  -> database tables (schema in database/database.sql)
```

- **API and static hosting:** `backend/main.py` creates the FastAPI app, mounts `frontend/` at `/app`, validates request bodies with Pydantic models, checks the signed-in user's role, and implements API routes. It also creates/updates required tables when database-backed routes run.
- **Persistence:** `backend/database.py` loads the root `.env` and returns a MySQL connection wrapped by `ActivityConnection`. The database, not browser storage, holds operational records and uploaded file bytes.
- **Authentication:** `backend/security.py` hashes passwords with PBKDF2-SHA256, creates random session tokens, and hashes tokens before storage. The browser keeps its bearer token in `sessionStorage`; `frontend/js/api.js` attaches it to requests. Closing a browser session can clear that browser-side token, but server-side expiry and logout are controlled by the API.
- **Activity and edit history:** `backend/activity.py` records committed operational writes and generates notifications. `record_edit_history` stores before/after snapshots for supported edited records; monthly reporting also has its own change table. These are related but different records.
- **Uploads:** Report files and inspection photographs are stored in database BLOB columns, not a separate uploads folder. CSV/PDF exports are generated by API responses and downloaded by the browser.

## 3. Installation and local use

Prerequisites: Python 3.10 or later, a running MySQL/MariaDB server, an application database account, and a modern browser. The Python dependency list is `backend/requirements.txt` (the duplicate `database/requirements.txt` has the same packages).

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
```

Create a root-level `.env` using `.env.example` as a template. Set `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, and `CORS_ORIGINS` for the actual environment. Never place real passwords in `.env.example`, documentation, screenshots, or Git. For a new empty installation, a database administrator can run `database/database.sql`; the application also has additive table/column checks in `ensure_application_tables()` for an existing schema. Back up an existing database before upgrades.

Run the app from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/app/login.html`. `/` redirects there. `/health` verifies that the app can connect to the database. `/auth/status` tells the login page whether first-account setup is needed. The first created account is an Admin. For a Windows desktop launcher, double-click `Start Weather Station App.bat`; `launcher.py` can test the database, write `.env`, start/stop Uvicorn, and show a LAN URL.

`127.0.0.1` works **only on the machine running the app**. To serve colleagues on the same permitted network, bind to `0.0.0.0`, allow the selected port through the host firewall, and share the host computer's reachable IP. For internet/public access, use an approved HTTPS reverse proxy, access controls, backups, and an appropriate firewall policy; an IP-based HTTP link alone is not safe for passwords or volunteer account details. See `DEPLOYMENT.md` for deployment examples. The launcher writes `CORS_ORIGINS=*`, so review that setting before any production use.

The app's own database-copy helper is `python -m backend.migrate_database --target NEW_SCHEMA`. It refuses to replace an existing target and retains the source; read `README.md` before using it. A full backup/restore is required for a database containing views, triggers, routines, or scheduled events. Password recovery for an existing user is `python -m backend.reset_password USERNAME`; the temporary password is prompted for rather than supplied on the command line.

## 4. Accounts, access, and sensitive data

Roles are stored on `users.department`. The UI hides pages/actions for convenience, while API dependencies and route checks are the security boundary. A hidden button is **not** an authorization rule. Some endpoints additionally limit an Observation Officer or Observation Supervisor to stations in `user_station_assignments`, and some edit/delete endpoints limit changes to the original recorder unless the user is Admin.

| Role group | Intended access in this implementation |
| --- | --- |
| Admin | User accounts and assignments; all operational panels; activity log, KPI, and operational search; maintenance targets and visitor categories. |
| Observation Officer / Observation Supervisor | Assigned-station work and relevant inspection/QC functions. They do not have access to Volunteer Data or Volunteers at Stations. |
| Observation Supervisor at HQ | Network review and HQ inspection decisions; volunteer-related pages; reporting views. Inspection routing and closure are controlled by the HQ workflow rules. |
| Data Quality Control Officer / Observation Processing Officer | Data-count and request entry, reporting management, volunteer-related entry, and quality workflows according to route checks. |
| Instrument Maintenance and Calibration Officer | Maintenance and instrument-related work, including maintenance responses to routed inspections. |
| Big Data Specialist / Data Quality Control Specialist / Division Manager | Broad read access and assigned management-review actions, but many data-entry forms are read-only. |

The precise rule for any operation is the dependency and branch in `backend/main.py`, especially `require_it`, `require_volunteer_data_access`, `require_data_operations_writer`, the inspection-stage helpers, and record-owner checks. `frontend/js/api.js` contains the corresponding navigation and control visibility rules. Do not assume every visible page grants write access.

`station_volunteers` contains volunteer IDs, account names/numbers, and phone numbers. Limit database backups, exports, log access, and user permissions accordingly. Activity metadata is designed not to copy passwords or uploaded file bytes, but authorized edit-history responses may contain the before/after values of a volunteer record. CSV/PDF exports contain the filtered personal details shown by the endpoint.

## 5. Main workflows and calculations

- **Stations and sites:** A station has a code, name, latitude, longitude, altitude, province, district, sector, category, and one status: Operational, Under maintenance, Suspended, or Closed. Station imports accept CSV. The Sites view is derived dynamically by grouping stations with the same **latitude, longitude, and altitude**; there is no independent sites table. Assigned-station roles see only their assigned stations/sites.
- **Equipment:** The instrument catalog defines equipment and the station categories where it is available. Station instruments are actual assignments to stations, with installed/calibration/replacement dates and condition. The instrument summary computes effective status and alerts from recommended dates; alerts begin in the six-month window before a due date.
- **Maintenance:** A field record identifies a station, maintenance date, technicians, and instruments serviced. Each selected instrument can have its own issue, action, and recommendation, allowing instrument-level history. Maintenance and pre-maintenance reports can be uploaded for several selected stations.
- **Maintenance planning:** Admin configures targets by station category and fiscal year, either quarterly or yearly with a target number of visits. The fiscal year runs July-June: Q1 Jul-Sep, Q2 Oct-Dec, Q3 Jan-Mar, Q4 Apr-Jun. Maintenance Summary reads actual `maintenance_records` dates and evaluates Maintained, Pending, Not maintained, or Not configured. It displays quarterly progress, percentages, filters, and CSV/PDF exports. A report attachment is not itself a completed maintenance visit.
- **Inspections:** An inspector records a station finding, uploads the required station photo, and may attach reports covering multiple stations. HQ review can close the inspection or route it to Maintenance or Management. The receiving team can comment and change status. A Maintenance "Not Solved" response routes to Management. Actions generate notifications and preserve an edit/action trail; each panel's edit rights are checked by role and record ownership.
- **Quality control:** Suspected Data records issues and their resolution/final-review steps. Monthly volunteer data files are a separate workflow from the Volunteers at Stations personal register.
- **Reporting:** Monthly reporting saves the submitted station count and a non-reported-stations file. Expected stations are Operational + Under maintenance + Suspended; Closed is excluded. The displayed reporting percentage uses Operational / Expected. Saved changes appear in monthly history.
- **Data Counts:** Counts are recorded by month and one or several station categories. The combined-category entry is one total for the selected group, not a separate copy per category. Totals and graphs are shown by month, fiscal quarter, and fiscal year.
- **Data Requests:** Tracks counts by month and configurable requester categories; the page shows filtered totals and fiscal-period charts. Visitor categories are configured separately by Admin.
- **Station Visitors:** A visit has one date, station, institution/school, mission, category, and visitor count. Existing legacy multi-day rows retain their old end date and are labeled in the UI; new/edited visits use one date in both legacy database date fields.
- **Volunteers at Stations:** One register row associates a volunteer with a station and ID, account type/name/number, and mobile phone. Station code and location are read from the selected station. The register supports filters, search, sorting, pagination, edit history, and CSV/PDF export. The same volunteer ID cannot be duplicated at one station.
- **Discussions and notifications:** A discussion has selected participants, messages, and a close action. Operational changes and inspection/discussion events notify relevant active users. The notification panel supports unread filtering, paging, refresh, and read controls. Admin can inspect the Activity page.
- **Search and KPI:** Both are Admin-only. Search is a database-backed, rule-based question/answer interface with station context; it is **not** a trained language model or a general-purpose AI chat service. KPI summarizes selected user activity and pending QC work.

## 6. API map

FastAPI's interactive schema is at `/docs` when the app runs. Most routes need `Authorization: Bearer <session token>` and a completed password change. Use the UI for ordinary work rather than manually calling routes.

| Route family | Main purpose |
| --- | --- |
| `/auth/*` | First Admin setup, login, current user, password change, logout, setup status. |
| `/users*` | Admin user directory, assignments, account changes, and export. |
| `/stations*`, `/sites` | Station CRUD/import/export and calculated site groupings. |
| `/instruments*`, `/station-instruments*` | Instrument catalog, CSV import/export, assignments, and date/condition details. |
| `/maintenance*`, `/maintenance-reports*`, `/pre-maintenance-reports*` | Field records, instrument history, attached reports, download, and export. |
| `/maintenance-frequencies*`, `/maintenance-summary*` | Category fiscal targets and schedule status/export. |
| `/station-inspections*`, `/station-inspection-reports*` | Findings, stage actions, photos, shared reports, download. |
| `/suspected-data*` | QC record, resolution, final review, export. |
| `/data-counts*`, `/data-requests*` | Monthly counts, combined categories, requests, summaries. |
| `/visitor-categories*`, `/station-visitors*` | Visitor category settings and dated visitor log. |
| `/station-volunteers*`, `/volunteer-data*` | Personal volunteer register/export versus monthly QC/filtered/filled files and comments. |
| `/reporting-status*` | Monthly status, edit history, non-reported file upload/download. |
| `/discussions*`, `/discussion-users` | Participant selection, discussion messages, close. |
| `/notifications*`, `/activity`, `/record-history/*` | Alerts, Admin activity feed, per-record before/after history. |
| `/kpi`, `/data-search`, `/health` | Admin dashboard, operational Q&A, database health. |

List endpoints commonly accept search, filter, `sort_by`, `sort_order`, `page`, and `page_size`; supported parameters differ by endpoint. CSV/PDF export routes use the same relevant filters where implemented. Read the OpenAPI page for exact request/response shapes rather than guessing from this summary.

## 7. Database map

`database/database.sql` defines the initial schema and seeds a station registry. `backend/main.py` also runs `ensure_application_tables()` to create missing tables and add compatible columns on an existing installation. Schema changes should be backed up and reviewed before production upgrades. The following tables are present in the schema:

| Area | Tables and their roles |
| --- | --- |
| Identity | `users` (accounts/roles); `user_sessions` (hashed login sessions); `user_station_assignments` (assigned stations). |
| Accountability | `operation_activity` (committed operational actions); `record_edit_history` (record snapshots); `notifications` (per-user alerts). |
| Network | `stations` (station master data and status); sites are computed from station coordinates. |
| Equipment | `instruments` (catalog); `instrument_station_categories` (catalog applicability); `station_instruments` (installed equipment); `instrument_alert_deliveries` (due-alert delivery tracking). |
| Maintenance | `maintenance_records` (visits); `maintenance_record_instruments` (one instrument's work on a visit); `maintenance_reports` and `maintenance_report_stations` (shared report files/coverage); `pre_maintenance_reports` and `pre_maintenance_report_stations`; `category_maintenance_targets` (current category/fiscal targets); `station_maintenance_frequencies` (older station-level schedule data). |
| Inspection and QC | `station_inspections` (finding/stage/status); `station_inspection_comments` (actions/comments); `station_inspection_photos`; `station_inspection_reports` and `station_inspection_report_stations`; `suspected_data_records`. |
| Reporting and demand | `monthly_reporting_status`; `monthly_reporting_changes`; `monthly_non_reported_station_files`; `monthly_data_requests`; `monthly_data_counts` (older count data); `monthly_category_data_counts`; `monthly_combined_data_counts`. |
| Volunteers and visitors | `volunteer_data_files`; `volunteer_report_comments`; `station_volunteers` (personal register); `visitor_categories`; `station_visitors`. |
| Collaboration and migration | `discussions`; `discussion_participants`; `discussion_messages`; `app_migration_markers` (schema/data migration markers). |

Station/volunteer, report/station, instrument/station, and user/station relationships are enforced using foreign keys. The initial SQL is not a nightly backup. Back up the live database separately, and test a restore. Files stored as BLOBs make database backups include uploaded documents and photographs.

## 8. File-by-file reference

Paths below are relative to the repository root. The descriptions state each file's primary responsibility. The exact screen and endpoint behavior remains in the referenced source.

### Root files

| File | Purpose |
| --- | --- |
| `.env` | Local, private database/CORS configuration; ignored by Git. Never publish its contents. |
| `.env.example` | Safe example of environment variable names and development defaults; fill in your own values in `.env`. |
| `.gitignore` | Keeps `.env`, virtual environments, bytecode, and Python caches out of Git; Markdown documentation is allowed. |
| `.dockerignore` | Excludes local secrets, Git metadata, caches, and `database/` from the Docker build context. |
| `README.md` | Short overview, setup, migration, verification, and publishing notes. |
| `APP_DOCUMENTATION.md` | This complete user/developer/file-map guide. |
| `DEPLOYMENT.md` | Windows LAN launcher, hosted deployment example, first account, and production checklist. |
| `Dockerfile` | Builds a Python image with backend requirements, backend code, and frontend assets; starts Uvicorn on `$PORT` or 8000. It does not bundle the database or `.env`. |
| `launcher.py` | Tkinter Windows launcher for database connection settings, `.env` writing, local server lifecycle, and a LAN sharing URL. |
| `Start Weather Station App.bat` | Windows double-click entry point that runs `launcher.py` from the project directory. |
| `and notifications` | Text file containing a list of project paths. It is not imported by the application or required for startup; review its provenance before distributing the repository. |

`.git/` is Git's local repository database, and `__pycache__/` directories are generated Python bytecode caches. Neither is application source. A `.venv/` folder, if you create one, is a local Python environment and is not needed in a source-code archive; verify that it is not staged for Git.

### `backend/`

| File | Purpose |
| --- | --- |
| `__init__.py` | Marks `backend` as an importable Python package. |
| `main.py` | FastAPI app: static mount, Pydantic models, schema compatibility, authentication/permission dependencies, all operational routes, exports, notifications, search, and KPI. This is the main server module. |
| `database.py` | Loads root `.env`, opens MariaDB/MySQL connections, and wraps them for activity logging. |
| `workspace.py` | Documents and Agenda schema, upload validation and limits, ownership rules, progress validation, and due-date tracking. |
| `activity.py` | Intercepts committed operational writes, records activity metadata, and creates notification recipients within the transaction. |
| `security.py` | Password hashing/checking and secure session-token creation/hash functions. |
| `migrate_database.py` | Copies the configured schema into a new named schema after checks; does not remove the original. |
| `reset_password.py` | Command-line temporary password reset and existing session invalidation. |
| `requirements.txt` | Python dependency list: FastAPI, Uvicorn, MySQL connector, dotenv, ReportLab. |

### `database/`

| File | Purpose |
| --- | --- |
| `database.sql` | Initial database tables, constraints, indexes, and seed station data. Run only after selecting the intended database and taking any needed backup. |
| `station_classification.sql` | Data-maintenance script classifying known station names and linking sample instruments to station categories; review before running against live data. |
| `requirements.txt` | Duplicate Python dependency list retained alongside database tooling; `backend/requirements.txt` is the one used by the documented app setup/Dockerfile. |

### `frontend/` pages

Every HTML file is an actual application page; its page-specific behavior is mostly in the matching JavaScript file. `frontend/js/api.js` provides shared session, navigation, history, sorting, export, pagination, and notifications behavior.

| File | Purpose |
| --- | --- |
| `login.html` | Sign-in, first Admin creation, and forced password-change forms. |
| `index.html` | Signed-in welcome/workspace links and network summary. |
| `stations.html` | Station form, CSV import/export, filters, status, registry table. |
| `sites.html` | Derived site list and stations grouped at matching coordinates/altitude. |
| `instruments.html` | Instrument catalog entry, category applicability, import/export, and catalog table. |
| `station-instruments.html` | Instruments installed at a station, operating condition, and calibration/replacement dates. |
| `instrument-status-summary.html` | Filtered status and due-date alerts for installed instruments. |
| `maintenance.html` | Field maintenance records with per-instrument details and shared maintenance report uploads. |
| `pre-maintenance-reports.html` | Multi-station pre-maintenance report upload and review/download. |
| `maintenance-summary.html` | Fiscal schedule status, quarterly cells, filters, percentages, and export. |
| `station-inspections.html` | Inspection findings, photo, shared reports, role-specific action panels, and workflow. |
| `suspected-data.html` | Suspected-data/QC reporting, resolution, and final-review register. |
| `reporting-status.html` | Monthly reporting, expected/operational counts, non-reported file, and change history. |
| `data-counts.html` | Monthly observation totals by one/combined categories and fiscal charts. |
| `data-requests.html` | Monthly data-request categories, totals, date filtering, and charts. |
| `station-visitors.html` | Dated station visits, institution/school, mission, category, and visitor count. |
| `station-volunteers.html` | Personal station-volunteer register, location lookup, filters, and exports. |
| `volunteer-data.html` | Monthly volunteer QC/filtered/filled file submissions and review comments. |
| `discussions.html` | Discussion creation, participants, messages, notifications, and closing. |
| `documents.html` | Shared document uploads with title, date, description, file downloads, replacement versions, filters, exports, and history. |
| `agenda.html` | Agenda items with due dates, priority, progress, tracking notes, and overdue indicators. |
| `ideas.html` | Idea proposals, date, expected benefit, next steps, status and priority, with searchable register and edit history. |
| `activity.html` | Admin-only, paginated operational activity log. |
| `users.html` | Admin-only account creation/editing, roles, station assignments, and directory. |
| `kpi.html` | Admin-only activity and pending-work metrics. |
| `search.html` | Admin-only operational question/answer and matching record results. |

### `frontend/js/`

| File | Purpose |
| --- | --- |
| `config.js` | Chooses the API base URL for backend-served pages versus local Live Server ports. |
| `api.js` | Shared authenticated fetch, session/role helpers, navigation, notifications, errors, tables, sorting, pagination, exports, and inline record history. |
| `auth.js` | Login/bootstrap/password-change form logic. |
| `home.js` | Welcome identity/date, role-aware workspace links, summary counts. |
| `stations.js` | Station form, status, CSV import, registry list, filtering, editing. |
| `sites.js` | Derived-site retrieval, filters, station lists, pagination. |
| `instruments.js` | Catalog management, category choices, import, export, sorting. |
| `station-instruments.js` | Installed-instrument entry, station/category selection, dates and conditions. |
| `analytics-pages.js` | Shared controller for Data Counts, Instrument Summary, and Maintenance Summary pages; fiscal calculations and chart/table rendering. |
| `maintenance-frequency.js` | Admin form for category/fiscal-year maintenance targets. |
| `maintenance.js` | Field work, per-instrument detail entry/history, maintenance report selection/upload, maintenance tables. |
| `pre-maintenance-reports.js` | Pre-maintenance multi-station selection, upload/download, filters, and summary. |
| `station-inspections.js` | Inspection creation/editing, photographs/reports, staged actions/comments, workflow display. |
| `suspected-data.js` | QC issue lifecycle, role-specific actions, filters and result table. |
| `reporting-status.js` | Monthly reporting UI, non-reported file operations, summaries, and inline change history. |
| `data-requests.js` | Request counts, editable categories, filters, and fiscal charts. |
| `station-visitors.js` | One-day visit entry, institution, visitor categories, filtered register. |
| `station-volunteers.js` | Personal volunteer entry, station-derived location, filters, sorting/paging, history and downloads. |
| `volunteer-data.js` | Monthly file upload/download and HQ comments. |
| `discussions.js` | Discussion participants, messages, close action and listing. |
| `workspace-pages.js` | Shared Documents/Agenda forms, authenticated uploads/downloads, filters, sorting, pagination, exports, ownership controls, and edit history. |
| `activity.js` | Admin activity table with paging/refresh. |
| `users.js` | Admin user directory and station assignment forms. |
| `kpi.js` | KPI month selection and metric rendering. |
| `search.js` | Search conversation UI, retained station context, and result rendering. |

### `frontend/assets/` and styling

| File | Purpose |
| --- | --- |
| `css/app.css` | Shared colors, typography, responsive layout, forms, cards, tables, alerts, and page-specific styles. |
| `assets/weather-station-login.png` | Login-page photograph. |
| `assets/icons/LUCIDE-LICENSE.txt` | License/attribution for the included Lucide icons. |
| `assets/icons/radio-tower.svg` | Stations tile icon. |
| `assets/icons/map-pinned.svg` | Sites tile icon. |
| `assets/icons/wrench.svg` | Maintenance tile icon. |
| `assets/icons/clipboard-check.svg` | Inspections tile icon. |
| `assets/icons/messages-square.svg` | Discussions tile icon. |
| `assets/icons/shield-check.svg` | QC tile icon. |
| `assets/icons/package-search.svg` | Station-equipment tile icon. |
| `assets/icons/gauge.svg` | Instrument-status tile icon. |
| `assets/icons/chart-no-axes-combined.svg` | Data-count/reporting tile icon. |
| `assets/icons/inbox.svg` | Requests tile icon. |
| `assets/icons/search.svg` | Search tile icon. |
| `assets/icons/target.svg` | KPI tile icon. |
| `assets/icons/users-round.svg` | Visitor/volunteer tile icon. |
| `assets/icons/users.svg` | User directory tile icon. |

### `tests/`

The test suite uses Python `unittest`, with mocked database connections for focused server behavior. It does **not** replace a full browser/production database acceptance test.

| File | Purpose |
| --- | --- |
| `test_analytics.py` | Fiscal periods, combined counts, maintenance schedule, progress, due-alert calculations. |
| `test_instrument_csv_import.py` | Instrument CSV parsing and import compatibility. |
| `test_instrument_maintenance_history.py` | Per-instrument maintenance history behavior. |
| `test_maintenance_instruments.py` | Per-instrument maintenance payload validation and persistence behavior. |
| `test_operation_activity.py` | Transactional activity and notification behavior, including rollback and metadata limits. |
| `test_record_history.py` | Before/after history, role restrictions, and safe user snapshots. |
| `test_reporting_status.py` | Reporting snapshots, counts, history, and file-related behavior. |
| `test_sorting.py` | Server-side collection filtering/sorting behavior. |
| `test_station_csv_import.py` | Station export/import round-trip compatibility. |
| `test_station_status.py` | Station status values and legacy migration checks. |
| `test_station_visitors.py` | One-day visit and institution validation plus legacy-date handling. |
| `test_station_volunteers.py` | Volunteer access restrictions, duplicate IDs, summaries, and filtered export. |

## 9. Verification and troubleshooting

Run these from the repository root after changes:

```powershell
python -m unittest discover -s tests -q
python -m py_compile backend/main.py
node --check frontend/js/api.js
git diff --check
```

If login says **Failed to fetch**, confirm the browser is opening `/app/login.html` on the FastAPI host/port, the Uvicorn process is still running, and `frontend/js/config.js` points at the intended API. On the app host, test `http://127.0.0.1:8000/health`; it should report `{"status":"ok"}` when the database is reachable. A failure on another computer may instead be a firewall/routing issue. If the API is up but the database is down, check `.env` host/port and the MariaDB listener. HeidiSQL is a database client, not the database server itself.

For a Windows host:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
Test-NetConnection 127.0.0.1 -Port 8000
Invoke-WebRequest "http://127.0.0.1:8000/health" -UseBasicParsing
```

Do not treat a page load (`200` for HTML) as proof that authenticated API calls or MariaDB work. Do not upload a copy of `.env` or a live database dump when requesting help; redact credentials and personal data first.
# Instrument Elements and Current Availability

## Permanent Registry Numbers

The final owner-approved registry is in `backend/catalog_schedule_20261015.py`:
42 entries, numbered 1-21 and 51-71. The earlier 43-entry version remains in
`backend/catalog_registry_20261015.py` as the migration source. The one-time
update removed old Hygrograph 59 (element 10), backed up its 13 assignments and
availability records in Deleted items, and shifted old 60-72 down by one.
There are now 3,104 station assignments. All surviving internal IDs and their
history were preserved. The removed registry number is now occupied by
Pluviograph; restoring the old backup requires resolving that identity conflict.

`instruments.registry_number` is now the actual primary key. The old
`instrument_id` remains an internal AUTO_INCREMENT unique compatibility key so
existing station, maintenance, audit and backup foreign-key links stay valid.
Registry Number is editable only during creation/initial assignment; the API
rejects later changes even by Admin. `backend/instrument_registry.py` migrates
indexes and installs a database trigger that also rejects SQL-level changes.
Names are no longer unique. The remaining Hygrograph is registry 67, element 184.
Registry 19 is Anemometer and registry 20 is Wind Vane;
the old catalog names are gone, without deleting their linked history.

The Instruments form/table, CSV template, exports and audit snapshots expose
Registry Number. Imports match by registry, so name changes update the same
record. Repeated registry numbers cannot describe different instruments.
Legacy CSVs without registry numbers can update uniquely named existing rows
only; new instruments and ambiguous names require a registry number. Station
instrument dropdowns and maintenance selection labels include registry numbers.
Station-instrument CSV exports include Registry Number and imports use it to
distinguish duplicate names. Old backups created before this migration may need
a registry number supplied through a data migration before restoring an
instrument; current backups preserve registry numbers and reject conflicts.

Shared element IDs are supported across instruments, including 2112.
Different catalog names for the same ID are retained
as display aliases, not counted twice; any selected Operational assignment can
supply that ID. Registry 18 uses AWS_Visibility for 1432 as clarified. Registry
21 uses AWS_Sunshine Duration|Evapotranspiration|AWS_Dew Point for
1441|1151|2512. Registry 63 retains the supplied 1000 Cm instrument label, but
its parameter is 100 cm soil temperature and its corrected element is 180.
Element 190 is Low Cloud Type. Field names can be edited without changing registry.

## Observation Schedules

The local schedule update is Rule version 2. Admin can edit each parameter's
Observation schedule: Regular interval (integer minutes from 1 to 1440) or
Fixed daily times (HH:MM). Thus 1, 5, 10 and 60 minute intervals are supported.
Interval slots start at midnight each day. Ten minutes means 00:00, 00:10,
00:20, and so on; hourly means minute 00 of each permitted hour. Seconds are
normalized within their minute. Fixed-time observations must match the selected
minute, not merely occur somewhere that day. Multiple fixed times are counted
in the intraday/hourly group. Daylight hours 06:00-18:00 inclusive, existing
station overrides and cloud-parent rules remain. A cloud exemption uses the
parent value at the same scheduled minute. Off-schedule values earn no credit.

AWS defaults are 10 minutes for the supplied IDs. Manual hourly defaults retain
daylight restrictions where previously configured. Soil 176/177/179/180 uses
06:00,12:00,18:00; 2/3/5/85/477/18 uses 08:00; 573/575 uses 18:00;
574/576 uses 06:00. Defaults for IDs absent from the instrument catalog are
retained as archived schedules, not invented instrument mappings. Current
Operational assignments and category eligibility still control expectations.

Minute duplicates retain a numeric value over a blank, with the last numeric
value controlling cloud exemptions. New calculations use these schedules;
previous reports remain unchanged. Re-upload data to recalculate old months.
Requests exceeding 100 million scheduled checks are rejected with a message to
reduce months or enabled stations/elements. Legacy counting remains available
for old configurations but does not enforce minute-specific schedules.

`backend/catalog_schedule_20261015.py` is an explicit one-time migration, not a
startup action. It archives the removed instrument, audits the renumbering and
schedule update, and reinstalls the immutable-registry trigger. Do not rerun
earlier catalog migration scripts after this update. Normal startup and repeat
runs preserve later Admin changes. Restart the backend and hard-refresh the
browser after deployment; catalog schema version 5 prevents a new frontend
from uploading to a backend without dated downtime support.

### Registry-Wide Reporting and Filters

Rule version 4 assesses all permitted registered stations whose station status
is included in Admin's Report station coverage setting, not only stations with
rows in the uploaded dataset. The default included statuses are Operational,
Under maintenance and Suspended; Admin may also choose Closed. Existing manual
station enable/disable choices, per-station hour exceptions and element exclusions
are retained. Newly registered stations automatically receive a default profile.
Assigned-station users remain restricted to their assigned stations. Each upload
uses the dated instrument inventory snapshot as before; station inclusion does
not override an instrument's recorded downtime.

Months still come from the file's dated observations. Missing stations receive
their complete scheduled expected counts with zero available readings. Stations
with no applicable expectations are Not assessed, not failed observations.
Historical reports remain frozen; re-upload to generate registry-wide reports
from an older dataset. The one-time local scope upgrade is audited and can be
applied through `backend.data_upload_scope.save_registry_scope_upgrade`.

Result filters support multiple station categories, statuses and element IDs,
in addition to stations, districts and month range. Empty selections mean All.
Category/status/district filters use the current station registry. Selected
elements recalculate station totals, weighted summary percentages and chart scores;
stations without any selected applicable element are omitted. Historical element
cadences are recovered from each run's saved rule snapshot when necessary.
Per-run PDF/CSV/Word/ZIP downloads use the same applied filters, including narrowed
daily/hourly exceptions; changing a control without Apply does not silently change
the download. Permissions are enforced before report contents are loaded.

Large Word reports retain every selected station and element score, with grouped
downtime, when daily/hourly exceptions exceed 20,000 rows. Details ZIP retains the
full exceptions. Word exports exceeding 20,000 station-element scores still require
a narrower selection or CSV/ZIP. `backend/data_upload_scope.py` centralizes coverage
and filtering. `tests/test_data_upload_scope.py` tests missing-station coverage,
statuses, element percentages, filtered details and large Word summaries.
Catalog schema 6 and report-filter capability 1 prevent new controls from being
used against an old backend. Restart the backend and hard-refresh after upgrading.

### Large Observation CSV Uploads

Observation columns are matched by header name, not position. Required headers
station_id, element_id, element_name, date_time and value may occur in any order;
extra columns such as station_name are accepted and ignored. Duplicate required
headers are rejected as ambiguous. This applies to CSV and Excel, including the
six-column station-name export format.

Observation CSV uploads support up to 2 GiB (displayed as 2 GB). XLSX/XLSM and
ordinary document uploads retain their separate 20 MB limits. Save large Excel
datasets as CSV. The 500,000-row limit no longer applies to observation uploads.
Parsing supports up to 500 distinct station codes, 2,000 source element codes,
12 months, 6,000 station-month assessments and 100 million scheduled checks.
These processing limits remain independent of the uploaded file's byte size.

`backend/observation_storage.py` streams authenticated request chunks to private
disk storage, computes SHA-256, and builds a temporary SQLite duplicate index.
Minute/hourly lookups use a bounded day cache rather than retaining all source
rows in Python memory. CSV numeric/blank duplicate rules and status exclusions
remain unchanged. The index is removed after success or failure. Partial and
failed uploads are removed; committed source files are retained for backup.
One observation upload per application worker is accepted at a time; concurrent
requests receive 429. No whole-file request.body(), frontend arrayBuffer(), or
gigabyte-sized MySQL source BLOB is used for large uploads.

Private storage defaults to `data/observation_uploads/`, excluded from Git and
not exposed through StaticFiles. Override it with `OBSERVATION_STORAGE_DIR`.
The service account needs read/write access. Allow at least four times the CSV
file size in free disk space (about 8 GiB for a 2 GiB upload); index expansion
depends on the data. Low-space checks stop processing safely. CSV fields are
bounded by the standard CSV parser; extremely long cells are rejected.

Large sources are referenced by storage key, byte count and SHA-256 in each
run's saved configuration snapshot. Detailed results are gzip-compressed on disk;
database summaries retain a reference, counters, station/element results and PDF.
Existing inline reports remain compatible. Downloads still produce PDF, CSV,
Word and ZIP, not internal JSON. Back up BOTH MariaDB and the private observation
storage directory together. Restoring only the database can leave report files
unavailable; the download route reports this explicitly.

Restart the backend and hard-refresh the browser after upgrading. The frontend
checks the server's advertised CSV capability before sending a large file. If a
reverse proxy is used, its request-size and request-timeout limits must also
permit the upload; this change does not modify external proxy configuration.
Large uploads are synchronous; keep the page open until calculation completes.
There is no resumable upload support. `tests/test_large_observation_uploads.py`
covers streaming limits, partial cleanup, disk-backed parity, more than 500,000
rows and an optional real >1 GiB upload/parse/score integration test. Run that
test with `RUN_GB_OBSERVATION_TEST=1` when sufficient test disk space is available.

An interrupted request body raises ClientDisconnect. This is handled as an
incomplete upload (HTTP 400), with partial-file cleanup and release of the upload
slot, rather than an unhandled ASGI exception. The browser warns before leaving
while an upload is active and shows a specific network-interruption message.
A disconnected browser cannot receive the server's error response; inspect Upload
history before retrying if the interruption occurred after calculation began.

Prefer `http://127.0.0.1:8000/app/station-performance-upload.html` when the backend
runs on port 8000. VS Code Live Server can refresh the page when generated upload
files change inside its watched workspace. `.vscode/settings.json` excludes the
default observation-storage folder while retaining Live Server's default ignores.
Restart Live Server to apply this workspace setting. If observation storage is
customized to another watched folder, exclude that folder too. ClientDisconnect
alone does not identify whether refresh, network loss, cancellation, or another
connection failure caused the interruption.

### Dated Downtime and Reserved Clouds

Rule version 3 supersedes the earlier current-status-for-the-whole-month policy.
Configuration includes cloud placeholders 116,117,118,120,121,122,124,125,126,
128,129,130, with editable names, no observation hours/times, disabled and Not
scheduled until configured. They do not invent instrument mappings or contribute
expected observations. Cloud-parent controls appear only on cloud parameters.

Proposed-station checkboxes automatically reflect current Operational assignments.
Other statuses are unchecked and disabled; returning to Operational checks them
again unless Admin explicitly excluded that station. These automatic checks are
separate from historical assessment eligibility. A currently inactive instrument
can still have expected readings during earlier Operational days. Likewise an
Operational instrument's past downtime remains excluded. Any Operational
instrument supplying a shared element can satisfy that element; no double count.

On Station Instruments, Status effective date records when a status started.
When changing status without a date, the backend uses today. Changes take effect
at 00:00 and remain effective until the next dated status. One status per day is
supported. No past shutdown dates are invented; enter known dates explicitly.
Word daily/hourly detail tables and detailed CSVs show the actual downtime status
in Entered. PDF reports include grouped downtime dates and an Entered column.
Old reports retain their frozen inventory/rules; re-upload to recalculate.
`tests/test_downtime_reporting.py` covers dated exclusions, operational recovery,
automatic selections, placeholders, and report status output.

## Latest Catalog Update

The owner-approved 15 October 2026 table is represented in
`backend/catalog_update_20261015.py`. Its explicit `apply_update` function was run
once locally: 33 existing instruments changed, 3 were added (Human Observation,
Vapor Pressure Paper, Barometer), and 52 missing Operational station assignments
were added. All 2,964 pre-existing assignments retained their IDs, station IDs,
instrument IDs and statuses. There are now 41 catalog instruments and 3,016
assignments. This update supersedes the initial mappings described below.

Approved corrections: 100 cm soil uses element 180 (190 remains Low Cloud Type),
soil depth labels are 10=176, 20=177, 50=179, 100=180, the existing AWS
Thermohygrometer was renamed Thermohygrography in place and now has Principal
Station as its catalog category, with Air temperature|Relative humidity for
183|184. Existing AWS assignments remain stored; category-based proposals now
follow Principal Station. The two supplied Hygrograph rows merge to
10|184 with Humidity_Hygro|Relative humidity. Digital hygrometer's empty pipe
segment was removed, and Rainfall normalized to Rainfall Station.

CSV import now normalizes name whitespace, accepts the Rainfall alias, removes
empty parameter-name pipe segments, validates name/ID counts, and rejects an ID
with conflicting names across instruments before writing. Blank status cells
preserve existing catalog status. Preview lists category changes affecting
installed instruments; the importer requires explicit confirmation to preserve
those assignments while updating catalog categories. It never silently deletes
or moves station-instrument records. Every changed existing instrument retains
an edit reason and before/after history. Export CSV from Instruments to obtain
the current corrected catalog for future re-imports.

New element IDs 10, 1081 and 1132 need Admin schedule configuration; their cadence
and expected hours are not invented from instrument names. The bulk update does
not automatically rewrite stored schedules or historical upload reports.

## Catalog-Driven Element Configuration and Scores

Observation-upload element IDs and names now come exclusively from the instrument
catalog, not the VBA parameter names or the uploaded file's element_name column.
`parameters_taken` contains `|`-separated names in the same order as `element_ids`.
Counts must match and names must be nonblank. IDs shared by multiple instruments
may have display aliases. Uploads report name-count errors and stop until
these are corrected. No names are guessed or silently duplicated.

`catalog_configuration` overlays catalog identities and category-matched station proposals
onto saved schedules. Existing cadence, hours, enabled flags, and station-hour
overrides remain. Cloud parents remain when both IDs exist in the catalog;
references to removed IDs are removed. Archived schedules for temporarily unmapped IDs are retained
without contributing expected observations, and are restored when mapped again.
New IDs without saved schedules require Admin to configure and
save a schedule; they initially default disabled rather than inventing hours.
Element IDs/names are catalog-managed on the configuration page. Proposals
follow element ID -> catalog instruments -> their
station categories -> registered stations in those categories. Categories from
multiple instruments providing the same element are combined, and station codes
are deduplicated and restricted to the signed-in user's accessible stations.
The matching category names appear beside each proposed list. Admin may uncheck
stations individually, reselect them, or clear all, then save with an edit reason.
`excluded_station_codes` stores these per-element choices; selected station codes
are the category-matched proposals minus these exclusions. An empty selection
means none, never All stations. New matching stations are proposed by default;
previously unchecked stations remain excluded after reload. A proposal does
not create an assignment or assume availability: only actual Operational
station-instrument assignments contribute expected counts. Empty category
matches exclude all stations rather than meaning All stations.
The catalog configuration response includes `catalog_schema_version=3`. The
page refuses to display old VBA configuration or upload data when the running
backend lacks this capability marker. Restart the backend and hard-refresh the
page after updating the application; refreshing HTML alone does not reload
Python modules in an already-running Uvicorn process.

New upload snapshots contain `element_results`: station, month, catalog element
ID/name, expected, available, missing and score. Scores are available/expected;
zero expected is Not assessed, never 100%. The latest-results endpoint applies
the same station/district/month filters and latest station-month run selection to
element results, then computes weighted totals per element ID and recorded name.
Old runs without element scores require re-upload and are not rewritten.
Element scores appear in summary/detail tables, PDF and Word reports, CSV reports
as a separate table, and `element_scores.csv` inside Details ZIP.

The instrument catalog stores editable `element_ids` as a JSON array, displayed
as pipe-separated Elements and included in catalog CSV/PDF exports and edit
history. CSV import accepts `Elements` or `element_ids`; repeated instrument
names merge their IDs. An import without that column preserves existing IDs.
Station Instruments also displays Element IDs per assignment, sourced from its
catalog instrument. This column is searchable, sortable, included in CSV/PDF
exports, and captured in subsequent station-instrument edit snapshots. An
unmapped instrument displays Not mapped; its IDs are defined in the catalog.

Station Instruments includes Unavailable. Each new upload uses dated
station-instrument status history for the uploaded period. Only
Operational assignments contribute expected observations. Needs Calibration,
Needs Replacement, Under Maintenance, Inactive, and Unavailable are excluded.
Multiple instruments supplying one element use OR logic: any Operational
assignment makes the element expected, without double-counting it. An element
mapped in the catalog but not assigned at a station is excluded there.

Admin's Category assignments panel adds missing active instruments at stations
matching their catalog station categories. It is repeatable, preserves existing
assignments, and initializes new assignments as Operational. Installation dates
remain blank; the assumed operational baseline is marked in the comment, not
presented as a real installation date. The initial local setup added 2,962
assignments, leaving 2,964 Operational assignments. Thirteen mappings from the
supplied table were initialized following owner confirmation: soil 10 cm=176,
20 cm=179, 50 cm=190, 100 cm=177, and Hygrograph=184.
These are owner-specified mappings, not corrections to the original parameter
definitions. Hygrograph uses only element 184, as subsequently corrected by the
owner. Unspecified IDs remain unmapped.

`backend/instrument_availability.py` loads station-scoped availability snapshots
and implements category defaults. `station_instrument_availability` stores
dated audit states and is included when a station-instrument record is deleted and
restored. Availability periods are also captured in edit-history snapshots.
These dated states now calculate upload expectations. The
upload snapshot explicitly records `availability_mode=dated_status`.

Every observation upload saves its inventory snapshot in `config_snapshot`.
Subsequent edits do not change earlier reports: upload again to reassess.
Mapped daily elements use inventory instead of legacy daily exclusions. Existing
parameter schedules, cloud-parent exemptions and station-specific hour overrides
remain active. Unmapped elements retain configured expectations and are listed
in report warnings, preventing an incomplete mapping from silently inflating
performance. New uploaded registered station codes receive a temporary profile
using default parameter hours; saved station-specific profiles still take precedence.
