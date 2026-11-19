# PeaWork HRMS — R5 Readiness and HR Process Gap Analysis

**Date:** 2026-11-19
**Scope:** `peawork/` (v2.1.0), migrations, web + API surface, tests, R5 infrastructure
**Method:** static inspection, route/model inventory, migration-to-model comparison, full test run
**Status after this pass:** app boots, **381/381 tests pass**, model/migration drift in the audited areas reconciled

> This is an engineering readiness assessment, not legal, tax, or payroll-compliance advice.
> Statutory requirements must be validated with a local payroll/tax professional per country.

---

## 1. Verdict

PeaWork has a credible HRMS core: employee records, leave, attendance, documents, payroll runs,
talent, lifecycle changes, onboarding/offboarding, audit, and a 130-route REST API.
It is **not yet a multi-country HR/payroll product**, and R5 is **scaffolded rather than operable**
in several areas.

The biggest gaps are not missing CRUD screens. They are:

1. **No progressive tax/social-security engine** — payroll applies configurable flat/ceiling rules, not country tax law.
2. **No statutory outputs** — no Form 16/24Q, PF ECR, ESI return, W-2, RTI, or country filing exports.
3. **Several "R5 capabilities" are libraries or config flags with no call site** — MFA, SSO/SCIM, tenancy, billing, object storage.
4. **No recruitment (ATS), expenses, assets, learning, or employee-relations modules.**
5. **No configurable approval/workflow engine or notification delivery** — approvals are per-domain code paths, and the `Notification` table is never written.

### Fixed during this audit (blocking issues)

| Issue | Impact | Resolution |
|---|---|---|
| `peawork/otel.py` imported the OTel SDK unconditionally | **App could not start**; all API tests failed at import | Optional/lazy imports; correct `Resource`, tracer and middleware usage; no duplicate `/metrics` |
| Journey/Separation models did not match migration `7663d9f4f9c0` | Runtime `TypeError` on every journey creation; missing `org_id`, notes, exit flags | Models realigned to migration exactly |
| `ReviewCheckIn` and `FeedbackRequest` model drift | 360-feedback and check-ins crashed; collection error | Both models aligned to migration `9b144bd950a3` |
| `ChangeRequest` missing `details` and job-change columns | Lifecycle create failed; job-change submit/approve returned 404 | Added `details`, `event`, `reason_code`, snapshots, approval_step, applied_at |
| `api/v1.py` lost imports (`User`, `Department`, `Position`, `LeaveType`, `PayrollRun`, `Shift`, `WorkSchedule`) | `NameError` → 500 on many endpoints | Imports restored |
| `security.py` used `struct`/`List` without imports and imported TOTP helpers from the package | MFA helper crash | Imports fixed, constant-time compare, backup-code consumption corrected |
| `minio_s3_module.py` referenced `ClientError` outside its scope | S3 existence/list calls crashed | Lazy import inside functions |
| Health test hard-coded v2.0.0 | False failure after version bump | Test uses `peawork.__version__` |

Verification:

```powershell
.venv\Scripts\python.exe -c "from peawork.main import create_app; create_app(); print('ok')"
.venv\Scripts\python.exe -m pytest tests/v2 -q      # 381 passed
.venv\Scripts\python.exe -m ruff check peawork --select F821
```

**Test coverage caveat:** the 381 tests cover web/API domains (employees, leave, time, payroll,
lifecycle, talent, documents, auth). There are **zero tests** for HA, DR/backup, billing,
OpenTelemetry, MFA/TOTP, SSO/SCIM, tenancy, or object storage.

---

## 2. What is reliably done

"Reliable" here means: implemented end-to-end, exercised by the test suite, and backed by a migration.

| Capability | Evidence | Reliability |
|---|---|---|
| Organizations, users, roles, sessions, CSRF, password policy | `models.py`, `security.py`, `deps.py`, `tests/v2/test_auth_and_csrf.py` | Solid |
| Employee system of record + org structure (departments, positions, managers) | 49 mapped tables, employee API/web, `test_employees.py` | Solid |
| Leave types, balances, requests, approvals, accrual job | `services/leave.py`, `services/jobs.py`, `test_leave.py` | Solid |
| Attendance, shifts, schedules, timesheets, biometric punch ingest | `services/attendance.py`, `timesheets.py`, `api/devices.py`, `test_time.py` | Solid |
| Documents, private download, acknowledgements, audit trail | `services/documents.py`, `test_documents_and_audit.py` | Solid |
| Payroll runs, workflow states, payslips, variance, bank/accounting exports | `services/payroll.py`, `test_payroll*.py` (32 tests incl. golden files) | Solid for Indian flat-rule payroll |
| Statutory rule versions (PF/ESI/PT/TDS) with effective dates and country/state fields | `StatutoryRuleVersion`, `services/payroll.py` | Partial — storage and application work; no statutory calculation engine |
| Talent: review cycles, goals/OKRs, check-ins, 360 feedback, skills, calibration, development goals | `services/talent.py`, `test_talent*.py` (37 tests) | Solid |
| Lifecycle change requests and effective-dated job changes | `services/lifecycle.py`, `job_change.py`, 63 tests | Solid after model fix |
| Onboarding/offboarding journeys and separations | `services/journeys.py`, models now aligned | Functional; limited tests |
| Bulk import, Excel reports (employee register + full workbook) | `services/bulk_import.py`, `reports.py` | Functional |
| English/Hindi UI strings | `i18n.py` (`SUPPORTED = ("en","hi")`) | Partial — UI labels only, no localized formats |
| Audit logging of writes and sensitive reads | `services/audit.py`, audit screens | Solid |

### R5 infrastructure: real vs scaffold

| Area | What exists | Can it be used in production today? |
|---|---|---|
| HA | `ha.py`: node registry, heartbeat, readiness gate, middleware; wired in `main.py` | **No** — in-process state, no shared store, no failover/leader election |
| DR/backup | `backup.py`: `pg_dump`/restore, checksums, retention, cross-region copy hooks, PITR signal helper; `/health/dr/*` when enabled | **Partially** — good primitives, but no scheduler daemon, no restore drills, no WAL archiving setup |
| Billing | `billing.py`: plans, usage counters, limits/overage — **in-memory dicts** | **No** — no provider, no webhooks, no persistence, no routes |
| SSO/SCIM | `sso.py`: OIDC/SCIM helper library | **No** — not routed or wired into login |
| MFA/TOTP | `security.py`: TOTP/HOTP/backup codes, `verify_mfa_token` | **No** — login flow never calls it |
| Object storage | `minio_s3_module.py`: MinIO/S3 adapter | **No** — document service still writes to local disk |
| Tenancy | config flags only (`tenancy_enabled`, resolver path) | **No** — no tenant column, filter, or resolver |
| Observability | `otel.py` (optional), `metrics.py` (Prometheus definitions), `/metrics` | **Partially** — metrics are defined but largely not incremented; tracing is opt-in |

---

## 3. HR process coverage map (hire-to-retire)

Legend: **Done** = end-to-end + tests · **Partial** = core exists, material gaps · **Missing** = no model/service/surface

| HR process | Status | Evidence / gap |
|---|---|---|
| Workforce planning, headcount budget | **Missing** | No plan/requisition/budget models |
| Job architecture, skills, profiles | **Partial** | `Job`, `Skill`, `ProficiencyLevel`, `EmployeeSkill`; no job catalog versioning or compensation bands |
| Recruitment / ATS (requisition → offer) | **Missing** | No Candidate/Application/Interview/Offer models or routes |
| Background checks, e-signature | **Missing** | Documents support upload/acknowledgement only |
| Onboarding | **Partial** | Journey templates/tasks, docs; no asset provisioning, e-sign, or portal checklist notifications |
| Core employee records & org | **Done** | Effective-dated employee history, job changes |
| Transfers, promotions, manager changes | **Done** | `ChangeRequest` + job-change approval, apply-if-due |
| Time & attendance | **Done** | Attendance, shifts, schedules, timesheets, biometric punches |
| Leave & holidays | **Partial** | Accrual + approvals; holidays are org-wide, not per location/legal entity |
| Payroll processing | **Partial** | India-oriented runs; no off-cycle, loans/advances, reimbursements, garnishments, multi-currency FX |
| Statutory compliance & filings | **Partial → Missing** | Rules stored, but no returns, challans, Form 16/W-2, or e-filing exports |
| Benefits administration | **Partial** | Plan catalog + enrollment; no carrier feed, eligibility rules, or payroll deduction linkage |
| Compensation review cycle | **Partial** | Compensation history/components; no cycle budget, merit matrix, or approval workflow |
| Performance management | **Done** | Cycles, goals, check-ins, 360 feedback, calibration |
| Learning & development | **Missing** | Development goals exist; no courses, assignments, or certifications |
| Employee engagement / surveys | **Missing** | No survey or pulse models |
| Employee relations, grievances, disciplinary | **Missing** | No case management |
| Health, safety, incidents | **Missing** | No incident/risk models |
| Offboarding & separation | **Partial** | Separation stages, exit flags, offboarding journey; no asset recovery, exit-interview content, or final-settlement calculation |
| Notifications & reminders | **Missing** | `Notification` model exists but is never created; no email/SMS/push |
| Approvals / workflow engine | **Partial** | Hard-coded approvals per domain; no configurable multi-step workflow |
| ESS / MSS portal | **Done** | Web UI for self-service and manager views |
| Reporting & analytics | **Partial** | Two Excel workbooks; no dashboards, scheduled reports, or HR metrics warehouse |
| Audit, retention, privacy | **Partial** | Strong audit trail; no retention policy, consent register, DSAR export/erasure |
| Integrations | **Partial** | Bank/accounting exports, biometric ingest; no generic webhooks/API keys |

### Concrete missing models

There is no table for: candidate, application, interview, offer, expense claim, loan/advance,
asset assignment, course/enrollment, survey, grievance/case, consent, DSAR request, or workflow
definition/step. `Notification` exists but has no producer.

---

## 4. Country localization

### What already helps

- `Organization.country_code/currency/timezone`
- `LegalEntity.country_code/currency/tax_id`
- `StatutoryRuleVersion.country_code/state_code/effective_from/effective_to/rates(JSON)`
- `TaxRegime` enum with `india_old`, `india_new`, `us_federal`, `uk_paye`, `generic`
- Per-employee `Compensation.currency` and `tax_regime`

This is a good **data foundation**, but the calculation path is one generic
flat/ceiling rule applicator (`apply_statutory_rule`). It cannot express progressive
slabs, wage bases, annualization, exemption caps, or employer/employee splits per country.

### India (primary market)

**Implemented today**
- PF/ESI/PT/TDS rule types; wage ceiling and `cutoff`/`cap` semantics
- Effective-dated rule versions, per state code
- Payslip lines, payroll workflow, bank and accounting exports
- Basic INR defaults, Asia/Kolkata timezone

**Gaps to close**
- Progressive TDS with FY slabs, standard deduction, 80C/80D/HRA/LTA exemptions, old vs new regime comparison
- State professional tax slabs (not one flat PT rule per org)
- PF: EPS/EDLI split, wage ceiling, voluntary contribution, ECR file
- ESI: contribution periods, wage ceiling changes, return file
- Gratuity, statutory bonus, LWF, leave encashment, full-and-final settlement
- Form 16, Form 24Q, challans, statutory registers
- PAN/Aadhaar/IFSC validation and masked display (partially present as masked fields)
- Location-specific holiday calendars and fiscal year handling

### United States

**Status: not implemented.** `TaxRegime.US_FEDERAL` is only an enum value.

**Required**
- Federal withholding (W-4, filing status, dependents, supplemental wages)
- FICA (Social Security/Medicare), FUTA/SUTA, state unemployment and state income tax
- Pre-tax deductions: 401(k), Section 125 (health/FSA/HSA), commuter
- Wage bases, annualization, multi-state allocation, reciprocity
- Pay frequency rules (weekly/biweekly/semimonthly/monthly), FLSA overtime
- W-2, W-2c, 1099-NEC, quarterly 941/state returns
- Garnishments, child support, benefit eligibility (ACA), pay transparency fields

### Europe (country-by-country, not one regime)

**Status: not implemented.** `TaxRegime.UK_PAYE` is only an enum value; there is no EU model.

Europe cannot be served by one calculator. Minimum architecture:

- Per-country payroll engine selected by `LegalEntity.country_code`
- Country-specific statutory rule schemas (JSON) validated per engine
- Employer vs employee contributions, wage bases, ceilings, proration
- Country outputs: payslip legal fields, filing formats, statutory reports

Representative country requirements (engineering scope):

| Country | Core requirements |
|---|---|
| UK | PAYE, National Insurance, student loans, pension auto-enrolment, RTI/FPS, P60/P45, statutory pay |
| Germany | Lohnsteuer, solidarity surcharge, church tax, social insurance (KV/PV/RV/AV), ELStAM, DEÜV |
| France | URSSAF contributions, CSG/CRDS, DSN reporting, payslip mandate, works-council data |
| Netherlands | Loonheffing, social insurance, 30% ruling, pension, loonaangifte |
| Spain/Italy/Poland | Country-specific IRPF/INPS/IRPEF/ZUS and filing formats |

Cross-cutting EU requirements:

- GDPR: lawful basis, consent register, DSAR export/erasure, retention schedules, processor records
- Working Time Directive: rest periods, maximum hours, holiday entitlement
- Works councils / co-determination: change notifications and consultation records
- Payslip and record-retention rules differ by country
- Multi-currency payroll and FX revaluation; local bank formats (SEPA, BACS, etc.)
- EU/UK data residency and cross-border transfer controls

### Recommended localization architecture

1. **`PayrollCountryEngine` protocol**
   `validate_employee()`, `compute_lines(payslip_context, rules)`, `statutory_reports(period)`.
2. **Rule schema per country** — do not force every country into PF/ESI/PT/TDS keys.
3. **Legal entity drives jurisdiction** — employee work location drives state/province.
4. **Statutory artifacts** as first-class outputs: return files, payslip PDFs, registers.
5. **Golden-file tests per country and tax year** (the India payroll golden tests are the right pattern).
6. **Versioned rules with effective dates** — already present; extend to slabs and formulas.
7. **Never compute tax in templates or ad-hoc services** — one engine, one rule source.

---

## 5. Prioritized remediation plan

### P0 — make current claims true (1–3 weeks)

1. Wire MFA into login (`verify_mfa_token`, backup codes, enrollment endpoints, audit events).
2. Persist and deliver notifications; emit them for leave, timesheet, payroll, lifecycle, journeys.
3. Replace generic statutory applicator with a slab-capable tax engine interface; implement India TDS/PF/ESI/PT properly.
4. Add `alembic check` / metadata-vs-migration test to CI to prevent the drift fixed in this pass.
5. Make R5 flags honest: either wire SSO/SCIM, tenancy, billing, and object storage, or document them as experimental and hide them from release notes.
6. Add tests for HA, backup/restore, billing, MFA, and OTel.

### P1 — complete the HR suite (1–2 months)

1. Recruitment/ATS: requisition → candidate → interview → offer → convert to employee.
2. Expenses and reimbursements, loans/advances, and payroll deduction linkage.
3. Asset assignment and offboarding recovery.
4. Learning: courses, assignments, certifications, compliance training.
5. Configurable approval workflow + delegation + SLA escalation.
6. DSAR/consent/retention toolkit (GDPR + India DPDP Act).
7. Statutory outputs: India Form 16/24Q/PF ECR/ESI; UK RTI; US W-2/941.
8. Real ops: shared-state HA, scheduled backup worker, restore drills, alerting.

### P2 — scale and differentiation (quarter+)

1. Compensation review cycles with budgets and merit matrices.
2. Engagement surveys and employee-relations case management.
3. Analytics warehouse, dashboards, scheduled reports.
4. US state-by-state payroll and EU country engines (start with UK + Germany).
5. Billing provider integration (Stripe/Paddle) with webhooks and entitlement enforcement.
6. Health & safety / incident management.

---

## 6. Suggested immediate next build

If the goal is an India-first product that can be sold, the highest-value next increment is:

**Country payroll engine + India statutory outputs**

- Introduce `peawork/payroll/engines/` with `base.py` (protocol) and `india.py`.
- Implement progressive TDS (old/new regime), PF split, ESI, state PT, gratuity, bonus.
- Add Form 16, 24Q, PF ECR, and ESI return exports.
- Add golden-file tests for at least three salary structures and two states.
- Keep the existing `StatutoryRuleVersion` table as the versioned input source.

That closes the gap between "payroll runs" and "payroll is usable by an Indian employer",
which is a stronger differentiator than adding more shallow modules.

---

## Appendix — inventory used for this analysis

| Metric | Value |
|---|---|
| Mapped database tables | 49 |
| Alembic migrations | 16 (single head `7663d9f4f9c0`) |
| REST API routes (`/api/v1`) | 130 |
| Web routes | 85 |
| Service modules | 21 |
| Tests | 381 passing across 16 files |
| Supported UI locales | en, hi |
| Payroll statutory rule types | PF, ESI, PT, TDS |
| Tax regimes declared | India old/new, US federal, UK PAYE, generic |
| Tax regimes implemented | India flat-rule only |
