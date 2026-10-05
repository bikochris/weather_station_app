# Deployment and account setup

## Windows network launcher

Double-click `Start Weather Station App.bat` to open the local launcher. Enter
the MariaDB server hostname or IP address shown in the HeidiSQL session settings,
test the connection, and select **Start app**. The launcher displays the network
URL that other users on the same network can open.

The database password is stored only in the local `.env` file, which is excluded
from Git. A remote MariaDB server must allow connections from the computer running
the application. Windows Firewall must also allow inbound TCP traffic to the
selected application port (8000 by default).

## Railway deployment

1. Create a private GitHub repository and push this project to it. Confirm that
   `.env` is not included.
2. In Railway, create a project and choose **Deploy from GitHub repo** for the app.
   Railway detects the included `Dockerfile`.
3. Add a MySQL database to the same Railway project.
4. On the application service, add these reference variables from the MySQL service:

   ```text
   DB_HOST=${{MySQL.MYSQLHOST}}
   DB_PORT=${{MySQL.MYSQLPORT}}
   DB_USER=${{MySQL.MYSQLUSER}}
   DB_PASSWORD=${{MySQL.MYSQLPASSWORD}}
   DB_NAME=${{MySQL.MYSQLDATABASE}}
   ```

   `MySQL` must match the database service name shown on the Railway canvas.
5. Under the app service's Networking settings, generate a public domain.
6. Set `CORS_ORIGINS` to that complete HTTPS domain, without a trailing slash.
7. Set the health-check path to `/health`, then redeploy the app service.
8. Open the generated domain. It redirects to `/app/login.html`, where the first
   IT administrator is created.

Keep the MySQL service private. The web application uses Railway's private service
variables, so public database access is not required.

## First account

1. Start the API and open `http://127.0.0.1:8000/app/login.html`.
2. When no users exist, the page displays the one-time administrator form.
3. Create the first account. It is assigned the `Admin` role.
4. Sign in, open **Users**, and assign each account its operational role and stations.

Passwords must contain at least eight characters. Use unique passwords of at least
12 characters for a published system. Passwords are stored as salted PBKDF2-SHA256
hashes; session tokens are stored only as SHA-256 hashes.

## Permissions

- `Admin`: manage users, operational records, and the activity log.
- Instrument Maintenance and Calibration Officer: manage maintenance and technical responses.
- Observation Officer and Observation Supervisor: work with assigned stations.
- Data quality and processing roles: manage reporting, quality reviews, and relevant workflows.
- Specialist and Division Manager roles: review network records and assigned workflow decisions.

These checks are enforced by the API as well as the interface. The final active
administrator cannot be deleted, disabled, or reassigned to another role.

## Production checklist

1. Put FastAPI behind an HTTPS reverse proxy such as Nginx, Caddy, or IIS. FastAPI
   serves the frontend under `/app/` by default.
2. If the frontend is hosted separately, change `frontend/js/config.js` to the
   public HTTPS API address.
3. In `.env`, set `CORS_ORIGINS` to the exact frontend HTTPS origin. Do not use `*`
   in production.
4. Use a dedicated MariaDB account with access only to `Weather_Stations_App` and use a strong
   database password.
5. Keep `.env` outside version control and restrict its filesystem permissions.
6. Back up MariaDB regularly and test restoring those backups.
7. Run the API as a managed service rather than from an interactive terminal.

Example API command behind a reverse proxy:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 2
```

Example production settings:

```text
DB_HOST=127.0.0.1
DB_PORT=3308
DB_USER=Chris32
DB_PASSWORD=replace-with-a-strong-password
DB_NAME=Weather_Stations_App
CORS_ORIGINS=https://weather.example.org
```

