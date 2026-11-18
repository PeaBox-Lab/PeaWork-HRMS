# PeaWork HRMS v2 – Deployment on Windows and Linux VMs

**Status:** design note, 2026-11. This document describes how to deploy PeaWork
HRMS to a single-tenant instance per client, behind a reverse proxy (nginx or
Caddy) with TLS. It assumes the application runs on port 8090 via the
development server or a production WSGI/ASGI server.

---

## 1. Quick-start for development (Windows or Linux)

```bash
# 1. Create a venv (once)
python -m venv .venv
.\.venv\Scripts\activate        # Windows PowerShell
# or: source .venv/bin/activate    # Linux/macOS

# 2. Install requirements
pip install -r requirements/v2.txt
pip install -r requirements/v2-dev.txt

# 3. Copy .env and fill in secrets
cp deploy\.env.example .env
# Edit .env: database URL, secret key, admin password, domain, etc.

# 4. Initialise the DB (creates tables and seeds demo data)
.\.venv\Scripts\python.exe -m peawork.main  # starts server once then exits

# 5. Start the dev server (keeps running)
.\.venv\Scripts\python.exe -m uvicorn peawork.main:app
# Listens on 127.0.0.1:8090
```

The dev server is intentionally bound to `127.0.0.1:8090`. A reverse proxy
(nginx/Caddy) is required for public access and TLS.

---

## 2. Production on Linux (Ubuntu / Debian / RHEL)

### 2.1 System service

Create `/etc/systemd/system/peawork.service`:

```ini
[Unit]
Description=PeaWork HRMS v2
After=network.target postgresql.service
Wants=network.target

[Service]
Type=simple
WorkingDirectory=/opt/peawork
EnvironmentFile=/etc/default/peawork
ExecStart=.venv/bin/python -m uvicorn peawork.main:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 2.2 Environment file (`/etc/default/peawork`)

```env
# Required
PEAWORK_DATABASE_URL=postgresql+psycopg://peawork:PASSWORD@localhost/peawork
PEAWORK_SECRET_KEY=a-very-long-random-string-at-least-32-chars
PEAWORK_ENV=production
PEAWORK_DEBUG=false

# Optional but recommended
PEAWORK_BASE_URL=https://hr.example.com
PEAWORK_BEHIND_PROXY=true          # if sitting behind nginx/Caddy
PEAWORK_ALLOWED_HOSTS=hr.example.com
PEAWORK_COOKIE_SECURE=true         # HttpOnly + Secure flags
PEAWORK_BOOTSTRAP_ADMIN_PASSWORD=ChangeMe123!   # change on first login

# Feature flags
PEAWORK_SEED_DEMO_DATA=false
PEAWORK_LOG_LEVEL=WARNING
```

### 2.3 Nginx reverse proxy (TLS termination)

```nginx
server {
    listen 443 ssl;
    server_name hr.example.com;

    ssl_certificate /etc/ssl/certs/hr.example.com/fullchain.pem;
    ssl_certificate_key /etc/ssl/certs/hr.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for CSRF / session cookies
        proxy_set_header Cookie $http_cookie;

        # Increase timeout for file uploads / PDFs
        proxy_connect_timeout 60s;
        proxy_read_timeout 300s;
    }
}
```

```nginx
# Redirect HTTP → HTTPS
server {
    listen 80;
    server_name hr.example.com;
    return 301 https://$host$request_uri;
}
```

### 2.4 Caddy reverse proxy (simpler, automatic TLS)

Install Caddy and configure `/etc/caddy/Caddyfile`:

```
hr.example.com {
    proxy / 127.0.0.1:8090 {
        header_up Host {host}
        header_up X-Real-IP {remote_addr}
        header_up X-Forwarded-For {http_x_forwarded_for}
        header_up X-Forwarded-Proto {scheme}
    }
    # Automatic HTTPS via Let's Encrypt
    tls admin@example.com
}
```

### 2.5 PostgreSQL

Ensure PostgreSQL 16 is running and the `peawork` database exists with a
user that has permissions. The `PEAWORK_DATABASE_URL` in the env file must point
at it.

---

## 3. Production on Windows

### 3.1 Install script (PowerShell)

Save as `deploy-windows-install.ps1` and run as **Administrator** (or with
elevated privileges):

```powershell
# -------------------------------------------------------
# PeaWork HRMS v2 – Windows install (NSSM or Scheduled Task)
# -------------------------------------------------------

# 1. Prerequisites
Write-Host "=== Creating venv ===" -ForegroundColor Cyan
if (-Not (Test-Path .\.venv)) {
    python -Venv .venv
}
Set-Location .\.venv
.\Scripts\activate.ps1

Write-Host "=== Installing deps ===" -ForegroundColor Cyan
Set-Location ..\..
pip install -r requirements\v2.txt
pip install -r requirements\v2-dev.txt

Write-Host "=== Configuring .env ===" -ForegroundColor Cyan
if (-Not (Test-Path .\.env)) {
    Copy-Item deploy\.env.example .env
}
Write-Host "Edit .env manually and set at least:" -ForegroundColor Yellow
Write-Host "  PEAWORK_DATABASE_URL" -ForegroundColor Cyan
Write-Host "  PEAWORK_SECRET_KEY" -ForegroundColor Cyan
Write-Host "  PEAWORK_ENV=production" -ForegroundColor Cyan

Write-Host "=== Installing as a Windows Service (NSSM) ===" -ForegroundColor Cyan
# NSSM download: https://nssm.cc/
if (-Not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Host "NSSM not found – installing..." -ForegroundColor Yellow
    # Download NSSM from https://nssm.cc/ and add to PATH, or skip to Scheduled Task
}
# Create the service
nssm install "PeaWork-HRMS" ^
    -exe "C:\Path\To\peawork\.venv\Scripts\python.exe" `
    -args "peawork.main:app" `
    -start SERVICE `
    -interactive 0 `
    -env "PEAWORK_DATABASE_URL=postgresql+psycopg://user:pass@localhost/peawork" `
    -env "PEAWORK_SECRET_KEY=super-secret" `
    -env "PEAWORK_ENV=production" `
    -env "PEAWORK_BEHIND_PROXY=true" `
    -env "PEAWORK_COOKIE_SECURE=true"

nssm start "PeaWork-HRMS"

Write-Host "=== Done ===" -ForegroundColor Green
Write-Host "Open https://your-domain in a browser." -ForegroundColor Green
```

**If NSSM is not available**, the script falls back to a Scheduled Task:

```powershell
# Fallback: Scheduled Task (run on logon, repeat every 1 minute)
$action = New-ScheduledTaskAction -Execute "python" -Arguments "-m uvicorn peawork.main:app"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "SYSTEM"
$settings = New-ScheduledTaskSettings -ExecutionPolicy Realtime
Register-ScheduledTask -TaskName "PeaWork-HRMS" -Action $action -Trigger $trigger -Settings $settings
```

### 3.2 Reverse proxy on Windows

- **Option A:** Install [nginx for Windows](https://nginx.org/en/download.html) or
  [Caddy](https://caddyserver.com/download) and terminate TLS as on Linux.
- **Option B:** Use [IIS ARR](https://www.iis.net/downloads/microsoft/application-request-routing-arr) to proxy
  `http://localhost:8090` to the public domain.

### 3.3 Post-install steps

1. Browse to `https://your-domain` and log in with the admin password set in
   `.env`.
2. Immediately change the admin password via `/profile`.
3. Review `PEAWORK_ALLOWED_HOSTS` and set your actual domain(s).
4. Set `PEAWORK_BEHIND_PROXY=true` when running behind a reverse proxy.
5. Enable `PEAWORK_COOKIE_SECURE=true` for `HttpOnly; Secure` cookies.
6. Run `alembic upgrade head` once on first start to ensure the DB schema is
   current.
7. Set up regular backups (see § 5).

---

## 4. Client provisioning script

Save as `deploy/scripts/provision-client.sh` (Linux) or
`deploy\windows\provision-client.ps1` (PowerShell).

### 4.1 Linux provisioning script (bash)

```bash
#!/usr/bin/env bash
set -euo pipefail

# Usage: ./deploy/scripts/provision-client.sh CLIENT_DOMAIN CLIENT_EMAIL
DOMAIN=$1
ADMIN_EMAIL=$2

# 1. Clone / pull the repo into /opt/<domain>
REPO_DIR="/opt/${DOMAIN//./-}"
if [ -d "$REPO_DIR" ]; then
    git -C "$REPO_DIR" pull
else
    git clone https://github.com/yourorg/peawork-hrms.git "$REPO_DIR"
fi
cd "$REPO_DIR"

# 2. Bootstrap venv and deps
python -m venv .venv
.source .venv/bin/activate
pip install -r requirements/v2.txt
pip install -r requirements/v2-dev.txt

# 3. Configure .env
cp deploy/.env.example .env
cat > .env <<EOF
PEAWORK_DATABASE_URL=postgresql+psycopg://peawork:${PG_PASSWORD:-changeme}@localhost/${DOMAIN}
PEAWORK_SECRET_KEY=$(openssl rand -hex 32)
PEAWORK_ENV=production
PEAWORK_DEBUG=false
PEAWORK_BASE_URL=https://${DOMAIN}
PEAWORK_BEHIND_PROXY=true
PEAWORK_ALLOWED_HOSTS=${DOMAIN}
PEAWORK_COOKIE_SECURE=true
PEAWORK_BOOTSTRAP_ADMIN_PASSWORD=${ADMIN_EMAIL:-admin@${DOMAIN}}
PEAWORK_SEED_DEMO_DATA=false
PEAWORK_LOG_LEVEL=WARNING
EOF

# 4. Systemd service
cp deploy/systemd/peawork.service /etc/systemd/system/peawork-${DOMAIN}.service
sed -i "s|%DOMAIN%|${DOMAIN}|g" /etc/systemd/system/peawork-${DOMAIN}.service
sed -i "s|%PG_PASSWORD%|${PG_PASSWORD:-changeme}|g" /etc/systemd/system/peawork-${DOMAIN}.service
systemctl daemon-reload
systemctl enable --now "peawork-${DOMAIN}"

# 5. Nginx / Caddy TLS (example for Caddy)
# ... (see § 2.4)

# 6. First login – change password
echo "1. Open https://${DOMAIN} and log in"
echo "2. Change the temporary admin password via /profile"
echo "2. Run alembic upgrade head if not automatic"
```

### 4.2 Windows provisioning (PowerShell)

See the install script in § 3.1 – the same script provisions a single client.
For multi-client, run the script per client with different domain/email values.

---

## 5. Backup and restore

### 5.1 Linux – PostgreSQL dump (cron)

```bash
# /etc/cron.d/peawork-backup
0 2 * * * /usr/local/bin/peawork-backup.sh
```

```bash
#!/usr/bin/env bash
# /usr/local/bin/peawork-backup.sh
set -euo pipefail
BACKUP_DIR="/var/backups/peawork"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"

# PostgreSQL dump
pg_dump -U peawork -d peawork > "$BACKUP_DIR/peawork-db-$TIMESTAMP.sql"

# Optional: documents directory (if using file storage)
tar -czf "$BACKUP_DIR/peawork-documents-$TIMESTAMP.tar.gz" /opt/peawork/data/documents

# Keep only last 14 days
find "$BACKUP_DIR" -name "peawork-db-*.sql" -mtime +14 -delete
find "$BACKUP_DIR" -name "peawork-documents-*.tar.gz" -mtime +14 -delete
```

### 5.2 Windows – Task Scheduler backup

Use the built‑in Windows Task Scheduler to run `pg_dump` (via WSL or PG native
client) and zip the documents folder. Alternatively, use a third‑party backup
agent that supports SQL Server / PostgreSQL.

### 5.3 Restore

- **Linux:** `pg_restore -U peawork -d peawork < peawork-db-YYYYMMDD-HHMMSS.sql`
- **Windows:** Restore the `.sql` file via `psql` (PG native client or pgAdmin).

---

## 6. Enabling for clients

1. **Provision the VM** – use the scripts above or the manual steps.
2. **Configure the domain** – DNS A record → VM public IP; reverse proxy TLS
   cert via Let's Encrypt (Linux) or third‑party (Windows).
3. **Set the client’s env** – database URL, admin password, base URL, allowed
   hosts. Keep `PEAWORK_SEED_DEMO_DATA=false`.
4. **Test the login** – `admin@example.com` / the temporary password.
5. **Change the admin password** immediately on first login.
6. **Invite the client’s users** – add employees, managers, HR via the admin UI
   or API.
7. **Go live** – the client can now use PeaWork HRMS at `https://<their-domain>`.

---

## 7. Checklist before going live

- [ ] `.env` filled with real database URL, secret key, admin password
- [ ] `PEAWORK_BEHIND_PROXY=true` if behind nginx/Caddy
- [ ] `PEAWORK_COOKIE_SECURE=true` for Secure/HttpOnly cookies
- [ ] `alembic upgrade head` applied (exit‑code 1 from INFO stream is normal)
- [ ] Reverse proxy (nginx/Caddy) configured with TLS, forwarding to `:8090`
- [ ] `PEAWORK_ALLOWED_HOSTS` set to the actual domain(s)
- [ ] PostgreSQL running and reachable from the app
- [ ] NSSM service (Windows) or systemd service (Linux) started and `Restart=always`
- [ ] First admin login: change the temporary password
- [ ] Optional: run `pip-audit --scope requirements/v2.txt requirements/v2-dev.txt`
- [ ] Optional: run `ruff check peawork tests/v2` – should be clean
- [ ] Optional: run `mypy peawork/ --ignore-missing-imports` – informational only
- [ ] Optional: schedule regular backups ( § 5 )
- [ ] Optional: set up monitoring (Uptime Kuma, Prometheus + Grafana, etc.)