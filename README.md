# PeaWork v1.0.0  
India-first HRIS for 50–2,000 employee organizations. v1.0 adds production-readiness controls and evidence gates to the tenant-isolated cloud, people, hire-to-onboard, workforce, talent and payroll foundation.  
## Included  
### HR foundation  
- Secure login, PBKDF2 password hashing, role-based access and audit events  
- Employee master, effective-dated hire history, departments, positions and manager relationships  
- Leave requests and approvals, India holiday calendar, document register and CSV export  
- Operational dashboard, reports, REST API, SQLite local mode and Docker local-server mode  
### v1.0 Production hardening candidate
- CSRF, secure response headers, login throttling and revocable tenant-bound sessions
- Versioned password hashing and default-secret/bootstrap startup gates
- Upload type/MIME/size controls and fail-closed malware-scanner option
- Retention policies, legal holds, data-subject requests, grievance and breach workflows
- English/Hindi shell localization and global accessibility improvements
- Accessibility findings, DR exercises and load-test evidence records
- Eight evidence-based production gates for independent security, privacy, accessibility, localization, resilience and performance review
- Readiness command that fails while evidence is missing or high-risk events remain open

### v0.6 Cloud beta
- Separate cloud control plane and cloud audit trail
- Database-per-tenant SQLite and schema-per-tenant PostgreSQL isolation
- Tenant-bound sessions, subscription limits and custom-domain state
- OIDC/SAML configuration and hashed one-time SCIM provisioning tokens
- Tenant-prefixed local or S3-compatible object storage
- Health checks, metrics, backup/PITR policies and backup history
- Local SQLite preflight and migration into SQLite or PostgreSQL tenant targets
- Cloud Compose, environment template and deployment preflight scripts

### v0.5 India payroll beta
- Configurable earnings, deductions, reimbursements and employer costs
- Effective-dated salary structures and employee compensation
- Attendance-linked payable days and loss-of-pay proration
- Approved variable inputs, arrears, recoveries and reimbursements
- Payroll preview, prior-period variance checks and finalization controls
- Employee payslips and debit-credit-balanced accounting export
- Validation gates for statutory components; no statutory rates are represented as filing-ready

### v0.4 Talent
- Performance cycles, participants and review deadlines
- Weighted goals/OKRs with alignment, metrics and update history
- Structured check-ins and evidence-based self/manager reviews
- 360 feedback with relationship and visibility controls
- Skill and competency profiles with manager verification
- Development plans and measurable actions
- Calibration sessions, adjusted ratings, performance/potential buckets and rationale

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
- Candidate directory, r  sum   registration and source tracking  
- Candidate-to-requisition applications with a controlled pipeline  
- Interviews, structured scorecards, ratings, feedback and recommendations  
- Offers with compensation, joining date and acceptance lifecycle  
- Configurable onboarding journeys and relative-due-date task templates  
- Generated onboarding plans with task ownership, blockers and completion tracking  
- Probation review scheduling, ratings and confirm/extend/exit recommendations  
- Atomic employee conversion that creates employee history and closes the successful application/requisition  
- New authenticated APIs for requisitions, candidates, applications and onboarding plans  
## Run locally  
### Windows     one-click setup  
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
Open `http://127.0.0.1:8090`.  
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
- `GET /api/v1/performance-cycles`
- `GET /api/v1/goals`
- `GET /api/v1/feedback-requests`
- `GET /api/v1/skills`
- `GET /api/v1/development-plans`
- `GET /api/v1/payroll-periods`
- `GET /api/v1/payroll-runs`
- `GET /api/v1/payslips`
- `GET /api/v1/cloud/tenants`
- `GET /api/v1/cloud/service-checks`
- `POST /scim/v2/Users`
- `GET /metrics`  
See `docs/ARCHITECTURE.md`, `docs/PRODUCT_REQUIREMENTS.md`, `docs/ROADMAP.md`, `docs/SECURITY.md`, and `docs/V02_HIRE_TO_ONBOARD.md`, `docs/V03_WORKFORCE.md`, and `docs/V04_TALENT.md`, and `docs/V05_PAYROLL_BETA.md`, and `docs/V06_CLOUD_BETA.md`, `docs/V10_PRODUCTION_HARDENING.md`, and `docs/PRODUCTION_CONTROLS.md`.


## Cloud beta quick start

```bash
cp .env.cloud.example .env
# Replace every placeholder secret in .env
docker compose -f docker-compose.cloud.yml up -d --build
python scripts/create_object_bucket.py
```

Run `python scripts/cloud_preflight.py` in the configured application environment before opening cloud access. The cloud beta records SSO, domain, TLS and PITR orchestration state but requires provider integration and independent validation before production use.


## Production readiness

The repository is a production-hardening candidate, not a self-certified production release. Configure approved secrets and scanning, complete independent assessments and operational exercises, attach evidence in Production Readiness, then run:

```bash
python scripts/readiness_check.py
```

The command exits unsuccessfully until every required gate is verified and no open high/critical security event remains.
