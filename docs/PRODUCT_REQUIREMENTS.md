# Product Requirements    
## Initial market    
India-first, 50   2,000 employee organizations. Local laptop/server launch first while preserving a shared application core for SaaS.    
## Personas    
Employee, manager, HR operations, HRBP, recruiter, payroll administrator, auditor and system administrator.    
## v0.1 acceptance criteria    
1. Admin can authenticate and view a dashboard.    
2. HR can create and locate employees.    
3. Every hire creates a dated history event.    
4. HR can create departments and positions.    
5. User can submit leave; authorized role can approve/reject it.    
6. HR can upload and register employee documents.    
7. HR can export employee data to CSV.    
8. Admin can inspect audit events.    
9. API endpoints require a valid session.    
10. Application can run locally and in Docker.    
## Explicit exclusions from v0.1    
Payroll calculations, statutory filings, biometric devices, recruiting, performance, multi-tenancy, mobile apps and production SSO. These are staged in the roadmap rather than represented as complete.  
## v0.2 acceptance criteria  
1. HR or recruiter can create, open, hold, close and cancel a requisition.  
2. HR or recruiter can register a candidate, r  sum   metadata and source.  
3. A candidate can be linked to an open requisition through a unique application.  
4. An application can progress through the controlled candidate pipeline.  
5. Authorized users can schedule interviews and record ratings, scorecards, feedback and recommendations.  
6. HR or recruiter can issue an offer and record acceptance, decline or withdrawal.  
7. An accepted offer can generate an onboarding plan from a configurable journey.  
8. Generated tasks have due dates, owner roles and tracked statuses.  
9. HR or managers can record a probation review and recommendation.  
10. HR can convert a candidate to an employee with an effective-dated HIRE event and linked audit history.  
11. Requisition, candidate, application and onboarding APIs require a valid authorized session.  
12. Existing v0.1 databases receive the v0.2 schema without destructive migration.  
## v0.3 acceptance criteria  
1. HR can define shifts with start/end time, breaks, grace and minimum hours.  
2. HR can define weekly-off patterns and assign effective-dated work patterns to employees.  
3. Attendance can be created or corrected once per employee per date without duplicate rows.  
4. Worked and overtime hours are derived from punches and shift rules.  
5. Employees can request overtime and authorized roles can approve or reject it.  
6. Employees can create weekly timesheets, record project/activity hours, submit and correct rejected sheets.  
7. Authorized roles can approve or reject submitted timesheets.  
8. HR can register biometric devices and import attendance CSV files with row-level results.  
9. An authenticated biometric API can upsert employee punches.  
10. HR can run a transactional monthly leave accrual across all active employees.  
11. The same leave type and period cannot be accrued twice.  
12. Managers can view attendance and leave events in one monthly calendar.  
13. Windows setup remains visible on failure and launches PeaWork after successful installation.

## v0.4 acceptance criteria
1. HR can create and status-control a performance cycle with review deadlines.
2. HR can enroll employees and assign managers to a cycle.
3. Employees or authorized roles can create weighted goals/OKRs and align them to parent goals.
4. Goal owners can record progress, current metric values, status and evidence over time.
5. Employees and managers can record structured check-ins with accomplishments, blockers, support and next steps.
6. Users can request and respond to 360 feedback with relationship and visibility controls.
7. Manager-only feedback is not displayed to the subject; anonymous-summary feedback masks its author.
8. HR can maintain competency and skill libraries.
9. Employees and managers can assess competencies and skills with evidence and verification.
10. Employees and managers can create development plans and track measurable development actions.
11. Employees can submit self-reviews and managers can submit manager reviews.
12. HR can create calibration sessions, record adjusted outcomes and retain rationale.
13. HR can finalize the participant rating and summary after calibration.
14. Talent APIs require a valid authorized session.
15. Existing v0.3 databases receive the v0.4 schema without destructive migration.

## v0.5 acceptance criteria
1. Payroll can define earnings, deductions, reimbursements and employer-cost components with GL mappings.
2. Statutory components identify an effective rule version and cannot finalize without qualified validation.
3. Payroll can create reusable salary structures with fixed, percent-CTC, percent-component and input calculations.
4. Employees can receive effective-dated compensation assignments.
5. Payroll can define periods and synchronize attendance/payable-day inputs from Workforce.
6. Authorized users can enter and approve variable pay, arrears, recoveries and adjustments.
7. Employees can submit reimbursement claims and authorized roles can decide them.
8. Payroll preview calculates gross pay, deductions, net pay and employer cost with two-decimal rounding.
9. Preview compares employee net pay to the latest finalized prior period.
10. Finalization blocks unresolved calculation errors, unvalidated statutory rules, missing accounting mappings and variance exceptions above the selected threshold.
11. Finalization publishes one payslip per calculated employee.
12. Employees can view only their own payslips; payroll roles can view all.
13. Accounting export identifies debits and credits and is delivered only when balanced.
14. Payroll APIs require an authorized session.
15. Existing v0.4 databases receive the v0.5 schema without destructive migration.

## v0.6 acceptance criteria
1. The control plane can create an isolated tenant and initialize the full PeaWork schema.
2. Tenant application data is stored in a separate SQLite file or PostgreSQL schema.
3. A tenant session token cannot be replayed against another tenant.
4. Switching tenants invalidates the previous application session.
5. Only the local control tenant can administer the global cloud control plane.
6. Subscription employee limits block employee creation and candidate conversion at the configured threshold.
7. Custom domains have unique verification tokens and tracked verification/TLS states.
8. OIDC/SAML provider configuration is recorded per tenant without representing unvalidated federation as enabled.
9. SCIM bearer tokens are displayed once, stored only as hashes and provision users only into the target tenant.
10. Uploads support tenant-prefixed local or S3-compatible object storage.
11. Health checks record control and tenant database status and latency.
12. Backup policies record retention, PITR, RPO and RTO; backup runs preserve outcome and location.
13. Migration preflight inventories the full SQLite source and requires explicit execution confirmation.
14. Migration supports isolated SQLite destinations and PostgreSQL tenant schemas.
15. Cloud APIs require a control-tenant administrator.
16. Existing v0.5 local operation remains available on port 8090.


## v1.0 acceptance criteria
1. Unsafe browser requests require a valid session-bound CSRF token.
2. Cloud/production responses use secure cookies, HSTS and hardened browser headers.
3. Sessions are tenant-bound, registered server-side, expiring and revocable.
4. Failed login attempts are throttled and create durable security evidence.
5. Password hashes are versioned and legacy hashes upgrade after successful authentication.
6. Uploads enforce file extension, content type and size; production can require fail-closed malware scanning.
7. Cloud/production startup rejects default secrets and bootstrap passwords.
8. Retention jobs operate only on approved, non-held policies and preserve run counts/evidence.
9. Privacy requests cannot complete without verified identity and evidence.
10. Breach incidents cannot close without containment and notification rationale.
11. The shell supports keyboard navigation, visible focus, reduced motion and English/Hindi locale selection.
12. Accessibility findings retain severity, status, owner and resolution evidence.
13. Completed DR exercises require verified restore evidence, actual RPO/RTO and approver.
14. Load-test records retain approved concurrency context, errors and latency percentiles.
15. Production readiness remains false until all required external/internal evidence gates are Verified.
16. Open high/critical security events block the readiness command.
17. Existing v0.6 tenant, PostgreSQL, storage and migration behavior remains available on port 8090.

## v1.1 acceptance criteria
1. HR can maintain global legal entities, locations, cost centers and projects.
2. HR can maintain job families, profiles, levels, grades and job skill requirements.
3. Each employee can have a unique global employee profile and identifier.
4. Effective-dated assignments preserve entity, location, organization, project, job and manager history.
5. Assignment creation rejects overlapping periods for the same assignment number.
6. Transfers require approval, close the old assignment and create a linked new assignment.
7. Transfer approval synchronizes current employee master fields and records an event.
8. Salary changes require approval and preserve non-overlapping salary history.
9. Job requirements compare to employee skills and identify proficiency gaps.
10. Journey definitions generate relative-date tasks with role-based ownership.
11. Evidence-required journey tasks cannot close without support.
12. Offboarding approval generates access, asset, handover, payroll and exit tasks.
13. Separation completion is blocked while required offboarding tasks remain open.
14. Completed separation closes the assignment, inactivates the employee/user and records a lifecycle event.
15. Core-HR APIs require a valid authorized session.
16. Existing v1.0 tenant, security, privacy and readiness controls remain intact.
