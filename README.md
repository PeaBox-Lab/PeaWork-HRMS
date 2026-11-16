# PeaWork v0.3.0

India-first HRIS for 50–2,000 employee organizations. v0.3 adds complete workforce operations to the delivered people and hire-to-onboard foundation while keeping one FastAPI/Jinja application core for local, Docker and future cloud deployment.

## Included

### HR foundation
- Secure login, PBKDF2 password hashing, role-based access and audit events
- Employee master, effective-dated hire history, departments, positions and manager relationships
- Leave requests and approvals, India holiday calendar, document register and CSV export
- Operational dashboard, reports, REST API, SQLite local mode and Docker local-server mode

### v0.3 Workforce
- Shifts, weekly-off patterns and effective-dated employee assignments
- Attendance with calculated worked/overtime hours and duplicate-safe daily upserts
- Overtime requests and approvals
- Weekly timesheets, project/activity entries, submission, correction and approval
- Biometric device register, CSV import and authenticated punch API
- Transactional monthly leave accrual jobs with duplicate-run protection
- Monthly manager calendar combining attendance and leave events
- One-click Windows setup that remains visible and launches the application

### v0.2 Hire-to-onboard
- Requisitions with openings, target dates, hiring managers and lifecycle status
- Candidate directory, résumé registration and source tracking
- Candidate-to-requisition applications with a controlled pipeline
- Interviews, structured scorecards, ratings, feedback and recommendations
- Offers with compensation, joining date and acceptance lifecycle
- Configurable onboarding journeys and relative-due-date task templates
- Generated onboarding plans with task ownership, blockers and completion tracking
- Probation review scheduling, ratings and confirm/extend/exit recommendations
- Atomic employee conversion that creates employee history and closes the successful application/requisition
- New authenticated APIs for requisitions, candidates, applications and onboarding plans

## Run locally

### Windows — one-click setup
1. Double-click `install.bat`.
2. The setup window remains open and reports any installation failure.
3. After successful setup, press a key to launch the server and open PeaWork in your browser.
4. For later use, double-click `start_peawork.bat`; installation is not repeated.

Command-line alternative:
```bat
install.bat
```

### Linux/macOS
```bash
chmod +x install_local.sh
./install_local.sh
. .venv/bin/activate
python run.py
```

Open `http://127.0.0.1:8080`.

Demo credentials: `admin` / `ChangeMe123!`

**Change the password and `PEAWORK_SECRET` before real use.** This remains a functional MVP, not a production compliance certification.

## Docker local server
```bash
export PEAWORK_SECRET='a-long-random-secret'
docker compose up -d --build
```

## Test
```bash
pytest -q
```

## API
Interactive documentation: `/docs`

- `GET /api/v1/employees`
- `GET /api/v1/departments`
- `GET /api/v1/leave-requests`
- `GET /api/v1/requisitions`
- `GET /api/v1/candidates`
- `GET /api/v1/applications`
- `GET /api/v1/onboardings`
- `GET /api/v1/attendance`
- `GET /api/v1/shifts`
- `GET /api/v1/timesheets`
- `POST /api/v1/biometric-punch`

See `docs/ARCHITECTURE.md`, `docs/PRODUCT_REQUIREMENTS.md`, `docs/ROADMAP.md`, `docs/SECURITY.md`, and `docs/V02_HIRE_TO_ONBOARD.md`, and `docs/V03_WORKFORCE.md`.
