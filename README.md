# PeaWork v0.1.0

India-first HRIS foundation for 50–2,000 employees, designed as one codebase for local and future cloud deployment.

## Included

- Secure login, role-based access and PBKDF2 password hashing
- Employee master and effective-dated employment timeline
- Departments, positions, grades and manager relationships
- Leave types, India holiday calendar, requests and approvals
- Employee document register and uploads
- HR dashboard and operational reports
- CSV employee export
- Immutable-style application audit log
- REST API and OpenAPI docs
- SQLite local mode and Docker local-server mode
- Automated smoke and workflow tests

## Run locally

### Windows

```bat
install_local.bat
.venv\Scriptsctivate
python run.py
```

### Linux/macOS

```bash
chmod +x install_local.sh
./install_local.sh
. .venv/bin/activate
python run.py
```

Open http://127.0.0.1:8080

Demo credentials: `admin` / `ChangeMe123!`

**Change the password and `PEAWORK_SECRET` before real use.** This build is an MVP and must receive an external security and payroll/compliance review before production use.

## Docker local server

```bash
export PEAWORK_SECRET='a-long-random-secret'
docker compose up -d --build
```

Open `http://server-ip:8080` on your LAN.

## Test

```bash
pytest -q
```

## API

Interactive documentation: `/docs`

- `GET /api/v1/employees`
- `GET /api/v1/departments`
- `GET /api/v1/leave-requests`

## Deployment branches

- **Local desktop/server:** SQLite or PostgreSQL migration target, local documents, Docker/LAN.
- **Cloud:** same domain/UI code; replace storage and session infrastructure, add tenant isolation, PostgreSQL, object storage, SSO, monitoring and PITR.

See `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/SECURITY.md`, and `docs/PRODUCT_REQUIREMENTS.md`.
