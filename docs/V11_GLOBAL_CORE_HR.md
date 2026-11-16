# v1.1 Global Core HR

## Purpose and scope

This release expands the employee system of record around global legal entities, locations, cost centers, projects, jobs, effective-dated assignments, transfers, salary history, skills, onboarding/transition journeys, employee events and offboarding. It preserves all v1.0 security and production-readiness controls.

## Global organization and job data

- Legal entities store country, registration, masked tax identifier, currency and timezone.
- Work locations store entity, country, state/region, city, timezone and address.
- Cost centers support legal-entity ownership, hierarchy and employee owner.
- Projects support entity, cost center, manager, dates, client and status.
- Job families group job profiles. Job profiles store code, title, level, grade, description and responsibilities.
- Job skill requirements define minimum proficiency and criticality. Active assignments are compared with employee skill profiles to surface gaps.

## Global employee and assignment record

Each employee may have a unique global employee ID and global profile, separate from local employee number. Effective-dated assignments identify the employee’s legal entity, location, department, cost center, project, job profile, position, manager, employment type and worker type.

An assignment cannot be created when its effective date overlaps another record for the same assignment number. The current-assignment helper selects the active assignment on a requested date.

## Transfers

1. HR creates a transfer against the employee’s active source assignment and effective date.
2. The transfer remains Pending Approval and leaves the source record unchanged.
3. Approval closes the old assignment on the day before the effective date.
4. Approval creates a new assignment, inheriting every field not explicitly changed.
5. The employee master’s department, position, manager and display location are synchronized.
6. A TRANSFER lifecycle event links the source assignment, target assignment, reason and transfer record.

The requester may also approve in this beta because role-level segregation has not been fully separated; production organizations should configure independent requester/approver roles and access reviews.

## Salary data

Salary changes store employee, assignment, currency, annual base, annual variable target, effective dates, reason and approval evidence. Approval closes the prior approved salary record the day before the new effective date and records a SALARY_CHANGE event. Salary history is separate from payroll salary structures so HR compensation history remains effective-dated even when payroll configuration changes.

Salary access remains restricted to HR/admin workflows. Production should add field-level encryption, stricter compensation roles, masking and purpose-based reporting.

## Journeys and task management

Journey definitions support Onboarding, Transfer, Promotion, Return, Offboarding and Custom journeys, optionally scoped by country, entity and employment type. Starting a journey generates tasks from relative offsets and assigns owners by role or manager relationship.

Tasks support Open, In Progress, Blocked, Completed and Waived states. Templates can require completion evidence. A journey completes automatically only when every generated task is Completed or Waived.

## Offboarding

1. HR submits a separation case with type, dates, reason, regretted flag and rehire eligibility.
2. Approval generates access, asset, handover, payroll and exit-interview tasks from the configured templates.
3. Evidence-required tasks cannot close without an evidence reference.
4. Separation cannot complete while a required task remains open.
5. Completion closes the assignment, marks the employee Inactive, disables linked users and records a SEPARATION lifecycle event.

## Controls and evidence

- Effective dates preserve assignment and salary history instead of overwriting current values.
- Transfers and salary changes are approval-gated.
- Required lifecycle/offboarding tasks block completion.
- Evidence-required tasks cannot complete without support.
- Employee events provide a durable, dated history linked to their source transaction.
- Every material create, request, approval, transfer, salary and separation action writes to the application audit trail.

All global employment, compensation, privacy, retention and separation configurations require local legal, tax, payroll and HR review before use in a country.
