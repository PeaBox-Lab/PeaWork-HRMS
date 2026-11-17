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
> `peawork/` with a green v2 test suite (`pytest tests/v2 -q`). Remaining R1
> items are CSV import/export, document acknowledgements, Hindi localisation,
> CI/coverage gates and the operational load/restore rehearsals. See
> `docs/V2_QUICKSTART.md`. A GitHub Actions workflow now covers lint, the v2
> tests and migration checks; type-checking, container builds, SBOM and coverage
> thresholds remain open.

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
- [ ] Document register + private storage + authenticated download + acknowledgements.

### Leave
- [x] Leave types, entitlements, holiday calendar, working-day calculation.
- [x] Balances with accrual, carry-forward, and negative-balance policy.
- [x] Request → manager approval → HR override, with overlap and balance checks.
- [x] Cancel/withdraw, half-days, comp-off hooks (calculation later).
- [x] Team calendar (month view).

### Experience
- [x] Responsive PWA shell, accessible navigation.
- [ ] English + Hindi (gettext).
- [x] Employee self-service: profile, leave, documents, notifications.
- [x] Manager workspace: team, pending approvals.
- [x] Admin console: users, roles, settings, audit.

### Delivery
- [x] Docker Compose: app + PostgreSQL + Caddy.
- [x] systemd unit for non-Docker deployments.
- [x] `Makefile`: `make run`, `make test`, `make lint`, `make migrate`, `make backup`.
- [ ] CI: lint, type-check, tests, migration check, container build, SBOM.
- [x] Operations runbook: deploy, rollback, restore, rotate secrets, upgrade.

### Quality gates
- [ ] ≥ 80% line coverage on domain services; critical authz matrix 100% covered.
- [ ] `pip-audit` clean of critical/high; container scan clean.
- [ ] axe-core clean on login, dashboard, employee, leave pages.
- [ ] Load test: 100 concurrent users, p95 < 500 ms for list pages.
- [ ] Backup/restore rehearsal with RPO ≤ 15 min, RTO ≤ 2 h.

**Exit:** Go/No-Go checklist in the audit passes; 2-week UAT with a real HR team; payroll explicitly excluded.

---

## R2 — Time & attendance (v2.1)

- Shifts, weekly-off patterns, effective-dated work schedules.
- Attendance capture: manual, CSV, biometric device API (HMAC-signed).
- Derived worked/overtime/late/early with a documented rule engine.
- Timesheets with project/activity, submit/correct/approve.
- Leave accrual and attendance reconciliation as **idempotent scheduled jobs**.
- Background worker (Dramatiq + Redis, or PostgreSQL queue), retries, dead-letter, job console.
- Manager calendar combining leave, attendance, holidays.
- Data quality: duplicate punch resolution, timezone-aware timestamps, device clock skew policy.

**Exit:** payroll can consume a frozen, reconciled time period; job failures are visible and retryable.

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
