# PeaWork HRMS v2 — Quick Start

PeaWork v2 is a modular, Linux-hostable HRMS built on Python 3.12, FastAPI,
SQLAlchemy 2.x, PostgreSQL 16 and server-rendered Jinja2. It lives alongside the
legacy v1.2 monolith in `app/`, which remains available as a reference
implementation but is not the target for new production deployments.

## What v2.0 includes

- Single-organisation deployment with UUID public identifiers
- Authentication with Argon2id, opaque database-backed sessions, login
  throttling, CSRF double-submit protection and forced password rotation
- Employee master data, departments, positions, reporting lines and history
- Leave types, balances, requests, approvals, cancellation and team calendar
- Private document upload/download with type/size validation, audit trail and acknowledgements
- Append-only audit log, role-based access control and object-level checks
- Admin screens for users, organisation settings and audit review
- Sample data generator (100-employee hierarchy) with a one-click clean/reset
- CSV/XLSX bulk employee import with all-or-nothing validation and manager linking
- Excel exports: employee register and a nine-sheet full organisation workbook
- REST API with bearer/CSRF support and consistent problem+json errors
- Prometheus metrics, liveness/readiness probes and structured request logs
- Alembic migrations, a maintenance CLI, Docker Compose and a hardened systemd unit

## 1. Local development (SQLite)

Requirements: Python 3.12+, `pip`, and a C toolchain only if a wheel is missing.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements/v2-dev.txt

export PEAWORK_ENV=dev
export PEAWORK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export PEAWORK_DATABASE_URL="sqlite:///./data/peawork_v2.db"
export PEAWORK_BOOTSTRAP_ADMIN_EMAIL="admin@example.com"
export PEAWORK_BOOTSTRAP_ADMIN_PASSWORD="ChangeThisNow123!"
export PEAWORK_SEED_DEMO_DATA=true

python -m alembic upgrade head
python -m peawork init --demo
python -m uvicorn peawork.main:app --reload --host 127.0.0.1 --port 8090
```

Open <http://127.0.0.1:8090> and sign in with your bootstrap administrator.

Demo accounts created by `--demo`:

| Account | Password | Role |
| --- | --- | --- |
| `manager@example.com` | `ManagerDemo123!` | Manager |
| `employee@example.com` | `EmployeeDemo123!` | Employee |

Demo data is for development only. Never enable `PEAWORK_SEED_DEMO_DATA` in
production.

### Windows development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements\v2-dev.txt
$env:PEAWORK_ENV = "dev"
$env:PEAWORK_DATABASE_URL = "sqlite:///./data/peawork_v2.db"
$env:PEAWORK_SECRET_KEY = "replace-with-a-long-random-value"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m peawork init --demo
.\.venv\Scripts\python.exe -m uvicorn peawork.main:app --reload --port 8090
```

## 2. Tests

```bash
pytest tests/v2 -q          # v2 suite only
pytest -q                   # full repository suite (v1 + v2)
```

The v1 suite contains one pre-existing order-dependent payroll test recorded as
finding **M-4** in `docs/AUDIT_2026-11.md`; run `pytest tests/test_app.py -q`
for a clean v1-only result.

## 3. Maintenance CLI

```bash
python -m peawork init [--demo]           # organisation, admin, reference data
python -m peawork create-admin --email ... # add another administrator
python -m peawork seed-demo               # demo employees (non-production)
python -m peawork seed-sample --count 100 # 100-employee sample hierarchy
python -m peawork seed-sample --count 50 --replace  # regenerate sample data
python -m peawork clean-sample            # remove only sample-flagged records
python -m peawork export --out report.xlsx          # full nine-sheet workbook
python -m peawork export --employees-only --out employees.xlsx
python -m peawork reset-password --email ... # revoke sessions, issue new password
python -m peawork check                   # schema, counts and readiness summary
```

`init`, `create-admin`, `reset-password` print generated one-time passwords once.
Those accounts must change the password at first sign-in.

## 4. Sample data, bulk import and Excel reports

### 4.1 Generate a sample organisation

```bash
python -m peawork seed-sample --count 100
python -m peawork seed-sample --count 100 --replace   # wipe and regenerate
python -m peawork clean-sample                          # remove only sample data
```

The generator produces a deterministic hierarchy: a CEO, one department head per
functional area, a team manager per sub-team, and scaled individual contributors.
Sample employees are numbered `SMP-0001…`, use the `SMP-` employee-number pattern
and addresses at `@sample.example.com`. All sample records carry the
`is_sample` flag, so cleanup never touches real data.

### 4.2 Bulk employee import

Upload a CSV or XLSX file from the admin console at `/admin/import`, or use the
provided templates:

- `/admin/import/template.csv`
- `/admin/import/template.xlsx`

The importer accepts aliases such as `email`, `emp_no`, `designation`,
`joining_date` and `position_name`. It creates missing departments and positions
when the admin enables auto-create, resolves managers by employee number or email
(possibly on later rows), validates every row, and commits the whole file in a
savepoint. Any error rolls back the entire upload.

### 4.3 Excel reports

- `GET /admin/reports/employees.xlsx` — employee register workbook.
- `GET /admin/reports/full.xlsx` — full report workbook (Summary, Employees,
  Departments, Positions, Leave Requests, Leave Balances, Documents, Users,
  Audit Log).

Downloads are written through the audit trail and formula-injection safe cells are
used on every sheet.

## 5. Production with Docker Compose

```bash
cp deploy/.env.example deploy/.env
# Replace every placeholder: secret key, database password, admin password,
# domain, metrics token and file paths.
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

The stack runs PostgreSQL 16, the application (Gunicorn + Uvicorn workers) and
Caddy for automatic TLS. PostgreSQL is not published on the host. The
application container applies `alembic upgrade head`, bootstraps the
organisation/administrator if needed (idempotent), then starts the server.
For multi-replica deployments, run migrations and bootstrap once as a release
step instead of on every replica.

Check status:

```bash
curl -fsS http://127.0.0.1:8090/health/live
curl -fsS http://127.0.0.1:8090/health/ready
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
```

## 5. Production with systemd

```bash
sudo useradd --system --home /var/lib/peawork --shell /usr/sbin/nologin peawork
sudo mkdir -p /opt/peawork /etc/peawork /var/lib/peawork/documents
sudo chown -R peawork:peawork /var/lib/peawork

# Deploy the repository to /opt/peawork, create its venv and install v2.txt.
sudo -u peawork /opt/peawork/.venv/bin/pip install -r /opt/peawork/requirements/v2.txt

sudo install -m 0640 -o root -g peawork deploy/.env.example /etc/peawork/peawork.env
sudoedit /etc/peawork/peawork.env          # production values only

sudo -u peawork /opt/peawork/.venv/bin/alembic upgrade head
sudo -u peawork /opt/peawork/.venv/bin/python -m peawork init

sudo install -m 0644 deploy/systemd/peawork.service /etc/systemd/system/peawork.service
sudo systemctl daemon-reload
sudo systemctl enable --now peawork
systemctl status peawork
journalctl -u peawork -f
```

Bind the app to loopback and place a TLS-terminating reverse proxy in front of
it. Set `PEAWORK_BEHIND_PROXY=true` and restrict `PEAWORK_ALLOWED_HOSTS` to the
public hostname.

## 6. Configuration

All settings use the `PEAWORK_` prefix and can be provided as environment
variables or in a `.env` file. Key production settings:

| Variable | Purpose |
| --- | --- |
| `PEAWORK_ENV` | `dev`, `test` or `production` |
| `PEAWORK_SECRET_KEY` | Random value of at least 32 characters |
| `PEAWORK_DATABASE_URL` | `postgresql+psycopg://...` in production |
| `PEAWORK_BASE_URL` | Public base URL, used for cookies and absolute links |
| `PEAWORK_ALLOWED_HOSTS` | Comma-separated trusted hostnames |
| `PEAWORK_BEHIND_PROXY` | Trust `X-Forwarded-For` from the reverse proxy |
| `PEAWORK_COOKIE_SECURE` | `true` behind HTTPS |
| `PEAWORK_BOOTSTRAP_ADMIN_*` | First-boot administrator |
| `PEAWORK_STORAGE_DIR` | Private document directory |
| `PEAWORK_MAX_UPLOAD_BYTES` | Upload size ceiling |
| `PEAWORK_METRICS_TOKEN` | Bearer/token protection for `/metrics` |
| `PEAWORK_SEED_DEMO_DATA` | Must remain `false` in production |

Production startup is refused when the default secret, SQLite, or a missing /
default bootstrap password is used. This is a safety gate, not a substitute for
secrets management.

## 7. Migrations and upgrades

```bash
alembic upgrade head                 # apply migrations
alembic current                      # show the applied revision
alembic check                        # verify models and migrations agree
alembic revision --autogenerate -m "describe change"
```

Always back up before upgrading. The Docker entrypoint and systemd
`ExecStartPre` both run `alembic upgrade head`; for multi-worker or
multi-replica deployments, run migrations once as a release step instead.

## 8. Backup and restore

```bash
sudo -u peawork ./deploy/backup.sh
```

The script writes a PostgreSQL custom-format dump, a compressed document
archive and SHA-256 checksums under `/var/backups/peawork`, then prunes backups
older than `PEAWORK_BACKUP_RETENTION_DAYS` (default 14). It targets native or
systemd installs. For Docker Compose, run `pg_dump` inside the `db` service and
archive the `peawork_documents` volume instead.

Restore outline:

1. Stop the application.
2. Strip the `+psycopg` driver suffix from the database URL, then run
   `pg_restore --clean --if-exists -d "$PG_URL" database.dump`.
3. `tar -xzf documents.tar.gz -C /var/lib/peawork`.
4. Start the application and run `python -m peawork check`.

Test restores on a schedule; an untested backup is not a recovery plan.

## 9. Security notes

- Serve only over HTTPS in production; keep `PEAWORK_COOKIE_SECURE=true`.
- Use a dedicated PostgreSQL role with access limited to the PeaWork database.
- Keep the document directory private to the application user.
- Protect `/metrics` with `PEAWORK_METRICS_TOKEN` or network policy.
- Rotate `PEAWORK_SECRET_KEY` deliberately; it invalidates sessions.
- Review `/admin/audit` regularly and forward logs to durable storage.
- Apply dependency and base-image updates, then rerun `pytest -q` and
  `alembic check`.

## 10. Repository layout

```text
peawork/            v2 application package
  api/v1.py         JSON API
  web/              server-rendered routes
  services/         domain and authorisation logic
  migrations/       Alembic environment and revisions
  templates/        Jinja2 templates
  static/           vendored HTMX, CSS, PWA assets
tests/v2/           v2 test suite
deploy/             Dockerfile, Compose, Caddy, systemd, backup
docs/               audit, roadmap, architecture and release notes
app/                legacy v1.2 monolith (reference only)
```

See `docs/ARCHITECTURE_V2.md`, `docs/ROADMAP.md` and
`docs/AUDIT_2026-11.md` for design decisions and release gates.
