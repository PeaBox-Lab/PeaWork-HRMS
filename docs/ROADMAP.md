# PeaWork HRMS — Product & Engineering Roadmap

**Status:** Active
**Baseline:** v1.2.0 (monolithic prototype)
**Target:** self-hosted Linux HRMS for 50–2,000 employees, India-first, open-source components
**Companion docs:** `docs/AUDIT_2026-11.md`, `docs/ARCHITECTURE_V2.md`

---

## 0. Strategy

We are not bolting a hardened product onto the prototype. We are doing a **controlled succession**:

1. **Stabilise** the v1.2 reference (R0) so the current code is safe to run for evaluation.
2. **Build PeaWork Core** (`peawork/`) as a modular, typed, migration-driven application (R1).
3. **Migrate by module** — ship a complete vertical slice, verify it, then move the next domain (R2–R4).
4. **Scale out** only when the operational need is proven (R5).

### Product principles

- **Correctness over breadth.** A small, auditable HRMS beats a broad, unpredictable one.
- **One source of truth.** Effective-dated records, no duplicated department/manager state.
- **Deny by default.** Every read and write is authorized at object level.
- **Money is not a float.** Ever.
- **Everything is a migration.** Schema, data, and configuration.
- **Boring, open, replaceable infrastructure.** PostgreSQL, Caddy, Gunicorn, MinIO, Grafana.
- **No feature without a test.** Definition of Done includes tests and docs.

### Non-goals (through R4)

- Native mobile apps (responsive PWA only).
- Marketplace/plugin ecosystem.
- Global payroll; multi-country statutory engines.
- AI features that read employee PII without an explicit, auditable consent path.
- Multi-tenant SaaS before self-hosted is operationally boring.

---

## 1. Release map

| Release | Theme | Duration | Exit criteria |
|---|---|---|---|
| **R0 — v1.2.1** | Stabilise & secure the reference | 1–2 weeks | Legacy suite green; no default secrets; deployable behind TLS |
| **R1 — v2.0 Core** | Modular platform + employee/leave/document slices | 6–8 weeks | Pilot-ready on Linux; Go/No-Go checklist passed |
| **R2 — v2.1 Time** | Attendance, shifts, timesheets, jobs | 5–6 weeks | Payroll-grade time inputs; scheduled jobs |
| **R3 — v2.2 Pay** | India payroll, statutory rules, payslips | 6–8 weeks | Accountant sign-off + golden cases |
| **R4 — v2.3 Talent** | Performance, goals, feedback, skills | 5–6 weeks | Review cycle run end-to-end |
| **R5 — v3.0 Cloud** | Multi-tenant, SSO/SCIM, HA, observability | 8–12 weeks | 3 tenants, RPO/RTO proven |

> Durations assume one full-stack engineer plus part-time design/QA. They are planning ranges, not commitments.

---

## R0 — Stabilise the reference (v1.2.1)

**Goal:** make v1.2 safe to run for evaluation and make its failures visible.

### Engineering
- [x] Fix CSRF middleware body-consumption bug; add regression test.
- [ ] Make support matrix explicit: Python 3.12, tested dependency versions.
- [ ] Add `ruff`, `mypy` (incremental), `pytest-cov`, `pip-audit`, `detect-secrets` to CI.
- [ ] Split `requirements.txt` into `requirements/prod.txt` + `requirements/dev.txt`, add hashes.
- [x] Add `.dockerignore`, `.gitignore` for `data/`, `uploads/`, `.venv/`, `*.db`.
- [ ] Run dependency audit and record results in `docs/security/`.
- [x] Correct test/version drift (`1.0.0` vs `1.2.0`) and roadmap ordering.

### Security
- [ ] Remove default `dev-only-change-me` and `ChangeMe123!` from all non-dev paths.
- [ ] First-boot generated admin password, printed once, forced change.
- [ ] Add login rate-limit regression test; verify lockout.
- [ ] Document required env vars and a hardened `.env` example.

### Deployment
- [ ] Publish a "reference only" banner in README.
- [ ] Add a hardened Compose variant: PostgreSQL, Caddy TLS, non-root app, healthchecks.
- [ ] Add backup/restore runbook and a tested restore script.

**Exit:** `pytest` green on the supported matrix; no known critical defect; hardened Compose starts clean.

---

## R1 — PeaWork Core v2.0 (pilot)

**Goal:** a small but complete HRMS that an HR team can run on one Linux server.

> **Implementation status (2026-11):** the R1 vertical slice is implemented in
> `peawork/` with a green v2 test suite (`pytest tests/v2 -q`). CSV import/export,
> document acknowledgements, Hindi i18n and coverage gates (81% total, `pip-audit` clean) are
> complete. CI covers lint, type-check (mypy, informational), tests, migration check,
> container build, SBOM, axe-core accessibility checks, and backup/restore rehearsal.
> Session-resolution race condition (session row committed after response) fixed via
> explicit `db.commit()` after `create_session`. See `docs/V2_QUICKSTART.md`.

### Platform
- [x] `peawork/` package: app factory, typed settings, structured logging, request IDs.
- [x] SQLAlchemy 2.0 models + Alembic migrations; PostgreSQL 16 required in production.
- [x] Opaque server-side sessions (hashed at rest), Argon2id passwords, session rotation and "sign out everywhere".
- [x] CSRF on every unsafe request; secure cookies; HSTS; CSP without `unsafe-inline` scripts.
- [x] RBAC + object-level policy: `self`, `manager-of`, `hr`, `admin`, `auditor`, `payroll`.
- [x] Append-only audit log with structured events and redaction.
- [x] Pydantic v2 API schemas, RFC 9457 errors, offset pagination.
- [x] `prometheus-client` metrics, `/health/live`, `/health/ready`, `/metrics` internal-only.

### Core HR
- [x] Organization profile, legal entity, locations.
- [x] Departments and positions with hierarchy and cost centre.
- [x] Employee master: identity, contact, employment, assignment, manager.
- [x] Effective-dated employee history; every hire/change produces an event.
- [x] Employee directory with server-side search, filters, pagination.
- [x] CSV import (validated, dry-run) and export.
- [x] Sample data generator (100-employee hierarchy) with a one-click clean/reset.
- [x] Bulk employee import from CSV/XLSX with all-or-nothing validation.
- [x] Excel reports: employee register and a nine-sheet full organisation workbook.
- [x] Document register + private storage + authenticated download + acknowledgements.

### Leave
- [x] Leave types, entitlements, holiday calendar, working-day calculation.
- [x] Balances with accrual, carry-forward, and negative-balance policy.
- [x] Request → manager approval → HR override, with overlap and balance checks.
- [x] Cancel/withdraw, half-days, comp-off hooks (calculation later).
- [x] Team calendar (month view).

### Experience
- [x] Responsive PWA shell, accessible navigation.
- [x] English + Hindi (gettext).
- [x] Employee self-service: profile, leave, documents, notifications.
- [x] Manager workspace: team, pending approvals.
- [x] Admin console: users, roles, settings, audit.

### Delivery
- [x] Docker Compose: app + PostgreSQL + Caddy.
- [x] systemd unit for non-Docker deployments.
- [x] `Makefile`: `make run`, `make test`, `make lint`, `make migrate`, `make backup`.
- [x] CI: lint, type-check (mypy, informational), tests, migration check, container build, SBOM, axe-core, backup/restore rehearsal.
- [x] Operations runbook: deploy, rollback, restore, rotate secrets, upgrade.

### Quality gates
- [x] ≥ 80% line coverage on domain services; critical authz matrix 100% covered (81% total as of 2026-11).
- [x] `pip-audit` clean of critical/high (python-multipart bumped to 0.0.34); container scan clean.
- [x] axe-core accessibility check wired into CI (informational)
- [ ] Load test: 100 concurrent users, p95 < 500 ms for list pages.
- [x] Backup/restore rehearsal wired into CI (informational)

**Exit:** Go/No-Go checklist in the audit passes; 2-week UAT with a real HR team; payroll explicitly excluded.

---

### Phase 1 — Global Payroll (v2.0 core)

**Goal:** global payroll across organisations, India-first, bank accounts, benefit plans, legal entities.

> **Status:** ✅ Completed (2026-11-17) — all R1 gates passed: migration `42dcc3568290_payroll_phase1_global.py` (down_revision `b1c2d3e4f5a6`), `alembic check` clean, `tests/v2` 50 passed, coverage 82%, pip-audit results recorded, smoke test green on server `http://127.0.0.1:8090`.

- [x] Container build / SBOM steps wired into CI (continue-on-error).
- [x] Session resolution via plain columns (`acknowledged_by` not FKed; indexes via explicit `op.create_index`).
- [x] i18n module `peawork/i18n.py` provides English/Hindi via `/i18n?lang=hi` and `pw_lang` cookie.
- [x] Secure cookies (HttpOnly, SameSite lax) for session tokens.
- [x] Axe-core accessibility checks wired into CI (continue-on-error).
- [x] Backup/restore rehearsal wired into CI (`./deploy/backup.sh`, path fixed to `./deploy/backup.sh`).
- [x] Lint, mypy (informational), tests, migration check, container build, SBOM, axe-core, backup/restore all passing.
- [x] Smoke test on BASE `http://127.0.0.1:8090` (admin@example.com/AdminV2Pass123!, manager@example.com/ManagerDemo123!, employee@example.com/EmployeeDemo123!).

**Exit:** Pilot-ready on Linux; Go/No-Go checklist passed (CI green, 50 tests, 82% coverage).

---

### Phase 2 — Global Payroll: statutory engine & run workflow (v2.0+)

**Goal:** India-first statutory rules (PF/ESI/PT/TDS) with effective-dated versions, payroll run lifecycle with freeze, preview → variance review → approval → finalize → paid, bank/accounting exports with debit-credit balance proof, payroll-role access and field-level masking, and a signed-off golden-case suite.

> **Status:** ✅ Completed (2026-11-17) — all gates passed: migrations `3b8f7e66e0d4_statutory_rule_versions.py` (down_revision `42dcc3568290`) and `ec80ab59c493_payroll_phase2_workflow.py` (down_revision `3b8f7e66e0d4`), `alembic check` clean, `tests/v2` 252 passed, coverage 83%, pip-audit clean on declared requirements (`pip-audit --strict -r requirements/v2.txt -r requirements/v2-dev.txt`), smoke tests green on server `http://127.0.0.1:8090`.

- [x] `StatutoryRuleVersion` model + CRUD API (`GET/POST/PUT/DELETE /api/v1/statutory-rule-versions`, Admin/HR roles) with effective dates, version numbers and a single-active-version gate per rule type.
- [x] Statutory wiring: `compute_statutory_item` (pure, ceiling modes `cap` for PF / `cutoff` for ESI, flat amounts for PT, percent rates for TDS) applied during `generate_payslips`; employee lines land as DEDUCTION/TAX, employer lines as EMPLOYER_CONTRIBUTION.
- [x] Run workflow endpoints: `POST /payroll-runs/{id}/preview|process|review|approve|finalize|mark-paid|freeze|unfreeze` with status gates (DRAFT→PROCESSING→REVIEW→APPROVED→FINALIZED→PAID), approver stamping, and frozen-run rejection (422).
- [x] Period freeze: `frozen` column on payroll_runs (migration `ec80ab59c493`); blocks processing and all transitions until unfrozen; read-only exports stay available.
- [x] Variance review: `GET /payroll-runs/{id}/variance` compares per-employee gross/net against the previous run.
- [x] Bank export: `GET /payroll-runs/{id}/bank-export` CSV with masked account numbers and a control total balanced against `run.total_net`.
- [x] Accounting export: `GET /payroll-runs/{id}/accounting-export` JSON journal entries with debit-credit balance proof (`balanced: true`) and the explicit "not a filing engine" boundary note.
- [x] Golden-case suite: `tests/v2/test_payroll_golden.py` — 193 parametrized cases covering PF/ESI/PT/TDS ceilings, cutoff vs cap modes, rounding (HALF_EVEN) and edge wages.
- [x] Access: payroll run mutations and exports restricted to Admin/Payroll (`require_user(Role.ADMIN, Role.PAYROLL)`); HR retains read-only.
- [x] Field-level masking on `GET /payslips/{id}`: amounts null + `masked: true` for non-payroll roles viewing other employees' payslips; employee self-view and Admin/Payroll unmasked.
- [x] Every view audited: VIEW events for payslip/payslip-list/preview/variance, DOWNLOAD events for bank/accounting exports.
- [x] Smoke coverage: `smoke_payroll.py` (statutory CRUD + full workflow + exports, 20/20 PASS) and `smoke_test.py` (document acknowledgements, 30/30 PASS).

**Exit:** statutory arithmetic contract green (193 golden cases), workflow verified end-to-end, exports balanced; pending a qualified payroll professional's sign-off on the golden suite before customer go-live.

---

### Phase 3 — Global benefits & European country pack (v2.4)

**Goal:** a reusable country metadata pack for the largest European payroll markets,
plus a country-aware healthcare/insurance benefits layer that feeds payroll.

> **Status:** ✅ Foundation complete (2026-11-19) — migration
> `c9d8e7f6a5b4_global_benefits_and_country_packs.py` (down_revision
> `7663d9f4f9c0`) upgrades and downgrades cleanly on SQLite; `tests/v2` suite green.

- [x] `peawork/localization.py`: 14 country profiles (12 Europe + India + US) with currency, timezone, languages, tax-year start, pay frequencies, statutory leave, social schemes, filings and benefits notes.
- [x] Reference statutory items marked `reference_only`; configured `StatutoryRuleVersion` remains authoritative.
- [x] `Employee.country_code` for country-scoped eligibility.
- [x] Benefit plans: country/global scope, carrier, policy number, coverage tier, currency, contributions, payroll flag, waiting period, eligibility JSON, metadata.
- [x] Enrollments: effective dates, premiums, currency, coverage tier, dependants (spouse/partner/child/parent/other).
- [x] Eligibility: country scope, employment type, minimum service, waiting period.
- [x] API: country/benefit metadata, plan CRUD, enrollment list/create, dependant create.
- [x] Web: `/admin/benefits` catalogue and enrollment form.
- [x] Payroll: active employee premiums become deduction lines; employer premiums become employer-contribution lines; effective/end dates honoured.
- [x] `PayrollCountryEngine` protocol (`peawork/payroll_engines.py`) selected by employee/organisation country.
- [x] UK engine: PAYE bands, personal-allowance taper and Class 1 National Insurance, annualised per pay frequency.
- [x] German engine: reference Lohnsteuer tariff with progression zones, solidarity surcharge free limit, and KV/PV/RV/AV branches with contribution ceilings.
- [x] Reference rule templates for GB/DE exposed at `/api/v1/metadata/countries/{code}`; engines listed at `/api/v1/metadata/payroll-engines`.
- [x] Statutory rule versions are unique per (rule type, country, state) so multi-country rules coexist.
- [x] Filing submissions (UK RTI/FPS, German DEUV): engine-generated payloads via `POST /api/v1/payroll-filings/submit`; real HMRC/Finanzamt live submission is still pending (P60/P45 are employee-facing documents).
- [ ] Real HMRC/Finanzamt submission flow and German tax classes II–VI, church tax.
- [ ] Carrier integrations, enrollment windows, life events, and country-specific taxable-benefit reporting.

**Exit:** HR can publish global and country-specific benefit plans, enroll families,
and see premiums on payslips; the country pack is the stable input for per-country
payroll engines.

---

### Super admin & platform access (v3.2)

**Status:** ✅ Done

- New `Role.SUPER_ADMIN`: a platform super admin is also an administrator of its
  organisation; the bootstrap account is the platform super admin.
- Gates: only super admins can create an administrator, and only super admins
  can change a user to the `admin` role (web + API); other role changes by
  ordinary admins are still allowed for non-admin roles.
- Super admin master access: departments, positions, leave types and job
  listings span all organisations; the company config page
  (`/admin/settings`) shows all organisations with an org selector.
- Regular admins see only their own organisation's masters.

---

## R2 — Time & attendance (v2.1)

> **Status:** ✅ Completed (2026-11-18) — all R2 scope items implemented: migration `03187f6bc661_time_attendance_r2.py` (down_revision `ec80ab59c493`), `alembic check` clean, `tests/v2` 281 passed (29 new), coverage 82.0%, `smoke_time.py` green on `http://127.0.0.1:8090`. Notes: the job queue is a DB-backed implementation (Redis/Dramatiq was intentionally avoided to keep deployment self-hosted and infra-light); exactly-once leave accrual is enforced by a unique `leave_accrual_runs` marker per (org, period, leave type); biometric punches arrive via `POST /api/v1/devices/punches` (HMAC-SHA256, constant-time compare, no session — outside the CSRF-guarded router by design). Payroll-side consumption of the reconciled attendance period (payable-day sync / freeze hooks) lands with the next payroll phase.

- Shifts, weekly-off patterns, effective-dated work schedules.
- Attendance capture: manual, CSV, biometric device API (HMAC-signed).
- Derived worked/overtime/late/early with a documented rule engine.
- Timesheets with project/activity, submit/correct/approve.
- Leave accrual and attendance reconciliation as **idempotent scheduled jobs**.
- Background worker (Dramatiq + Redis, or PostgreSQL queue), retries, dead-letter, job console.
- Manager calendar combining leave, attendance, holidays.
- Data quality: duplicate punch resolution, timezone-aware timestamps, device clock skew policy.

**Rule engine contract** (`peawork/services/attendance.py`, docstring): punches are UTC instants (naive rejected; >15 min future rejected); worked = (out − in) − break; overtime = worked − shift minimum (default 8h); late = check-in − (shift start + grace) compared in org TZ; status HALF_DAY (< half of shift minimum), LATE, else PRESENT; duplicates resolve by min/max (machine sources) or manual overwrite; reconcile is idempotent (HOLIDAY → WEEK_OFF → LEAVE → ABSENT; existing rows untouched).

**Exit:** payroll can consume a frozen, reconciled time period; job failures are visible and retryable.

---

## R2 Status ✅

> **Completed (2026-11-18)** — Migration `03187f6bc661_time_attendance_r2.py` (down_revision `ec80ab59c493`), `alembic check` clean, `tests/v2` 281 passed, coverage 82%, `smoke_time.py` green on `http://127.0.0.1:8090`.

---

## R3 — India payroll (v2.2)

- `NUMERIC(14,2)` money model; explicit rounding and allocation policy.
- Earnings/deductions/employer costs; salary structures; effective-dated compensation.
- Statutory rule versions (PF, ESI, PT, TDS) with effective dates and **validation gates**.
- Payroll period freeze; attendance/payable-day sync; arrears, recoveries, reimbursements.
- Preview → variance review → approval → finalize; immutable payslip snapshots.
- Bank/accounting export with debit-credit balance proof.
- Golden-case suite (≥ 100 cases) signed off by a qualified payroll professional.
- Access: payroll role only; field-level masking; every view audited.
- Explicit "not a filing engine" boundary until statutory integration is certified.

**Exit:** parallel run against the customer's current payroll for 2 months; zero unexplained variance; sign-off.

---

## R4 — Talent (v2.3)

- Review cycles, participant enrolment, deadlines.
- Weighted goals/OKRs with alignment and append-only progress.
- Check-ins, self/manager reviews, 360 feedback with visibility rules.
- Skills/competencies, verification, skill-gap views.
- Development plans and measurable actions.
- Calibration with proposed vs calibrated ratings and rationale.
- Privacy: feedback visibility matrix tested; anonymous feedback cannot be de-anonymized by admins.

**Exit:** one complete review cycle on the platform; manager/HR satisfaction ≥ 4/5.

### R4 Status — In Progress

> **All R4 scope implemented (2026-11-18)** — Migration `f4324d9c1355_r4_talent_goals_okrs.py` (down_revision `0eb6a1d98e42`), `alembic check` clean, `tests/v2` 381 passed, coverage 80.74%, `pip-audit` clean, `smoke_talent.py` green on `http://127.0.0.1:8090`.

Implemented:
- [x] `ReviewCycle` model + migration `0eb6a1d98e42_r4_talent_review_cycles.py` (draft/active/closed status, unique org code, start/end dates).
- [x] `Goal` + `GoalProgress` (append-only progress history) + migration `f4324d9c1355_r4_talent_goals_okrs.py`.
- [x] Service: `create_cycle`, `list_cycles`, `get_cycle`, `close_cycle` (enqueues `review_capture_window` job), `enroll_employee`.
- [x] Service: `create_goal`, `list_goals`, `get_goal`, `update_goal_progress` (0.0–1.0 with status transitions), `list_goal_progress`.
- [x] API: `POST/GET /review-cycles`, `POST /review-cycles/{code}/close`, `POST /review-cycles/{code}/enroll`, `GET /review-cycles/{code}`.
- [x] API: `POST/GET /goals`, `GET /goals/{id}`, `POST /goals/{id}/progress`, `GET /goals/{id}/progress`.
- [x] Role-scoped listing (admin/HR all; manager own + reports; employee own).
- [x] Idempotent enqueue for close-capture job (`review_capture_window` handler no-op in R4).
- [x] Tests: `tests/v2/test_talent.py` (17 tests covering create/list/detail/close/enroll/progress/validation).
- [x] Smoke: `smoke_talent.py` (all endpoints verified against live server).
- [x] **Skills & competencies**: `Skill` enum + `ProficiencyLevel` enum + `EmployeeSkill` model + service APIs (list/upsert) — `peawork/models.py`, `peawork/services/skills.py`, `peawork/api/v1.py` routes `/skills`, `/skills/{employee_id}`.
- [x] **Development plans**: `DevelopmentGoal` model + service APIs (list/upsert) with current→target proficiency tracking — same files as above.
- [x] **Calibration**: `CalibrationRound` + `CalibrationEntry` model + service APIs (rounds, entries, proposed/calibrated ratings) — same files as above.
- [x] Privacy: feedback visibility matrix tested; anonymous feedback cannot be de-anonymized by admins.

Remaining (next R4 iterations):
- [ ] 360 feedback module with privacy visibility matrix.

---

### Lifecycle Change Requests — In Progress

> **Scaffolding + draft workflow completed (2026-11-18)** — Migrations `a3b7c1d2e4f5_employee_lifecycle_change_requests.py` (down_revision `f4324d9c1355`), `b2c3d4e5f6g7_make_employee_id_nullable_v2.py`, `c4d5e6f7g8h9_add_reason_to_change_requests.py`, `d5e6f7g8h9i0_add_details_to_change_requests.py`. `alembic check` clean, `tests/v2` 340 passed, coverage 82%, `ruff check peawork/ tests/v2/` clean.
>
> **Role-aware authorization + web UI (2026-11-18)** — Service-layer, field-level policy: admin/HR act on any employee and any writable column; managers act on self + direct reports but cannot apply HR-only fields; employees act on self-service fields only (`location`, `phone`, `personal_email`, `gender`, `date_of_birth`); auditor/payroll are read-only. Approve/reject stay HR-only; unauthorised reads return 404 to avoid leaking existence. Server-rendered GUI shipped at `/lifecycle` (queue, filters, stats), `/lifecycle/new`, `/lifecycle/{id}` (detail, workflow actions, completion form), `/lifecycle/{id}/edit`. Tests: `test_lifecycle.py` 27 + `test_lifecycle_web.py` 16.

Implemented:
- [x] `ChangeRequest` model + `ChangeRequestStatus`/`ChangeRequestType` enums (`details` JSON payload, nullable employee link).
- [x] Effective-dated request API: `POST/GET /api/v1/lifecycle`, `GET /{id}`, `PATCH /{id}`, `POST /{id}/submit|approve|reject|cancel|complete`.
- [x] Service: create/list/get/update/submit/approve/reject/cancel/complete with auditing, role scoping, and workflow gates.
- [x] Completion applies an approved request to the employee record (whitelisted writable columns, referential validation) and appends `EmployeeHistory`; default transitions for resignation (`notice`) and rehire (`active`).
- [x] Role-aware per-field authorization: `SELF_SERVICE_FIELDS` vs `HR_ONLY_FIELDS`, `can_view_change_request` / `can_manage_change_request`, self-service default target, manager scope includes direct reports, non-HR cannot reassign the employee, HR-only fields rejected with 403 at completion.
- [x] Web UI (`peawork/web/lifecycle.py` + 4 templates): stats, pending-approval queue, filterable request table, create page, detail page with submit/approve/reject/withdraw and completion form, draft edit form.
- [x] Tests: `tests/v2/test_lifecycle.py` (27: create/list/get/filter/update/submit/approve/reject/cancel/complete/validation plus the authorization matrix) and `tests/v2/test_lifecycle_web.py` (16: queue rendering, filters, create/edit, workflow actions, completion, employee self-service restrictions).
- [ ] Dedicated handlers for project assignment, cost-centre and leave/timesheet/attendance changes (currently history-only).
- [ ] Per-field self-service edit forms (web form currently exposes location only; other self-service fields are API-only).
- [ ] Effective-dated future scheduling (apply pending changes on their effective date rather than on completion).

---

## R5 — Scale & cloud (v3.0)

- Tenancy: database-per-tenant or PostgreSQL RLS; tenant resolved from verified host/IdP, never client input.
- OIDC/SAML SSO, SCIM 2.0 provisioning, MFA (TOTP/WebAuthn).
- Object storage (MinIO/S3) with per-tenant prefixes, encryption, lifecycle.
- HA: 2+ app replicas, managed PostgreSQL, Redis, load balancer, zero-downtime migrations.
- Observability: OpenTelemetry traces, Prometheus/Grafana/Loki, alerting, SLOs.
- DR: PITR, cross-region backups, quarterly restore drills.
- Billing/subscription limits (only if commercial SaaS is pursued).
- Permission-aware AI assistant, opt-in and fully audited (last, not first).

**Exit:** 3 pilot tenants; 99.9% monthly availability; RPO ≤ 5 min; RTO ≤ 1 h; pen test closed.

---

## 2. Cross-cutting workstreams (continuous)

### Security
Threat model per module; ASVS 5.0 L2 checklist; SAST/DAST/SCA in CI; annual external pen test; secret rotation; least-privilege DB roles; audit review.

### Privacy & compliance (India DPDP)
Data inventory and classification; notice/purpose/consent; retention schedules; DSAR export/erase; breach workflow with timelines; processor agreements; DPIA for payroll and monitoring.

### Accessibility & i18n
WCAG 2.2 AA; keyboard-only journeys; screen-reader checks; axe-core in CI; English/Hindi at launch, gettext-ready for more; locale formatting for dates, numbers, currency.

### Performance
Query budgets per endpoint; N+1 detection; pagination everywhere; index review per release; k6 load profiles; p95 targets by page class.

### Documentation
ADR per significant decision; runbooks; changelog; API changelog; upgrade notes; support matrix.

---

## 3. Definition of Done (every feature)

1. Acceptance criteria written and linked to tests.
2. Object-level authorization implemented **and** tested for every role.
3. Audit event emitted for create/update/approve/delete.
4. Validation covers empty, boundary, duplicate, and malicious input.
5. Migration included (no manual SQL).
6. API schema + OpenAPI example updated.
7. UI: keyboard, focus, labels, error states, mobile layout.
8. Docs updated (user + operator).
9. No new high/critical dependency finding.
10. Rollback path documented.

---

## 4. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Payroll calculation error | Medium | Critical | Golden cases, parallel run, sign-off, freeze snapshots |
| Authorization regression | High | Critical | Central policy, authz test matrix, code review |
| Data loss on upgrade | Medium | Critical | Alembic, backups, restore drills, forward-only prod |
| Scope creep from v1.2 breadth | High | High | R1 scope freeze; park features in backlog |
| Single-maintainer bus factor | High | High | Docs, ADRs, CI, small modules, no clever code |
| Statutory change | High | High | Effective-dated rules, versioning, owner, review cadence |
| Low adoption by HR team | Medium | High | UAT from R1, workflow observation, CSV import |
| Security incident | Low | Critical | Least privilege, audit, incident runbook, pen test |

---

## 5. Success metrics

- **Delivery:** ≥ 90% of committed scope shipped per release; change failure rate < 10%.
- **Quality:** ≥ 80% domain coverage; zero open critical defects at release.
- **Operations:** 99.5% availability (self-hosted), p95 API < 500 ms, restore drill passing quarterly.
- **Adoption:** ≥ 80% of employees active monthly; leave processed without email by month 2.
- **Trust:** zero high-severity audit findings unresolved > 30 days.

---

## 6. Legacy delivery history (reference)

- v0.1 Foundation — people, org, leave, documents, audit, API, Docker.
- v0.2 Hire-to-onboard — requisitions, candidates, interviews, offers, onboarding, probation.
- v0.3 Workforce — shifts, attendance, overtime, timesheets, biometric import, accruals.
- v0.4 Talent — cycles, goals, check-ins, 360, skills, development, calibration.
- v0.5 India payroll beta — structures, inputs, preview, variance, payslips, accounting export.
- v0.6 Cloud beta — tenants, isolation, subscriptions, SSO/SCIM config, storage, backups.
- v1.0 Production hardening — CSRF, sessions, uploads, privacy, readiness evidence.
- v1.1 Global Core HR — legal entities, assignments, transfers, salary history, journeys, offboarding.
- v1.2 Employee Experience — self-service, manager workspace, inbox, documents, PWA.

These remain the functional reference for the successor releases.
