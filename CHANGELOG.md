# Changelog

## 1.2.0 - Employee Experience
- Added employee self-service profile and assignment workspace.
- Added approval-gated personal/contact data changes with before/after evidence.
- Added employee service requests and HR resolution tracking.
- Added document-version acknowledgements.
- Added notification channel, digest and quiet-hour preferences.
- Added unified inbox references for approvals and lifecycle tasks.
- Added manager team overview, approvals, task list and team calendar.
- Added responsive PWA manifest, icon, service worker and offline fallback.
- Added experience APIs, tests, acceptance criteria and operating documentation.

## 1.1.0 - Global Core HR
- Added global legal entities, locations, cost centers and projects.
- Added job families, profiles, grades, levels and skill requirements.
- Added global employee profiles and identifiers.
- Added effective-dated employee assignments and assignment history.
- Added approval-gated global transfers with inherited target assignments.
- Added approval-gated effective-dated salary history.
- Added job-to-employee skill-gap reporting.
- Added reusable onboarding/transition journey definitions and task evidence.
- Added employee lifecycle events linked to source transactions.
- Added approval-gated offboarding, access/asset/handover/payroll tasks and separation completion controls.
- Added APIs, tests, acceptance criteria and Global Core HR operating documentation.

## 1.0.0 - Production hardening candidate
- Added strict CSRF protection and hardened browser response headers.
- Added versioned password hashing, legacy upgrade and login throttling.
- Added server-side tenant-bound session expiry and revocation.
- Added upload allow-lists, size controls and fail-closed malware scan integration.
- Added cloud/production default-secret and bootstrap-password startup gates.
- Added retention policies, legal holds, retention-run evidence and conservative deletion rules.
- Added data-subject request, grievance and breach-response workflows.
- Added English/Hindi shell localization, skip navigation, focus visibility and reduced-motion handling.
- Added accessibility findings, DR exercise and load-test evidence workflows.
- Added eight evidence-based production-release gates and readiness command.
- Added load-test, dependency-inventory and readiness scripts plus production controls documentation.

## 0.6.0 - Cloud beta
- Added a separate cloud control plane and audit trail.
- Added database/schema-per-tenant isolation and tenant-bound sessions.
- Added PostgreSQL tenant-schema support and cloud Compose configuration.
- Added S3-compatible tenant-prefixed object storage.
- Added subscription plans, lifecycle state and employee-limit enforcement.
- Added custom-domain verification and TLS orchestration state.
- Added OIDC/SAML configuration records and hashed one-time SCIM credentials.
- Added core SCIM user provisioning with role allow-listing.
- Added health checks, metrics, backup policies, backup runs and PITR tracking.
- Added local SQLite preflight and migration into SQLite or PostgreSQL tenant targets.
- Added cloud security tests, acceptance criteria, environment templates and operating documentation.

## 0.5.0 - India payroll beta
- Added configurable earnings, deductions, reimbursements and employer costs.
- Added effective-dated statutory rule versions with qualified-validation gates.
- Added salary structures and effective-dated employee compensation.
- Added attendance/payable-day synchronization from Workforce.
- Added approved variable inputs, arrears, recoveries and reimbursement claims.
- Added payroll previews, prior-period net-pay variances and threshold controls.
- Added finalization gates for statutory validation, calculation errors and GL mappings.
- Added published employee payslips and privacy-aware access.
- Added debit-credit-balanced accounting CSV export.
- Added payroll APIs, lifecycle tests, acceptance criteria and specialist-review documentation.

## 0.4.0 - Talent
- Added performance cycles, deadlines, participants and workflow statuses.
- Added weighted goals/OKRs, parent alignment, metric progress and update history.
- Added structured employee-manager check-ins.
- Added 360 feedback requests, responses and visibility controls.
- Added competency and skill libraries, evidence, proficiency and manager verification.
- Added development plans and measurable development-action tracking.
- Added self, manager, calibrated and finalized performance review outcomes.
- Added calibration sessions with performance/potential buckets, rationale and risk flags.
- Added Talent navigation, dashboard metrics, authenticated APIs and end-to-end tests.
- Preserved the conflict-free local PeaWork port at 8090.

## 0.3.0 - Workforce  
- Fixed Windows setup so installation errors remain visible and successful setup launches PeaWork.  
- Added a reusable daily `start_peawork.bat` launcher.  
- Added shifts, weekly-off patterns and effective-dated employee work assignments.  
- Added attendance upserts with worked-hours and overtime calculation.  
- Added overtime requests and manager/HR decisions.  
- Added weekly timesheets, entries, submission, correction and approval.  
- Added biometric device registration, CSV import and authenticated punch API.  
- Added transactional monthly leave accrual jobs with duplicate-run protection.  
- Added a manager calendar combining attendance and leave events.  
- Added workforce APIs, tests, acceptance criteria and operating documentation.  
## 0.2.0 - Hire-to-onboard  
- Added requisitions, candidates and candidate-to-requisition applications.  
- Added controlled recruitment stages, interviews, scorecards and recommendations.  
- Added offer issuance, response status and accepted-offer handoff.  
- Added configurable onboarding journeys and relative-date task templates.  
- Added generated onboarding task plans, ownership, blockers and completion tracking.  
- Added probation review scheduling and recommendations.  
- Added transactional candidate-to-employee conversion and effective-dated HIRE history.  
- Added recruiter role, navigation, dashboard metrics and authenticated v0.2 APIs.  
- Added end-to-end workflow tests and v0.2 operating documentation.  
## 0.1.0 - Foundation  
- People master, organization and positions.  
- Effective-dated hire event, leave workflow and document register.  
- Dashboard, reports, API, RBAC basics, audit, SQLite and Docker.
