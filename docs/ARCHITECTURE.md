# Architecture    
PeaWork starts as a modular monolith. Modules share one transaction boundary while keeping domain separation: identity/access, people, organization, leave, documents, reporting and audit.    
## Current local topology    
Browser     FastAPI/Jinja UI + REST API     SQLite     local upload vault.    
## Local server target    
Browser     reverse proxy/TLS     PeaWork app     PostgreSQL     encrypted file storage. Package through Docker Compose, scheduled backups and restore verification.    
## Cloud target    
CDN/WAF     load balancer     stateless PeaWork application     managed PostgreSQL + object storage + queue/workers. Add tenant_id to all tenant-owned records, database row-level security, per-tenant encryption context, centralized observability, SSO/SAML, SCIM and point-in-time recovery.    
## Why modular monolith first    
For the 50   2,000 employee target, a modular monolith lowers deployment and transaction complexity. Extract payroll calculations, notifications, reporting and integrations into services only after measurable scale or release-cadence pressure.  
## v0.2 modules  
The recruitment and onboarding workflows remain inside the modular monolith and share the existing identity, organization, employee, notification and audit infrastructure. New domain tables isolate requisitions, candidates, applications, interviews, offers, journey templates, onboarding instances, onboarding tasks and probation reviews. Candidate conversion uses one SQLite transaction so employee creation and recruitment closure cannot diverge.  
## v0.3 workforce module  
Workforce operations remain in the shared modular monolith. Effective-dated work patterns join employees to shifts and weekly-off definitions. Attendance is unique by employee/date so manual, CSV and API sources converge on one record. Timesheet headers control submission and approval while dated entries preserve project/activity detail. Leave accrual runs and entries provide run-level traceability; each leave type can run only once per period, and balance changes occur in the same database transaction.


## v0.4 talent module
Talent remains inside the modular monolith and reuses employee, manager, department, identity and audit data. Performance cycles provide the time boundary; participants connect employees and managers to self, manager, calibrated and final outcomes. Goals preserve parent alignment and append-only progress updates. Feedback requests and responses separate workflow state from content and apply visibility filtering in application queries. Skills and competencies use reusable libraries with employee evidence. Development plans own measurable actions. Calibration sessions preserve proposed and calibrated ratings, buckets, rationale and risk flags without overwriting the manager-review evidence.


## v0.5 payroll beta module
Payroll remains in the modular monolith and consumes employee, compensation, attendance, reimbursement, identity and audit records. Configuration is separated into component definitions, statutory rule versions, salary structures and effective-dated employee compensation. Payroll previews snapshot employee totals and component lines so finalized payslips and accounting exports remain traceable even when later configuration changes. Statutory rule versions are effective-dated and validation-gated. Finalization performs calculation, mapping and variance controls; accounting export independently proves debit-credit balance.


## v0.6 cloud control plane
Cloud beta separates global control metadata from tenant application data. The control database stores tenant identity, domains, subscriptions, identity-provider metadata, hashed SCIM credentials, storage settings, backup policies, service checks, migration jobs and cloud audit events. Domain data uses database-per-tenant SQLite for local cloud simulation and schema-per-tenant PostgreSQL for the cloud target. Request context selects one tenant before any domain query executes. Session signatures include the tenant slug. Upload storage keys are tenant-prefixed. The migration assistant copies or loads a complete local SQLite population into one isolated destination.


## v1.0 production control layer
Production hardening adds a request security middleware, server-side session registry, login-attempt and security-event evidence, privacy/retention workflows, breach records, readiness evidence, accessibility findings, DR exercises and load-test results. Control-plane records remain separate from tenant HR data. Release approval is evidence-based: the application can enforce technical controls, but independent assessments and qualified review remain explicit gates rather than self-attested flags.


## v1.1 Global Core HR
Global Core HR adds effective-dated organizational, job, assignment and salary records without replacing the original employee master used by earlier modules. Controlled transfers close the source assignment and create a new target assignment in one transaction. Lifecycle events link each material employment change to its source transaction. Journey definitions and relative-date task templates provide a common engine for onboarding and other employee transitions; offboarding uses a dedicated approval and clearance workflow because separation has irreversible access, asset, payroll and recordkeeping consequences.
