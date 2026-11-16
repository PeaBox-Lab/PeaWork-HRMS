# v0.2 Hire-to-onboard

## Workflow

1. HR or recruiter opens a requisition against a department, position and hiring manager.
2. A candidate is registered and linked to one or more open requisitions through applications.
3. The application moves through Applied, Screening, Interview, Assessment and Offer stages.
4. Interviewers record a 1–5 rating, evidence-based scorecard, feedback and recommendation.
5. HR issues an offer. Accepting it makes the candidate available for onboarding.
6. HR selects an onboarding journey. PeaWork generates dated tasks from relative offsets and assigns owners by role.
7. HR and managers track tasks, blockers and probation review outcomes.
8. HR converts the onboarding record into an employee. The transaction creates the employee and HIRE history event, marks the candidate Hired, closes the application and closes a filled requisition.

## Roles

- `admin`: all v0.2 administration and workflow actions.
- `hr`: all recruitment, onboarding, probation and conversion actions.
- `recruiter`: requisitions, candidates, applications, interviews, offers and onboarding start.
- `manager`: read pipeline, complete interview scorecards, update onboarding tasks and record probation reviews.
- `employee`: no recruitment or onboarding access.

## Status controls

- Requisition: Draft, Open, On Hold, Closed, Cancelled.
- Application stages: Applied, Screening, Interview, Assessment, Offer, Offer Accepted, Hired, Rejected, Withdrawn.
- Offer: Draft, Issued, Accepted, Declined, Withdrawn.
- Onboarding task: Open, In Progress, Blocked, Completed, Waived.
- Probation recommendation: Pending, Confirm, Extend, Exit.

## Data and audit behavior

All v0.2 tables are created with `CREATE TABLE IF NOT EXISTS`, so the schema is added safely when an existing v0.1 database starts. Material workflow actions write to the shared audit log. Uploaded résumés use the existing local upload area and should receive malware scanning and content enforcement before production use.

## Production gaps

The release intentionally preserves the v0.1 security boundary. Before production, add CSRF protection, secure cookies, granular business-unit scoping, encrypted storage, malware scanning, centralized audit storage, SSO/MFA and DPDP-validated candidate consent, retention, access and deletion workflows.
